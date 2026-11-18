"""Clientes conocidos de un negocio, deducidos de sus reservas (no hay tabla de clientes)."""

from datetime import datetime

from sqlmodel import Session, func, or_, select

from src.database.models.finances_model import Finances
from src.database.models.reservation_model import Reservation

DETAIL_RESERVATIONS = 20


def normalize_name(name: str) -> str:
    return name.strip().lower()


def _visited(r: Reservation, now: datetime) -> bool:
    return r.reservation_start_date <= now and r.status != "NO_SHOW"


def find_customers(session: Session, business_id: str, now: datetime, query: str = "", limit: int = 10) -> list[dict]:
    """
    Un cliente por nombre normalizado, con su contacto no vacio mas reciente,
    ordenados por su reserva mas reciente. `last_visit` es la ultima reserva ya
    pasada en la que vino (None si solo tiene reservas futuras).
    """
    statement = select(Reservation).where(Reservation.business_id == business_id)
    text = query.strip().lower()
    if text:
        pattern = f"%{text}%"
        statement = statement.where(or_(
            func.lower(Reservation.customer_name).like(pattern),
            func.lower(Reservation.customer_email).like(pattern),
            Reservation.customer_phone.like(f"%{''.join(text.split())}%"),  # type: ignore[union-attr]
        ))
    reservations = session.exec(
        statement.order_by(Reservation.reservation_start_date.desc())  # type: ignore[union-attr]
    ).all()

    customers: dict[str, dict] = {}
    for r in reservations:
        key = normalize_name(r.customer_name)
        if not key:
            continue
        customer = customers.setdefault(
            key, {"name": r.customer_name.strip(), "phone": None, "email": None, "last_visit": None}
        )
        customer["phone"] = customer["phone"] or r.customer_phone or None
        customer["email"] = customer["email"] or r.customer_email or None
        if customer["last_visit"] is None and _visited(r, now):
            customer["last_visit"] = r.reservation_start_date
    return list(customers.values())[:limit]


def known_contact(session: Session, business_id: str, name: str) -> tuple[str | None, str | None]:
    """Telefono y email mas recientes de las reservas de un cliente con ese nombre."""
    key = normalize_name(name)
    reservations = session.exec(
        select(Reservation)
        .where(
            Reservation.business_id == business_id,
            func.lower(func.trim(Reservation.customer_name)) == key,
            or_(Reservation.customer_phone.is_not(None), Reservation.customer_email.is_not(None)),  # type: ignore[union-attr]
        )
        .order_by(Reservation.reservation_start_date.desc())  # type: ignore[union-attr]
    ).all()
    phone = next((r.customer_phone for r in reservations if r.customer_phone), None)
    email = next((r.customer_email for r in reservations if r.customer_email), None)
    return phone, email


def _reservation_item(r: Reservation) -> dict:
    return {"id": r.id, "start": r.reservation_start_date, "service": r.service, "in_charge": r.in_charge, "status": r.status}


def customer_detail(session: Session, business_id: str, name: str, now: datetime) -> dict | None:
    """Ficha de un cliente: contacto, visitas, «no vino», gasto y sus ultimas reservas."""
    reservations = session.exec(
        select(Reservation)
        .where(Reservation.business_id == business_id, func.lower(func.trim(Reservation.customer_name)) == normalize_name(name))
        .order_by(Reservation.reservation_start_date.desc())  # type: ignore[union-attr]
    ).all()
    if not reservations:
        return None
    ids = [r.id for r in reservations]
    spent = session.exec(
        select(func.coalesce(func.sum(Finances.amount), 0)).where(
            Finances.business_id == business_id,
            Finances.type == "INCOME",
            Finances.reservation_id.in_(ids),  # type: ignore[union-attr]
        )
    ).one()
    upcoming = [r for r in reservations if r.status == "PENDING" and r.reservation_start_date >= now]
    return {
        "name": reservations[0].customer_name.strip(),
        "phone": next((r.customer_phone for r in reservations if r.customer_phone), None),
        "email": next((r.customer_email for r in reservations if r.customer_email), None),
        "visits": sum(r.status == "COMPLETED" for r in reservations),
        "no_shows": sum(r.status == "NO_SHOW" for r in reservations),
        "total_spent": round(float(spent), 2),
        "last_visit": next((r.reservation_start_date for r in reservations if _visited(r, now)), None),
        "next_reservation": _reservation_item(upcoming[-1]) if upcoming else None,
        "reservations": [_reservation_item(r) for r in reservations[:DETAIL_RESERVATIONS]],
    }
