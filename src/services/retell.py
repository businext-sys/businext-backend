"""
Integracion con Retell (agente de voz, businext-sys/businext#117).

Retell firma cada peticion (webhook de llamada entrante y funciones del
agente) con la cabecera `X-Retell-Signature: v=<timestamp ms>,d=<hex>`, donde
`d = HMAC-SHA256(cuerpo_crudo + timestamp, RETELL_API_KEY)`. Solo sirve la API
key marcada como «webhook» en el dashboard de Retell.
"""

import hashlib
import hmac
import os
import re
import time

from fastapi import HTTPException, Request

SIGNATURE_PATTERN = re.compile(r"v=(\d+),d=([0-9a-fA-F]+)")
MAX_SIGNATURE_AGE_MS = 5 * 60 * 1000


def verify_signature(body: bytes, signature: str | None, api_key: str | None, now_ms: int | None = None) -> bool:
    if not api_key or not signature:
        return False
    match = SIGNATURE_PATTERN.fullmatch(signature.strip())
    if not match:
        return False
    timestamp, digest = match.groups()
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if abs(now_ms - int(timestamp)) > MAX_SIGNATURE_AGE_MS:
        return False
    expected = hmac.new(api_key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest.lower())


def sign(body: bytes, api_key: str, now_ms: int | None = None) -> str:
    """Firma como Retell; la usan los tests y las pruebas manuales."""
    timestamp = str(int(time.time() * 1000) if now_ms is None else now_ms)
    digest = hmac.new(api_key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return f"v={timestamp},d={digest}"


async def require_retell_signature(request: Request) -> None:
    """Dependencia: 401 si la peticion no viene firmada por Retell (o falta RETELL_API_KEY)."""
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-retell-signature"), os.getenv("RETELL_API_KEY")):
        raise HTTPException(status_code=401, detail="Invalid Retell signature")
