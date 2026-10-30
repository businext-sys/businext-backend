"""
Radar de tendencias (businext-sys/businext#161).

- `GET`/`PUT /trends/preferences`: preferencias de contenido del negocio
  (owner y manager, capability `canViewTrends`).
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from src.api.auth import AuthContext, require_manager_or_owner
from src.database.database import SessionDep
from src.database.models.trends_model import ContentPreferences, ContentPreferencesPublic

from .assistant import require_authorization_header

router = APIRouter(prefix="/trends", tags=["trends"], dependencies=[Depends(require_authorization_header)])

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
