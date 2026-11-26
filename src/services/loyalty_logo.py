"""
Logo propio de cada barberia en su pase de Apple Wallet (docs/adr/0003).

- `resize_logo()` convierte la imagen subida en los PNG que pide Apple:
  `logo.png` (cabe en 160x50 pt) y `@2x`, e `icon.png` (29x29 pt) con `@2x` y
  `@3x`. Puro, sin red.
- `upload_logo()` los sube a Supabase Storage (API REST con la service role
  key, como el resto de llamadas a Supabase del backend) en una carpeta nueva
  por subida, asi las URLs son inmutables y se pueden cachear.
- `pass_images()` los descarga para empaquetar el pase, con cache en memoria.
  Si falla, el pase lleva el logo de Businext: nunca rompe la descarga.

El bucket (`LOYALTY_LOGO_BUCKET`, por defecto `loyalty-logos`) es publico y hay
que crearlo a mano en Supabase (ver README).
"""

import io
import logging
import os
from collections import OrderedDict
from uuid import uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "loyalty-logos"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SOURCE_PIXELS = 4096 * 4096
ACCEPTED_FORMATS = {"PNG", "JPEG", "WEBP"}

# Tamanos en pixeles (Apple: logo de 160x50 pt como maximo, icono de 29x29 pt).
LOGO_SIZES = {"logo.png": (160, 50), "logo@2x.png": (320, 100)}
ICON_SIZES = {"icon.png": 29, "icon@2x.png": 58, "icon@3x.png": 87}
IMAGE_NAMES = (*LOGO_SIZES, *ICON_SIZES)
PREVIEW_NAME = "logo@2x.png"  # la que se guarda en `LoyaltyProgram.logo_url`

FETCH_TIMEOUT = 5
_CACHE_SIZE = 64
_cache: "OrderedDict[str, dict[str, bytes]]" = OrderedDict()


class InvalidLogo(ValueError):
    """La imagen no es valida; el mensaje va tal cual al usuario."""


class StorageNotConfigured(RuntimeError):
    pass


class StorageError(RuntimeError):
    pass


def bucket() -> str:
    return os.getenv("LOYALTY_LOGO_BUCKET", "").strip() or DEFAULT_BUCKET


# ---------------------------------------------------------------------------
# Imagen
# ---------------------------------------------------------------------------

def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _trim(image: Image.Image) -> Image.Image:
    """Quita el margen transparente para que el logo ocupe todo el hueco del pase."""
    box = image.getchannel("A").getbbox()
    return image.crop(box) if box else image


def resize_logo(raw: bytes) -> dict[str, bytes]:
    """PNG de logo e icono en los tamanos de Apple, con transparencia. Lanza InvalidLogo."""
    if not raw:
        raise InvalidLogo("El archivo está vacío")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise InvalidLogo("El logo no puede pesar más de 2 MB")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format not in ACCEPTED_FORMATS:
                raise InvalidLogo("Sube el logo en PNG, JPG o WebP")
            if probe.width * probe.height > MAX_SOURCE_PIXELS:
                raise InvalidLogo("El logo es demasiado grande (máximo 4096×4096 px)")
            image = ImageOps.exif_transpose(probe).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidLogo("No se pudo leer la imagen. Sube un PNG, JPG o WebP") from exc
    image = _trim(image)

    images: dict[str, bytes] = {}
    for name, box in LOGO_SIZES.items():
        logo = image.copy()
        logo.thumbnail(box, Image.Resampling.LANCZOS)  # cabe en la caja sin deformarse
        images[name] = _png(logo)
    for name, side in ICON_SIZES.items():
        # Cuadrado: el logo centrado, sin deformar, sobre fondo transparente.
        icon = ImageOps.pad(image, (side, side), method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
        images[name] = _png(icon)
    return images


# ---------------------------------------------------------------------------
# Supabase Storage
# ---------------------------------------------------------------------------

def _storage_settings() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise StorageNotConfigured("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY")
    return url, key


def public_url(supabase_url: str, path: str) -> str:
    return f"{supabase_url}/storage/v1/object/public/{bucket()}/{path}"


def folder_url(logo_url: str) -> str:
    return logo_url.rsplit("/", 1)[0]


def upload_logo(business_id: str, images: dict[str, bytes]) -> str:
    """Sube los PNG a `{bucket}/{business_id}/{uuid}/` y devuelve la URL publica de `logo@2x.png`."""
    supabase_url, key = _storage_settings()
    folder = f"{business_id}/{uuid4().hex}"
    headers = {"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "image/png",
               "Cache-Control": "max-age=31536000", "x-upsert": "true"}
    with httpx.Client(timeout=20) as client:
        for name, content in images.items():
            try:
                response = client.post(
                    f"{supabase_url}/storage/v1/object/{bucket()}/{folder}/{name}", content=content, headers=headers,
                )
            except httpx.HTTPError as exc:
                raise StorageError(f"Supabase Storage no responde: {exc}") from exc
            if not response.is_success:
                raise StorageError(f"Supabase Storage respondió {response.status_code}: {response.text[:200]}")
    return public_url(supabase_url, f"{folder}/{PREVIEW_NAME}")


def delete_logo(logo_url: str | None) -> None:
    """Borra los PNG de un logo anterior. Mejor esfuerzo: si falla, solo queda basura en el bucket."""
    if not logo_url:
        return
    try:
        supabase_url, key = _storage_settings()
    except StorageNotConfigured:
        return
    prefix = f"{supabase_url}/storage/v1/object/public/{bucket()}/"
    if not logo_url.startswith(prefix):
        return
    folder = folder_url(logo_url)[len(prefix):]
    try:
        response = httpx.request(
            "DELETE",
            f"{supabase_url}/storage/v1/object/{bucket()}",
            json={"prefixes": [f"{folder}/{name}" for name in IMAGE_NAMES]},
            headers={"Authorization": f"Bearer {key}", "apikey": key},
            timeout=10,
        )
        if not response.is_success:
            logger.warning("No se pudo borrar el logo anterior (%s): %s", response.status_code, response.text[:200])
    except httpx.HTTPError:
        logger.warning("No se pudo borrar el logo anterior", exc_info=True)


def pass_images(logo_url: str | None) -> dict[str, bytes] | None:
    """Logo e icono del negocio para el pase, o None para usar los de Businext. Nunca falla."""
    if not logo_url:
        return None
    if logo_url in _cache:
        _cache.move_to_end(logo_url)
        return _cache[logo_url]
    folder = folder_url(logo_url)
    images: dict[str, bytes] = {}
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT) as client:
            for name in IMAGE_NAMES:
                response = client.get(f"{folder}/{name}")
                response.raise_for_status()
                images[name] = response.content
    except httpx.HTTPError:
        logger.warning("No se pudo descargar el logo %s; el pase lleva el de Businext", logo_url, exc_info=True)
        return None
    _cache[logo_url] = images
    if len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return images
