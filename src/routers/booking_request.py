"""Authenticated booking-requests endpoints — require JWT."""

from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import select

from src.database.database import SessionDep
from src.api.auth import AuthContext, require_subscription
from src.database.models.booking_request_model import (
    BookingRequest,
    BookingRequestPublic,
)
from src.database.models.reservation_model import Reservation
from src.database.models.business_conf_model import BusinessConfiguration
from src.services.email_service import (
    send_email,
    email_request_accepted,
    email_request_rejected,
)

router = APIRouter(prefix="/booking-requests", tags=["booking-requests"])


@router.get("/", response_model=list[BookingRequestPublic])
def list_booking_requests(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
    status: str | None = None,
):
    """List booking requests for this business."""
    query = select(BookingRequest).where(
        BookingRequest.business_id == auth.business_id
    )
    if status:
        query = query.where(BookingRequest.status == status)
    query = query.order_by(BookingRequest.created_at.desc())  # type: ignore
    return session.exec(query).all()


@router.post("/{request_id}/accept")
def accept_booking_request(
    request_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Accept a booking request → creates a Reservation."""
    booking = session.exec(
        select(BookingRequest).where(
            BookingRequest.id == request_id,
            BookingRequest.business_id == auth.business_id,
        )
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if booking.status != "REQUESTED":
        raise HTTPException(status_code=409, detail="La solicitud ya fue respondida")

    # Create the reservation
    reservation = Reservation(
        business_id=auth.business_id,
        customer_name=booking.client_name,
        in_charge=booking.employee_name or "Owner",
        reservation_start_date=booking.requested_date,
        reservation_end_date=booking.requested_date + timedelta(minutes=30),
        time_per_reservation=30,
        status="PENDING",
        service=booking.service,
    )
    session.add(reservation)

    # Update booking request
    booking.status = "ACCEPTED"
    booking.responded_at = datetime.now(timezone.utc)
    session.add(booking)
    session.commit()
    session.refresh(reservation)

    # Send email to client
    biz = session.exec(
        select(BusinessConfiguration).where(
            BusinessConfiguration.business_id == auth.business_id
        )
    ).first()
    biz_name = biz.business_name if biz else "Negocio"
    date_str = booking.requested_date.strftime("%d/%m/%Y %H:%M")
    subject, html = email_request_accepted(
        booking.client_name, booking.service, date_str, biz_name, booking.employee_name
    )
    send_email(booking.client_email, subject, html)

    return {
        "status": "ACCEPTED",
        "reservation_id": reservation.id,
        "message": "Reserva confirmada y cliente notificado.",
    }


@router.post("/{request_id}/reject")
def reject_booking_request(
    request_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
    reason: str | None = None,
):
    """Reject a booking request."""
    booking = session.exec(
        select(BookingRequest).where(
            BookingRequest.id == request_id,
            BookingRequest.business_id == auth.business_id,
        )
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if booking.status != "REQUESTED":
        raise HTTPException(status_code=409, detail="La solicitud ya fue respondida")

    booking.status = "REJECTED"
    booking.responded_at = datetime.now(timezone.utc)
    session.add(booking)
    session.commit()

    # Send email to client
    biz = session.exec(
        select(BusinessConfiguration).where(
            BusinessConfiguration.business_id == auth.business_id
        )
    ).first()
    biz_name = biz.business_name if biz else "Negocio"
    subject, html = email_request_rejected(booking.client_name, biz_name, reason)
    send_email(booking.client_email, subject, html)

    return {"status": "REJECTED"}
