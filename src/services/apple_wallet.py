"""
Pases de Apple Wallet (`storeCard`) para la tarjeta de fidelidad. Ver docs/adr/0003.

- `wallet_config()` lee el certificado del Pass Type ID, su clave, el
  intermedio WWDR y los identificadores de las variables de entorno. Sin
  alguna, devuelve None y las rutas de Wallet responden 503.
- `build_pass_json()` es puro: decide el contenido del pase.
- `package_pass()` empaqueta el `.pkpass` (manifest con SHA-1 y firma PKCS#7
  separada del manifest, con el WWDR incluido).
- `push_updates()` avisa por APNs (HTTP/2, certificado del pase como cliente
  TLS) a los dispositivos registrados; Wallet pide entonces el pase nuevo.
"""

import hashlib
import io
import json
import logging
import os
import ssl
import tempfile
import threading
import zipfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from dotenv import load_dotenv

from src.services.trend_sources import BUSINESS_TIMEZONE

load_dotenv()

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "wallet"
PKPASS_MEDIA_TYPE = "application/vnd.apple.pkpass"
WEB_SERVICE_PATH = "/wallet/apple"
APNS_URL = "https://api.push.apple.com/3/device/{token}"
LAST_VISITS = 5
MAX_LOCATIONS = 10  # limite de Apple

# Colores de Businext (businext/DESIGN.md) si el negocio no elige otros.
DEFAULT_BACKGROUND = "#0F0E0C"
DEFAULT_FOREGROUND = "#F4F1EC"
DEFAULT_LABEL = "#41D9D6"

REQUIRED_ENV = (
    "APPLE_WALLET_PASS_TYPE_ID",
    "APPLE_WALLET_TEAM_ID",
    "APPLE_WALLET_CERT",
    "APPLE_WALLET_KEY",
    "APPLE_WALLET_WWDR",
    "PUBLIC_API_URL",
)


class WalletNotConfigured(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(f"Apple Wallet sin configurar: faltan {', '.join(missing)}")


@dataclass(frozen=True)
class WalletConfig:
    pass_type_id: str
    team_id: str
    cert_pem: bytes
    key_pem: bytes
    key_password: bytes | None
    wwdr_pem: bytes
    public_api_url: str

    @property
    def web_service_url(self) -> str:
        return self.public_api_url + WEB_SERVICE_PATH


def _read_pem(value: str) -> bytes:
    """El valor es el PEM (con saltos reales o `\\n` escapados) o la ruta a un fichero PEM."""
    if "-----BEGIN" in value:
        return value.replace("\\n", "\n").encode()
    return Path(value).expanduser().read_bytes()


def wallet_config() -> WalletConfig:
    """Lanza WalletNotConfigured con los nombres (nunca los valores) de lo que falta."""
    missing = [name for name in REQUIRED_ENV if not os.getenv(name, "").strip()]
    if missing:
        raise WalletNotConfigured(missing)
    try:
        cert, key, wwdr = (_read_pem(os.environ[n].strip()) for n in ("APPLE_WALLET_CERT", "APPLE_WALLET_KEY", "APPLE_WALLET_WWDR"))
    except OSError as exc:
        logger.error("Apple Wallet: no se pudo leer un PEM: %s", exc)
        raise WalletNotConfigured(["APPLE_WALLET_CERT/KEY/WWDR (fichero ilegible)"]) from exc
    password = os.getenv("APPLE_WALLET_KEY_PASSWORD") or None
    return WalletConfig(
        pass_type_id=os.environ["APPLE_WALLET_PASS_TYPE_ID"].strip(),
        team_id=os.environ["APPLE_WALLET_TEAM_ID"].strip(),
        cert_pem=cert,
        key_pem=key,
        key_password=password.encode() if password else None,
        wwdr_pem=wwdr,
        public_api_url=os.environ["PUBLIC_API_URL"].strip().rstrip("/"),
    )


def wallet_available() -> bool:
    try:
        wallet_config()
    except WalletNotConfigured:
        return False
    return True


# ---------------------------------------------------------------------------
# Contenido del pase
# ---------------------------------------------------------------------------

@dataclass
class PassLocation:
    latitude: float
    longitude: float


@dataclass
class PassData:
    """Todo lo que muestra el pase; lo arma loyalty_service desde la base de datos."""

    serial_number: str
    auth_token: str
    business_name: str
    customer_name: str
    stamps: int
    stamps_required: int
    reward: str
    next_appointment: datetime | None  # hora de pared de Madrid, como las reservas
    last_visits: list[tuple[datetime, str]]
    booking_url: str
    directions_url: str | None
    locations: list[PassLocation]
    background_color: str | None = None
    foreground_color: str | None = None
    label_color: str | None = None


def _rgb(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def _w3c(moment: datetime) -> str:
    """Wallet exige fechas ISO 8601 con zona; las reservas son horas de pared de Madrid."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BUSINESS_TIMEZONE)
    return moment.isoformat(timespec="seconds")


def _link(key: str, label: str, url: str) -> dict:
    return {"key": key, "label": label, "value": url, "attributedValue": f'<a href="{url}">{label}</a>'}


def directions_url(address: str | None, maps_link: str | None, location: PassLocation | None) -> str | None:
    if maps_link:
        return maps_link
    if location:
        return f"https://maps.apple.com/?daddr={location.latitude},{location.longitude}"
    if address:
        return f"https://maps.apple.com/?q={quote(address)}"
    return None


def build_pass_json(data: PassData, config: WalletConfig) -> dict:
    ready = data.stamps >= data.stamps_required
    missing = data.stamps_required - data.stamps
    if data.next_appointment:
        next_field = {
            "key": "next",
            "label": "PRÓXIMA CITA",
            "value": _w3c(data.next_appointment),
            "dateStyle": "PKDateStyleMedium",
            "timeStyle": "PKDateStyleShort",
            "changeMessage": "Tu próxima cita: %@",
        }
    else:
        next_field = {"key": "next", "label": "PRÓXIMA CITA", "value": "Sin cita"}

    visits = "\n".join(f"{d.strftime('%d/%m/%Y')} · {service}" for d, service in data.last_visits)
    back_fields = [
        {"key": "visits", "label": "Últimas visitas", "value": visits or "Todavía no hay visitas"},
        _link("book", "Reservar cita", data.booking_url),
    ]
    if data.directions_url:
        back_fields.append(_link("directions", "Cómo llegar", data.directions_url))
    back_fields.append({
        "key": "rules",
        "label": "Cómo funciona",
        "value": f"Cada cita completada en {data.business_name} suma un sello. "
                 f"Con {data.stamps_required} sellos: {data.reward}.",
    })

    pass_json = {
        "formatVersion": 1,
        "passTypeIdentifier": config.pass_type_id,
        "teamIdentifier": config.team_id,
        "serialNumber": data.serial_number,
        "authenticationToken": data.auth_token,
        "webServiceURL": config.web_service_url,
        "organizationName": data.business_name,
        "description": f"Tarjeta de fidelidad de {data.business_name}",
        "logoText": data.business_name,
        "sharingProhibited": True,
        "backgroundColor": _rgb(data.background_color or DEFAULT_BACKGROUND),
        "foregroundColor": _rgb(data.foreground_color or DEFAULT_FOREGROUND),
        "labelColor": _rgb(data.label_color or DEFAULT_LABEL),
        "storeCard": {
            "headerFields": [],
            "primaryFields": [{
                "key": "stamps",
                "label": "SELLOS",
                "value": f"{data.stamps}/{data.stamps_required}",
                "changeMessage": "Ya tienes %@ sellos",
            }],
            "secondaryFields": [
                next_field,
                {"key": "customer", "label": "CLIENTE", "value": data.customer_name},
            ],
            "auxiliaryFields": [{
                "key": "reward",
                "label": "PREMIO",
                "value": f"¡{data.reward} disponible!" if ready
                         else f"{data.reward}: te falta{'n' if missing > 1 else ''} {missing}",
                "changeMessage": "%@",
            }],
            "backFields": back_fields,
        },
    }
    if data.next_appointment:
        pass_json["relevantDate"] = _w3c(data.next_appointment)
    if data.locations:
        pass_json["locations"] = [
            {"latitude": loc.latitude, "longitude": loc.longitude, "relevantText": f"Estás cerca de {data.business_name}"}
            for loc in data.locations[:MAX_LOCATIONS]
        ]
    return pass_json


# ---------------------------------------------------------------------------
# Firma y empaquetado
# ---------------------------------------------------------------------------

def _default_images() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(ASSETS_DIR.glob("*.png"))}


def sign_manifest(manifest: bytes, config: WalletConfig) -> bytes:
    """Firma PKCS#7 separada (DER) del manifest con el certificado del pase y el WWDR incluido."""
    cert = x509.load_pem_x509_certificate(config.cert_pem)
    key = serialization.load_pem_private_key(config.key_pem, password=config.key_password)
    wwdr = x509.load_pem_x509_certificate(config.wwdr_pem)
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(manifest)
        .add_signer(cert, key, hashes.SHA256())  # type: ignore[arg-type]
        .add_certificate(wwdr)
        .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])
    )


def package_pass(pass_json: dict, config: WalletConfig, images: dict[str, bytes] | None = None) -> bytes:
    files = {"pass.json": json.dumps(pass_json, ensure_ascii=False).encode(), **(images or _default_images())}
    manifest = json.dumps({name: hashlib.sha1(content).hexdigest() for name, content in files.items()}).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", manifest)
        archive.writestr("signature", sign_manifest(manifest, config))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Pushes (APNs)
# ---------------------------------------------------------------------------

@contextmanager
def _tls_files(config: WalletConfig):
    """`ssl.load_cert_chain` solo lee ficheros: el PEM va a un temporal que se borra al salir."""
    with tempfile.TemporaryDirectory() as tmp:
        cert_path, key_path = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
        cert_path.write_bytes(config.cert_pem)
        key_path.write_bytes(config.key_pem)
        os.chmod(key_path, 0o600)
        yield str(cert_path), str(key_path)


def send_apns(push_tokens: Iterable[str], config: WalletConfig) -> list[str]:
    """Push vacio a cada token; devuelve los que APNs da por muertos (410) para borrarlos."""
    tokens = sorted(set(push_tokens))
    if not tokens:
        return []
    dead: list[str] = []
    with _tls_files(config) as (cert_path, key_path):
        context = ssl.create_default_context()
        context.load_cert_chain(cert_path, key_path, password=config.key_password)
        with httpx.Client(http2=True, verify=context, timeout=10) as client:
            for token in tokens:
                try:
                    response = client.post(APNS_URL.format(token=token), headers={"apns-topic": config.pass_type_id}, json={})
                except httpx.HTTPError:
                    logger.exception("APNs: fallo de red enviando el aviso de pase")
                    continue
                if response.status_code == 410:
                    dead.append(token)
                elif response.status_code != 200:
                    logger.warning("APNs respondio %s: %s", response.status_code, response.text[:200])
    return dead


def push_updates(push_tokens: list[str], on_dead_tokens=None) -> None:
    """En segundo plano, como los avisos al equipo: la peticion que cambia la tarjeta no espera a APNs."""
    if not push_tokens:
        return
    try:
        config = wallet_config()
    except WalletNotConfigured:
        return

    def run():
        try:
            dead = send_apns(push_tokens, config)
            if dead and on_dead_tokens:
                on_dead_tokens(dead)
        except Exception:
            logger.exception("APNs: no se pudieron enviar los avisos de pase")

    threading.Thread(target=run, daemon=True).start()
