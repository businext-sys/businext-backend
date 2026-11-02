"""
Radar de tendencias (businext-sys/businext#161).

- `GET`/`PUT /trends/preferences`: preferencias de contenido del negocio
  (owner y manager, capability `canViewTrends`).
- `GET /trends/insight`, `POST /trends/insight/refresh` (1 al dia),
  `GET /trends/suggestions` y `PATCH /trends/suggestions/{id}`: analisis del
  dia y sugerencias de contenido (owner y manager).
- `POST /internal/trends/ingest` y `POST /internal/trends/analyze`: ingesta y
  analisis diarios, lanzados por el workflow `trends-ingest.yml`.
- `POST /internal/trends/dispatch`: avisos push de las sugerencias, lanzado
  cada 15 minutos por el cron job de Render `businext-trends-dispatch`.

Las rutas de `/internal/trends` exigen `Authorization: Bearer <TRENDS_JOB_SECRET>`
(401 sin el).
"""

import hmac
import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from src.api.auth import AuthContext, require_manager_or_owner
from src.database.database import SessionDep
from src.database.models.trends_model import (
    ContentPreferences,
    ContentPreferencesPublic,
    ContentSuggestion,
    ContentSuggestionPublic,
    ContentSuggestionStatusUpdate,
    SuggestionStatus,
    TrendInsight,
    TrendInsightPublic,
)
from src.services.suggestion_dispatcher import dispatch_suggestions
from src.services.trend_analysis import TrendAnalysisError, analyze_business, analyze_trends, today_snapshot
from src.services.trend_sources import BUSINESS_TIMEZONE, ingest_trends

from .assistant import require_authorization_header

router = APIRouter(prefix="/trends", tags=["trends"], dependencies=[Depends(require_authorization_header)])


def require_job_secret(authorization: str | None = Header(default=None)):
    secret = os.getenv("TRENDS_JOB_SECRET")
    if not secret or not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Firma del job no valida")


internal_router = APIRouter(prefix="/internal/trends", tags=["trends"], dependencies=[Depends(require_job_secret)])

PREFERENCES_UNAVAILABLE = HTTPException(status_code=503, detail="Las preferencias de contenido no están disponibles")
INSIGHT_UNAVAILABLE = HTTPException(status_code=503, detail="El análisis de tendencias no está disponible")


def _preferences(session, business_id: str) -> ContentPreferences:
    return session.get(ContentPreferences, business_id) or ContentPreferences(business_id=business_id)


def _public(preferences: ContentPreferences) -> ContentPreferencesPublic:
    return ContentPreferencesPublic.model_validate(
        {**preferences.model_dump(), "networks": json.loads(preferences.networks)}
    )


@router.get("/preferences", response_model=ContentPreferencesPublic)
def get_content_preferences(session: SessionDep, auth: AuthContext = Depends(require_manager_or_owner)):
    """Preferencias de contenido del negocio (sin fila: los valores por defecto, desactivado)."""
    try:
        return _public(_preferences(session, auth.business_id))
    except SQLAlchemyError:
        session.rollback()
        raise PREFERENCES_UNAVAILABLE


@router.put("/preferences", response_model=ContentPreferencesPublic)
def update_content_preferences(
    body: ContentPreferencesPublic, session: SessionDep, auth: AuthContext = Depends(require_manager_or_owner)
):
    """Guarda las preferencias de contenido completas del negocio."""
    try:
        preferences = _preferences(session, auth.business_id)
        preferences.sqlmodel_update({**body.model_dump(), "networks": json.dumps(body.networks)})
        preferences.updated_at = datetime.utcnow()
        session.add(preferences)
        session.commit()
        session.refresh(preferences)
        return _public(preferences)
    except SQLAlchemyError:
        session.rollback()
        raise PREFERENCES_UNAVAILABLE


def _utc(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=timezone.utc) if moment else None


def _insight_public(insight: TrendInsight) -> TrendInsightPublic:
    return TrendInsightPublic(
        id=insight.id,
        day=insight.day,
        analysis=insight.analysis,
        matched_services=json.loads(insight.matched_services),
        trends=json.loads(insight.trends_used),
        created_at=_utc(insight.created_at),
        refreshed_at=_utc(insight.refreshed_at),
    )


def _suggestion_public(suggestion: ContentSuggestion) -> ContentSuggestionPublic:
    return ContentSuggestionPublic.model_validate(
        {
            **suggestion.model_dump(),
            "script": json.loads(suggestion.script),
            "source_trend_ids": json.loads(suggestion.source_trend_ids),
            "publish_at": _utc(suggestion.publish_at),
            "sent_at": _utc(suggestion.sent_at),
        }
    )


@router.get("/insight", response_model=TrendInsightPublic)
def get_insight(session: SessionDep, auth: AuthContext = Depends(require_manager_or_owner)):
    """Ultimo analisis de tendencias del negocio, con las tendencias que usa."""
    insight = session.exec(
        select(TrendInsight).where(TrendInsight.business_id == auth.business_id).order_by(TrendInsight.day.desc())
    ).first()
    if not insight:
        raise HTTPException(status_code=404, detail="Todavía no hay ningún análisis de tendencias")
    return _insight_public(insight)


@router.post("/insight/refresh", response_model=TrendInsightPublic)
def refresh_insight(session: SessionDep, auth: AuthContext = Depends(require_manager_or_owner)):
    """Regenera el analisis de hoy y sus sugerencias pendientes. Una vez al dia."""
    today = datetime.now(BUSINESS_TIMEZONE).date()
    preferences = session.get(ContentPreferences, auth.business_id)
    if not preferences or not preferences.enabled:
        raise HTTPException(status_code=409, detail="Activa el radar de tendencias para generar el análisis")
    insight = session.exec(
        select(TrendInsight).where(TrendInsight.business_id == auth.business_id, TrendInsight.day == today)
    ).first()
    if insight and insight.refreshed_at:
        raise HTTPException(status_code=429, detail="Ya has actualizado el análisis hoy. Podrás volver a hacerlo mañana.")
    snapshot = today_snapshot(session, preferences, today)
    if not snapshot or snapshot.payload == "[]":
        raise HTTPException(status_code=409, detail="Todavía no hay tendencias de hoy")
    try:
        return _insight_public(analyze_business(session, preferences, snapshot, datetime.utcnow(), refresh=True))
    except (TrendAnalysisError, SQLAlchemyError):
        session.rollback()
        raise INSIGHT_UNAVAILABLE


@router.get("/suggestions", response_model=list[ContentSuggestionPublic])
def list_suggestions(
    session: SessionDep, status: SuggestionStatus | None = None, auth: AuthContext = Depends(require_manager_or_owner)
):
    """Sugerencias de contenido del negocio, por hora de publicacion."""
    query = select(ContentSuggestion).where(ContentSuggestion.business_id == auth.business_id)
    if status:
        query = query.where(ContentSuggestion.status == status)
    return [_suggestion_public(s) for s in session.exec(query.order_by(ContentSuggestion.publish_at)).all()]


@router.patch("/suggestions/{suggestion_id}", response_model=ContentSuggestionPublic)
def update_suggestion(
    suggestion_id: int,
    body: ContentSuggestionStatusUpdate,
    session: SessionDep,
    auth: AuthContext = Depends(require_manager_or_owner),
):
    """Marca una sugerencia como abierta, descartada o publicada."""
    suggestion = session.get(ContentSuggestion, suggestion_id)
    if not suggestion or suggestion.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Sugerencia no encontrada")
    suggestion.status = body.status
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return _suggestion_public(suggestion)


@internal_router.post("/ingest")
def ingest(session: SessionDep):
    """Guarda las tendencias del dia; relanzarlo el mismo dia no repite consultas ni filas."""
    return ingest_trends(session)


@internal_router.post("/analyze")
def analyze(session: SessionDep):
    """Analiza los negocios activados con tendencias de hoy; relanzarlo no repite analisis."""
    return analyze_trends(session)


@internal_router.post("/dispatch")
def dispatch(session: SessionDep):
    """Envia los avisos de las sugerencias que tocan; relanzarlo no reenvia ninguno."""
    return dispatch_suggestions(session, datetime.utcnow())
