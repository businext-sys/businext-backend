"""
Tests de las acciones con confirmacion del asistente (businext-backend#22,
businext-sys/businext#99): proponer no escribe; solo confirmar ejecuta.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.database.models.booking_request_model import BookingRequest
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.main import app
from src.services.assistant import actions
from src.services.assistant.tools import TOOLS, ToolContext

BIZ = "owner-1"
AUTH_HEADER = {"Authorization": "Bearer test"}


def auth(role="owner", user_id=BIZ, business_id=BIZ) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        business_id=business_id,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


def reservation(start: str, customer="Ana", in_charge="Dani", status="PENDING"):
    moment = datetime.fromisoformat(start)
    return Reservation(
        business_id=BIZ,
        customer_name=customer,
        in_charge=in_charge,
        reservation_start_date=moment,
        reservation_end_date=moment + timedelta(minutes=30),
        time_per_reservation=30,
        status=status,
        service="Corte",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        today = datetime.now(timezone.utc).date().isoformat()
        session.add_all([
            Profile(id=BIZ, display_name="Dani"),
            Product(business_id=BIZ, name="Corte", price=15, type="service"),
            reservation(f"{today}T10:00:00"),
            reservation(f"{today}T12:00:00", customer="Hecha", status="COMPLETED"),
            BookingRequest(
                business_id=BIZ,
                client_name="Sara",
                client_email="sara@example.com",
                client_phone="600",
                service="Tinte",
                requested_date=datetime.fromisoformat(f"{today}T17:00:00"),
            ),
        ])
        session.commit()
        yield session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_subscription] = auth
    yield TestClient(app)
    app.dependency_overrides.clear()


def propose(session, name, args, conversation_id="c-1", ctx_auth=None):
    ctx = ToolContext(
        auth=ctx_auth or auth(), session=session, today=date.today(), conversation_id=conversation_id
    )
    return TOOLS[name].handler(ctx, args)


def confirm(client, action_id, decision="confirm", conversation_id="c-1") -> str:
    response = client.post(
        f"/assistant/actions/{action_id}/confirm",
        json={"conversation_id": conversation_id, "decision": decision},
        headers=AUTH_HEADER,
    )
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["message_start", "text_delta", "message_end"]
    assert events[0]["conversation_id"] == conversation_id
    return events[1]["delta"]


def pending_reservation_id(session) -> int:
    return session.exec(select(Reservation).where(Reservation.customer_name == "Ana")).one().id


class TestProponer:
    def test_proponer_no_escribe_y_devuelve_la_tarjeta(self, session):
        rid = pending_reservation_id(session)
        result = propose(session, "cancel_reservation", {"id": rid})
        block = result.blocks[0]
        assert block["type"] == "confirmation"
        assert block["status"] == "pending"
        assert block["title"] == "Cancelar reserva"
        assert "Ana" in block["description"]
        assert result.data["pending_confirmation"] is True
        assert session.get(Reservation, rid) is not None

    def test_reserva_inexistente_o_de_otro_negocio(self, session):
        assert propose(session, "cancel_reservation", {"id": 9999}).data == {"error": "No existe esa reserva."}
        other = auth(user_id="otro", business_id="otro")
        rid = pending_reservation_id(session)
        assert "error" in propose(session, "cancel_reservation", {"id": rid}, ctx_auth=other).data

    def test_crear_con_conflicto_de_horario_no_propone(self, session):
        today = datetime.now(timezone.utc).date().isoformat()
        result = propose(
            session,
            "create_reservation",
            {"customer_name": "Luis", "service": "Corte", "start": f"{today}T10:15"},
        )
        assert "conflicto" in result.data["error"]
        assert result.blocks == []

    def test_importe_negativo(self, session):
        result = propose(session, "register_cash_movement", {"type": "expense", "amount": -5, "concept": "x"})
        assert "error" in result.data

    def test_solo_el_owner_puede_revertir(self, session):
        employee = auth(role="employee", user_id="emp-1")
        result = propose(session, "revert_reservation", {"id": 1}, ctx_auth=employee)
        assert "dueño" in result.data["error"]


class TestConfirmar:
    def test_confirmar_cancela_la_reserva_una_sola_vez(self, client, session):
        rid = pending_reservation_id(session)
        action_id = propose(session, "cancel_reservation", {"id": rid}).blocks[0]["action_id"]

        assert confirm(client, action_id).startswith("Hecho")
        assert session.get(Reservation, rid) is None
        assert "ya no está disponible" in confirm(client, action_id)

    def test_cancel_no_modifica_nada(self, client, session):
        rid = pending_reservation_id(session)
        action_id = propose(session, "cancel_reservation", {"id": rid}).blocks[0]["action_id"]
        assert confirm(client, action_id, decision="cancel") == "Vale, no he hecho nada."
        assert session.get(Reservation, rid) is not None

    def test_otra_conversacion_no_ejecuta(self, client, session):
        rid = pending_reservation_id(session)
        action_id = propose(session, "cancel_reservation", {"id": rid}).blocks[0]["action_id"]
        assert "ya no está disponible" in confirm(client, action_id, conversation_id="otra")
        assert session.get(Reservation, rid) is not None

    def test_otro_usuario_no_ejecuta(self, client, session):
        rid = pending_reservation_id(session)
        manager = auth(role="manager", user_id="manager-1")
        action_id = propose(session, "cancel_reservation", {"id": rid}, ctx_auth=manager).blocks[0]["action_id"]
        assert "ya no está disponible" in confirm(client, action_id)
        assert session.get(Reservation, rid) is not None

    def test_caducada_no_ejecuta(self, client, session):
        rid = pending_reservation_id(session)
        action_id = propose(session, "cancel_reservation", {"id": rid}).blocks[0]["action_id"]
        actions._pending[action_id].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert "ya no está disponible" in confirm(client, action_id)
        assert session.get(Reservation, rid) is not None

    def test_error_de_negocio_se_explica(self, client, session):
        done = session.exec(select(Reservation).where(Reservation.customer_name == "Hecha")).one().id
        action_id = propose(session, "cancel_reservation", {"id": done}).blocks[0]["action_id"]
        assert confirm(client, action_id) == "No se ha podido hacer: No se puede eliminar una reserva ya completada"

    def test_completar_registra_el_cobro_con_el_precio_del_servicio(self, client, session):
        rid = pending_reservation_id(session)
        action_id = propose(session, "complete_reservation", {"id": rid}).blocks[0]["action_id"]
        assert confirm(client, action_id).startswith("Hecho")
        assert session.get(Reservation, rid).status == "COMPLETED"
        finance = session.exec(select(Finances).where(Finances.reservation_id == rid)).one()
        assert (finance.amount, finance.type, finance.creator) == (15, "INCOME", "Dani")

    def test_crear_y_mover_reserva(self, client, session):
        tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        args = {"customer_name": "Luis", "service": "Corte", "start": f"{tomorrow}T11:00"}
        action_id = propose(session, "create_reservation", args).blocks[0]["action_id"]
        assert confirm(client, action_id).startswith("Hecho")
        created = session.exec(select(Reservation).where(Reservation.customer_name == "Luis")).one()
        assert created.in_charge == "Dani"

        action_id = propose(
            session, "reschedule_reservation", {"id": created.id, "new_start": f"{tomorrow}T13:00"}
        ).blocks[0]["action_id"]
        assert confirm(client, action_id).startswith("Hecho")
        session.refresh(created)
        assert created.reservation_start_date.hour == 13

    def test_aceptar_solicitud_crea_la_reserva(self, client, session):
        request = session.exec(select(BookingRequest)).one()
        action_id = propose(
            session, "answer_booking_request", {"id": request.id, "decision": "accept"}
        ).blocks[0]["action_id"]
        assert confirm(client, action_id).startswith("Hecho")
        session.refresh(request)
        assert request.status == "ACCEPTED"
        assert session.exec(select(Reservation).where(Reservation.customer_name == "Sara")).one()

    def test_registrar_gasto(self, client, session):
        action_id = propose(
            session, "register_cash_movement", {"type": "expense", "amount": 12.5, "concept": "Toallas"}
        ).blocks[0]["action_id"]
        assert confirm(client, action_id) == "Hecho: gasto de 12.50 € registrado (Toallas)."
        expense = session.exec(select(Finances).where(Finances.type == "EXPENSE")).one()
        assert (expense.amount, expense.concept, expense.creator) == (12.5, "Toallas", "Dani")


def test_confirmar_sin_token_devuelve_401():
    response = TestClient(app).post(
        "/assistant/actions/x/confirm", json={"conversation_id": "c", "decision": "confirm"}
    )
    assert response.status_code == 401
