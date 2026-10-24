"""
Agente de voz Retell multitenant (businext-sys/businext#117): webhook de
llamada entrante, funciones del agente y configuracion por negocio.
"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.booking_request_model import BookingRequest
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.member_model import BusinessMember
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import Reservation
from src.database.models.voice_agent_model import VoiceAgentConfig, VoicePhoneNumber
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.routers import public_booking, voice_agent
from src.services import retell

API_KEY = "key_test"
OWNER = "owner-1"
OTHER = "owner-2"
NUMBER = "+34910000001"
OTHER_NUMBER = "+34910000002"
TOMORROW = (datetime.now() + timedelta(days=1)).date()


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", API_KEY)
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_template")
    monkeypatch.setattr(public_booking, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(public_booking, "send_push_notifications", lambda **kwargs: None)
    monkeypatch.setattr(voice_agent, "send_email", lambda *args, **kwargs: None)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for business_id, name, number in ((OWNER, "Barbería Centro", NUMBER), (OTHER, "Otra", OTHER_NUMBER)):
            session.add(BusinessConfiguration(business_id=business_id, business_name=name, business_phone="910"))
            session.add(VoicePhoneNumber(phone_number=number, business_id=business_id))
            session.add(VoiceAgentConfig(business_id=business_id, enabled=True))
        session.add(Location(business_id=OWNER, name="Centro", address="Calle Mayor 1"))
        session.add(Product(business_id=OWNER, name="Corte", price=15, type="servicio"))
        session.add(Product(business_id=OTHER, name="Afeitado", price=10, type="servicio"))
        session.add(Profile(id="marta", display_name="Marta", email="marta@example.com"))
        session.add(BusinessMember(business_id=OWNER, member_user_id="marta"))
        for day in range(7):
            session.add(WorkingHours(business_id=OWNER, day_of_week=day, start_time="09:00", end_time="11:00"))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def signed_post(client, path, payload, key=API_KEY):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "X-Retell-Signature": retell.sign(body, key)}
    return client.post(path, content=body, headers=headers)


def inbound(client, to_number):
    payload = {
        "event": "call_inbound",
        "call_inbound": {"call_id": "call_1", "from_number": "+34600000000", "to_number": to_number},
    }
    return signed_post(client, "/retell/inbound", payload)


def function(client, name, args, business_id=OWNER, to_number=NUMBER):
    payload = {
        "name": name,
        "call": {
            "call_id": "call_1",
            "from_number": "+34600000000",
            "to_number": to_number,
            "retell_llm_dynamic_variables": {"business_id": business_id},
        },
        "args": args,
    }
    return signed_post(client, f"/retell/functions/{name}", payload)


BOOKING = {
    "client_name": "Sara",
    "client_email": "sara@example.com",
    "service": "Corte",
    "date": TOMORROW.isoformat(),
    "time": "10:00",
}


class TestSignature:
    def test_verifica_la_firma_de_retell(self):
        body = b'{"a":1}'
        assert retell.verify_signature(body, retell.sign(body, API_KEY), API_KEY)
        assert not retell.verify_signature(body, retell.sign(body, "otra"), API_KEY)
        assert not retell.verify_signature(b'{"a":2}', retell.sign(body, API_KEY), API_KEY)
        assert not retell.verify_signature(body, None, API_KEY)
        assert not retell.verify_signature(body, retell.sign(body, API_KEY), None)

    def test_rechaza_firmas_caducadas(self):
        body = b"{}"
        old = retell.sign(body, API_KEY, now_ms=1_000_000)
        assert not retell.verify_signature(body, old, API_KEY, now_ms=1_000_000 + 6 * 60 * 1000)

    @pytest.mark.parametrize("path", ["/retell/inbound", "/retell/functions/get_services"])
    def test_sin_firma_valida_responde_401(self, client, path):
        assert client.post(path, json={"event": "call_inbound"}).status_code == 401
        assert signed_post(client, path, {"event": "call_inbound"}, key="otra").status_code == 401


class TestInbound:
    def test_devuelve_el_negocio_del_numero(self, client):
        response = inbound(client, NUMBER)
        assert response.status_code == 200
        data = response.json()["call_inbound"]
        assert data["override_agent_id"] == "agent_template"
        variables = data["dynamic_variables"]
        assert variables["business_id"] == OWNER
        assert variables["business_name"] == "Barbería Centro"
        assert variables["booking_mode"] == "request"
        assert "Centro" in variables["locations"]
        assert all(isinstance(v, str) for v in variables.values())

    def test_rechaza_un_numero_sin_negocio(self, client):
        assert inbound(client, "+34999999999").json() == {"call_inbound": {"reject": True}}

    def test_rechaza_si_el_agente_esta_desactivado(self, client, session):
        config = session.exec(select(VoiceAgentConfig).where(VoiceAgentConfig.business_id == OWNER)).one()
        config.enabled = False
        session.commit()
        assert inbound(client, NUMBER).json() == {"call_inbound": {"reject": True}}


class TestFunctions:
    def test_servicios_del_negocio_de_la_llamada(self, client):
        data = function(client, "get_services", {}).json()
        assert [s["name"] for s in data["services"]] == ["Corte"]
        assert data["employees"] == ["Marta"]

    def test_el_modelo_no_elige_el_negocio(self, client):
        # Un business_id que no es el del numero marcado se rechaza.
        assert function(client, "get_services", {}, business_id=OTHER).status_code == 403
        # Y los argumentos no cambian de negocio.
        data = function(client, "get_services", {"business_id": OTHER}).json()
        assert [s["name"] for s in data["services"]] == ["Corte"]

    def test_disponibilidad_real(self, client):
        data = function(client, "check_availability", {"date": TOMORROW.isoformat()}).json()
        assert data["slots"] == {"Marta": ["09:00", "09:30", "10:00", "10:30"]}

    def test_modo_solicitud_crea_una_solicitud(self, client, session):
        data = function(client, "book_appointment", BOOKING).json()
        assert data["ok"] and data["mode"] == "request"
        request = session.get(BookingRequest, data["request_id"])
        assert (request.business_id, request.status, request.employee_name) == (OWNER, "REQUESTED", "Marta")
        assert request.client_phone == "+34600000000"
        assert session.exec(select(Reservation)).all() == []

    def test_modo_confirmar_crea_la_reserva(self, client, session):
        config = session.exec(select(VoiceAgentConfig).where(VoiceAgentConfig.business_id == OWNER)).one()
        config.booking_mode = "confirm"
        session.commit()
        data = function(client, "book_appointment", BOOKING).json()
        assert data["ok"] and data["mode"] == "confirm"
        reservation = session.get(Reservation, data["reservation_id"])
        assert (reservation.business_id, reservation.in_charge, reservation.status) == (OWNER, "Marta", "PENDING")
        assert reservation.reservation_start_date == datetime.combine(TOMORROW, datetime.min.time()).replace(hour=10)
        assert session.exec(select(BookingRequest)).all() == []
        # El hueco queda ocupado.
        again = function(client, "book_appointment", {**BOOKING, "client_email": "otro@example.com"}).json()
        assert again["ok"] is False

    def test_una_hora_no_disponible_vuelve_como_error(self, client):
        data = function(client, "book_appointment", {**BOOKING, "time": "18:00"}).json()
        assert data == {"ok": False, "error": "Esa hora no está disponible. Consulta la disponibilidad y ofrece otra."}

    def test_agente_desactivado(self, client, session):
        config = session.exec(select(VoiceAgentConfig).where(VoiceAgentConfig.business_id == OWNER)).one()
        config.enabled = False
        session.commit()
        assert function(client, "get_services", {}).status_code == 403


def as_role(role: str) -> AuthContext:
    return AuthContext(
        user_id=OWNER if role == "owner" else "marta",
        business_id=OWNER,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


class TestConfig:
    AUTH = {"Authorization": "Bearer test"}

    def test_owner_lee_y_cambia_la_configuracion(self, client):
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
        assert client.get("/voice-agent/config", headers=self.AUTH).json() == {
            "enabled": True, "booking_mode": "request", "phone_numbers": [NUMBER],
        }
        response = client.patch("/voice-agent/config", json={"booking_mode": "confirm"}, headers=self.AUTH)
        assert response.json() == {"enabled": True, "booking_mode": "confirm", "phone_numbers": [NUMBER]}
        response = client.patch("/voice-agent/config", json={"enabled": False}, headers=self.AUTH)
        assert response.json()["enabled"] is False
        assert response.json()["booking_mode"] == "confirm"

    def test_modo_no_valido(self, client):
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
        assert client.patch("/voice-agent/config", json={"booking_mode": "x"}, headers=self.AUTH).status_code == 422

    def test_sin_fila_esta_desactivado(self, client, session):
        session.delete(session.exec(select(VoiceAgentConfig).where(VoiceAgentConfig.business_id == OWNER)).one())
        session.commit()
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
        assert client.get("/voice-agent/config", headers=self.AUTH).json()["enabled"] is False

    @pytest.mark.parametrize("role", ["employee", "manager"])
    def test_rechaza_a_quien_no_es_owner(self, client, role):
        app.dependency_overrides[get_auth_context] = lambda: as_role(role)
        assert client.get("/voice-agent/config", headers=self.AUTH).status_code == 403
        assert client.patch("/voice-agent/config", json={"enabled": False}, headers=self.AUTH).status_code == 403

    def test_sin_cabecera_responde_401(self, client):
        assert client.get("/voice-agent/config").status_code == 401


def test_la_plantilla_del_agente_usa_las_funciones_del_backend():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "scripts" / "retell" / "sync.py"
    spec = importlib.util.spec_from_file_location("retell_sync", path)
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    llm = sync.build_llm(json.loads(sync.TEMPLATE.read_text()), "https://api.example.com/")
    custom = {t["name"]: t["url"] for t in llm["general_tools"] if t["type"] == "custom"}
    assert set(custom) == set(voice_agent.FUNCTIONS)
    assert custom["get_services"] == "https://api.example.com/retell/functions/get_services"
