"""
Avisos push al equipo (businext-sys/businext#220).
"""
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import src.main  # noqa: F401  (registra tablas)
from src.api.auth import AccessCapabilities, AuthContext, require_active_member, require_subscription
from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.member_model import BusinessMember
from src.database.models.notification_model import NotificationPreferences
from src.database.models.profile_model import Profile
from src.database.models.push_token_model import PushToken
from src.database.models.reservation_model import Reservation
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.services import team_notifications

BIZ = "owner-1"
DAY = date(2026, 11, 18)
AUTH = {"Authorization": "Bearer test"}


def as_user(user_id=BIZ, role="owner") -> AuthContext:
    return AuthContext(
        user_id=user_id, business_id=BIZ, role=role, account_type="owner" if role == "owner" else "member",
        member_status="active", subscription_active=True, capabilities=AccessCapabilities(can_access_app=True),
    )


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


def booked(start: datetime, in_charge="Marta", status="PENDING", customer="Juan") -> Reservation:
    return Reservation(
        business_id=BIZ, customer_name=customer, in_charge=in_charge, reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=30), time_per_reservation=30, status=status,
        service="Corte de pelo",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            BusinessConfiguration(business_id=BIZ, business_name="Barbería"),
            Profile(id=BIZ, display_name="Dani"),
            Profile(id="marta", display_name="Marta"),
            Profile(id="pablo", display_name="Pablo"),
            Profile(id="luis", display_name="Luis"),
            BusinessMember(business_id=BIZ, member_user_id="marta", role="employee"),
            BusinessMember(business_id=BIZ, member_user_id="pablo", role="manager"),
            BusinessMember(business_id=BIZ, member_user_id="luis", role="employee", status="inactive"),
            PushToken(user_id=BIZ, token="t-owner"),
            PushToken(user_id="marta", token="t-marta"),
            PushToken(user_id="pablo", token="t-pablo"),
            PushToken(user_id="luis", token="t-luis"),
            WorkingHours(business_id=BIZ, day_of_week=DAY.weekday(), start_time="10:00", end_time="20:00"),
        ])
        session.commit()
        yield session


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def record(tokens, title, body, data=None, channel_id=None):
        calls.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data})

    monkeypatch.setattr(team_notifications, "send_push_notifications", record)
    monkeypatch.setattr(team_notifications, "_send_in_background", record)
    return calls


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_subscription] = as_user
    app.dependency_overrides[require_active_member] = as_user
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestProximaCita:
    def test_al_encargado_15_minutos_antes_y_una_sola_vez(self, session, sent):
        session.add(booked(at(10, 30)))
        session.commit()
        assert team_notifications.run_scheduled(session, at(10, 10))["upcoming"] == 0
        assert team_notifications.run_scheduled(session, at(10, 15))["upcoming"] == 1
        assert team_notifications.run_scheduled(session, at(10, 16))["upcoming"] == 0
        [push] = sent
        assert push["tokens"] == ["t-marta"]
        assert (push["title"], push["body"]) == ("10:30 · Juan", "Corte de pelo")
        assert push["data"]["type"] == "upcoming" and push["data"]["reservation_id"]

    def test_sin_encargado_conocido_va_al_owner_y_respeta_preferencias(self, session, sent):
        session.add_all([
            booked(at(10, 30), in_charge="Owner"),
            NotificationPreferences(user_id=BIZ, upcoming_minutes=60),
        ])
        session.commit()
        team_notifications.run_scheduled(session, at(9, 45))
        assert [p["tokens"] for p in sent if p["data"]["type"] == "upcoming"] == [["t-owner"]]

    def test_no_vino_no_avisa(self, session, sent):
        session.add(booked(at(10, 30), status="NO_SHOW"))
        session.commit()
        assert team_notifications.run_scheduled(session, at(10, 20))["upcoming"] == 0


class TestResumenDeLaManana:
    def test_cada_uno_lo_suyo_y_solo_a_su_hora(self, session, sent):
        session.add_all([
            booked(at(10)), booked(at(12), in_charge="Dani"), booked(at(13), status="NO_SHOW"),
        ])
        session.commit()
        assert team_notifications.run_scheduled(session, at(8, 59))["daily_summary"] == 0
        assert team_notifications.run_scheduled(session, at(9, 0))["daily_summary"] == 3
        assert team_notifications.run_scheduled(session, at(9, 1))["daily_summary"] == 0
        bodies = {p["tokens"][0]: p["body"] for p in sent}
        assert bodies == {
            "t-owner": "Hoy 2 citas, primera a las 10:00",
            "t-pablo": "Hoy 2 citas, primera a las 10:00",
            "t-marta": "Hoy 1 cita, primera a las 10:00",
        }
        assert sent[0]["data"] == {"type": "daily_summary", "date": DAY.isoformat()}

    def test_sin_citas_no_se_envia(self, session, sent):
        session.add(booked(at(10), in_charge="Dani"))
        session.commit()
        team_notifications.run_scheduled(session, at(9, 0))
        assert "t-marta" not in [p["tokens"][0] for p in sent]


class TestCierreDelDia:
    def test_30_minutos_tras_el_cierre_al_owner_y_managers(self, session, sent):
        session.add_all([booked(at(10)), booked(at(11)), booked(at(12), status="COMPLETED")])
        session.commit()
        assert team_notifications.run_scheduled(session, at(20, 15))["day_close"] == 0
        assert team_notifications.run_scheduled(session, at(20, 30))["day_close"] == 2
        assert team_notifications.run_scheduled(session, at(20, 45))["day_close"] == 0
        assert sorted(p["tokens"][0] for p in sent) == ["t-owner", "t-pablo"]
        assert sent[0]["body"] == "2 citas de hoy siguen pendientes. Márcalas completadas para cuadrar la caja."
        assert sent[0]["data"] == {"type": "day_close", "date": DAY.isoformat()}


class TestCambios:
    def test_crear_avisa_al_encargado_y_al_owner_salvo_a_quien_lo_hizo(self, client, sent):
        body = {"customer_name": "Juan", "in_charge": "Marta", "reservation_start_date": at(11).isoformat(),
                "status": "PENDING", "service": "Corte de pelo"}
        assert client.post("/reservations/", json=body, headers=AUTH).status_code == 200
        [push] = sent
        assert push["tokens"] == ["t-marta"]
        assert push["title"] == "Nueva cita: Juan"
        assert push["data"]["type"] == "reservation_changed"
        assert push["data"]["date"] == DAY.isoformat()

    def test_mover_y_cancelar(self, client, session, sent):
        app.dependency_overrides[require_subscription] = lambda: as_user("marta", "employee")
        reservation = booked(at(11))
        session.add(reservation)
        session.commit()
        client.patch(f"/reservations/{reservation.id}", json={"reservation_start_date": at(12).isoformat()}, headers=AUTH)
        client.patch(f"/reservations/{reservation.id}", json={"customer_phone": "600"}, headers=AUTH)
        client.delete(f"/reservations/{reservation.id}", headers=AUTH)
        assert [(p["title"], p["tokens"]) for p in sent] == [
            ("Cita cambiada: Juan", ["t-owner"]),
            ("Cita cancelada: Juan", ["t-owner"]),
        ]

    def test_sin_aviso_si_lo_desactiva(self, client, session, sent):
        session.add(NotificationPreferences(user_id="marta", reservation_changes=False))
        session.commit()
        body = {"customer_name": "Juan", "in_charge": "Marta", "reservation_start_date": at(11).isoformat(),
                "status": "PENDING", "service": "Corte de pelo"}
        client.post("/reservations/", json=body, headers=AUTH)
        assert sent == []


class TestPreferencias:
    def test_por_defecto_todo_activado_y_se_guardan(self, client):
        defaults = client.get("/notification-preferences", headers=AUTH).json()
        assert defaults == {
            "upcoming": True, "upcoming_minutes": 15, "daily_summary": True, "daily_summary_hour": 9,
            "reservation_changes": True, "day_close": True,
        }
        changed = {**defaults, "upcoming_minutes": 30, "day_close": False}
        assert client.put("/notification-preferences", json=changed, headers=AUTH).json() == changed
        assert client.get("/notification-preferences/", headers=AUTH).json() == changed
        assert client.put("/notification-preferences", json={**defaults, "daily_summary_hour": 24}, headers=AUTH).status_code == 422
