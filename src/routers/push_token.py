"""Registro de tokens de Expo Push Notifications (issue #031)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from src.api.auth import AuthContext, get_auth_context
from src.database.database import SessionDep
from src.database.models.push_token_model import (
    PushToken,
    PushTokenCreate,
    PushTokenPublic,
)

router = APIRouter(prefix="/users", tags=["push-tokens"])


@router.post("/{user_id}/push-tokens", response_model=PushTokenPublic, status_code=201)
def register_push_token(
    user_id: str,
    body: PushTokenCreate,
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Registra (o reasigna) un Expo push token para un usuario.

    No requiere suscripcion activa (a diferencia de la mayoria de
    endpoints) porque registrar el token del dispositivo debe funcionar
    incluso si la suscripcion del owner esta vencida — es la propia app
    quien decide que UI mostrar segun `capabilities.can_access_app`.

    Solo el propio usuario puede registrar su token (`user_id` del path
    debe coincidir con el del JWT).
    """
    if user_id != auth.user_id:
        raise HTTPException(
            status_code=403,
            detail="No puedes registrar un push token para otro usuario",
        )

    existing = session.exec(
        select(PushToken).where(PushToken.token == body.token)
    ).first()

    if existing:
        # El mismo token de dispositivo puede haber quedado asociado a otro
        # usuario (ej. logout + login con otra cuenta en el mismo telefono).
        existing.user_id = user_id
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    push_token = PushToken(user_id=user_id, token=body.token)
    session.add(push_token)
    session.commit()
    session.refresh(push_token)
    return push_token


@router.delete("/{user_id}/push-tokens", status_code=204)
def unregister_push_token(
    user_id: str,
    body: PushTokenCreate,
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    """Elimina un push token (ej. al hacer logout en el dispositivo)."""
    if user_id != auth.user_id:
        raise HTTPException(
            status_code=403,
            detail="No puedes eliminar un push token de otro usuario",
        )
    existing = session.exec(
        select(PushToken).where(
            PushToken.token == body.token, PushToken.user_id == user_id
        )
    ).first()
    if existing:
        session.delete(existing)
        session.commit()
