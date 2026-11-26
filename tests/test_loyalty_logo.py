"""
Logo propio de la barberia en el pase de Apple Wallet (docs/adr/0003): tamanos
de Apple, subida a Supabase Storage y respaldo al logo de Businext.
"""
import io
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.loyalty_model import LoyaltyProgram, WalletRegistration
from src.main import app
from src.services import apple_wallet, loyalty_logo, loyalty_service
from tests.wallet_certs import make_certs, set_wallet_env

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
SUPABASE = "https://proj.supabase.co"
CERTS = make_certs()
REAL_CLIENT = httpx.Client


def png(width: int, height: int, color=(200, 30, 30, 255), fmt="PNG", margin: int = 0) -> bytes:
    image = Image.new("RGBA", (width + 2 * margin, height + 2 * margin), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (width, height), color), (margin, margin))
    out = io.BytesIO()
    if fmt == "JPEG":
        image = image.convert("RGB")
    image.save(out, format=fmt)
    return out.getvalue()


def size_of(raw: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == "PNG"
        return image.size


def _auth(role: str) -> AuthContext:
    return AuthContext(
        user_id=f"{role}-user", business_id=OWNER, role=role,
        account_type="owner" if role == "owner" else "member",
        member_status=None if role == "owner" else "active", subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.delenv("LOYALTY_LOGO_BUCKET", raising=False)
    monkeypatch.setattr(apple_wallet, "push_updates", lambda tokens, on_dead_tokens=None: None)
    loyalty_logo._cache.clear()


@pytest.fixture
def storage(monkeypatch):
    """Supabase Storage en memoria: `httpx.Client` y `httpx.request` del modulo pasan por aqui."""
    files: dict[str, bytes] = {}
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if request.method == "POST" and path.startswith("/storage/v1/object/loyalty-logos/"):
            files[path.removeprefix("/storage/v1/object/loyalty-logos/")] = request.content
            return httpx.Response(200, json={"Key": path})
        if request.method == "GET" and path.startswith("/storage/v1/object/public/loyalty-logos/"):
            content = files.get(path.removeprefix("/storage/v1/object/public/loyalty-logos/"))
            return httpx.Response(200, content=content) if content is not None else httpx.Response(404)
        if request.method == "DELETE" and path == "/storage/v1/object/loyalty-logos":
            import json
            for prefix in json.loads(request.content)["prefixes"]:
                files.pop(prefix, None)
            return httpx.Response(200, json=[])
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(loyalty_logo.httpx, "Client", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(
        loyalty_logo.httpx, "request",
        lambda method, url, **kw: REAL_CLIENT(transport=transport).request(method, url, **{k: v for k, v in kw.items() if k != "timeout"}),
    )
    return type("Storage", (), {"files": files, "calls": calls})


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessConfiguration(business_id=OWNER, business_name="Barbería Sol"))
        session.add(LoyaltyProgram(business_id=OWNER, enabled=True, stamps_required=3))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_auth_context] = lambda: _auth("owner")
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def upload(client, raw: bytes, name="logo.png", content_type="image/png"):
    return client.put("/loyalty/program/logo", files={"file": (name, raw, content_type)}, headers=AUTH)


# --- Tamanos de Apple ----------------------------------------------------------

def test_logo_ancho_cabe_en_la_caja_de_apple():
    images = loyalty_logo.resize_logo(png(1000, 200))
    assert set(images) == {"logo.png", "logo@2x.png", "icon.png", "icon@2x.png", "icon@3x.png"}
    assert size_of(images["logo.png"]) == (160, 32)
    assert size_of(images["logo@2x.png"]) == (320, 64)
    assert size_of(images["icon.png"]) == (29, 29)
    assert size_of(images["icon@2x.png"]) == (58, 58)
    assert size_of(images["icon@3x.png"]) == (87, 87)


def test_logo_alto_limita_por_la_altura_y_quita_el_margen_transparente():
    images = loyalty_logo.resize_logo(png(300, 600, margin=200))
    assert size_of(images["logo.png"]) == (25, 50)
    assert size_of(images["logo@2x.png"]) == (50, 100)


def test_logo_jpg_se_convierte_a_png():
    images = loyalty_logo.resize_logo(png(400, 400, fmt="JPEG"))
    assert size_of(images["logo@2x.png"]) == (100, 100)


@pytest.mark.parametrize("raw, message", [
    (b"", "vacío"),
    (b"no es una imagen", "No se pudo leer"),
    (png(10, 10, fmt="GIF"), "PNG, JPG o WebP"),
    (b"\0" * (loyalty_logo.MAX_UPLOAD_BYTES + 1), "2 MB"),
])
def test_logo_invalido(raw, message):
    with pytest.raises(loyalty_logo.InvalidLogo, match=message):
        loyalty_logo.resize_logo(raw)


# --- Subida ------------------------------------------------------------------

def test_el_owner_sube_el_logo_y_se_regeneran_los_pases(client, session, storage, monkeypatch):
    pushed: list[list[str]] = []
    monkeypatch.setattr(apple_wallet, "push_updates", lambda tokens, on_dead_tokens=None: pushed.append(tokens))
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    session.add(WalletRegistration(
        device_library_identifier="d", pass_type_identifier="p", serial_number=card.serial_number, push_token="t",
    ))
    session.commit()

    response = upload(client, png(600, 200))
    assert response.status_code == 200
    logo_url = response.json()["logo_url"]
    assert logo_url.startswith(f"{SUPABASE}/storage/v1/object/public/loyalty-logos/{OWNER}/")
    assert logo_url.endswith("/logo@2x.png")
    folder = logo_url.removeprefix(f"{SUPABASE}/storage/v1/object/public/loyalty-logos/").rsplit("/", 1)[0]
    assert sorted(storage.files) == sorted(f"{folder}/{n}" for n in loyalty_logo.IMAGE_NAMES)
    post = next(c for c in storage.calls if c.method == "POST")
    assert post.headers["authorization"] == "Bearer service-key"
    assert post.headers["content-type"] == "image/png"
    assert session.exec(select(LoyaltyProgram)).one().logo_url == logo_url
    assert pushed == [["t"]]
    assert client.get("/loyalty/program", headers=AUTH).json()["logo_url"] == logo_url


def test_subir_otro_logo_borra_el_anterior(client, session, storage):
    first = upload(client, png(600, 200)).json()["logo_url"]
    second = upload(client, png(300, 300, color=(0, 0, 255, 255))).json()["logo_url"]
    assert first != second
    old_folder = first.removeprefix(f"{SUPABASE}/storage/v1/object/public/loyalty-logos/").rsplit("/", 1)[0]
    assert not any(path.startswith(old_folder) for path in storage.files)
    assert len(storage.files) == len(loyalty_logo.IMAGE_NAMES)


def test_quitar_el_logo(client, session, storage):
    upload(client, png(600, 200))
    response = client.delete("/loyalty/program/logo", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["logo_url"] is None
    assert storage.files == {}


def test_solo_el_owner_sube_el_logo(client, storage):
    app.dependency_overrides[get_auth_context] = lambda: _auth("manager")
    assert upload(client, png(100, 100)).status_code == 403
    assert client.delete("/loyalty/program/logo", headers=AUTH).status_code == 403
    assert storage.files == {}


def test_logo_invalido_da_422(client, storage):
    response = upload(client, b"hola", name="logo.txt", content_type="text/plain")
    assert response.status_code == 422
    assert "PNG, JPG o WebP" in response.json()["detail"]


def test_sin_supabase_configurado_da_503(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    assert upload(client, png(100, 100)).status_code == 503


def test_si_storage_falla_da_502_y_no_cambia_el_logo(client, session, monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "Bucket not found"}))
    monkeypatch.setattr(loyalty_logo.httpx, "Client", lambda **kw: REAL_CLIENT(transport=transport, **kw))
    assert upload(client, png(100, 100)).status_code == 502
    assert session.exec(select(LoyaltyProgram)).one().logo_url is None


def test_bucket_configurable(client, storage, monkeypatch):
    monkeypatch.setenv("LOYALTY_LOGO_BUCKET", "otro-bucket")
    # El almacen falso solo conoce loyalty-logos: la subida a otro bucket falla con 500 → 502.
    assert upload(client, png(100, 100)).status_code == 502
    assert storage.calls[0].url.path.startswith("/storage/v1/object/otro-bucket/")


# --- El pase -----------------------------------------------------------------

def _pass_files(session, card) -> dict[str, bytes]:
    raw = loyalty_service.render_pass(session, card, apple_wallet.wallet_config())
    archive = zipfile.ZipFile(io.BytesIO(raw))
    return {name: archive.read(name) for name in archive.namelist()}


def test_el_pase_lleva_el_logo_del_negocio(client, session, storage, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    upload(client, png(600, 200))
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    files = _pass_files(session, card)
    for name in loyalty_logo.IMAGE_NAMES:
        stored = next(content for path, content in storage.files.items() if path.endswith(f"/{name}"))
        assert files[name] == stored
    assert size_of(files["logo@2x.png"]) == (300, 100)

    # Segunda vez desde la cache: no vuelve a descargar.
    gets = sum(1 for c in storage.calls if c.method == "GET")
    _pass_files(session, card)
    assert sum(1 for c in storage.calls if c.method == "GET") == gets


def test_sin_logo_el_pase_lleva_el_de_businext(session, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    default = (apple_wallet.ASSETS_DIR / "logo@2x.png").read_bytes()
    assert _pass_files(session, card)["logo@2x.png"] == default


def test_si_el_logo_no_se_puede_descargar_el_pase_lleva_el_de_businext(session, storage, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    program = session.exec(select(LoyaltyProgram)).one()
    program.logo_url = f"{SUPABASE}/storage/v1/object/public/loyalty-logos/{OWNER}/borrado/logo@2x.png"
    session.add(program)
    session.commit()
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    default = (apple_wallet.ASSETS_DIR / "logo@2x.png").read_bytes()
    assert _pass_files(session, card)["logo@2x.png"] == default


# --- logoText ----------------------------------------------------------------

def _pass_json(session, card) -> dict:
    import json

    return json.loads(_pass_files(session, card)["pass.json"])


def test_con_logo_propio_el_pase_no_repite_el_nombre(client, session, storage, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    upload(client, png(600, 200))
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    data = _pass_json(session, card)
    assert "logoText" not in data
    assert data["organizationName"] == "Barbería Sol"


def test_sin_logo_propio_el_pase_lleva_el_nombre(session, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    assert _pass_json(session, card)["logoText"] == "Barbería Sol"


def test_si_el_logo_no_se_descarga_vuelve_el_nombre(session, storage, monkeypatch):
    set_wallet_env(monkeypatch, CERTS)
    program = session.exec(select(LoyaltyProgram)).one()
    program.logo_url = f"{SUPABASE}/storage/v1/object/public/loyalty-logos/{OWNER}/borrado/logo@2x.png"
    session.add(program)
    session.commit()
    card = loyalty_service.get_or_create_card(session, OWNER, "ana@example.com", "Ana")
    assert _pass_json(session, card)["logoText"] == "Barbería Sol"
