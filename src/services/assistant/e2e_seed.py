"""
Datos semilla para los E2E del chat (businext-sys/businext#101).

    python -m src.services.assistant.e2e_seed --business-id <user id del owner de prueba>

Borra y vuelve a crear los datos "E2E" de ese negocio (no toca nada mas).
Usar solo contra una base de pruebas.
"""

import argparse
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, delete, select

from src.database.models.booking_request_model import BookingRequest
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation

PREFIX = "E2E "
EMAIL_PREFIX = "e2e-"
SERVICE = "Corte"


def seed(session: Session, business_id: str, today: date) -> None:
    session.exec(delete(Reservation).where(Reservation.business_id == business_id, col(Reservation.customer_name).startswith(PREFIX)))
    session.exec(delete(BookingRequest).where(BookingRequest.business_id == business_id, col(BookingRequest.client_email).startswith(EMAIL_PREFIX)))
    session.exec(delete(Finances).where(Finances.business_id == business_id, col(Finances.concept).startswith(PREFIX)))

    owner = session.get(Profile, business_id)
    in_charge = owner.display_name if owner and owner.display_name else "Owner"

    if not session.exec(select(Product).where(Product.business_id == business_id, Product.name == SERVICE)).first():
        session.add(Product(business_id=business_id, name=SERVICE, price=15, type="service"))

    for hour, customer in ((18, "Ana"), (19, "Luis")):
        start = datetime.combine(today, time(hour, 0))
        session.add(Reservation(
            business_id=business_id,
            customer_name=f"{PREFIX}{customer}",
            in_charge=in_charge,
            reservation_start_date=start,
            reservation_end_date=start + timedelta(minutes=30),
            time_per_reservation=30,
            status="PENDING",
            service=SERVICE,
        ))

    session.add(BookingRequest(
        business_id=business_id,
        client_name=f"{PREFIX}Sara",
        client_email=f"{EMAIL_PREFIX}sara@example.com",
        client_phone="600000000",
        service=SERVICE,
        requested_date=datetime.combine(today + timedelta(days=1), time(11, 0)),
    ))

    noon = datetime.combine(today, time(12, 0))
    session.add(Finances(business_id=business_id, concept=f"{PREFIX}{SERVICE}", amount=25, type="INCOME", creator=in_charge, created_at=noon))
    session.add(Finances(business_id=business_id, concept=f"{PREFIX}Toallas", amount=5, type="EXPENSE", creator=in_charge, created_at=noon))
    session.commit()


if __name__ == "__main__":
    from src.database.database import get_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", required=True)
    args = parser.parse_args()
    with Session(get_engine()) as session:
        seed(session, args.business_id, datetime.now(ZoneInfo("Europe/Madrid")).date())
    print(f"Datos E2E creados para {args.business_id}")
