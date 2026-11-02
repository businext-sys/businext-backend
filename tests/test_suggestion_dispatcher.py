"""
Avisos push de las sugerencias de contenido (businext-sys/businext#166).
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.member_model import BusinessMember
from src.database.models.push_token_model import PushToken
from src.database.models.trends_model import ContentPreferences, ContentSuggestion
from src.main import app
from src.services import suggestion_dispatcher

OWNER = "owner-1"
SECRET = "job-secret"
NOW = datetime(2026, 11, 2, 12, 0)  # 13:00 en Madrid


@pytest.fixture
def push(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(suggestion_dispatcher, "send_push_notifications", fake)
    monkeypatch.setenv("TRENDS_ENABLED", "true")
    monkeypatch.setenv("TRENDS_JOB_SECRET", SECRET)
    return fake


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def preferences(session):
    preferences = ContentPreferences(business_id=OWNER, enabled=True)
    session.add(preferences)
    session.add(BusinessConfiguration(business_id=OWNER, business_name="Barberia"))
    session.add(BusinessMember(business_id=OWNER, member_user_id="manager-1", role="manager", status="active"))
    session.add(BusinessMember(business_id=OWNER, member_user_id="manager-2", role="manager", status="inactive"))
    session.add(BusinessMember(business_id=OWNER, member_user_id="employee-1", role="employee", status="active"))
    for user_id in [OWNER, "manager-1", "manager-2", "employee-1", "otro-negocio"]:
        session.add(PushToken(user_id=user_id, token=f"token-{user_id}"))
    session.commit()
    return preferences


def suggestion(session, publish_at: datetime, business_id: str = OWNER, **fields) -> ContentSuggestion:
    suggestion = ContentSuggestion(
        business_id=business_id, insight_id=1, network="tiktok", format="reel",
        idea="Transicion antes y despues de un corte degradado", caption_hint="x",
        publish_at=publish_at, source_trend_ids="[]", **fields,
    )
    session.add(suggestion)
    session.commit()
    return suggestion


def dispatch(session, now: datetime = NOW) -> dict:
    return suggestion_dispatcher.dispatch_suggestions(session, now)


class TestDispatch:
    def test_envia_lo_que_toca_al_owner_y_managers(self, session, preferences, push):
        due = suggestion(session, datetime(2026, 11, 2, 12, 30))
        assert dispatch(session) == {"enabled": True, "sent": 1, "skipped": 0}
        kwargs = push.call_args.kwargs
        assert sorted(kwargs["tokens"]) == ["token-manager-1", "token-owner-1"]
        assert kwargs["title"] == "Idea de contenido para hoy"
        assert kwargs["body"] == "TikTok: Transicion antes y despues de un corte degradado"
        assert kwargs["data"] == {"type": "content_suggestion", "id": due.id}
        assert kwargs["channel_id"] == "content"
        assert due.status == "sent"
        assert due.sent_at == NOW

    def test_lo_que_aun_no_toca_sigue_pendiente(self, session, preferences, push):
        later = suggestion(session, datetime(2026, 11, 2, 13, 0))
        assert dispatch(session)["sent"] == 0
        assert later.status == "pending"
        push.assert_not_called()

    def test_el_ciclo_siguiente_no_reenvia(self, session, preferences, push):
        suggestion(session, datetime(2026, 11, 2, 12, 30))
        dispatch(session)
        assert dispatch(session, datetime(2026, 11, 2, 12, 15))["sent"] == 0
        assert push.call_count == 1

    def test_nada_dentro_del_horario_silencioso(self, session, preferences, push):
        late = suggestion(session, datetime(2026, 11, 2, 22, 0))
        assert dispatch(session, datetime(2026, 11, 2, 21, 30)) == {"enabled": True, "sent": 0, "skipped": 1}
        assert late.status == "pending"
        push.assert_not_called()
        assert dispatch(session, datetime(2026, 11, 3, 8, 0))["sent"] == 1

    def test_respeta_max_per_day(self, session, preferences, push):
        suggestion(session, datetime(2026, 11, 2, 9, 0), status="sent", sent_at=datetime(2026, 11, 2, 8, 15))
        waiting = suggestion(session, datetime(2026, 11, 2, 12, 30))
        assert dispatch(session) == {"enabled": True, "sent": 0, "skipped": 1}
        assert waiting.status == "pending"

    def test_varias_el_mismo_dia_hasta_el_limite(self, session, preferences, push):
        preferences.max_per_day = 2
        session.commit()
        for minute in (0, 10, 20):
            suggestion(session, datetime(2026, 11, 2, 12, minute))
        assert dispatch(session) == {"enabled": True, "sent": 2, "skipped": 1}

    def test_sin_dispositivos_se_marca_enviada(self, session, preferences, push):
        for token in session.exec(select(PushToken)).all():
            session.delete(token)
        session.commit()
        due = suggestion(session, datetime(2026, 11, 2, 12, 30))
        dispatch(session)
        assert push.call_args.kwargs["tokens"] == []
        assert due.status == "sent"

    def test_radar_desactivado_no_envia(self, session, preferences, push):
        preferences.enabled = False
        session.commit()
        suggestion(session, datetime(2026, 11, 2, 12, 30))
        assert dispatch(session)["sent"] == 0
        push.assert_not_called()

    def test_trends_enabled_false_no_hace_nada(self, session, preferences, push, monkeypatch):
        monkeypatch.setenv("TRENDS_ENABLED", "false")
        suggestion(session, datetime(2026, 11, 2, 12, 30))
        assert dispatch(session) == {"enabled": False, "sent": 0, "skipped": 0}
        push.assert_not_called()


class TestDispatchEndpoint:
    def test_con_el_secreto(self, session, preferences, push):
        suggestion(session, datetime(2020, 1, 1, 10, 0))
        response = TestClient(app).post("/internal/trends/dispatch", headers={"Authorization": f"Bearer {SECRET}"})
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    def test_sin_el_secreto_responde_401(self, session, push):
        assert TestClient(app).post("/internal/trends/dispatch").status_code == 401
