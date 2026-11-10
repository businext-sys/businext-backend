"""
Recordatorio por email el dia antes de la cita (businext-sys/businext#183).

Cada hora, un cron job de Render llama a `POST /internal/reminders/run`, que
envia un email a los clientes con reserva pendiente que empieza entre 23 y 25
horas despues, si el negocio tiene los recordatorios activados. La ventana
cubre dos pasadas del cron para no perder citas si una se retrasa;
`reminder_sent_at` evita repetir.
"""
from datetime import datetime, timedelta

from sqlmodel import Session, select

from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.reservation_model import Reservation
from src.services.email_service import email_appointment_reminder, send_email

WINDOW_START = timedelta(hours=23)
WINDOW_END = timedelta(hours=25)
DEFAULT_IN_CHARGE = "Owner"


def _location_address(session: Session, business_id: str) -> str | None:
    """La reserva no guarda sede: solo hay direccion si el negocio tiene una unica sede activa."""
    locations = session.exec(
        select(Location).where(Location.business_id == business_id, Location.is_active)
    ).all()
    return locations[0].address if len(locations) == 1 else None


def send_reminders(session: Session, now: datetime) -> dict:
    """Envia los recordatorios que tocan. `now` es la hora de pared del negocio, como las reservas."""
    due = session.exec(
        select(Reservation, BusinessConfiguration)
        .join(BusinessConfiguration, BusinessConfiguration.business_id == Reservation.business_id)
        .where(
            BusinessConfiguration.reminders_enabled,
            Reservation.status == "PENDING",
            Reservation.customer_email.is_not(None),
            Reservation.reminder_sent_at.is_(None),
            Reservation.reservation_start_date >= now + WINDOW_START,
            Reservation.reservation_start_date < now + WINDOW_END,
        )
    ).all()

    result = {"sent": 0, "failed": 0}
    for reservation, business in due:
        subject, html = email_appointment_reminder(
            reservation.customer_name,
            reservation.service,
            reservation.reservation_start_date.strftime("%d/%m/%Y %H:%M"),
            business.business_name,
            employee_name=reservation.in_charge if reservation.in_charge != DEFAULT_IN_CHARGE else None,
            business_phone=business.business_phone,
            business_email=business.business_email,
            location_address=_location_address(session, reservation.business_id),
        )
        if not send_email(reservation.customer_email, subject, html):
            result["failed"] += 1
            continue
        reservation.reminder_sent_at = datetime.utcnow()
        session.add(reservation)
        session.commit()
        result["sent"] += 1
    return result
