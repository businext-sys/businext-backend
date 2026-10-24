"""
Limites, timeout, observabilidad y uso del asistente (businext-backend#25).
"""
import json
import logging
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.database.models.assistant_usage_model import AssistantUsage
from src.main import app
from src.services.assistant import limits
from src.services.assistant.llm import LLMDelta, OpenAIAssistantLLM, cost_usd, get_assistant_llm
from src.services.assistant.orchestrator import TIMEOUT_MESSAGE, TurnStats, run_turn
from src.services.assistant.tools import ToolContext

AUTH = {"Authorization": "Bearer test"}


def auth(role="owner", user_id="user-1") -> AuthContext:
    return AuthContext(
        user_id=user_id,
        business_id="biz-1",
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


class TokensLLM:
    def __init__(self):
        self.calls = 0

    def stream(self, messages, tools):
        self.calls += 1
        yield LLMDelta(text="Hola, Ana")
        yield LLMDelta(prompt_tokens=1000, completion_tokens=100)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def llm():
    return TokensLLM()


@pytest.fixture
def client(engine, llm):
    app.dependency_overrides[get_session] = lambda: Session(engine)
    app.dependency_overrides[require_subscription] = auth
    app.dependency_overrides[get_assistant_llm] = lambda: llm
    yield TestClient(app)
    app.dependency_overrides.clear()


def chat(client, message="Hola") -> list[dict]:
    response = client.post("/assistant/chat", json={"message": message}, headers=AUTH)
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]


def test_superar_el_limite_del_usuario_responde_amable_sin_llamar_al_llm(client, llm, monkeypatch):
    monkeypatch.setattr(limits, "USER_LIMIT", 2)
    chat(client)
    chat(client)
    events = chat(client)
    assert [e["type"] for e in events] == ["message_start", "text_delta", "message_end"]
    assert events[1]["delta"] == limits.LIMIT_MESSAGE
    assert llm.calls == 2


def test_el_limite_del_negocio_cuenta_a_todos_sus_usuarios(monkeypatch):
    monkeypatch.setattr(limits, "BUSINESS_LIMIT", 2)
    assert limits.allow_message("biz-1", "a")
    assert limits.allow_message("biz-1", "b")
    assert not limits.allow_message("biz-1", "c")
    assert limits.allow_message("biz-2", "c")


def test_el_turno_que_se_pasa_de_tiempo_termina_con_un_mensaje(engine):
    stats = TurnStats()
    ctx = ToolContext(auth=auth(), session=Session(engine), today=date(2026, 10, 24))
    events = list(run_turn(TokensLLM(), ctx, {}, "c-1", [], "hola", stats=stats, timeout_seconds=-1))
    assert [e["type"] for e in events] == ["message_start", "text_delta", "message_end"]
    assert events[1]["delta"] == TIMEOUT_MESSAGE
    assert stats.error == "timeout"


def test_log_estructurado_con_tokens_y_sin_contenido(client, caplog):
    with caplog.at_level(logging.INFO, logger="src.routers.assistant"):
        chat(client, "¿Cuánto debe Ana López?")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("assistant_turn "))
    payload = json.loads(line[len("assistant_turn "):])
    assert payload["business_id"] == "biz-1"
    assert payload["prompt_tokens"] == 1000
    assert payload["completion_tokens"] == 100
    assert payload["cost_usd"] == pytest.approx(cost_usd(1000, 100))
    assert payload["tools"] == [] and payload["error"] is None
    assert "Ana" not in line


def test_registra_el_uso_del_dia_y_el_owner_lo_consulta(client, engine):
    chat(client)
    chat(client)
    with Session(engine) as session:
        row = session.exec(select(AssistantUsage)).one()
        assert (row.turns, row.prompt_tokens, row.completion_tokens) == (2, 2000, 200)

    days = client.get("/assistant/usage", headers=AUTH).json()
    assert days[0]["turns"] == 2
    assert days[0]["cost_usd"] == pytest.approx(2 * cost_usd(1000, 100))


def test_un_employee_no_consulta_el_uso(client):
    app.dependency_overrides[require_subscription] = lambda: auth(role="employee", user_id="emp-1")
    assert client.get("/assistant/usage", headers=AUTH).status_code == 403


def test_sin_tabla_de_uso_el_chat_sigue_y_el_endpoint_da_503(llm):
    bare = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    app.dependency_overrides[get_session] = lambda: Session(bare)
    app.dependency_overrides[require_subscription] = auth
    app.dependency_overrides[get_assistant_llm] = lambda: llm
    try:
        client = TestClient(app)
        assert chat(client)[-1]["type"] == "message_end"
        assert client.get("/assistant/usage", headers=AUTH).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_el_adaptador_de_openai_lee_el_uso_del_ultimo_chunk():
    delta = SimpleNamespace(content="Hola", tool_calls=None)
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3)),
    ]
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return iter(chunks)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    deltas = list(OpenAIAssistantLLM(client).stream([], []))
    assert seen["stream_options"] == {"include_usage": True}
    assert deltas[0].text == "Hola"
    assert (deltas[1].prompt_tokens, deltas[1].completion_tokens) == (12, 3)
