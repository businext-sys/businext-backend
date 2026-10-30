"""
Ingesta diaria de tendencias con Claude y el MCP de vidIQ (businext-sys/businext#164).
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.database.database import get_session
from src.database.models.trends_model import ContentPreferences, TrendSnapshot
from src.main import app
from src.services import trend_sources

SECRET = "job-secret"
JOB_AUTH = {"Authorization": f"Bearer {SECRET}"}
TREND = {
    "title": "Degradado en 30 segundos",
    "network": "tiktok",
    "url": "https://www.tiktok.com/@barber/video/1",
    "views": 120000,
    "outlier_score": 8.5,
    "hook": "Antes y despues",
    "format": "transicion",
    "audio": "original",
    "effort": "bajo",
}


def claude_response(text: str | None, stop_reason: str = "end_turn"):
    content = [SimpleNamespace(type="mcp_tool_use"), SimpleNamespace(type="mcp_tool_result")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


@pytest.fixture
def claude(monkeypatch):
    fake = MagicMock()
    fake.beta.messages.create.return_value = claude_response(json.dumps({"trends": [TREND] * 5}))
    monkeypatch.setattr(trend_sources, "client", fake)
    monkeypatch.setattr(trend_sources, "TRENDS_MCP_VIDIQ_URL", "https://mcp.vidiq.test/mcp")
    monkeypatch.setattr(trend_sources, "TRENDS_MCP_VIDIQ_TOKEN", "vidiq-token")
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
def client(session):
    session.add(ContentPreferences(business_id="b1", enabled=True))
    session.add(ContentPreferences(business_id="b2", enabled=True))
    session.add(ContentPreferences(business_id="b3", enabled=False, country="MX"))
    session.commit()
    return TestClient(app)


def snapshots(session) -> list[TrendSnapshot]:
    return session.exec(select(TrendSnapshot)).all()


def api_error(cls, status_code: int):
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("error", response=response, body=None)


class TestFetchVidiqTrends:
    def test_devuelve_las_tendencias(self, claude):
        trends = trend_sources.fetch_vidiq_trends("barberia", "es", "ES")
        assert len(trends) == 5
        assert trends[0].url == TREND["url"]
        request = claude.beta.messages.create.call_args.kwargs
        assert request["mcp_servers"][0]["authorization_token"] == "vidiq-token"
        assert request["tools"][0]["configs"] == {trend_sources.VIDIQ_TOOL: {"enabled": True}}

    def test_descarta_las_tendencias_sin_url(self, claude):
        claude.beta.messages.create.return_value = claude_response(json.dumps({"trends": [TREND, {**TREND, "url": ""}]}))
        assert len(trend_sources.fetch_vidiq_trends("barberia", "es", "ES")) == 1

    def test_lista_vacia_si_vidiq_no_devuelve_nada(self, claude):
        claude.beta.messages.create.return_value = claude_response(json.dumps({"trends": []}))
        assert trend_sources.fetch_vidiq_trends("barberia", "es", "ES") == []

    def test_rechazo(self, claude):
        claude.beta.messages.create.return_value = claude_response(None, stop_reason="refusal")
        with pytest.raises(trend_sources.TrendSourceError):
            trend_sources.fetch_vidiq_trends("barberia", "es", "ES")

    def test_limite_de_la_api(self, claude):
        claude.beta.messages.create.side_effect = api_error(anthropic.RateLimitError, 429)
        with pytest.raises(trend_sources.TrendSourceError):
            trend_sources.fetch_vidiq_trends("barberia", "es", "ES")

    def test_sin_configurar(self, claude, monkeypatch):
        monkeypatch.setattr(trend_sources, "client", None)
        with pytest.raises(trend_sources.TrendSourceError):
            trend_sources.fetch_vidiq_trends("barberia", "es", "ES")


class TestIngestEndpoint:
    def test_crea_un_snapshot_por_combinacion_activa(self, client, session, claude):
        response = client.post("/internal/trends/ingest", headers=JOB_AUTH)
        assert response.status_code == 200
        assert response.json() == {"enabled": True, "created": 1, "skipped": 0, "failed": 0}
        [snapshot] = snapshots(session)
        assert (snapshot.niche, snapshot.language, snapshot.country, snapshot.source) == ("barberia", "es", "ES", "vidiq")
        assert len(json.loads(snapshot.payload)) == 5
        assert claude.beta.messages.create.call_count == 1

    def test_relanzar_el_mismo_dia_no_duplica_ni_consulta(self, client, session, claude):
        client.post("/internal/trends/ingest", headers=JOB_AUTH)
        response = client.post("/internal/trends/ingest", headers=JOB_AUTH)
        assert response.json() == {"enabled": True, "created": 0, "skipped": 1, "failed": 0}
        assert len(snapshots(session)) == 1
        assert claude.beta.messages.create.call_count == 1

    def test_sin_datos_guarda_un_snapshot_vacio(self, client, session, claude):
        claude.beta.messages.create.return_value = claude_response(json.dumps({"trends": []}))
        client.post("/internal/trends/ingest", headers=JOB_AUTH)
        assert json.loads(snapshots(session)[0].payload) == []

    def test_un_fallo_no_guarda_nada(self, client, session, claude):
        claude.beta.messages.create.side_effect = api_error(anthropic.InternalServerError, 500)
        response = client.post("/internal/trends/ingest", headers=JOB_AUTH)
        assert response.json() == {"enabled": True, "created": 0, "skipped": 0, "failed": 1}
        assert snapshots(session) == []

    def test_desactivado_no_hace_nada(self, client, session, claude, monkeypatch):
        monkeypatch.setenv("TRENDS_ENABLED", "false")
        response = client.post("/internal/trends/ingest", headers=JOB_AUTH)
        assert response.json() == {"enabled": False, "created": 0, "skipped": 0, "failed": 0}
        assert snapshots(session) == []
        claude.beta.messages.create.assert_not_called()

    @pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer otro"}, {"Authorization": SECRET}])
    def test_sin_el_secreto_responde_401(self, client, claude, headers):
        assert client.post("/internal/trends/ingest", headers=headers).status_code == 401
        claude.beta.messages.create.assert_not_called()

    def test_sin_secreto_configurado_responde_401(self, client, claude, monkeypatch):
        monkeypatch.delenv("TRENDS_JOB_SECRET")
        assert client.post("/internal/trends/ingest", headers=JOB_AUTH).status_code == 401
