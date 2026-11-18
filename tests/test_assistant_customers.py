"""
Asistente: tarjetas en el historial, confirmar escribiendo, reservas walk-in
completadas y datos de cliente (businext-sys/businext#220).
"""
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import src.main  # noqa: F401  (registra tablas y herramientas)
from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.database.models.assistant_conversation_model import AssistantConversation, AssistantMessage
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.services.assistant import history
from src.services.assistant.orchestrator import system_prompt
from src.services.assistant.tools import TOOLS, ToolContext

BIZ = "owner-1"
TODAY = date.today()
NOW = datetime.combine(TODAY, datetime.min.time()).replace(hour=19, minute=45)
AUTH_HEADER = {"Authorization": "Bearer test"}


def auth(business_id=BIZ) -> AuthContext:
    return AuthContext(
        user_id=business_id,
        business_id=business_id,
        role="owner",
        account_type="owner",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


def reservation(start: datetime, customer="Jairo", phone=None, email=None, business_id=BIZ, status="PENDING"):
    return Reservation(
        business_id=business_id,
        customer_name=customer,
        in_charge="Dani",
        reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=30),
        time_per_reservation=30,
        status=status,
        service="Corte de pelo",
        customer_phone=phone,
        customer_email=email,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Profile(id=BIZ, display_name="Dani"),
            Product(business_id=BIZ, name="Corte de pelo", price=15, type="service"),
            WorkingHours(business_id=BIZ, day_of_week=TODAY.weekday(), start_time="10:00", end_time="20:00"),
            reservation(datetime.combine(TODAY - timedelta(days=30), datetime.min.time()), phone="600111222",
                        email="viejo@example.com"),
            reservation(datetime.combine(TODAY - timedelta(days=10), datetime.min.time()), customer=" jairo ",
                        email="jairo@example.com"),
            reservation(datetime.combine(TODAY - timedelta(days=5), datetime.min.time()), customer="Lucía"),
            reservation(datetime.combine(TODAY - timedelta(days=1), datetime.min.time()), customer="Jairo",
                        business_id="otro", phone="699999999"),
        ])
        session.commit()
        yield session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_subscription] = auth
    yield TestClient(app)
    app.dependency_overrides.clear()


def ctx(session, conversation_id="c-1", now=NOW) -> ToolContext:
    return ToolContext(auth=auth(), session=session, today=TODAY, conversation_id=conversation_id, now=now)


def call(session, name, args, **kwargs):
    return TOOLS[name].handler(ctx(session, **kwargs), args)


def at(hour: int, minute: int = 0) -> str:
    return f"{TODAY.isoformat()}T{hour:02d}:{minute:02d}"


class TestHistorial:
    def test_las_tarjetas_se_resumen_en_lenguaje_natural(self, session):
        conversation = AssistantConversation(id="c-1", business_id=BIZ, user_id=BIZ, title="x")
        card = {
            "type": "confirmation",
            "action_id": "a-1",
            "title": "Completar reserva",
            "description": "Jairo · Corte de pelo, el jueves 19/11 a las 09:00",
            "status": "pending",
        }
        listing = {"type": "reservation_list", "title": "Reservas de hoy", "reservations": [
            {"id": 7, "client_name": "Ana", "service": "Corte", "start_time": "2026-11-18T10:00:00", "status": "PENDING"},
        ]}
        session.add(conversation)
        session.add(AssistantMessage(id="m-1", conversation_id="c-1", role="assistant", blocks=json.dumps(
            [{"type": "text", "text": "Confírmalo."}, card, listing]
        )))
        session.commit()

        [message] = history.llm_history(session, conversation)
        assert "[tarjeta" not in message["content"] and "{" not in message["content"]
        assert "(Tarjeta «Completar reserva»: Jairo · Corte de pelo, el jueves 19/11 a las 09:00 — pendiente de confirmar, id a-1)" in message["content"]
        assert "Ana · Corte, 2026-11-18T10:00, PENDING, id 7" in message["content"]

    def test_la_vista_previa_limpia_el_formato_viejo(self, session):
        session.add(AssistantConversation(id="c-1", business_id=BIZ, user_id=BIZ, title="x"))
        leaked = 'Listo. [tarjeta mostrada al usuario: {"type": "confirmation", "action_id": "a"}]'
        session.add(AssistantMessage(id="m-1", conversation_id="c-1", role="assistant", blocks=json.dumps(
            [{"type": "text", "text": leaked}, {"type": "confirmation", "title": "x"}]
        )))
        session.commit()
        [summary] = history.list_conversations(session, auth())
        assert summary["last_message_preview"] == "Listo."

    def test_el_prompt_prohibe_el_formato_interno_y_lleva_la_hora(self, session):
        prompt = system_prompt(ctx(session))
        assert "Nunca escribas JSON, ids" in prompt
        assert "son las 19:45" in prompt
        assert "confirm_pending_action" in prompt


class TestConfirmarEscribiendo:
    def test_confirma_la_tarjeta_pendiente(self, session):
        proposed = call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(12)})
        action_id = proposed.blocks[0]["action_id"]
        assert proposed.data["action_id"] == action_id

        result = call(session, "confirm_pending_action", {"action_id": action_id})
        assert result.data["done"] is True
        assert result.blocks[0] == {**proposed.blocks[0], "status": "confirmed"}
        assert session.exec(select(Reservation).where(Reservation.customer_name == "Luis")).one()
        assert "error" in call(session, "confirm_pending_action", {"action_id": action_id}).data

    def test_sin_id_confirma_la_ultima_de_la_conversacion(self, session):
        call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(12)})
        call(session, "create_reservation", {"customer_name": "Eva", "service": "Corte de pelo", "start": at(13)})
        assert call(session, "confirm_pending_action", {}).data["done"] is True
        names = {r.customer_name for r in session.exec(select(Reservation)).all()}
        assert "Eva" in names and "Luis" not in names

    def test_otra_conversacion_no_ejecuta(self, session):
        proposed = call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(12)})
        result = call(session, "confirm_pending_action", {"action_id": proposed.blocks[0]["action_id"]}, conversation_id="otra")
        assert "error" in result.data
        assert not session.exec(select(Reservation).where(Reservation.customer_name == "Luis")).first()


class TestWalkIn:
    def test_ahora_mismo_y_completada_en_una_sola_tarjeta(self, session):
        result = call(session, "create_reservation", {
            "customer_name": "Jairo", "service": "Corte de pelo", "start": at(19, 45), "completed": True,
        })
        [card] = result.blocks
        assert card["title"] == "Crear reserva completada"
        assert call(session, "confirm_pending_action", {"action_id": card["action_id"]}).data["done"] is True
        created = session.exec(
            select(Reservation).where(Reservation.reservation_start_date == datetime.fromisoformat(at(19, 45)))
        ).one()
        assert created.status == "COMPLETED"
        finance = session.exec(select(Finances).where(Finances.reservation_id == created.id)).one()
        assert (finance.amount, finance.type) == (15, "INCOME")

    def test_a_la_misma_hora_sin_ser_ahora_respeta_el_cierre(self, session):
        result = call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(19, 45)},
                      now=NOW - timedelta(hours=3))
        assert "no se trabaja" in result.data["error"]

    def test_walk_in_fuera_de_horario_o_con_conflicto(self, session):
        late = call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(20, 5)},
                    now=NOW + timedelta(minutes=20))
        assert "no se trabaja" in late.data["error"]
        session.add(reservation(datetime.fromisoformat(at(19, 30)), customer="Ana"))
        session.commit()
        busy = call(session, "create_reservation", {"customer_name": "Luis", "service": "Corte de pelo", "start": at(19, 45)})
        assert "conflicto" in busy.data["error"]


class TestDatosDelCliente:
    def test_autocompleta_el_contacto_mas_reciente_del_mismo_nombre(self, session):
        result = call(session, "create_reservation", {"customer_name": "JAIRO", "service": "Corte de pelo", "start": at(12)})
        assert "600111222" in result.blocks[0]["description"]
        assert "jairo@example.com" in result.blocks[0]["description"]
        assert "missing_contact" not in result.data
        call(session, "confirm_pending_action", {})
        created = session.exec(select(Reservation).where(Reservation.customer_name == "JAIRO")).one()
        assert (created.customer_phone, created.customer_email) == ("600111222", "jairo@example.com")

    def test_el_contacto_dado_manda(self, session):
        result = call(session, "create_reservation", {
            "customer_name": "Jairo", "service": "Corte de pelo", "start": at(12),
            "customer_phone": "611 22 33 44", "customer_email": "nuevo@example.com",
        })
        assert "611223344 · nuevo@example.com" in result.blocks[0]["description"]

    def test_sin_contacto_pide_el_email(self, session):
        result = call(session, "create_reservation", {"customer_name": "Nuevo", "service": "Corte de pelo", "start": at(12)})
        assert result.blocks[0]["status"] == "pending"
        assert "email" in result.data["missing_contact"]

    def test_email_no_valido(self, session):
        result = call(session, "create_reservation", {
            "customer_name": "Nuevo", "service": "Corte de pelo", "start": at(12), "customer_email": "no-es-email",
        })
        assert "no es válido" in result.data["error"]

    def test_guarda_el_contacto_de_una_reserva(self, session):
        lucia = session.exec(select(Reservation).where(Reservation.customer_name == "Lucía")).one()
        result = call(session, "update_reservation_contact", {"id": lucia.id, "customer_email": "lucia@example.com"})
        assert result.data["done"] is True
        assert session.get(Reservation, lucia.id).customer_email == "lucia@example.com"

    def test_find_customers(self, session):
        [jairo] = call(session, "find_customers", {"query": "jai"}).data
        assert jairo["name"] == "jairo"
        assert (jairo["phone"], jairo["email"]) == ("600111222", "jairo@example.com")


class TestCustomersEndpoint:
    def test_agrupa_por_nombre_y_ordena_por_ultima_visita(self, client):
        response = client.get("/customers?limit=10", headers=AUTH_HEADER)
        assert response.status_code == 200
        assert [c["name"] for c in response.json()] == ["Lucía", "jairo"]
        jairo = response.json()[1]
        assert set(jairo) == {"name", "phone", "email", "last_visit"}
        assert (jairo["phone"], jairo["email"]) == ("600111222", "jairo@example.com")

    def test_filtra_por_texto_y_solo_el_negocio_del_usuario(self, client):
        assert [c["name"] for c in client.get("/customers/?q=JAI", headers=AUTH_HEADER).json()] == ["jairo"]
        assert client.get("/customers?q=699", headers=AUTH_HEADER).json() == []
        assert len(client.get("/customers?limit=1", headers=AUTH_HEADER).json()) == 1
