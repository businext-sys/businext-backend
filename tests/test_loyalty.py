"""
Tarjeta de fidelidad: sellos por reserva completada, regla del negocio y canje (docs/adr/0003).
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.loyalty_model import LoyaltyCard, LoyaltyProgram, LoyaltyStamp, WalletRegistration
from src.database.models.reservation_model import Reservation
from src.main import app
from src.services import apple_wallet, loyalty_service
from tests.wallet_certs import make_certs, set_wallet_env

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
WALLET_ENV = ("APPLE_WALLET_PASS_TYPE_ID", "APPLE_WALLET_TEAM_ID", "APPLE_WALLET_CERT", "APPLE_WALLET_KEY",
              "APPLE_WALLET_WWDR", "PUBLIC_API_URL")


def _auth(role: str) -> AuthContext:
    return AuthContext(
        user_id=f"{role}-user",
        business_id=OWNER,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status=None if role == "owner" else "active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


@pytest.fixture(autouse=True)
def pushes(monkeypatch):
    """Sin Wallet configurado por defecto; los pushes se apuntan en vez de ir a APNs."""
    for name in WALLET_ENV:
        monkeypatch.delenv(name, raising=False)
    sent: list[list[str]] = []
    monkeypatch.setattr(apple_wallet, "push_updates", lambda tokens, on_dead_tokens=None: sent.append(sorted(tokens)))
    return sent


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessConfiguration(business_id=OWNER, business_name="Barbería Sol"))
        session.add(LoyaltyProgram(business_id=OWNER, enabled=True, stamps_required=3, reward="Corte gratis"))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_auth_context] = lambda: _auth("owner")
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def reservation(session, start=datetime(2026, 11, 1, 10), **fields) -> Reservation:
    values = dict(
        business_id=OWNER, customer_name="Ana", in_charge=None, reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=30), time_per_reservation=30,
        status="PENDING", service="Corte", customer_email="Ana@Example.com",
    )
    values.update(fields)
    r = Reservation(**values)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def stamps(session) -> list[LoyaltyStamp]:
    return list(session.exec(select(LoyaltyStamp)).all())


def complete(client, reservation_id: int):
    return client.patch(f"/reservations/{reservation_id}", json={"status": "COMPLETED"}, headers=AUTH)


# --- Sellos ------------------------------------------------------------------

def test_completar_una_reserva_da_un_sello_y_crea_la_tarjeta(client, session):
    r = reservation(session)
    assert complete(client, r.id).status_code == 200

    card = session.exec(select(LoyaltyCard)).one()
    assert card.customer_email == "ana@example.com"
    assert card.customer_name == "Ana"
    assert [s.reservation_id for s in stamps(session)] == [r.id]
    assert len(card.auth_token) >= 16


def test_sellar_es_idempotente(session):
    r = reservation(session, status="COMPLETED")
    loyalty_service.sync_reservation(session, r)
    loyalty_service.sync_reservation(session, r)
    assert len(stamps(session)) == 1


def test_crear_una_reserva_ya_completada_tambien_sella(client, session):
    body = {
        "customer_name": "Ana", "in_charge": None, "reservation_start_date": "2026-11-01T10:00:00",
        "status": "COMPLETED", "service": "Corte", "time_per_reservation": 30, "customer_email": "ana@example.com",
    }
    assert client.post("/reservations/", json=body, headers=AUTH).status_code == 200
    assert len(stamps(session)) == 1


def test_pendiente_o_no_vino_no_sellan(client, session):
    r = reservation(session)
    client.patch(f"/reservations/{r.id}", json={"status": "NO_SHOW"}, headers=AUTH)
    assert stamps(session) == []


def test_revertir_quita_el_sello(client, session):
    r = reservation(session)
    complete(client, r.id)
    assert client.post(f"/reservations/{r.id}/revert", headers=AUTH).status_code == 200
    assert stamps(session) == []


def test_revertir_no_quita_un_sello_ya_canjeado(session):
    r = reservation(session, status="COMPLETED")
    loyalty_service.sync_reservation(session, r)
    stamp = stamps(session)[0]
    stamp.redeemed_at = datetime.utcnow()
    session.add(stamp)
    r.status = "PENDING"
    session.add(r)
    session.commit()
    loyalty_service.sync_reservation(session, r)
    assert len(stamps(session)) == 1


def test_programa_desactivado_no_sella(client, session):
    program = session.exec(select(LoyaltyProgram)).one()
    program.enabled = False
    session.add(program)
    session.commit()
    r = reservation(session)
    complete(client, r.id)
    assert stamps(session) == []


def test_sin_email_usa_el_de_otras_reservas_del_mismo_nombre(client, session):
    reservation(session, start=datetime(2026, 10, 1, 10), customer_email="ana@example.com")
    walk_in = reservation(session, customer_name="  ana ", customer_email=None)
    complete(client, walk_in.id)
    assert session.exec(select(LoyaltyCard)).one().customer_email == "ana@example.com"
    assert len(stamps(session)) == 1


def test_sin_ningun_email_no_hay_tarjeta(client, session):
    r = reservation(session, customer_name="Luis", customer_email=None)
    assert complete(client, r.id).status_code == 200
    assert session.exec(select(LoyaltyCard)).all() == []


def test_sellar_avisa_a_los_dispositivos_registrados(client, session, pushes):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    session.add(WalletRegistration(
        device_library_identifier="device-1", pass_type_identifier="pass.x", serial_number=card.serial_number,
        push_token="push-1",
    ))
    session.commit()
    before = card.updated_at
    r = reservation(session)
    pushes.clear()
    complete(client, r.id)
    session.refresh(card)
    assert pushes == [["push-1"]]
    assert card.updated_at > before


def test_touch_siempre_avanza_un_segundo_entero(session):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    loyalty_service.touch(card)
    first = card.updated_at
    loyalty_service.touch(card)
    assert first.microsecond == 0
    assert card.updated_at - first >= timedelta(seconds=1)


# --- Regla del negocio -------------------------------------------------------

def test_regla_por_defecto_sin_configurar(client, session):
    session.delete(session.exec(select(LoyaltyProgram)).one())
    session.commit()
    body = client.get("/loyalty/program", headers=AUTH).json()
    assert body["enabled"] is False
    assert body["stamps_required"] == 10
    assert body["reward"] == "Corte gratis"
    assert body["apple_wallet_available"] is False


def test_owner_cambia_la_regla_y_se_regeneran_los_pases(client, session, pushes):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    session.add(WalletRegistration(
        device_library_identifier="d", pass_type_identifier="p", serial_number=card.serial_number, push_token="t",
    ))
    session.commit()
    response = client.put("/loyalty/program", json={
        "enabled": True, "stamps_required": 8, "reward": "Barba gratis", "background_color": "#112233",
    }, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["stamps_required"] == 8
    assert pushes == [["t"]]


@pytest.mark.parametrize("body", [
    {"stamps_required": 0}, {"stamps_required": 51}, {"reward": ""}, {"background_color": "red"},
])
def test_regla_invalida_da_422(client, body):
    assert client.put("/loyalty/program", json={"enabled": True, **body}, headers=AUTH).status_code == 422


def test_solo_el_owner_cambia_la_regla(client):
    app.dependency_overrides[get_auth_context] = lambda: _auth("manager")
    assert client.put("/loyalty/program", json={"enabled": True}, headers=AUTH).status_code == 403
    assert client.get("/loyalty/program", headers=AUTH).status_code == 200


# --- Tarjeta y canje ---------------------------------------------------------

def test_tarjeta_del_cliente_por_email_o_nombre(client, session):
    for day in (1, 2):
        r = reservation(session, start=datetime(2026, 11, day, 10))
        complete(client, r.id)
    by_email = client.get("/loyalty/cards", params={"email": "ANA@example.com"}, headers=AUTH).json()
    by_name = client.get("/loyalty/cards", params={"name": "ana"}, headers=AUTH).json()
    assert by_email == by_name
    assert by_email["stamps"] == 2
    assert by_email["stamps_required"] == 3
    assert by_email["reward_ready"] is False
    assert by_email["pass_url"] is None  # Wallet sin configurar
    assert [v["visit_date"][:10] for v in by_email["visits"]] == ["2026-11-02", "2026-11-01"]


def test_tarjeta_inexistente_da_404(client):
    assert client.get("/loyalty/cards", params={"email": "nadie@example.com"}, headers=AUTH).status_code == 404


def test_crear_tarjeta_en_el_local_con_enlace_al_pase(client, monkeypatch):
    set_wallet_env(monkeypatch, make_certs())
    response = client.post("/loyalty/cards", json={"customer_name": "Eva", "customer_email": "Eva@Example.com"}, headers=AUTH)
    body = response.json()
    assert response.status_code == 200
    assert body["customer_email"] == "eva@example.com"
    assert body["pass_url"].startswith(f"https://api.test.example/public/loyalty/passes/{body['serial_number']}.pkpass?token=")


def test_canjear_gasta_los_sellos_mas_antiguos(client, session):
    for day in (1, 2, 3, 4):
        complete(client, reservation(session, start=datetime(2026, 11, day, 10)).id)
    serial = session.exec(select(LoyaltyCard)).one().serial_number
    body = client.post(f"/loyalty/cards/{serial}/redeem", headers=AUTH).json()
    assert body["stamps"] == 1
    redeemed = sorted(s.visit_date.day for s in stamps(session) if s.redeemed_at)
    assert redeemed == [1, 2, 3]
    assert client.post(f"/loyalty/cards/{serial}/redeem", headers=AUTH).status_code == 409


def test_no_se_canjea_la_tarjeta_de_otro_negocio(client, session):
    other = loyalty_service.get_or_create_card(session, "otro", "ana@example.com", "Ana")
    assert client.post(f"/loyalty/cards/{other.serial_number}/redeem", headers=AUTH).status_code == 404


# --- Pagina publica ----------------------------------------------------------

def test_info_publica_del_programa(client):
    body = client.get(f"/public/loyalty/{OWNER}").json()
    assert body == {"enabled": True, "stamps_required": 3, "reward": "Corte gratis", "apple_wallet_available": False}
    assert client.get("/public/loyalty/no-existe").status_code == 404


def _public_request(client, monkeypatch, email="eva@example.com"):
    from src.routers import public_booking

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(public_booking, "send_email", lambda to, subject, html, **k: sent.append((to, subject, html)))
    response = client.post(f"/public/book/{OWNER}/request", json={
        "client_name": "Eva", "client_email": email, "client_phone": "600000000",
        "service": "Corte", "requested_date": (datetime.utcnow() + timedelta(days=2)).isoformat(),
    })
    assert response.status_code == 201
    return response, {to: html for to, _, html in sent}


def test_la_solicitud_publica_no_devuelve_el_enlace_al_pase(client, session, monkeypatch):
    """Privacidad: quien pida con un email ajeno no obtiene la tarjeta de ese cliente."""
    set_wallet_env(monkeypatch, make_certs())
    response, _ = _public_request(client, monkeypatch)
    assert "wallet_pass_url" not in response.json()
    card = session.exec(select(LoyaltyCard)).one()
    assert card.auth_token not in response.text and card.serial_number not in response.text


def test_el_enlace_al_pase_va_en_el_email_de_la_solicitud(client, session, monkeypatch):
    set_wallet_env(monkeypatch, make_certs())
    _, emails = _public_request(client, monkeypatch)
    card = session.exec(select(LoyaltyCard)).one()
    html = emails["eva@example.com"]
    assert "Añadir a Apple Wallet" in html
    assert f"/public/loyalty/passes/{card.serial_number}.pkpass?token={card.auth_token}" in html


def test_sin_programa_el_email_no_trae_pase(client, session, monkeypatch):
    set_wallet_env(monkeypatch, make_certs())
    session.delete(session.exec(select(LoyaltyProgram)).one())
    session.commit()
    _, emails = _public_request(client, monkeypatch)
    assert "Apple Wallet" not in emails["eva@example.com"]
    assert session.exec(select(LoyaltyCard)).all() == []


def test_sin_wallet_configurado_el_email_no_trae_pase(client, session, monkeypatch):
    _, emails = _public_request(client, monkeypatch)
    assert "Apple Wallet" not in emails["eva@example.com"]
    assert session.exec(select(LoyaltyCard)).all() == []


def test_aceptar_la_solicitud_envia_el_enlace_en_la_confirmacion(client, session, monkeypatch):
    from src.database.models.booking_request_model import BookingRequest
    from src.routers import booking_request

    set_wallet_env(monkeypatch, make_certs())
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(booking_request, "send_email", lambda to, subject, html, **k: sent.append((to, subject, html)))
    request = BookingRequest(
        business_id=OWNER, client_name="Eva", client_email="eva@example.com", client_phone="600000000",
        service="Corte", requested_date=datetime.utcnow() + timedelta(days=2),
    )
    session.add(request)
    session.commit()
    response = client.post(f"/booking-requests/{request.id}/accept", headers=AUTH)
    assert response.status_code == 200
    assert "wallet_pass_url" not in response.json()
    card = session.exec(select(LoyaltyCard)).one()
    (to, subject, html), = sent
    assert to == "eva@example.com" and subject.startswith("¡Reserva confirmada!")
    assert "Añadir a Apple Wallet" in html and card.auth_token in html


# --- Reenvio del enlace desde el negocio ---------------------------------------

@pytest.fixture
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []

    def fake_send(to, subject, html, **kwargs):
        sent.append((to, subject, html))
        return "email-id"

    monkeypatch.setattr(loyalty_service, "send_email", fake_send)
    return sent


def test_el_barbero_reenvia_el_enlace_al_email_del_cliente(client, session, monkeypatch, outbox):
    set_wallet_env(monkeypatch, make_certs())
    app.dependency_overrides[get_auth_context] = lambda: _auth("employee")
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "<b>Ana</b>")
    response = client.post(f"/loyalty/cards/{card.serial_number}/send-pass", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["sent_to"] == "ana@example.com"
    assert body["pass_url"].endswith(f"/{card.serial_number}.pkpass?token={card.auth_token}")
    (to, subject, html), = outbox
    assert to == "ana@example.com"
    assert subject == "Tu tarjeta de fidelidad — Barbería Sol"
    assert "Añadir a Apple Wallet" in html and body["pass_url"] in html
    assert "<b>Ana</b>" not in html and "&lt;b&gt;Ana&lt;/b&gt;" in html
    assert "0 de 3" in html


def test_reenviar_el_enlace_de_otro_negocio_da_404(client, session, monkeypatch, outbox):
    set_wallet_env(monkeypatch, make_certs())
    other = loyalty_service.get_or_create_card(session, "otro", "ana@example.com", "Ana")
    assert client.post(f"/loyalty/cards/{other.serial_number}/send-pass", headers=AUTH).status_code == 404
    assert outbox == []


def test_reenviar_sin_wallet_configurado_da_503(client, session, outbox):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    assert client.post(f"/loyalty/cards/{card.serial_number}/send-pass", headers=AUTH).status_code == 503
    assert outbox == []


def test_reenviar_si_el_email_falla_da_502(client, session, monkeypatch):
    set_wallet_env(monkeypatch, make_certs())
    monkeypatch.setattr(loyalty_service, "send_email", lambda *a, **k: None)
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    assert client.post(f"/loyalty/cards/{card.serial_number}/send-pass", headers=AUTH).status_code == 502


# --- Activacion: las visitas anteriores no cuentan -------------------------------

def test_activar_fija_enabled_at_una_sola_vez(client, session):
    session.delete(session.exec(select(LoyaltyProgram)).one())
    session.commit()
    client.put("/loyalty/program", json={"enabled": False}, headers=AUTH)
    assert session.exec(select(LoyaltyProgram)).one().enabled_at is None
    client.put("/loyalty/program", json={"enabled": True}, headers=AUTH)
    first = session.exec(select(LoyaltyProgram)).one().enabled_at
    assert first is not None
    client.put("/loyalty/program", json={"enabled": False}, headers=AUTH)
    client.put("/loyalty/program", json={"enabled": True, "stamps_required": 5}, headers=AUTH)
    assert session.exec(select(LoyaltyProgram)).one().enabled_at == first


def test_las_visitas_anteriores_al_dia_de_activacion_no_sellan(client, session):
    program = session.exec(select(LoyaltyProgram)).one()
    program.enabled_at = datetime(2026, 11, 10, 15, 0)  # UTC: 16:00 en Madrid
    session.add(program)
    session.commit()
    before = reservation(session, start=datetime(2026, 11, 9, 18))
    same_day = reservation(session, start=datetime(2026, 11, 10, 9))
    after = reservation(session, start=datetime(2026, 11, 12, 10))
    for r in (before, same_day, after):
        assert complete(client, r.id).status_code == 200
    assert sorted(s.reservation_id for s in stamps(session)) == sorted([same_day.id, after.id])


# --- Escaneo del QR y sello manual (walk-in) ----------------------------------

TODAY = datetime(2026, 11, 20, 12, 30)


@pytest.fixture
def frozen_today(monkeypatch):
    monkeypatch.setattr(loyalty_service, "madrid_now", lambda: TODAY)
    return TODAY


def test_escanear_devuelve_la_tarjeta_del_negocio(client, session, frozen_today):
    complete(client, reservation(session, start=datetime(2026, 11, 1, 10)).id)
    card = session.exec(select(LoyaltyCard)).one()
    app.dependency_overrides[get_auth_context] = lambda: _auth("employee")
    response = client.get(f"/loyalty/scan/{card.scan_token}", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["serial_number"] == card.serial_number
    assert body["customer_name"] == "Ana"
    assert body["stamps"] == 1 and body["stamps_required"] == 3
    assert body["reward"] == "Corte gratis" and body["reward_ready"] is False
    assert [v["visit_date"][:10] for v in body["visits"]] == ["2026-11-01"]
    assert body["stamped_today"] is False and body["stamp_added"] is False


def test_escanear_una_tarjeta_de_otro_negocio_da_404(client, session):
    other = loyalty_service.get_or_create_card(session, "otro", "ana@example.com", "Ana")
    assert client.get(f"/loyalty/scan/{other.scan_token}", headers=AUTH).status_code == 404
    assert client.post(f"/loyalty/scan/{other.scan_token}/stamp", headers=AUTH).status_code == 404
    assert client.get("/loyalty/scan/no-existe", headers=AUTH).status_code == 404
    assert stamps(session) == []


def test_el_token_del_qr_no_es_el_email_ni_el_del_pase(session):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    assert len(card.scan_token) >= 20
    assert "ana" not in card.scan_token.lower()
    assert card.scan_token not in (card.auth_token, card.serial_number)


def test_sello_manual_idempotente_por_dia(client, session, pushes, frozen_today):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    session.add(WalletRegistration(
        device_library_identifier="d", pass_type_identifier="p", serial_number=card.serial_number, push_token="t",
    ))
    session.commit()
    pushes.clear()
    app.dependency_overrides[get_auth_context] = lambda: _auth("employee")

    first = client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH).json()
    assert first["stamp_added"] is True and first["stamped_today"] is True and first["stamps"] == 1
    assert first["visits"][0]["visit_date"].startswith("2026-11-20")
    assert pushes == [["t"]]

    again = client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH).json()
    assert again["stamp_added"] is False and again["stamps"] == 1
    assert pushes == [["t"]]
    (stamp,) = stamps(session)
    assert stamp.reservation_id is None and stamp.manual_day == TODAY.date()
    assert stamp.service == loyalty_service.MANUAL_STAMP_SERVICE


def test_sello_manual_otro_dia_si_suma(client, session, monkeypatch):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    for day in (20, 21):
        monkeypatch.setattr(loyalty_service, "madrid_now", lambda day=day: datetime(2026, 11, day, 11))
        assert client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH).json()["stamp_added"] is True
    assert len(stamps(session)) == 2


def test_sello_manual_no_suma_si_ya_hay_reserva_completada_hoy(client, session, frozen_today):
    complete(client, reservation(session, start=TODAY.replace(hour=10)).id)
    card = session.exec(select(LoyaltyCard)).one()
    body = client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH).json()
    assert body["stamp_added"] is False and body["stamped_today"] is True
    assert len(stamps(session)) == 1


def test_reserva_completada_el_dia_de_un_sello_manual_no_suma_otro(client, session, frozen_today):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH)
    r = reservation(session, start=TODAY.replace(hour=17), service="Barba")
    complete(client, r.id)
    (stamp,) = stamps(session)
    assert stamp.reservation_id == r.id and stamp.service == "Barba"

    # Revertir la reserva no borra la visita que el barbero sello a mano.
    client.post(f"/reservations/{r.id}/revert", headers=AUTH)
    (stamp,) = stamps(session)
    assert stamp.reservation_id is None and stamp.service == loyalty_service.MANUAL_STAMP_SERVICE


def test_sello_manual_con_el_programa_desactivado_da_409(client, session):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    program = session.exec(select(LoyaltyProgram)).one()
    program.enabled = False
    session.add(program)
    session.commit()
    assert client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH).status_code == 409
    assert client.get(f"/loyalty/scan/{card.scan_token}", headers=AUTH).status_code == 200


def test_escanear_y_canjear(client, session, monkeypatch):
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    for day in (1, 2, 3):
        monkeypatch.setattr(loyalty_service, "madrid_now", lambda day=day: datetime(2026, 11, day, 11))
        client.post(f"/loyalty/scan/{card.scan_token}/stamp", headers=AUTH)
    scanned = client.get(f"/loyalty/scan/{card.scan_token}", headers=AUTH).json()
    assert scanned["reward_ready"] is True
    app.dependency_overrides[get_auth_context] = lambda: _auth("employee")
    redeemed = client.post(f"/loyalty/cards/{scanned['serial_number']}/redeem", headers=AUTH).json()
    assert redeemed["stamps"] == 0
