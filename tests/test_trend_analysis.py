"""
Analisis del radar de tendencias y sugerencias de contenido (businext-sys/businext#165).
"""
import json
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AuthContext, _build_capabilities, get_auth_context
from src.database.database import get_session
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.reservation_model import Reservation
from src.database.models.trends_model import (
    ContentPreferences,
    ContentSuggestion,
    TrendInsight,
    TrendSnapshot,
)
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.services import trend_analysis, trend_sources

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
SECRET = "job-secret"
JOB_AUTH = {"Authorization": f"Bearer {SECRET}"}
MADRID = ZoneInfo("Europe/Madrid")
CUSTOMER = "Lucia Perez Garcia"
TREND_URL = "https://www.tiktok.com/@barber/video/1"
TRENDS = [
    {"title": "Degradado en 30 segundos", "network": "tiktok", "url": TREND_URL, "views": 120000,
     "outlier_score": 8.5, "hook": "Antes y despues", "format": "transicion", "audio": "original", "effort": "bajo"},
    {"title": "Barba perfecta", "network": "instagram", "url": "https://www.instagram.com/reel/abc", "views": 90000,
     "outlier_score": 5.0, "hook": None, "format": None, "audio": None, "effort": None},
]
SUGGESTION = {
    "network": "tiktok",
    "format": "reel",
    "idea": "Transicion antes y despues de un corte degradado",
    "script": ["Plano del cliente antes", "Corte en camara rapida", "Resultado final"],
    "caption_hint": "Tu degradado en 30 segundos",
    "audio_hint": "audio original",
    "source_trend_urls": [TREND_URL],
}
ANALYSIS = {
    "analysis": "Los videos de degradado encajan con tu Corte degradado, tu servicio mas pedido.",
    "matched_services": ["Corte degradado"],
    "suggestions": [
        SUGGESTION,
        {**SUGGESTION, "idea": "Inventada", "source_trend_urls": ["https://inventada.example/1"]},
    ],
}


def claude_response(payload: dict | None, stop_reason: str = "end_turn"):
    content = [] if payload is None else [SimpleNamespace(type="text", text=json.dumps(payload))]
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        model="claude-opus-5-5",
        usage=SimpleNamespace(
            input_tokens=900, output_tokens=400, cache_read_input_tokens=100, cache_creation_input_tokens=0
        ),
    )


def as_role(role: str, business_id: str = OWNER) -> AuthContext:
    return AuthContext(
        user_id=business_id if role == "owner" else "marta",
        business_id=business_id,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=_build_capabilities(role, True),
    )


def today() -> datetime:
    return datetime.now(MADRID).date()


@pytest.fixture
def claude(monkeypatch):
    fake = MagicMock()
    fake.beta.messages.create.return_value = claude_response(ANALYSIS)
    monkeypatch.setattr(trend_sources, "client", fake)
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
def business(session):
    preferences = ContentPreferences(business_id=OWNER, enabled=True)
    session.add(preferences)
    session.add(Product(business_id=OWNER, name="Corte degradado", price=15, type="servicio"))
    session.add(Product(business_id=OWNER, name="Cera", price=8, type="producto"))
    for day in range(7):
        session.add(WorkingHours(business_id=OWNER, day_of_week=day, start_time="10:00", end_time="20:00"))
    start = datetime.combine(today() - timedelta(days=3), time(18))
    for i, service in enumerate(["Corte degradado", "Corte degradado", "Barba"]):
        reservation = Reservation(
            business_id=OWNER, customer_name=CUSTOMER, in_charge=None, status="COMPLETED", service=service,
            reservation_start_date=start + timedelta(days=i % 2), reservation_end_date=start + timedelta(hours=1),
            time_per_reservation=30,
        )
        session.add(reservation)
        session.flush()
        session.add(Finances(
            business_id=OWNER, concept=service, amount=20 if service == "Barba" else 15, type="INCOME",
            creator=OWNER, reservation_id=reservation.id, customer_name=CUSTOMER,
            created_at=start + timedelta(days=i % 2),
        ))
    session.add(TrendSnapshot(
        niche="barberia", language="es", country="ES", source="vidiq", day=today(), payload=json.dumps(TRENDS)
    ))
    session.commit()
    return preferences


@pytest.fixture
def client(business, claude):
    app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
    return TestClient(app)


def suggestions(session) -> list[ContentSuggestion]:
    return session.exec(select(ContentSuggestion)).all()


class TestAggregates:
    def test_agregados_del_negocio(self, session, business):
        aggregates = trend_analysis.business_aggregates(session, OWNER, today())
        assert aggregates["services"] == [{"name": "Corte degradado", "price": 15}]
        assert aggregates["reservations_by_service"] == {"Corte degradado": 2, "Barba": 1}
        assert aggregates["income_by_service"] == {"Corte degradado": 30, "Barba": 20}
        assert aggregates["reservations_by_hour"] == {18: 3}
        assert aggregates["average_ticket"] == 16.67
        assert aggregates["reservations_last_7_days"] == 3
        assert CUSTOMER not in json.dumps(aggregates)


class TestPublishSlots:
    def preferences(self, session, **changes) -> ContentPreferences:
        preferences = ContentPreferences(business_id=OWNER, enabled=True, **changes)
        session.add(preferences)
        for day in range(7):
            session.add(WorkingHours(business_id=OWNER, day_of_week=day, start_time="10:00", end_time="20:00"))
        session.commit()
        return preferences

    def test_hora_con_mas_reservas_y_un_dia_por_sugerencia(self, session):
        preferences = self.preferences(session)
        now = datetime(2026, 11, 2, 8, 0)  # lunes, 09:00 en Madrid
        slots = trend_analysis.publish_slots(session, preferences, Counter({18: 3, 12: 1}), 2, now)
        assert slots == [datetime(2026, 11, 2, 17, 0), datetime(2026, 11, 3, 17, 0)]

    def test_respeta_el_horario_silencioso(self, session):
        preferences = self.preferences(session, quiet_from=time(17), quiet_to=time(20))
        now = datetime(2026, 11, 2, 8, 0)
        slots = trend_analysis.publish_slots(session, preferences, Counter({18: 3}), 1, now)
        assert slots == [datetime(2026, 11, 2, 9, 0)]

    def test_salta_los_dias_llenos(self, session):
        preferences = self.preferences(session)
        session.add(ContentSuggestion(
            business_id=OWNER, insight_id=1, network="tiktok", format="reel", idea="x", caption_hint="x",
            publish_at=datetime(2026, 11, 2, 12, 0), source_trend_ids="[]",
        ))
        session.commit()
        slots = trend_analysis.publish_slots(session, preferences, Counter({18: 3}), 1, datetime(2026, 11, 2, 8, 0))
        assert slots == [datetime(2026, 11, 3, 17, 0)]

    def test_cambio_de_hora(self, session):
        preferences = self.preferences(session)
        now = datetime(2026, 10, 24, 8, 0)  # sabado; el domingo 25 Madrid pasa a UTC+1
        slots = trend_analysis.publish_slots(session, preferences, Counter({18: 1}), 2, now)
        assert slots == [datetime(2026, 10, 24, 16, 0), datetime(2026, 10, 25, 17, 0)]

    def test_sin_horario_usa_cualquier_hora_fuera_del_silencio(self, session):
        preferences = ContentPreferences(business_id=OWNER, enabled=True)
        session.add(preferences)
        session.commit()
        slots = trend_analysis.publish_slots(session, preferences, Counter(), 1, datetime(2026, 11, 2, 8, 0))
        assert slots == [datetime(2026, 11, 2, 9, 0)]


class TestAnalyzeEndpoint:
    def test_crea_el_analisis_y_descarta_sugerencias_sin_tendencia(self, client, session, claude):
        response = client.post("/internal/trends/analyze", headers=JOB_AUTH)
        assert response.json() == {"enabled": True, "created": 1, "skipped": 0, "failed": 0}
        insight = session.exec(select(TrendInsight)).one()
        assert insight.input_tokens == 1000
        [suggestion] = suggestions(session)
        assert json.loads(suggestion.source_trend_ids) == [TREND_URL]
        assert json.loads(suggestion.script) == SUGGESTION["script"]

    def test_el_prompt_no_lleva_datos_de_clientes(self, client, claude):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        request = claude.beta.messages.create.call_args.kwargs
        assert CUSTOMER not in json.dumps(request, ensure_ascii=False)
        assert "Corte degradado" in request["messages"][0]["content"]
        assert request["system"][1]["cache_control"] == {"type": "ephemeral"}

    def test_publish_at_dentro_del_horario_y_fuera_del_silencio(self, client, session):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        [suggestion] = suggestions(session)
        local = suggestion.publish_at.replace(tzinfo=timezone.utc).astimezone(MADRID)
        assert time(10) <= local.time() < time(20)

    def test_sustituye_las_pendientes_de_analisis_anteriores(self, client, session):
        yesterday = datetime.combine(today() - timedelta(days=1), time(12))
        for status in ("pending", "sent"):
            session.add(ContentSuggestion(
                business_id=OWNER, insight_id=99, network="tiktok", format="reel", idea=status,
                caption_hint="x", publish_at=yesterday + timedelta(days=3), source_trend_ids="[]", status=status,
            ))
        session.commit()
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        assert sorted((s.idea, s.status) for s in suggestions(session)) == [
            (SUGGESTION["idea"], "pending"), ("sent", "sent"),
        ]

    @pytest.mark.parametrize("max_per_day, created", [(1, 1), (2, 2), (5, 3)])
    def test_crea_como_mucho_max_per_day(self, client, session, business, claude, max_per_day, created):
        business.max_per_day = max_per_day
        session.commit()
        many = [{**SUGGESTION, "idea": f"Idea {i}"} for i in range(4)]
        claude.beta.messages.create.return_value = claude_response({**ANALYSIS, "suggestions": many})
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        assert len(suggestions(session)) == created

    def test_recorta_los_textos_largos(self, client, session, claude):
        long = "Graba el antes y el despues de un corte degradado. " * 5
        suggestion = {**SUGGESTION, "idea": long, "script": [long, long, long], "caption_hint": long}
        claude.beta.messages.create.return_value = claude_response(
            {**ANALYSIS, "analysis": long, "suggestions": [suggestion]}
        )
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        sentence = long.split(". ")[0] + "."
        assert session.exec(select(TrendInsight)).one().analysis == f"{sentence} {sentence}"
        [saved] = suggestions(session)
        assert saved.idea == saved.caption_hint == sentence
        assert json.loads(saved.script) == [sentence] * 3

    def test_relanzar_no_repite(self, client, session, claude):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        response = client.post("/internal/trends/analyze", headers=JOB_AUTH)
        assert response.json() == {"enabled": True, "created": 0, "skipped": 1, "failed": 0}
        assert claude.beta.messages.create.call_count == 1

    def test_rechazo_no_guarda_nada(self, client, session, claude):
        claude.beta.messages.create.return_value = claude_response(None, stop_reason="refusal")
        response = client.post("/internal/trends/analyze", headers=JOB_AUTH)
        assert response.json() == {"enabled": True, "created": 0, "skipped": 0, "failed": 1}
        assert session.exec(select(TrendInsight)).all() == []

    def test_sin_tendencias_no_analiza(self, client, session, claude):
        session.exec(select(TrendSnapshot)).one().payload = "[]"
        session.commit()
        assert client.post("/internal/trends/analyze", headers=JOB_AUTH).json()["skipped"] == 1
        claude.beta.messages.create.assert_not_called()

    def test_desactivado_no_hace_nada(self, client, claude, monkeypatch):
        monkeypatch.setenv("TRENDS_ENABLED", "false")
        assert client.post("/internal/trends/analyze", headers=JOB_AUTH).json()["enabled"] is False
        claude.beta.messages.create.assert_not_called()

    def test_sin_el_secreto_responde_401(self, client):
        assert client.post("/internal/trends/analyze").status_code == 401


class TestInsightAndSuggestions:
    def test_insight_404_sin_analisis(self, client):
        assert client.get("/trends/insight", headers=AUTH).status_code == 404

    def test_insight_con_servicios_y_tendencias(self, client):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        body = client.get("/trends/insight", headers=AUTH).json()
        assert "Corte degradado" in body["analysis"]
        assert body["matched_services"] == ["Corte degradado"]
        assert body["trends"] == [{"title": TRENDS[0]["title"], "network": "tiktok", "url": TREND_URL}]
        assert body["refreshed_at"] is None

    def test_lista_y_filtra_sugerencias(self, client):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        [suggestion] = client.get("/trends/suggestions", headers=AUTH).json()
        assert suggestion["script"] == SUGGESTION["script"]
        assert suggestion["source_trend_ids"] == [TREND_URL]
        assert suggestion["publish_at"].endswith("Z") or suggestion["publish_at"].endswith("+00:00")
        assert client.get("/trends/suggestions?status=opened", headers=AUTH).json() == []

    @pytest.mark.parametrize("status", ["opened", "dismissed", "published"])
    def test_patch_cambia_el_estado(self, client, status):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        [suggestion] = client.get("/trends/suggestions", headers=AUTH).json()
        response = client.patch(f"/trends/suggestions/{suggestion['id']}", json={"status": status}, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["status"] == status

    def test_patch_rechaza_otros_estados_y_otros_negocios(self, client):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        [suggestion] = client.get("/trends/suggestions", headers=AUTH).json()
        url = f"/trends/suggestions/{suggestion['id']}"
        assert client.patch(url, json={"status": "sent"}, headers=AUTH).status_code == 422
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner", "otro-negocio")
        assert client.patch(url, json={"status": "opened"}, headers=AUTH).status_code == 404

    def test_employee_no_tiene_acceso(self, client):
        app.dependency_overrides[get_auth_context] = lambda: as_role("employee")
        assert client.get("/trends/insight", headers=AUTH).status_code == 403
        assert client.get("/trends/suggestions", headers=AUTH).status_code == 403


class TestRefresh:
    def test_un_refresh_al_dia(self, client, session, claude):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        session.exec(select(ContentSuggestion)).one().status = "sent"
        session.commit()
        first = client.post("/trends/insight/refresh", headers=AUTH)
        assert first.status_code == 200
        assert first.json()["refreshed_at"] is not None
        assert [s.status for s in suggestions(session)] == ["sent", "pending"]
        assert client.post("/trends/insight/refresh", headers=AUTH).status_code == 429
        assert claude.beta.messages.create.call_count == 2

    def test_refresh_sustituye_las_pendientes(self, client, session):
        client.post("/internal/trends/analyze", headers=JOB_AUTH)
        client.post("/trends/insight/refresh", headers=AUTH)
        assert len(session.exec(select(TrendInsight)).all()) == 1
        assert len(suggestions(session)) == 1

    def test_refresh_con_el_radar_desactivado(self, client, session, business):
        business.enabled = False
        session.commit()
        assert client.post("/trends/insight/refresh", headers=AUTH).status_code == 409

    def test_refresh_sin_tendencias_de_hoy(self, client, session):
        session.delete(session.exec(select(TrendSnapshot)).one())
        session.commit()
        assert client.post("/trends/insight/refresh", headers=AUTH).status_code == 409
