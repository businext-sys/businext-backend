"""Mensajes del negocio a clientes que llevan tiempo sin venir (issue #185)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from src.api.auth import AuthContext, require_manager_or_owner
from src.database.database import SessionDep
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.customer_message_model import (
    CustomerMessage,
    CustomerMessageCreate,
    CustomerMessageDraft,
    CustomerMessagePublic,
)
from src.database.models.reservation_model import Reservation
from src.services.email_service import APP_URL, email_customer_message, send_email

DAYS_BETWEEN_MESSAGES = 7

router = APIRouter(prefix="/customer-messages", tags=["customer-messages"])


def _latest_reservation(session: Session, business_id: str, email: str) -> Reservation:
    reservation = session.exec(
        select(Reservation)
        .where(
            Reservation.business_id == business_id,
            func.lower(Reservation.customer_email) == email.lower(),
        )
        .order_by(Reservation.reservation_start_date.desc())  # type: ignore[union-attr]
    ).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Ese email no es de ningún cliente del negocio")
    return reservation


def _business_config(session: Session, business_id: str) -> BusinessConfiguration | None:
    return session.exec(
        select(BusinessConfiguration).where(BusinessConfiguration.business_id == business_id)
    ).first()


@router.get("/draft", response_model=CustomerMessageDraft)
def get_customer_message_draft(
    email: str,
    session: SessionDep,
    auth: AuthContext = Depends(require_manager_or_owner),
):
    """Texto propuesto para escribir a un cliente del negocio."""
    reservation = _latest_reservation(session, auth.business_id, email)
    biz = _business_config(session, auth.business_id)
    biz_name = biz.business_name if biz else "nuestro negocio"
    message = (
        f"Hola {reservation.customer_name}, hace tiempo que no te vemos por {biz_name}. "
        f"Nos encantaría volver a verte: reserva tu próxima cita cuando quieras en "
        f"{APP_URL}/book/{auth.business_id}\n\n¡Un saludo!"
    )
    return CustomerMessageDraft(
        customer_email=email.lower(),
        customer_name=reservation.customer_name,
        message=message,
    )


@router.post("/", response_model=CustomerMessagePublic, status_code=201)
def send_customer_message(
    body: CustomerMessageCreate,
    session: SessionDep,
    auth: AuthContext = Depends(require_manager_or_owner),
):
    """Envía el email al cliente y lo registra; como mucho uno cada 7 días por cliente."""
    email = body.customer_email.lower()
    _latest_reservation(session, auth.business_id, email)

    recent = session.exec(
        select(CustomerMessage).where(
            CustomerMessage.business_id == auth.business_id,
            CustomerMessage.customer_email == email,
            CustomerMessage.created_at >= datetime.utcnow() - timedelta(days=DAYS_BETWEEN_MESSAGES),
        )
    ).first()
    if recent:
        raise HTTPException(
            status_code=429,
            detail=f"Ya se escribió a este cliente en los últimos {DAYS_BETWEEN_MESSAGES} días",
        )

    biz = _business_config(session, auth.business_id)
    subject, html = email_customer_message(
        body.message,
        biz.business_name if biz else "Negocio",
        business_phone=biz.business_phone if biz else None,
        business_email=biz.business_email if biz else None,
    )
    if not send_email(email, subject, html, reply_to=biz.business_email if biz else None):
        raise HTTPException(status_code=502, detail="No se pudo enviar el email")

    customer_message = CustomerMessage(
        business_id=auth.business_id,
        customer_email=email,
        message=body.message,
        sent_by=auth.user_id,
    )
    session.add(customer_message)
    session.commit()
    session.refresh(customer_message)
    return customer_message
