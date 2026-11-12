"""
Borrado de cuenta desde la app movil (requisito de App Store y Google Play).

Orden para un owner: cancelar en Stripe -> borrar los datos del negocio en
una transaccion -> borrar el usuario de Supabase Auth. Cada paso es
idempotente (las suscripciones ya canceladas, las filas ya borradas y el
usuario de Auth ya inexistente se toleran), asi que si falla el ultimo paso
la app puede reintentar `DELETE /users/me` sin riesgo.
"""

import logging
import os

import httpx
from fastapi import HTTPException
from sqlmodel import Session, col, delete, select

from src.api.auth import AuthContext
from src.database.models.assistant_conversation_model import (
    AssistantConversation,
    AssistantMessage,
)
from src.database.models.assistant_usage_model import AssistantUsage
from src.database.models.booking_request_model import BookingRequest
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.customer_message_model import CustomerMessage
from src.database.models.finances_model import Finances
from src.database.models.google_business_profile_model import GoogleBusinessProfile
from src.database.models.google_review_model import GoogleReview
from src.database.models.location_model import Location
from src.database.models.member_model import BusinessMember
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.push_token_model import PushToken
from src.database.models.reservation_model import Reservation
from src.database.models.subscription_model import Subscription
from src.database.models.trends_model import (
    ContentPreferences,
    ContentSuggestion,
    TrendInsight,
)
from src.database.models.voice_agent_model import (
    VoiceAgentConfig,
    VoiceCall,
    VoicePhoneNumber,
)
from src.database.models.weekly_summary_model import WeeklySummary
from src.database.models.working_hours_model import WorkingHours
from src.services.supabase_utils import get_supabase_settings

logger = logging.getLogger(__name__)

STRIPE_API_URL = "https://api.stripe.com/v1"

BILLABLE_SUBSCRIPTION_STATUSES = ("active", "trialing", "past_due")


async def _cancel_stripe_subscription(
    client: httpx.AsyncClient, secret_key: str, subscription_id: str
) -> None:
    """
    Cancela una suscripcion de Stripe de inmediato (sin esperar al fin del
    periodo). Si Stripe dice que no existe o ya esta cancelada, se da por buena.
    """
    url = f"{STRIPE_API_URL}/subscriptions/{subscription_id}"
    headers = {"Authorization": f"Bearer {secret_key}"}

    try:
        response = await client.delete(url, headers=headers)
        if response.is_success or response.status_code == 404:
            return

        # Stripe responde 400 al cancelar una suscripcion ya cancelada.
        if 400 <= response.status_code < 500:
            current = await client.get(url, headers=headers)
            if current.status_code == 404:
                return
            if current.is_success and current.json().get("status") == "canceled":
                return
    except httpx.HTTPError as exc:
        logger.error("Error de red cancelando %s en Stripe: %s", subscription_id, exc)
        raise HTTPException(
            status_code=502,
            detail="No se pudo cancelar la suscripcion en Stripe",
        )

    logger.error(
        "Stripe rechazo la cancelacion de %s: %s %s",
        subscription_id,
        response.status_code,
        response.text,
    )
    raise HTTPException(
        status_code=502,
        detail="No se pudo cancelar la suscripcion en Stripe",
    )


async def cancel_billable_subscriptions(session: Session, user_id: str) -> None:
    """
    Cancela en Stripe todas las suscripciones del usuario que puedan seguir
    cobrando. Sin `STRIPE_SECRET_KEY` y con alguna activa, falla con 500 antes
    de tocar nada: no puede quedar alguien cobrando sin cuenta.
    """
    subscriptions = session.exec(
        select(Subscription).where(
            Subscription.user_id == user_id,
            col(Subscription.status).in_(BILLABLE_SUBSCRIPTION_STATUSES),
        )
    ).all()
    if not subscriptions:
        return

    secret_key = os.getenv("STRIPE_SECRET_KEY")
    if not secret_key:
        logger.error(
            "Borrado de cuenta de %s bloqueado: tiene suscripcion activa y falta STRIPE_SECRET_KEY",
            user_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Stripe no esta configurado; no se puede cancelar la suscripcion",
        )

    async with httpx.AsyncClient(timeout=20.0) as client:
        for subscription in subscriptions:
            await _cancel_stripe_subscription(
                client, secret_key, subscription.stripe_subscription_id
            )


async def delete_auth_user(user_id: str) -> None:
    """Borra el usuario de Supabase Auth. Si ya no existe (404), no es error."""
    supabase_url, service_role_key = get_supabase_settings()

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.delete(
                f"{supabase_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                },
            )
    except httpx.HTTPError as exc:
        logger.error("Error de red borrando el usuario %s de Supabase Auth: %s", user_id, exc)
        raise HTTPException(
            status_code=502,
            detail="Datos borrados, pero no se pudo borrar el usuario; reintenta",
        )

    if response.is_success or response.status_code == 404:
        return

    logger.error(
        "Supabase Auth rechazo el borrado de %s: %s %s",
        user_id,
        response.status_code,
        response.text,
    )
    raise HTTPException(
        status_code=502,
        detail="Datos borrados, pero no se pudo borrar el usuario; reintenta",
    )


def _delete_user_rows(session: Session, user_id: str) -> None:
    """Filas personales de un usuario, sea owner o miembro."""
    conversation_ids = select(AssistantConversation.id).where(
        AssistantConversation.user_id == user_id
    )
    session.exec(
        delete(AssistantMessage).where(
            col(AssistantMessage.conversation_id).in_(conversation_ids)
        )
    )
    session.exec(delete(AssistantConversation).where(AssistantConversation.user_id == user_id))
    session.exec(delete(PushToken).where(PushToken.user_id == user_id))
    session.exec(delete(WorkingHours).where(WorkingHours.member_user_id == user_id))
    session.exec(delete(BusinessMember).where(BusinessMember.member_user_id == user_id))
    # Antes que el usuario de Auth, para no depender de si la FK de profile tiene cascade.
    session.exec(delete(Profile).where(Profile.id == user_id))


def _delete_business_rows(session: Session, business_id: str) -> None:
    """
    Todos los datos de un negocio, hijos antes que padres segun las FK:
    mensajes -> conversaciones, finanzas -> reservas/productos, reseñas ->
    perfil de Google, booking requests/miembros -> ubicaciones ->
    configuracion.
    """
    member_ids = select(BusinessMember.member_user_id).where(
        BusinessMember.business_id == business_id
    )
    session.exec(delete(PushToken).where(col(PushToken.user_id).in_(member_ids)))

    conversation_ids = select(AssistantConversation.id).where(
        AssistantConversation.business_id == business_id
    )
    session.exec(
        delete(AssistantMessage).where(
            col(AssistantMessage.conversation_id).in_(conversation_ids)
        )
    )

    for model in (
        AssistantConversation,
        AssistantUsage,
        Finances,
        GoogleReview,
        GoogleBusinessProfile,
        ContentSuggestion,
        TrendInsight,
        ContentPreferences,
        CustomerMessage,
        VoiceCall,
        VoiceAgentConfig,
        VoicePhoneNumber,
        WeeklySummary,
        BookingRequest,
        Reservation,
        Product,
        WorkingHours,
        BusinessMember,
        Location,
        BusinessConfiguration,
    ):
        session.exec(delete(model).where(model.business_id == business_id))


def delete_database_rows(session: Session, auth: AuthContext, is_business_owner: bool) -> None:
    """Borra en una sola transaccion; si algo falla no queda nada a medias."""
    try:
        if is_business_owner:
            _delete_business_rows(session, auth.business_id)
            session.exec(delete(Subscription).where(Subscription.user_id == auth.user_id))
        _delete_user_rows(session, auth.user_id)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Error borrando los datos de la cuenta %s", auth.user_id)
        raise HTTPException(status_code=500, detail="No se pudieron borrar los datos de la cuenta")


async def delete_account(session: Session, auth: AuthContext) -> None:
    """
    Borra la cuenta del usuario autenticado.

    - Owner del negocio (`business_id == user_id`): cancela sus suscripciones
      en Stripe, borra todos los datos del negocio (los empleados pierden el
      acceso, pero sus usuarios de Auth se conservan) y borra su usuario.
    - Miembro (manager/employee, o una fila de miembro con rol owner en un
      negocio ajeno): borra solo su membresia y sus datos personales.
    """
    is_business_owner = auth.role == "owner" and auth.business_id == auth.user_id

    # Falla antes de borrar nada si faltan credenciales: si no, quedaria el usuario vivo sin datos.
    get_supabase_settings()

    if is_business_owner:
        await cancel_billable_subscriptions(session, auth.user_id)

    delete_database_rows(session, auth, is_business_owner)

    await delete_auth_user(auth.user_id)
