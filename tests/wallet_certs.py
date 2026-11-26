"""Certificados de prueba para firmar pases: una CA que hace de WWDR y un certificado de pase firmado por ella."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PASS_TYPE_ID = "pass.test.businext.loyalty"
TEAM_ID = "TEAM123456"


@dataclass
class FakeCerts:
    cert_pem: str
    key_pem: str
    wwdr_pem: str


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def make_certs() -> FakeCerts:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Test WWDR"))
        .issuer_name(_name("Test WWDR"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.USER_ID, PASS_TYPE_ID),
            x509.NameAttribute(NameOID.COMMON_NAME, f"Pass Type ID: {PASS_TYPE_ID}"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(ca_key, hashes.SHA256())
    )
    return FakeCerts(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        wwdr_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


def set_wallet_env(monkeypatch, certs: FakeCerts) -> None:
    monkeypatch.setenv("APPLE_WALLET_PASS_TYPE_ID", PASS_TYPE_ID)
    monkeypatch.setenv("APPLE_WALLET_TEAM_ID", TEAM_ID)
    # Como en un panel de variables de entorno: el PEM en una linea con `\n` escapados.
    monkeypatch.setenv("APPLE_WALLET_CERT", certs.cert_pem.replace("\n", "\\n"))
    monkeypatch.setenv("APPLE_WALLET_KEY", certs.key_pem)
    monkeypatch.setenv("APPLE_WALLET_WWDR", certs.wwdr_pem)
    monkeypatch.delenv("APPLE_WALLET_KEY_PASSWORD", raising=False)
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.test.example/")
