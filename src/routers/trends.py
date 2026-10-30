"""
Radar de tendencias (businext-sys/businext#161).

- `GET`/`PUT /trends/preferences`: preferencias de contenido del negocio
  (owner y manager, capability `canViewTrends`).
- `POST /internal/trends/ingest`: ingesta diaria de tendencias, lanzada por el
  workflow `trends-ingest.yml` con `Authorization: Bearer <TRENDS_JOB_SECRET>`
  (401 sin el).
"""

import hmac
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from src.api.auth import AuthContext, require_manager_or_owner
from src.database.database import SessionDep
from src.database.models.trends_model import ContentPreferences, ContentPreferencesPublic
from src.services.trend_sources import ingest_trends

from .assistant import require_authorization_header

router = APIRouter(prefix="/trends", tags=["trends"], dependencies=[Depends(require_authorization_header)])


def require_job_secret(authorization: str | None = Header(default=None)):
    secret = os.getenv("TRENDS_JOB_SECRET")
    if not secret or not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Firma del job no valida")


internal_router = APIRouter(prefix="/internal/trends", tags=["trends"], dependencies=[Depends(require_job_secret)])

PREFERENCES_UNAVAILABLE = HTTPException(status_code=503, detail="Las preferencias de contenido no están disponibles")


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


@internal_router.post("/ingest")
def ingest(session: SessionDep):
    """Guarda las tendencias del dia; relanzarlo el mismo dia no repite consultas ni filas."""
    return ingest_trends(session)
