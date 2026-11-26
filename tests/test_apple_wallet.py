"""
Pase de Apple Wallet: contenido, firma del .pkpass y web service de PassKit (docs/adr/0003).
"""
import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from cryptography.hazmat.primitives.serialization import pkcs7
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.loyalty_model import LoyaltyCard, LoyaltyProgram, LoyaltyStamp, WalletRegistration
from src.database.models.reservation_model import Reservation
from src.main import app
from src.services import apple_wallet, loyalty_service
from tests.wallet_certs import PASS_TYPE_ID, TEAM_ID, make_certs, set_wallet_env

OWNER = "owner-1"
DEVICE = "device-abc"
CERTS = make_certs()


@pytest.fixture(autouse=True)
def wallet_env(monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    monkeypatch.setattr(apple_wallet, "push_updates", lambda tokens, on_dead_tokens=None: None)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessConfiguration(business_id=OWNER, business_name="Barbería Sol"))
        session.add(LoyaltyProgram(
            business_id=OWNER, enabled=True, stamps_required=10, reward="Corte gratis", background_color="#102030",
        ))
        session.add(Location(
            business_id=OWNER, name="Centro", address="Calle Mayor 1, Madrid", latitude=40.4168, longitude=-3.7038,
        ))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


@pytest.fixture
def card(session) -> LoyaltyCard:
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    for day in (1, 2, 3):
        start = datetime(2026, 11, day, 10)
        r = Reservation(
            business_id=OWNER, customer_name="Ana", in_charge=None, reservation_start_date=start,
            reservation_end_date=start + timedelta(minutes=30), time_per_reservation=30,
            status="COMPLETED", service="Corte", customer_email="ana@example.com",
        )
        session.add(r)
        session.commit()
        session.add(LoyaltyStamp(card_id=card.id, reservation_id=r.id, visit_date=start, service="Corte"))
    upcoming = datetime(2099, 1, 15, 17, 30)
    session.add(Reservation(
        business_id=OWNER, customer_name="Ana", in_charge=None, reservation_start_date=upcoming,
        reservation_end_date=upcoming + timedelta(minutes=30), time_per_reservation=30,
        status="PENDING", service="Barba", customer_email="ANA@example.com",
    ))
    session.commit()
    return card


def apple_auth(card: LoyaltyCard) -> dict:
    return {"Authorization": f"ApplePass {card.auth_token}"}


def pass_json(session, card) -> dict:
    config = apple_wallet.wallet_config()
    return apple_wallet.build_pass_json(loyalty_service.pass_data(session, card, loyalty_service.get_program(session, OWNER)), config)


def fields(section: list[dict]) -> dict:
    return {f["key"]: f for f in section}


# --- Contenido ---------------------------------------------------------------

def test_pass_json_identidad_y_web_service(session, card):
    data = pass_json(session, card)
    assert data["formatVersion"] == 1
    assert data["passTypeIdentifier"] == PASS_TYPE_ID
    assert data["teamIdentifier"] == TEAM_ID
    assert data["serialNumber"] == card.serial_number
    assert data["authenticationToken"] == card.auth_token
    assert data["webServiceURL"] == "https://api.test.example/wallet/apple"
    assert data["organizationName"] == "Barbería Sol"
    assert data["backgroundColor"] == "rgb(16, 32, 48)"
    assert data["foregroundColor"] == "rgb(244, 241, 236)"


def test_pass_json_qr_con_token_opaco(session, card):
    data = pass_json(session, card)
    assert data["barcodes"] == [{
        "format": "PKBarcodeFormatQR", "message": card.scan_token, "messageEncoding": "iso-8859-1",
    }]
    assert "ana@example.com" not in json.dumps(data["barcodes"])
    assert card.auth_token not in json.dumps(data["barcodes"])


def test_pass_json_frontal_sellos_y_proxima_cita(session, card):
    store = pass_json(session, card)["storeCard"]
    assert fields(store["primaryFields"])["stamps"]["value"] == "3/10"
    next_field = fields(store["secondaryFields"])["next"]
    assert next_field["value"] == "2099-01-15T17:30:00+01:00"
    assert next_field["dateStyle"] == "PKDateStyleMedium"
    assert fields(store["secondaryFields"])["customer"]["value"] == "Ana"
    assert fields(store["auxiliaryFields"])["reward"]["value"] == "Corte gratis: te faltan 7"


def test_pass_json_premio_disponible(session, card):
    program = loyalty_service.get_program(session, OWNER)
    program.stamps_required = 3
    session.add(program)
    session.commit()
    store = pass_json(session, card)["storeCard"]
    assert fields(store["auxiliaryFields"])["reward"]["value"] == "¡Corte gratis disponible!"


def test_pass_json_dorso_y_ubicacion(session, card):
    data = pass_json(session, card)
    back = fields(data["storeCard"]["backFields"])
    assert back["visits"]["value"].splitlines() == ["03/11/2026 · Corte", "02/11/2026 · Corte", "01/11/2026 · Corte"]
    assert back["book"]["value"].endswith(f"/book/{OWNER}")
    assert back["directions"]["value"] == "https://maps.apple.com/?daddr=40.4168,-3.7038"
    assert '<a href="' in back["book"]["attributedValue"]
    assert data["locations"] == [{"latitude": 40.4168, "longitude": -3.7038, "relevantText": "Estás cerca de Barbería Sol"}]
    assert data["relevantDate"] == "2099-01-15T17:30:00+01:00"


def test_pass_json_sin_cita_ni_coordenadas(session):
    card = loyalty_service.get_or_create_card(session, OWNER, "luis@example.com", "Luis")
    location = session.exec(select(Location)).one()
    location.latitude = location.longitude = None
    location.maps_link = "https://maps.google.com/?q=sol"
    session.add(location)
    session.commit()
    data = pass_json(session, card)
    assert fields(data["storeCard"]["secondaryFields"])["next"]["value"] == "Sin cita"
    assert "locations" not in data and "relevantDate" not in data
    assert fields(data["storeCard"]["backFields"])["directions"]["value"] == "https://maps.google.com/?q=sol"


# --- Firma -------------------------------------------------------------------

def test_pkpass_manifest_y_firma(session, card, tmp_path):
    raw = loyalty_service.render_pass(session, card, apple_wallet.wallet_config())
    archive = zipfile.ZipFile(io.BytesIO(raw))
    names = set(archive.namelist())
    assert {"pass.json", "manifest.json", "signature", "icon.png", "icon@2x.png", "logo.png"} <= names

    manifest_bytes = archive.read("manifest.json")
    manifest = json.loads(manifest_bytes)
    assert set(manifest) == names - {"manifest.json", "signature"}
    for name, digest in manifest.items():
        assert hashlib.sha1(archive.read(name)).hexdigest() == digest

    signature = archive.read("signature")
    certs = pkcs7.load_der_pkcs7_certificates(signature)
    assert len(certs) == 2  # el del pase y el WWDR

    if not shutil.which("openssl"):
        pytest.skip("sin openssl para verificar la firma")
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "signature").write_bytes(signature)
    (tmp_path / "wwdr.pem").write_text(CERTS.wwdr_pem)
    result = subprocess.run(
        ["openssl", "smime", "-verify", "-binary", "-inform", "DER", "-in", str(tmp_path / "signature"),
         "-content", str(tmp_path / "manifest.json"), "-CAfile", str(tmp_path / "wwdr.pem"),
         "-purpose", "any", "-out", "/dev/null"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_pem_desde_fichero(monkeypatch, tmp_path):
    path = tmp_path / "key.pem"
    path.write_text(CERTS.key_pem)
    monkeypatch.setenv("APPLE_WALLET_KEY", str(path))
    assert apple_wallet.wallet_config().key_pem == CERTS.key_pem.encode()


# --- Descarga publica --------------------------------------------------------

def test_descarga_publica_con_token(client, card):
    response = client.get(f"/public/loyalty/passes/{card.serial_number}.pkpass", params={"token": card.auth_token})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apple.pkpass"
    assert zipfile.ZipFile(io.BytesIO(response.content)).read("pass.json")


def test_descarga_con_token_incorrecto_da_404(client, card):
    response = client.get(f"/public/loyalty/passes/{card.serial_number}.pkpass", params={"token": "x" * 32})
    assert response.status_code == 404


@pytest.mark.parametrize("missing", ["APPLE_WALLET_CERT", "PUBLIC_API_URL"])
def test_sin_configurar_da_503_claro(client, card, monkeypatch, missing):
    monkeypatch.delenv(missing)
    response = client.get(f"/public/loyalty/passes/{card.serial_number}.pkpass", params={"token": card.auth_token})
    assert response.status_code == 503
    assert missing in response.json()["detail"]
    assert "BEGIN" not in response.text
    url = f"/wallet/apple/v1/passes/{PASS_TYPE_ID}/{card.serial_number}"
    assert client.get(url, headers=apple_auth(card)).status_code == 503


# --- Web service de PassKit --------------------------------------------------

def register(client, card, headers=None, pass_type=PASS_TYPE_ID, token="apns-token-1"):
    return client.post(
        f"/wallet/apple/v1/devices/{DEVICE}/registrations/{pass_type}/{card.serial_number}",
        json={"pushToken": token}, headers=headers if headers is not None else apple_auth(card),
    )


def test_registro_201_y_luego_200(client, session, card):
    assert register(client, card).status_code == 201
    assert register(client, card, token="apns-token-2").status_code == 200
    registration = session.exec(select(WalletRegistration)).one()
    assert registration.push_token == "apns-token-2"


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "ApplePass otro-token-cualquiera"},
    {"Authorization": "Bearer token"},
])
def test_registro_sin_autorizacion_da_401(client, session, card, headers):
    assert register(client, card, headers=headers).status_code == 401
    assert session.exec(select(WalletRegistration)).all() == []


def test_registro_con_otro_pass_type_da_401(client, card):
    assert register(client, card, pass_type="pass.otro").status_code == 401


def test_token_de_otra_tarjeta_no_vale(client, session, card):
    other = loyalty_service.get_or_create_card(session, OWNER, "luis@example.com", "Luis")
    assert register(client, card, headers=apple_auth(other)).status_code == 401


def test_pases_actualizados_desde(client, session, card):
    url = f"/wallet/apple/v1/devices/{DEVICE}/registrations/{PASS_TYPE_ID}"
    assert client.get(url).status_code == 204  # sin registros
    register(client, card)

    body = client.get(url).json()
    assert body["serialNumbers"] == [card.serial_number]
    tag = body["lastUpdated"]
    assert client.get(url, params={"passesUpdatedSince": tag}).status_code == 204

    loyalty_service.touch(card)
    session.add(card)
    session.commit()
    body = client.get(url, params={"passesUpdatedSince": tag}).json()
    assert body["serialNumbers"] == [card.serial_number]
    assert int(body["lastUpdated"]) > int(tag)


def test_ultimo_pase_y_304(client, session, card):
    url = f"/wallet/apple/v1/passes/{PASS_TYPE_ID}/{card.serial_number}"
    assert client.get(url).status_code == 401
    loyalty_service.touch(card)
    session.add(card)
    session.commit()

    response = client.get(url, headers=apple_auth(card))
    assert response.status_code == 200
    last_modified = response.headers["last-modified"]
    assert client.get(url, headers={**apple_auth(card), "If-Modified-Since": last_modified}).status_code == 304

    loyalty_service.touch(card)
    session.add(card)
    session.commit()
    assert client.get(url, headers={**apple_auth(card), "If-Modified-Since": last_modified}).status_code == 200


def test_if_modified_since_antiguo_devuelve_el_pase(client, card):
    url = f"/wallet/apple/v1/passes/{PASS_TYPE_ID}/{card.serial_number}"
    old = format_datetime(datetime(2000, 1, 1, tzinfo=timezone.utc), usegmt=True)
    assert client.get(url, headers={**apple_auth(card), "If-Modified-Since": old}).status_code == 200


def test_baja_del_dispositivo(client, session, card):
    register(client, card)
    url = f"/wallet/apple/v1/devices/{DEVICE}/registrations/{PASS_TYPE_ID}/{card.serial_number}"
    assert client.delete(url).status_code == 401
    assert client.delete(url, headers=apple_auth(card)).status_code == 200
    assert session.exec(select(WalletRegistration)).all() == []


def test_log(client):
    assert client.post("/wallet/apple/v1/log", json={"logs": ["Signature invalid"]}).status_code == 200
    assert client.post("/wallet/apple/v1/log", content=b"no es json").status_code == 200


# --- APNs --------------------------------------------------------------------

def test_apns_push_vacio_con_topic_y_tokens_muertos(monkeypatch):
    import httpx

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(410 if request.url.path.endswith("/dead") else 200)

    real_client = httpx.Client
    monkeypatch.setattr(apple_wallet.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    dead = apple_wallet.send_apns(["alive", "dead", "alive"], apple_wallet.wallet_config())
    assert dead == ["dead"]
    assert [r.url.path for r in requests] == ["/3/device/alive", "/3/device/dead"]
    assert all(r.headers["apns-topic"] == PASS_TYPE_ID and r.content == b"{}" for r in requests)
