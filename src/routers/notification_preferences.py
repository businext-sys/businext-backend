"""Que avisos push al equipo recibe el usuario (src/services/team_notifications.py)."""

from datetime import datetime

from fastapi import APIRouter, Depends

from src.api.auth import AuthContext, require_active_member
from src.database.database import SessionDep
from src.database.models.notification_model import NotificationPreferencesPublic
from src.services.team_notifications import preferences

router = APIRouter(prefix="/notification-preferences", tags=["notifications"])


@router.get("/", response_model=NotificationPreferencesPublic)
def get_notification_preferences(session: SessionDep, auth: AuthContext = Depends(require_active_member)):
    """Preferencias del usuario; sin fila, todo activado (15 min antes, resumen a las 9)."""
    return preferences(session, auth.user_id)


@router.put("/", response_model=NotificationPreferencesPublic)
def update_notification_preferences(
    body: NotificationPreferencesPublic, session: SessionDep, auth: AuthContext = Depends(require_active_member)
):
    current = preferences(session, auth.user_id)
    current.sqlmodel_update({**body.model_dump(), "updated_at": datetime.utcnow()})
    session.add(current)
    session.commit()
    session.refresh(current)
    return current
