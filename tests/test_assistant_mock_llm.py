"""
Modo mock del asistente y semilla E2E (businext-sys/businext#101): los prompts
de los flows Maestro devuelven siempre su tarjeta.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.database.models.reservation_model import Reservation
from src.main import app
from src.services.assistant import e2e_seed
from src.services.assistant.llm import OpenAIAssistantLLM, get_assistant_llm
from src.services.assistant.mock_llm import FALLBACK_TEXT, MockAssistantLLM

OWNER = "owner-e2e"
AUTH = {"Authorization": "Bearer test"}


def owner() -> AuthContext:
    return AuthContext(
        user_id=OWNER,
        business_id=OWNER,
        role="owner",
        account_type="owner",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        e2e_seed.seed(session, OWNER, datetime.now(ZoneInfo("Europe/Madrid")).date())
    app.dependency_overrides[get_session] = lambda: Session(engine)
    app.dependency_overrides[require_subscription] = owner
    app.dependency_overrides[get_assistant_llm] = MockAssistantLLM
    yield TestClient(app), engine
    app.dependency_overrides.clear()


def blocks_for(client, message: str) -> list[dict]:
    response = client.post("/assistant/chat", json={"message": message}, headers=AUTH)
    events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]
    assert events[-1]["type"] == "message_end"
    return [e["block"] for e in events if e["type"] == "block"]


def test_chip_reservas_de_hoy_devuelve_la_tarjeta_de_reservas(client):
    blocks = blocks_for(client[0], "¿Qué reservas tengo hoy?")
    assert blocks[0]["type"] == "reservation_list"
    assert [r["client_name"] for r in blocks[0]["reservations"]] == ["E2E Ana", "E2E Luis"]


def test_cancelacion_devuelve_la_confirmacion_y_confirmar_la_ejecuta(client):
    test_client, engine = client
    response = test_client.post(
        "/assistant/chat", json={"message": "Cancela la próxima reserva de hoy"}, headers=AUTH
    )
    events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]
    confirmation = next(e["block"] for e in events if e["type"] == "block" and e["block"]["type"] == "confirmation")
    assert "E2E Ana" in confirmation["description"]

    result = test_client.post(
        f"/assistant/actions/{confirmation['action_id']}/confirm",
        json={"conversation_id": events[0]["conversation_id"], "decision": "confirm"},
        headers=AUTH,
    )
    assert "Hecho" in result.text
    with Session(engine) as session:
        names = [r.customer_name for r in session.exec(select(Reservation)).all()]
    assert names == ["E2E Luis"]


def test_chip_solicitudes_pendientes_devuelve_la_tarjeta(client):
    blocks = blocks_for(client[0], "¿Qué solicitudes de reserva tengo pendientes?")
    assert blocks[0]["type"] == "booking_request"
    assert blocks[0]["request"]["client_name"] == "E2E Sara"


def test_caja_y_mensaje_sin_palabra_clave(client):
    assert blocks_for(client[0], "¿Cómo va mi caja hoy?")[0]["balance"] == 20
    response = client[0].post("/assistant/chat", json={"message": "hola"}, headers=AUTH)
    assert FALLBACK_TEXT in response.text


def test_la_semilla_es_repetible(client):
    _, engine = client
    with Session(engine) as session:
        e2e_seed.seed(session, OWNER, datetime.now(ZoneInfo("Europe/Madrid")).date())
        assert len(session.exec(select(Reservation)).all()) == 2


def test_el_modo_mock_no_se_activa_en_render(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LLM_MODE", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(get_assistant_llm(), MockAssistantLLM)
    monkeypatch.setenv("RENDER", "true")
    assert isinstance(get_assistant_llm(), OpenAIAssistantLLM)
