"""
Recordatorio por email el dia antes de la cita (businext-sys/businext#183).
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.api.auth import AuthContext, _build_capabilities, get_auth_context
from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.reservation_model import Reservation
from src.main import app
from src.services import reminder_service

OWNER = "owner-1"
SECRET = "job-secret"
AUTH = {"Authorization": "Bearer test"}
JOB = {"Authorization": f"Bearer {SECRET}"}
NOW = datetime(2026, 11, 10, 10, 0)


@pytest.fixture
def email(monkeypatch):
    fake = MagicMock(return_value="email-id")
    monkeypatch.setattr(reminder_service, "send_email", fake)
    monkeypatch.setenv("REMINDERS_JOB_SECRET", SECRET)
    return fake


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def business(session):
    business = BusinessConfiguration(business_id=OWNER, business_name="Barberia Sol", business_phone="+34600111222")
    session.add(business)
    session.add(Location(business_id=OWNER, name="Centro", address="Calle Mayor 1"))
    session.commit()
    return business


def reservation(session, start: datetime, business_id: str = OWNER, **fields) -> Reservation:
    values = dict(
        business_id=business_id,
        customer_name="Ana",
        in_charge="Marta",
        reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=30),
        time_per_reservation=30,
        status="PENDING",
        service="Corte",
        customer_email="ana@example.com",
    )
    reservation = Reservation(**{**values, **fields})
    session.add(reservation)
    session.commit()
    return reservation


class TestSendReminders:
    def test_envia_un_unico_recordatorio_aunque_el_job_corra_varias_veces(self, session, business, email):
        booked = reservation(session, NOW + timedelta(hours=24))

        assert reminder_service.send_reminders(session, NOW) == {"sent": 1, "failed": 0}
        assert reminder_service.send_reminders(session, NOW + timedelta(hours=1)) == {"sent": 0, "failed": 0}

        email.assert_called_once()
        to, subject, html = email.call_args.args
        assert to == "ana@example.com"
        assert "Barberia Sol" in subject
        for text in ["Corte", "11/11/2026 10:00", "Marta", "Calle Mayor 1", "+34600111222"]:
            assert text in html
        session.refresh(booked)
        assert booked.reminder_sent_at is not None

    @pytest.mark.parametrize(
        "fields",
        [
            {"customer_email": None},
            {"status": "CANCELLED"},
            {"status": "COMPLETED"},
            {"reservation_start_date": NOW + timedelta(hours=26)},
            {"reservation_start_date": NOW + timedelta(hours=2)},
            {"reminder_sent_at": NOW},
            {"business_id": "otro-negocio"},
        ],
    )
    def test_no_envia_nada_si_no_toca(self, session, business, email, fields):
        reservation(session, NOW + timedelta(hours=24), **fields)
        assert reminder_service.send_reminders(session, NOW) == {"sent": 0, "failed": 0}
        email.assert_not_called()

    def test_no_envia_nada_con_los_recordatorios_apagados(self, session, business, email):
        business.reminders_enabled = False
        session.add(business)
        session.commit()
        reservation(session, NOW + timedelta(hours=24))
        assert reminder_service.send_reminders(session, NOW) == {"sent": 0, "failed": 0}
        email.assert_not_called()

    def test_reintenta_si_el_envio_falla(self, session, business, email):
        booked = reservation(session, NOW + timedelta(hours=24))
        email.return_value = None
        assert reminder_service.send_reminders(session, NOW) == {"sent": 0, "failed": 1}
        session.refresh(booked)
        assert booked.reminder_sent_at is None

    def test_sin_sede_unica_ni_empleado_asignado_no_los_muestra(self, session, business, email):
        session.add(Location(business_id=OWNER, name="Norte", address="Avenida Norte 9"))
        session.commit()
        reservation(session, NOW + timedelta(hours=24), in_charge="Owner")
        reminder_service.send_reminders(session, NOW)
        html = email.call_args.args[2]
        assert "Dirección" not in html
        assert "Profesional" not in html


class TestEndpoint:
    def test_sin_el_secreto_responde_401(self, session, email):
        assert TestClient(app).post("/internal/reminders/run").status_code == 401
        assert TestClient(app).post("/internal/reminders/run", headers=AUTH).status_code == 401

    def test_con_el_secreto_envia_los_recordatorios(self, session, business, email, monkeypatch):
        monkeypatch.setattr(reminder_service, "send_reminders", MagicMock(return_value={"sent": 0, "failed": 0}))
        response = TestClient(app).post("/internal/reminders/run", headers=JOB)
        assert response.status_code == 200
        assert response.json() == {"sent": 0, "failed": 0}


def as_role(role: str) -> AuthContext:
    return AuthContext(
        user_id=OWNER if role == "owner" else "marta",
        business_id=OWNER,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=_build_capabilities(role, True),
    )


class TestSetting:
    @pytest.mark.parametrize("role", ["owner", "manager"])
    def test_lee_y_cambia_el_ajuste(self, session, business, role):
        app.dependency_overrides[get_auth_context] = lambda: as_role(role)
        client = TestClient(app)
        assert client.get("/configuration/reminders", headers=AUTH).json() == {"reminders_enabled": True}
        response = client.put("/configuration/reminders", json={"reminders_enabled": False}, headers=AUTH)
        assert response.status_code == 200
        assert response.json() == {"reminders_enabled": False}
        session.refresh(business)
        assert business.reminders_enabled is False

    def test_employee_recibe_403(self, session, business):
        app.dependency_overrides[get_auth_context] = lambda: as_role("employee")
        client = TestClient(app)
        assert client.get("/configuration/reminders", headers=AUTH).status_code == 403
        assert client.put("/configuration/reminders", json={"reminders_enabled": False}, headers=AUTH).status_code == 403
