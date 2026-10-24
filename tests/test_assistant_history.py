"""
Historial de conversaciones del asistente (businext-backend#24).
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, require_subscription
from src.database.database import get_session
from src.database.models.assistant_conversation_model import AssistantConversation, AssistantMessage
from src.main import app
from src.services.assistant import history
from src.services.assistant.llm import LLMDelta, get_assistant_llm

AUTH = {"Authorization": "Bearer test"}


def user(user_id="user-1") -> AuthContext:
    return AuthContext(
        user_id=user_id,
        business_id="biz-1",
        role="owner",
        account_type="owner",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


class EchoLLM:
    def __init__(self):
        self.calls: list[list[dict]] = []

    def stream(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        yield LLMDelta(text="Respuesta ")
        yield LLMDelta(text=str(len(self.calls)))


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def llm():
    return EchoLLM()


@pytest.fixture
def as_user(engine, llm):
    def _as(user_id="user-1"):
        app.dependency_overrides[get_session] = lambda: Session(engine)
        app.dependency_overrides[require_subscription] = lambda: user(user_id)
        app.dependency_overrides[get_assistant_llm] = lambda: llm
        return TestClient(app)

    yield _as
    app.dependency_overrides.clear()


def chat(client, message, conversation_id=None) -> list[dict]:
    body = {"message": message, **({"conversation_id": conversation_id} if conversation_id else {})}
    response = client.post("/assistant/chat", json=body, headers=AUTH)
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: ")]


def test_guarda_el_turno_y_reanuda_con_contexto(as_user, engine, llm):
    client = as_user()
    first = chat(client, "¿Qué reservas tengo hoy por la tarde, después de comer?")
    conversation_id = first[0]["conversation_id"]

    with Session(engine) as session:
        conversation = session.get(AssistantConversation, conversation_id)
        assert conversation.title == "¿Qué reservas tengo hoy por la tarde, después de comer?"
        messages = session.exec(select(AssistantMessage).order_by(AssistantMessage.created_at)).all()
        assert [m.role for m in messages] == ["user", "assistant"]
        assert json.loads(messages[1].blocks) == [{"type": "text", "text": "Respuesta 1"}]
        assert messages[1].id == first[0]["message_id"]

    second = chat(client, "¿Y mañana?", conversation_id)
    assert second[0]["conversation_id"] == conversation_id
    sent = llm.calls[1]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[2]["content"] == "Respuesta 1"


def test_titulo_largo_se_recorta(as_user, engine):
    long = "a" * 80
    conversation_id = chat(as_user(), long)[0]["conversation_id"]
    with Session(engine) as session:
        title = session.get(AssistantConversation, conversation_id).title
    assert len(title) == history.TITLE_LENGTH and title.endswith("…")


def test_no_se_puede_reanudar_la_conversacion_de_otro(as_user, llm):
    conversation_id = chat(as_user("user-1"), "secreto")[0]["conversation_id"]
    other = chat(as_user("user-2"), "hola", conversation_id)
    assert other[0]["conversation_id"] != conversation_id
    assert [m["role"] for m in llm.calls[1]] == ["system", "user"]


def test_listar_abrir_y_borrar(as_user):
    client = as_user()
    conversation_id = chat(client, "Reservas de hoy")[0]["conversation_id"]

    listed = client.get("/assistant/conversations", headers=AUTH).json()
    assert listed == [{
        "id": conversation_id,
        "title": "Reservas de hoy",
        "last_message_preview": "Respuesta 1",
        "updated_at": listed[0]["updated_at"],
    }]

    opened = client.get(f"/assistant/conversations/{conversation_id}", headers=AUTH).json()
    assert [m["role"] for m in opened["messages"]] == ["user", "assistant"]
    assert opened["messages"][0]["blocks"] == [{"type": "text", "text": "Reservas de hoy"}]
    assert set(opened["messages"][0]) == {"id", "conversation_id", "role", "blocks", "created_at"}

    assert client.delete(f"/assistant/conversations/{conversation_id}", headers=AUTH).status_code == 204
    assert client.get("/assistant/conversations", headers=AUTH).json() == []
    assert client.get(f"/assistant/conversations/{conversation_id}", headers=AUTH).status_code == 404


def test_otro_usuario_no_ve_ni_borra(as_user):
    conversation_id = chat(as_user("user-1"), "Reservas de hoy")[0]["conversation_id"]
    other = as_user("user-2")
    assert other.get("/assistant/conversations", headers=AUTH).json() == []
    assert other.get(f"/assistant/conversations/{conversation_id}", headers=AUTH).status_code == 404
    assert other.delete(f"/assistant/conversations/{conversation_id}", headers=AUTH).status_code == 404


def test_el_contexto_se_recorta(as_user, engine, llm):
    client = as_user()
    conversation_id = chat(client, "uno")[0]["conversation_id"]
    for i in range(15):
        chat(client, f"mensaje {i}", conversation_id)
    assert len(llm.calls[-1]) == 1 + history.HISTORY_LIMIT + 1


def test_confirmar_guarda_el_resultado_en_la_conversacion(as_user, engine):
    client = as_user()
    conversation_id = chat(client, "hola")[0]["conversation_id"]
    response = client.post(
        "/assistant/actions/no-existe/confirm",
        json={"conversation_id": conversation_id, "decision": "confirm"},
        headers=AUTH,
    )
    assert response.status_code == 200
    opened = client.get(f"/assistant/conversations/{conversation_id}", headers=AUTH).json()
    assert "ya no está disponible" in opened["messages"][-1]["blocks"][0]["text"]


def test_sin_tablas_el_chat_funciona_y_el_historial_da_503(llm):
    bare = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    app.dependency_overrides[get_session] = lambda: Session(bare)
    app.dependency_overrides[require_subscription] = user
    app.dependency_overrides[get_assistant_llm] = lambda: llm
    try:
        client = TestClient(app)
        events = chat(client, "hola")
        assert events[-1]["type"] == "message_end"
        assert client.get("/assistant/conversations", headers=AUTH).status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_historial_y_uso_sin_cabecera_devuelven_401():
    client = TestClient(app)
    assert client.get("/assistant/conversations").status_code == 401
    assert client.get("/assistant/conversations/x").status_code == 401
    assert client.delete("/assistant/conversations/x").status_code == 401
    assert client.get("/assistant/usage").status_code == 401
