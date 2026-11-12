"""Borrado de cuenta desde la app movil (requisito de App Store y Google Play)."""

from fastapi import APIRouter, Depends

from src.api.auth import AuthContext, get_auth_context
from src.database.database import SessionDep
from src.services.account_service import delete_account

router = APIRouter(prefix="/users", tags=["account"])


@router.delete("/me", status_code=204)
async def delete_my_account(
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    """
    Borra la cuenta del usuario autenticado.

    No requiere suscripcion activa: un owner con la suscripcion vencida
    tambien tiene que poder borrar su cuenta. Es idempotente; si responde
    502 tras borrar los datos, la app puede reintentar.
    """
    await delete_account(session, auth)
