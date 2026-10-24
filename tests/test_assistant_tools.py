"""
Tests de las herramientas de lectura del asistente (businext-backend#21).

SQLite en memoria con datos de ejemplo; los handlers se llaman directamente.
"""
import json
from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import src.main  # noqa: F401  (registra todas las tablas y herramientas)
from src.api.auth import AccessCapabilities, AuthContext
from src.database.models.booking_request_model import BookingRequest
from src.database.models.finances_model import Finances
from src.database.models.google_review_model import GoogleReview
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.database.models.weekly_summary_model import WeeklySummary
from src.database.models.working_hours_model import WorkingHours
from src.services.assistant.tools import TOOLS, ToolContext

BIZ = "owner-1"
TODAY = date(2026, 10, 22)  # miercoles


def auth(role: str = "owner", user_id: str = BIZ) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        business_id=BIZ,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


def reservation_at(start: str, customer: str, in_charge: str = "Marta", status: str = "PENDING", business=BIZ):
    return Reservation(
        business_id=business,
        customer_name=customer,
        in_charge=in_charge,
        reservation_start_date=datetime.fromisoformat(start),
        reservation_end_date=datetime.fromisoformat(start),
        time_per_reservation=30,
        status=status,
        service="Corte",
    )


def finance(day: str, amount: float, type_: str = "INCOME", creator: str = "Marta", commission: float = 0):
    return Finances(
        business_id=BIZ,
        concept="Corte",
        amount=amount,
        type=type_,
        creator=creator,
        created_at=datetime.fromisoformat(f"{day}T12:00:00"),
        commission_amount=commission,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            reservation_at("2026-10-22T10:00:00", "Ana"),
            reservation_at("2026-10-22T09:00:00", "Luis", in_charge="Pedro"),
            reservation_at("2026-10-23T11:00:00", "Eva"),
            reservation_at("2026-10-22T12:00:00", "Otro negocio", business="otro"),
            finance("2026-10-22", 50, commission=10),
            finance("2026-10-22", 30, creator="Pedro"),
            finance("2026-10-22", 20, type_="EXPENSE"),
            finance("2026-10-21", 40),
            finance("2026-10-20", 100),
            finance("2026-09-30", 500),
            Profile(id="emp-1", display_name="Marta"),
            BookingRequest(
                business_id=BIZ,
                client_name="Sara",
                client_email="sara@example.com",
                client_phone="600",
                service="Tinte",
                requested_date=datetime(2026, 10, 24, 10, 0),
            ),
            BookingRequest(
                business_id=BIZ,
                client_name="Ya aceptada",
                client_email="x@example.com",
                client_phone="600",
                service="Corte",
                requested_date=datetime(2026, 10, 24, 11, 0),
                status="ACCEPTED",
            ),
            Product(business_id=BIZ, name="Corte", price=15, type="service"),
            GoogleReview(
                business_id=BIZ,
                profile_id=1,
                review_id="r1",
                author_title="Juan",
                review_text="Muy bien",
                review_rating=5,
                review_timestamp=1,
            ),
            GoogleReview(
                business_id=BIZ,
                profile_id=1,
                review_id="r2",
                author_title="Pepe",
                review_text="Regular",
                review_rating=3,
                review_timestamp=2,
            ),
            WeeklySummary(
                business_id=BIZ,
                week_start=date(2026, 10, 12),
                week_end=date(2026, 10, 18),
                narrative="Buena semana",
                kpis=json.dumps({"total_income": 300}),
            ),
            WorkingHours(business_id=BIZ, day_of_week=1, start_time="09:00", end_time="18:00"),
            WorkingHours(business_id=BIZ, day_of_week=0, start_time="10:00", end_time="14:00", enabled=False),
        ])
        session.commit()
        yield session


def run(name: str, session: Session, args: dict | None = None, ctx_auth: AuthContext | None = None):
    ctx = ToolContext(auth=ctx_auth or auth(), session=session, today=TODAY)
    return TOOLS[name].handler(ctx, args or {})


class TestGetReservations:
    def test_por_defecto_devuelve_las_de_hoy_ordenadas_y_solo_del_negocio(self, session):
        result = run("get_reservations", session)
        assert [r["client_name"] for r in result.data["reservations"]] == ["Luis", "Ana"]
        block = result.blocks[0]
        assert block["type"] == "reservation_list"
        assert block["title"] == "Reservas de hoy"
        assert block["reservations"][0] == {
            "id": block["reservations"][0]["id"],
            "client_name": "Luis",
            "service": "Corte",
            "start_time": "2026-10-22T09:00:00",
            "end_time": "2026-10-22T09:00:00",
            "employee_name": "Pedro",
            "status": "PENDING",
        }

    def test_manana_y_rango(self, session):
        assert run("get_reservations", session, {"date": "2026-10-23"}).blocks[0]["title"] == "Reservas de mañana"
        result = run("get_reservations", session, {"date": "2026-10-22", "end_date": "2026-10-23"})
        assert result.data["count"] == 3

    def test_filtra_por_profesional(self, session):
        result = run("get_reservations", session, {"employee": "pedro"})
        assert [r["client_name"] for r in result.data["reservations"]] == ["Luis"]

    def test_sin_reservas_devuelve_lista_vacia(self, session):
        result = run("get_reservations", session, {"date": "2026-12-25"})
        assert result.data["count"] == 0
        assert result.blocks[0]["reservations"] == []


class TestGetReservation:
    def test_detalle_y_reserva_de_otro_negocio(self, session):
        mine = run("get_reservations", session).data["reservations"][0]["id"]
        assert run("get_reservation", session, {"id": mine}).data["client_name"] == "Luis"
        other = session.query(Reservation).filter(Reservation.business_id == "otro").one().id
        assert run("get_reservation", session, {"id": other}).data == {"error": "No existe esa reserva."}


class TestGetCashSummary:
    def test_hoy_como_la_pagina_de_finanzas_y_comparado_con_ayer(self, session):
        block = run("get_cash_summary", session).blocks[0]
        assert block == {
            "type": "cash_summary",
            "period_label": "Hoy",
            "total_income": 80,
            "total_expense": 20,
            "balance": 60,
            "previous_balance": 40,
            "currency": "EUR",
        }

    def test_semana_de_lunes_a_domingo(self, session):
        block = run("get_cash_summary", session, {"period": "week"}).blocks[0]
        assert block["total_income"] == 220
        assert block["balance"] == 200

    def test_mes_compara_con_el_mes_anterior(self, session):
        block = run("get_cash_summary", session, {"period": "month"}).blocks[0]
        assert block["balance"] == 200
        assert block["previous_balance"] == 500

    def test_un_empleado_solo_ve_lo_suyo_y_neto_de_comision(self, session):
        employee = auth(role="employee", user_id="emp-1")
        block = run("get_cash_summary", session, ctx_auth=employee).blocks[0]
        assert block["total_income"] == 40  # 50 de Marta menos 10 de comision; lo de Pedro no cuenta
        assert block["total_expense"] == 20


class TestOtrasHerramientas:
    def test_solicitudes_pendientes_con_su_tarjeta(self, session):
        result = run("get_booking_requests", session)
        assert result.data["count"] == 1
        assert result.blocks == [
            {
                "type": "booking_request",
                "request": {
                    "id": result.data["requests"][0]["id"],
                    "client_name": "Sara",
                    "service": "Tinte",
                    "requested_date": "2026-10-24T10:00:00",
                    "employee_name": None,
                    "status": "REQUESTED",
                },
            }
        ]

    def test_productos(self, session):
        assert run("get_products", session).data == [{"name": "Corte", "price": 15, "type": "service"}]

    def test_resenas_recientes_y_por_estrellas(self, session):
        assert [r["author"] for r in run("get_reviews", session).data["reviews"]] == ["Pepe", "Juan"]
        assert run("get_reviews", session, {"rating": 5}).data["total"] == 1

    def test_resumen_semanal(self, session):
        data = run("get_weekly_summary", session).data
        assert data["narrative"] == "Buena semana"
        assert data["kpis"] == {"total_income": 300}

    def test_horario_ordenado_por_dia(self, session):
        assert run("get_working_hours", session).data == [
            {"day": "lunes", "open": False, "start": "10:00", "end": "14:00"},
            {"day": "martes", "open": True, "start": "09:00", "end": "18:00"},
        ]
