"""
Mensaje del negocio a un cliente en riesgo (businext-sys/businext#185).
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.customer_message_model import CustomerMessage
from src.database.models.reservation_model import Reservation
from src.main import app
from src.routers import customer_message

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
MESSAGE = {"customer_email": "Sara@Example.com", "message": "Hola Sara,\n¿vuelves pronto?"}


def _auth(role: str) -> AuthContext:
    return AuthContext(
        user_id=f"{role}-user",
        business_id=OWNER,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status=None if role == "owner" else "active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


@pytest.fixture
def sent(monkeypatch):
    emails = []

    def fake_send_email(to, subject, html, reply_to=None):
        emails.append({"to": to, "subject": subject, "html": html, "reply_to": reply_to})
        return "email-id"

    monkeypatch.setattr(customer_message, "send_email", fake_send_email)
    return emails


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessConfiguration(
            business_id=OWNER, business_name="Barbería Sol", business_email="hola@sol.example",
        ))
        session.add(Reservation(
            business_id=OWNER,
            customer_name="Sara",
            in_charge=None,
            reservation_start_date=datetime(2026, 8, 1, 10),
            reservation_end_date=datetime(2026, 8, 1, 10, 30),
            time_per_reservation=30,
            status="COMPLETED",
            service="Corte",
            customer_email="sara@example.com",
        ))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_auth_context] = lambda: _auth("owner")
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def test_borrador_con_nombre_negocio_y_enlace(client):
    response = client.get("/customer-messages/draft", params={"email": "SARA@example.com"}, headers=AUTH)
    assert response.status_code == 200
    draft = response.json()
    assert (draft["customer_email"], draft["customer_name"]) == ("sara@example.com", "Sara")
    assert "Sara" in draft["message"]
    assert "Barbería Sol" in draft["message"]
    assert f"/book/{OWNER}" in draft["message"]


def test_envia_y_registra(client, session, sent):
    response = client.post("/customer-messages/", json=MESSAGE, headers=AUTH)
    assert response.status_code == 201
    assert response.json()["customer_email"] == "sara@example.com"

    [email] = sent
    assert (email["to"], email["reply_to"]) == ("sara@example.com", "hola@sol.example")
    assert "Hola Sara,<br>¿vuelves pronto?" in email["html"]

    [record] = session.exec(select(CustomerMessage)).all()
    assert (record.business_id, record.sent_by) == (OWNER, "owner-user")


def test_email_que_no_es_cliente_da_404(client, sent):
    response = client.post(
        "/customer-messages/", json={**MESSAGE, "customer_email": "otro@example.com"}, headers=AUTH,
    )
    assert response.status_code == 404
    assert client.get("/customer-messages/draft", params={"email": "otro@example.com"}, headers=AUTH).status_code == 404
    assert sent == []


def test_repetir_antes_de_7_dias_da_429(client, session, sent):
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 201
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 429
    assert len(sent) == 1


def test_pasados_7_dias_se_puede_repetir(client, session, sent):
    session.add(CustomerMessage(
        business_id=OWNER,
        customer_email="sara@example.com",
        message="Antiguo",
        sent_by="owner-user",
        created_at=datetime.utcnow() - timedelta(days=8),
    ))
    session.commit()
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 201


def test_si_falla_el_envio_no_se_registra(client, session, monkeypatch):
    monkeypatch.setattr(customer_message, "send_email", lambda *args, **kwargs: None)
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 502
    assert session.exec(select(CustomerMessage)).all() == []


def test_manager_puede_y_employee_recibe_403(client, sent):
    app.dependency_overrides[get_auth_context] = lambda: _auth("employee")
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 403
    assert client.get("/customer-messages/draft", params={"email": "sara@example.com"}, headers=AUTH).status_code == 403

    app.dependency_overrides[get_auth_context] = lambda: _auth("manager")
    assert client.post("/customer-messages/", json=MESSAGE, headers=AUTH).status_code == 201
