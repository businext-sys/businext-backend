"""
Permisos por rol de las herramientas del asistente (businext-backend#23).
"""
import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import src.main  # noqa: F401  (registra tablas y herramientas)
from src.api.auth import AccessCapabilities, AuthContext
from src.database.models.finances_model import Finances
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.services.assistant.llm import LLMDelta, ToolCallDelta
from src.services.assistant.orchestrator import run_turn
from src.services.assistant.tools import TOOLS, ToolContext, tools_for

BIZ = "owner-1"
TODAY = date(2026, 10, 22)

MATRIX = {
    "get_reservations": {"owner", "manager", "employee"},
    "get_reservation": {"owner", "manager", "employee"},
    "get_booking_requests": {"owner", "manager", "employee"},
    "answer_booking_request": {"owner", "manager", "employee"},
    "get_products": {"owner", "manager", "employee"},
    "get_working_hours": {"owner", "manager", "employee"},
    "get_cash_summary": {"owner", "manager"},
    "get_weekly_summary": {"owner", "manager"},
    "get_reviews": {"owner", "manager"},
    "create_reservation": {"owner", "manager", "employee"},
    "reschedule_reservation": {"owner", "manager", "employee"},
    "cancel_reservation": {"owner", "manager", "employee"},
    "complete_reservation": {"owner", "manager", "employee"},
    "revert_reservation": {"owner"},
    "register_cash_movement": {"owner", "manager"},
}


def employee() -> AuthContext:
    return AuthContext(
        user_id="emp-1",
        business_id=BIZ,
        role="employee",
        account_type="member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.tools_seen: list[list[str]] = []
        self.calls: list[list[dict]] = []

    def stream(self, messages, tools):
        self.tools_seen.append([t["function"]["name"] for t in tools])
        self.calls.append([dict(m) for m in messages])
        yield from self.responses.pop(0)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        start = datetime(2026, 10, 22, 10, 0)
        session.add_all([
            Profile(id="emp-1", display_name="Marta"),
            Reservation(
                business_id=BIZ, customer_name="De Marta", in_charge="Marta",
                reservation_start_date=start, reservation_end_date=start + timedelta(minutes=30),
                time_per_reservation=30, status="PENDING", service="Corte",
            ),
            Reservation(
                business_id=BIZ, customer_name="De Pedro", in_charge="Pedro",
                reservation_start_date=start, reservation_end_date=start + timedelta(minutes=30),
                time_per_reservation=30, status="PENDING", service="Corte",
            ),
            Finances(business_id=BIZ, concept="Corte", amount=50, type="INCOME", creator="Marta",
                     created_at=datetime(2026, 10, 22, 12, 0)),
        ])
        session.commit()
        yield session


def ctx(session, auth=None) -> ToolContext:
    return ToolContext(auth=auth or employee(), session=session, today=TODAY, conversation_id="c-1")


def test_la_matriz_documentada_coincide_con_las_herramientas():
    assert {name: set(tool.roles) for name, tool in TOOLS.items()} == MATRIX


def test_tools_for_filtra_por_rol():
    assert "get_cash_summary" not in tools_for("employee")
    assert "revert_reservation" not in tools_for("manager")
    assert set(tools_for("owner")) == set(MATRIX)


def test_el_employee_no_recibe_la_caja_y_si_la_pide_no_hay_datos(session):
    llm = FakeLLM([
        [LLMDelta(tool_calls=[ToolCallDelta(index=0, id="c1", name="get_cash_summary", arguments="{}")])],
        [LLMDelta(text="Tu rol no tiene acceso a la caja.")],
    ])
    events = list(run_turn(llm, ctx(session), TOOLS, "c-1", [], "¿cómo va mi caja?"))

    assert "get_cash_summary" not in llm.tools_seen[0]
    assert not [e for e in events if e["type"] in ("block", "tool_status") and e.get("label")]
    tool_message = llm.calls[1][-1]
    assert json.loads(tool_message["content"]) == {"error": "El rol «employee» no tiene permiso para esto."}
    assert "50" not in tool_message["content"]


def test_el_employee_solo_ve_sus_reservas_aunque_pida_las_de_otro(session):
    result = TOOLS["get_reservations"].handler(ctx(session), {"employee": "Pedro"})
    assert [r["client_name"] for r in result.data["reservations"]] == ["De Marta"]


def test_el_employee_no_puede_tocar_reservas_de_otro(session):
    other = session.query(Reservation).filter(Reservation.in_charge == "Pedro").one().id
    assert TOOLS["get_reservation"].handler(ctx(session), {"id": other}).data == {"error": "No existe esa reserva."}
    result = TOOLS["cancel_reservation"].handler(ctx(session), {"id": other})
    assert result.data == {"error": "No existe esa reserva."}
    assert result.blocks == []


def test_el_employee_crea_reservas_a_su_nombre(session):
    result = TOOLS["create_reservation"].handler(
        ctx(session),
        {"customer_name": "Luis", "service": "Corte", "start": "2026-10-23T16:00", "in_charge": "Pedro"},
    )
    assert "con Marta" in result.blocks[0]["description"]
