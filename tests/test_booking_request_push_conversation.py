"""
La push de una solicitud de reserva abre una conversacion del asistente
(businext-sys/businext#100).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.database.database import get_session
from src.database.models.assistant_conversation_model import AssistantConversation, AssistantMessage
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.push_token_model import PushToken
from src.main import app
from src.routers import public_booking

OWNER = "owner-1"


def seed(session: Session) -> None:
    session.add(BusinessConfiguration(business_id=OWNER, business_name="Barbería"))
    session.add(PushToken(user_id=OWNER, token="ExponentPushToken[abc]"))
    session.commit()


@pytest.fixture
def sent(monkeypatch):
    pushes: list[dict] = []
    monkeypatch.setattr(public_booking, "send_push_notifications", lambda **kwargs: pushes.append(kwargs))
    monkeypatch.setattr(public_booking, "send_email", lambda *args, **kwargs: None)
    return pushes


def request_booking(session: Session) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        when = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = TestClient(app).post(
            f"{public_booking.router.prefix}/{OWNER}/request",
            json={
                "client_name": "Sara",
                "client_email": "sara@example.com",
                "client_phone": "600",
                "employee_name": "Marta",
                "service": "Tinte",
                "requested_date": when.replace(tzinfo=None).isoformat(),
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 201, response.text


def test_la_push_lleva_la_conversacion_con_la_tarjeta(sent):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        request_booking(session)

        data = sent[0]["data"]
        assert data["type"] == "booking_request"
        conversation = session.get(AssistantConversation, data["conversationId"])
        assert (conversation.business_id, conversation.user_id) == (OWNER, OWNER)
        assert conversation.title == "Solicitud de Sara"

        message = session.exec(select(AssistantMessage)).one()
        assert message.role == "assistant"
        blocks = json.loads(message.blocks)
        assert blocks[0]["text"].startswith("Tienes una nueva solicitud de reserva de **Sara**: Tinte con Marta, el ")
        assert blocks[1]["type"] == "booking_request"
        assert blocks[1]["request"]["id"] == data["bookingRequestId"]
        assert blocks[1]["request"]["status"] == "REQUESTED"


def test_sin_tablas_del_asistente_la_push_sale_sin_conversacion(sent):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [
        t for name, t in SQLModel.metadata.tables.items()
        if name not in ("assistantconversation", "assistantmessage")
    ]
    SQLModel.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        seed(session)
        request_booking(session)

    assert sent[0]["data"] == {"type": "booking_request", "bookingRequestId": sent[0]["data"]["bookingRequestId"]}
