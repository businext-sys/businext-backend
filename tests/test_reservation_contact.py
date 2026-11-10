"""
Telefono y email del cliente en cada reserva (businext-sys/businext#181).
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.booking_request_model import BookingRequest
from src.database.models.reservation_model import Reservation
from src.main import app
from src.routers import booking_request

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
TOMORROW = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
NEW_RESERVATION = {
    "customer_name": "Sara",
    "in_charge": None,
    "reservation_start_date": TOMORROW.isoformat(),
    "reservation_end_date": TOMORROW.isoformat(),
    "time_per_reservation": 30,
    "status": "PENDING",
    "service": "Corte",
}


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(booking_request, "send_email", lambda *args, **kwargs: None)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_auth_context] = lambda: AuthContext(
            user_id=OWNER,
            business_id=OWNER,
            role="owner",
            account_type="owner",
            subscription_active=True,
            capabilities=AccessCapabilities(can_access_app=True),
        )
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def test_sin_contacto_devuelve_null(client):
    assert client.post("/reservations/", json=NEW_RESERVATION, headers=AUTH).status_code == 200
    [reservation] = client.get("/reservations/", headers=AUTH).json()
    assert (reservation["customer_phone"], reservation["customer_email"]) == (None, None)


def test_crear_y_editar_con_contacto(client):
    created = client.post(
        "/reservations/",
        json={**NEW_RESERVATION, "customer_phone": " +34 600 000 000 ", "customer_email": "sara@example.com"},
        headers=AUTH,
    ).json()
    assert (created["customer_phone"], created["customer_email"]) == ("+34600000000", "sara@example.com")

    updated = client.patch(
        f"/reservations/{created['id']}",
        json={"time_per_reservation": 30, "customer_email": "otra@example.com"},
        headers=AUTH,
    ).json()
    assert (updated["customer_phone"], updated["customer_email"]) == ("+34600000000", "otra@example.com")

    [listed] = client.get("/reservations/", headers=AUTH).json()
    assert listed["customer_email"] == "otra@example.com"


def test_email_no_valido_se_rechaza(client):
    response = client.post("/reservations/", json={**NEW_RESERVATION, "customer_email": "no-es-email"}, headers=AUTH)
    assert response.status_code == 422


def test_aceptar_una_solicitud_copia_el_contacto(client, session):
    request = BookingRequest(
        business_id=OWNER,
        client_name="Sara",
        client_email="sara@example.com",
        client_phone="600 000 000",
        service="Corte",
        requested_date=TOMORROW,
        status="REQUESTED",
    )
    session.add(request)
    session.commit()

    data = client.post(f"/booking-requests/{request.id}/accept", headers=AUTH).json()
    reservation = session.get(Reservation, data["reservation_id"])
    assert (reservation.customer_phone, reservation.customer_email) == ("600000000", "sara@example.com")
