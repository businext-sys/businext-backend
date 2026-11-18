"""Clientes conocidos de un negocio, deducidos de sus reservas (no hay tabla de clientes)."""

from sqlmodel import Session, func, or_, select

from src.database.models.reservation_model import Reservation


def normalize_name(name: str) -> str:
    return name.strip().lower()


def find_customers(session: Session, business_id: str, query: str = "", limit: int = 10) -> list[dict]:
    """Un cliente por nombre normalizado, con su contacto no vacio mas reciente; ultima visita primero."""
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
            key, {"name": r.customer_name.strip(), "phone": None, "email": None, "last_visit": r.reservation_start_date}
        )
        customer["phone"] = customer["phone"] or r.customer_phone or None
        customer["email"] = customer["email"] or r.customer_email or None
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
