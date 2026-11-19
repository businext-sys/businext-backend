"""
Duracion por servicio, huecos que la respetan y estado «no vino» (businext-sys/businext#220).
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import src.main  # noqa: F401  (registra tablas y herramientas)
from src.api.auth import AccessCapabilities, AuthContext, require_owner, require_subscription
from src.database.database import get_session
from src.database.models.booking_request_model import BookingRequest
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.services.availability_service import get_available_slots, service_duration
from src.services.assistant.tools import TOOLS, ToolContext

BIZ = "owner-1"
DAY = date.today() + timedelta(days=7)
AUTH = {"Authorization": "Bearer test"}


def owner() -> AuthContext:
    return AuthContext(
        user_id=BIZ, business_id=BIZ, role="owner", account_type="owner", member_status="active",
        subscription_active=True, capabilities=AccessCapabilities(can_access_app=True),
    )


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute))


def booked(start: datetime, minutes=30, status="PENDING", customer="Ana") -> Reservation:
    return Reservation(
        business_id=BIZ, customer_name=customer, in_charge="Dani", reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=minutes), time_per_reservation=minutes,
        status=status, service="Corte",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Profile(id=BIZ, display_name="Dani"),
            BusinessConfiguration(business_id=BIZ, business_name="Barbería"),
            Product(business_id=BIZ, name="Corte", price=15, type="service"),
            Product(business_id=BIZ, name="Color", price=40, type="service", duration_minutes=60),
            WorkingHours(business_id=BIZ, day_of_week=DAY.weekday(), start_time="10:00", end_time="14:00"),
        ])
        session.commit()
        yield session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_subscription] = owner
    app.dependency_overrides[require_owner] = owner
    yield TestClient(app)
    app.dependency_overrides.clear()


def starts(session, minutes) -> list[str]:
    return [s["time"] for s in get_available_slots(session, BIZ, DAY, "Dani", minutes)]


class TestDuracion:
    def test_duracion_del_catalogo(self, session):
        assert service_duration(session, BIZ, " color ") == 60
        assert service_duration(session, BIZ, "Corte") == 30
        assert service_duration(session, BIZ, "Desconocido") == 30
        assert service_duration(session, BIZ, None) == 30

    def test_un_servicio_de_60_cabe_entero_en_el_horario(self, session):
        slots = starts(session, 60)
        assert slots[:3] == ["10:00", "10:15", "10:30"]
        assert slots[-1] == "13:00" and "13:30" not in slots

    def test_no_solapa_una_cita_existente(self, session):
        session.add(booked(at(11)))
        session.commit()
        slots = starts(session, 60)
        assert "10:00" in slots and "10:30" not in slots and "10:15" not in slots
        assert "11:00" not in slots and "11:30" in slots
        assert "10:30" in starts(session, 30)

    def test_no_vino_no_ocupa_el_hueco(self, session):
        session.add(booked(at(11), status="NO_SHOW"))
        session.commit()
        assert "11:00" in starts(session, 60)

    def test_respeta_la_duracion_real_de_la_cita(self, session):
        session.add(booked(at(10), minutes=90))
        session.commit()
        assert starts(session, 30)[0] == "11:30"

    def test_una_solicitud_ocupa_lo_que_dura_su_servicio(self, session):
        session.add(BookingRequest(
            business_id=BIZ, client_name="Sara", client_email="sara@example.com", client_phone="600",
            service="Color", requested_date=at(10), employee_name="Dani",
        ))
        session.commit()
        assert starts(session, 30)[0] == "11:00"

    def test_endpoint_publico_con_servicio(self, client):
        slots = client.get(f"/public/book/{BIZ}/availability?date={DAY}&employee_name=Dani&service=Color").json()["slots"]
        assert slots[-1]["time"] == "13:00"
        default = client.get(f"/public/book/{BIZ}/availability?date={DAY}&employee_name=Dani").json()["slots"]
        assert default[-1]["time"] == "13:30"


class TestReservas:
    def test_sin_duracion_explicita_usa_la_del_servicio(self, client, session):
        body = {"customer_name": "Luis", "in_charge": "Dani", "reservation_start_date": at(10).isoformat(),
                "status": "PENDING", "service": "Color"}
        created = client.post("/reservations/", json=body, headers=AUTH).json()
        assert created["time_per_reservation"] == 60
        assert created["reservation_end_date"].startswith(f"{DAY}T11:00")

    def test_la_duracion_explicita_se_respeta(self, client):
        body = {"customer_name": "Luis", "in_charge": "Dani", "reservation_start_date": at(10).isoformat(),
                "reservation_end_date": at(10).isoformat(), "time_per_reservation": 45,
                "status": "PENDING", "service": "Color"}
        created = client.post("/reservations/", json=body, headers=AUTH).json()
        assert created["time_per_reservation"] == 45
        assert created["reservation_end_date"].startswith(f"{DAY}T10:45")

    def test_mover_conserva_la_duracion(self, client, session):
        session.add(booked(at(10), minutes=60))
        session.commit()
        rid = session.exec(select(Reservation)).one().id
        moved = client.patch(f"/reservations/{rid}", json={"reservation_start_date": at(12).isoformat()}, headers=AUTH)
        assert moved.status_code == 200
        assert moved.json()["reservation_end_date"].startswith(f"{DAY}T13:00")
        assert moved.json()["time_per_reservation"] == 60

    def test_estado_no_valido(self, client):
        body = {"customer_name": "Luis", "in_charge": "Dani", "reservation_start_date": at(10).isoformat(),
                "status": "cancelled", "service": "Corte"}
        assert client.post("/reservations/", json=body, headers=AUTH).status_code == 422

    def test_aceptar_una_solicitud_usa_la_duracion_del_servicio(self, client, session, monkeypatch):
        from src.routers import booking_request
        monkeypatch.setattr(booking_request, "send_email", lambda *a, **k: True)
        request = BookingRequest(
            business_id=BIZ, client_name="Sara", client_email="sara@example.com", client_phone="600",
            service="Color", requested_date=at(12), employee_name="Dani",
        )
        session.add(request)
        session.commit()
        assert client.post(f"/booking-requests/{request.id}/accept", headers=AUTH).status_code == 200
        created = session.exec(select(Reservation)).one()
        assert (created.time_per_reservation, created.reservation_end_date) == (60, at(13))


def tool(session, name, args):
    ctx = ToolContext(auth=owner(), session=session, today=DAY, conversation_id="c-1")
    return TOOLS[name].handler(ctx, args)


class TestAsistente:
    def test_crear_un_servicio_largo_respeta_cierre_y_solapes(self, session):
        late = tool(session, "create_reservation", {"customer_name": "Luis", "service": "Color", "start": f"{DAY}T13:30"})
        assert "no se trabaja" in late.data["error"]
        session.add(booked(at(11)))
        session.commit()
        overlap = tool(session, "create_reservation", {"customer_name": "Luis", "service": "Color", "start": f"{DAY}T10:30"})
        assert "conflicto" in overlap.data["error"]

    def test_mover_conserva_la_duracion(self, session):
        session.add(booked(at(10), minutes=60))
        session.commit()
        rid = session.exec(select(Reservation)).one().id
        assert tool(session, "reschedule_reservation", {"id": rid, "new_start": f"{DAY}T12:00"}).data["done"]
        assert session.get(Reservation, rid).reservation_end_date == at(13)

    def test_huecos_con_servicio(self, session):
        slots = tool(session, "get_free_slots", {"date": DAY.isoformat(), "in_charge": "Dani", "service": "Color"})
        assert slots.data["free_slots"][-1]["time"] == "13:00"


class TestNoVino:
    def test_marcar_y_deshacer(self, session):
        session.add(booked(at(10)))
        session.commit()
        rid = session.exec(select(Reservation)).one().id
        result = tool(session, "mark_no_show", {"reservation_id": rid})
        assert result.blocks[0]["status"] == "confirmed"
        assert session.get(Reservation, rid).status == "NO_SHOW"
        assert session.exec(select(Finances)).all() == []
        tool(session, "mark_no_show", {"reservation_id": rid, "undo": True})
        assert session.get(Reservation, rid).status == "PENDING"

    def test_solo_desde_pendiente(self, client, session):
        session.add(booked(at(10), status="COMPLETED"))
        session.commit()
        rid = session.exec(select(Reservation)).one().id
        assert "error" in tool(session, "mark_no_show", {"reservation_id": rid}).data
        assert client.patch(f"/reservations/{rid}", json={"status": "NO_SHOW"}, headers=AUTH).status_code == 409

    def test_revertir_un_no_vino(self, client, session):
        session.add(booked(at(10), status="NO_SHOW"))
        session.commit()
        rid = session.exec(select(Reservation)).one().id
        assert client.post(f"/reservations/{rid}/revert", headers=AUTH).json()["status"] == "PENDING"

    def test_el_listado_incluye_los_no_vino_de_hoy(self, client, session):
        today = datetime.combine(date.today(), time(23, 0))
        session.add(booked(today, status="NO_SHOW"))
        session.commit()
        assert [r["status"] for r in client.get("/reservations/", headers=AUTH).json()] == ["NO_SHOW"]


class TestFichaDeCliente:
    def test_ficha(self, client, session):
        past = datetime.combine(date.today() - timedelta(days=3), time(10))
        visit = booked(past, status="COMPLETED", customer="Jairo")
        visit.customer_email = "jairo@example.com"
        session.add_all([
            visit,
            booked(past - timedelta(days=7), status="NO_SHOW", customer="jairo "),
            booked(at(12), customer="JAIRO"),
            booked(at(13), customer="Otro"),
        ])
        session.commit()
        session.add(Finances(business_id=BIZ, concept="Corte", amount=15, type="INCOME", creator="Dani",
                             reservation_id=visit.id, customer_name="Jairo"))
        session.commit()

        response = client.get("/customers/detail?name=jairo", headers=AUTH)
        assert response.status_code == 200
        detail = response.json()
        assert (detail["visits"], detail["no_shows"], detail["total_spent"]) == (1, 1, 15.0)
        assert detail["email"] == "jairo@example.com"
        assert detail["last_visit"].startswith(past.date().isoformat())
        assert detail["next_reservation"]["start"].startswith(f"{DAY}T12:00")
        assert [r["status"] for r in detail["reservations"]] == ["PENDING", "COMPLETED", "NO_SHOW"]
        assert set(detail["reservations"][0]) == {"id", "start", "service", "in_charge", "status"}

    def test_cliente_desconocido(self, client):
        assert client.get("/customers/detail?name=nadie", headers=AUTH).status_code == 404


class TestHuecosDeHoy:
    def test_no_ofrece_horas_ya_pasadas_en_madrid(self, session, monkeypatch):
        from src.services import availability_service

        class Madrid1216(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(DAY.year, DAY.month, DAY.day, 11, 16, tzinfo=ZoneInfo("UTC")).astimezone(tz)

        monkeypatch.setattr(availability_service, "datetime", Madrid1216)
        assert starts(session, 30)[0] == "12:30"
