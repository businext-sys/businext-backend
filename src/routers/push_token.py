"""Expo push token registration."""

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
    Register (or reassign) an Expo push token for a user.

    Unlike most endpoints this does not require an active subscription:
    the app decides which UI to show from `capabilities.can_access_app`.
    Users may only register their own token.
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
        # Same device, different account (logout + login): reassign the token.
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
    """Unregister a push token, e.g. on device logout."""
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
