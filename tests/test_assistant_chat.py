"""
Tests de POST /assistant/chat (businext-backend#20) con el LLM mockeado.

Se sobreescriben `require_subscription` y `get_assistant_llm`: no se golpea
Supabase ni OpenAI.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.main import app
from src.routers import assistant as assistant_router
from src.services.assistant.llm import LLMDelta, ToolCallDelta, get_assistant_llm
from src.services.assistant.orchestrator import LLM_ERROR_MESSAGE, MAX_STEPS
from src.services.assistant.tools import Tool, ToolResult

AUTH = {"Authorization": "Bearer test"}


class FakeLLM:
    """Devuelve una respuesta (lista de deltas) por cada llamada a stream()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def stream(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        if isinstance(self.responses[0], Exception):
            raise self.responses.pop(0)
        yield from self.responses.pop(0)


def owner() -> AuthContext:
    return AuthContext(
        user_id="user-1",
        business_id="user-1",
        role="owner",
        account_type="owner",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


@pytest.fixture(autouse=True)
def sqlite_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        yield
    app.dependency_overrides.clear()


@pytest.fixture
def use_llm():
    def _use(llm):
        app.dependency_overrides[get_assistant_llm] = lambda: llm
        return llm

    app.dependency_overrides[require_subscription] = owner
    yield _use


@pytest.fixture
def client():
    return TestClient(app)


def events_of(response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in response.text.split("\n")
        if line.startswith("data: ")
    ]


def test_sin_token_devuelve_401(client):
    response = client.post("/assistant/chat", json={"message": "Hola"})
    assert response.status_code == 401


def test_token_invalido_devuelve_401(client):
    response = client.post(
        "/assistant/chat", json={"message": "Hola"}, headers={"Authorization": "Bearer roto"}
    )
    assert response.status_code == 401


def test_sin_openai_configurado_devuelve_503(client, use_llm):
    use_llm(None)
    response = client.post("/assistant/chat", json={"message": "Hola"}, headers=AUTH)
    assert response.status_code == 503


def test_mensaje_vacio_devuelve_422(client, use_llm):
    use_llm(FakeLLM([]))
    response = client.post("/assistant/chat", json={"message": ""}, headers=AUTH)
    assert response.status_code == 422


def test_stream_de_texto_sigue_el_contrato(client, use_llm):
    use_llm(FakeLLM([[LLMDelta(text="Ho"), LLMDelta(text="la")]]))
    response = client.post(
        "/assistant/chat", json={"message": "Hola", "conversation_id": "c-1"}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = events_of(response)
    assert [e["type"] for e in events] == ["message_start", "text_delta", "text_delta", "message_end"]
    assert events[0]["conversation_id"] == "c-1"
    message_id = events[0]["message_id"]
    assert all(e["message_id"] == message_id for e in events)
    assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == "Hola"


def test_sin_conversation_id_genera_uno(client, use_llm):
    use_llm(FakeLLM([[LLMDelta(text="Hola")]]))
    events = events_of(client.post("/assistant/chat", json={"message": "Hola"}, headers=AUTH))
    assert events[0]["conversation_id"]


def test_herramienta_emite_estado_bloque_y_devuelve_el_resultado_al_modelo(
    client, use_llm, monkeypatch
):
    block = {"type": "reservation_list", "title": "Hoy", "reservations": []}
    seen_args = {}

    def handler(ctx, args):
        seen_args.update(args)
        return ToolResult(data={"count": 0}, blocks=[block])

    tool = Tool(
        name="get_reservations",
        description="Reservas",
        parameters={"type": "object", "properties": {"date": {"type": "string"}}},
        status_label="Consultando reservas…",
        handler=handler,
    )
    monkeypatch.setattr(assistant_router, "TOOLS", {tool.name: tool})
    llm = use_llm(
        FakeLLM([
            [
                LLMDelta(tool_calls=[ToolCallDelta(index=0, id="call-1", name="get_reservations", arguments='{"da')]),
                LLMDelta(tool_calls=[ToolCallDelta(index=0, arguments='te": "2026-10-23"}')]),
            ],
            [LLMDelta(text="No tienes reservas hoy.")],
        ])
    )

    events = events_of(client.post("/assistant/chat", json={"message": "¿Qué tengo hoy?"}, headers=AUTH))

    assert [e["type"] for e in events] == [
        "message_start",
        "tool_status",
        "block",
        "tool_status",
        "text_delta",
        "message_end",
    ]
    assert events[1]["label"] == "Consultando reservas…"
    assert events[2]["block"] == block
    assert events[3]["label"] is None
    assert seen_args == {"date": "2026-10-23"}
    tool_message = llm.calls[1][-1]
    assert tool_message == {"role": "tool", "tool_call_id": "call-1", "content": '{"count": 0}'}


def test_herramienta_desconocida_no_rompe_el_turno(client, use_llm):
    llm = use_llm(
        FakeLLM([
            [LLMDelta(tool_calls=[ToolCallDelta(index=0, id="call-1", name="no_existe", arguments="{}")])],
            [LLMDelta(text="No puedo hacer eso.")],
        ])
    )
    events = events_of(client.post("/assistant/chat", json={"message": "x"}, headers=AUTH))
    assert events[-1]["type"] == "message_end"
    assert "desconocida" in llm.calls[1][-1]["content"]


def test_error_del_llm_emite_evento_error(client, use_llm):
    use_llm(FakeLLM([RuntimeError("openai caido")]))
    events = events_of(client.post("/assistant/chat", json={"message": "Hola"}, headers=AUTH))
    assert [e["type"] for e in events] == ["message_start", "error"]
    assert events[1]["message"] == LLM_ERROR_MESSAGE


def test_limita_los_pasos_de_herramientas(client, use_llm):
    loop = [LLMDelta(tool_calls=[ToolCallDelta(index=0, id="c", name="no_existe", arguments="{}")])]
    use_llm(FakeLLM([loop] * MAX_STEPS))
    events = events_of(client.post("/assistant/chat", json={"message": "x"}, headers=AUTH))
    assert events[-1]["type"] == "message_end"
    assert events[-2]["type"] == "text_delta"
