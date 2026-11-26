"""
Tarjeta de fidelidad y su pase de Apple Wallet. Ver docs/adr/0003.

- `/loyalty/...`: el negocio (JWT) configura la regla y consulta o canjea tarjetas.
- `/public/loyalty/...`: la pagina de reservas y la descarga del `.pkpass`.
- `/wallet/apple/v1/...`: el web service de PassKit que llama Wallet
  (`webServiceURL` del pase = `PUBLIC_API_URL` + `/wallet/apple`).
"""

import hmac
import logging
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, col, select

from src.api.auth import AuthContext, require_owner, require_subscription
from src.database.database import SessionDep
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.loyalty_model import (
    LoyaltyCard,
    LoyaltyCardCreate,
    LoyaltyCardPublic,
    LoyaltyPassSent,
    LoyaltyProgram,
    LoyaltyProgramPublic,
    LoyaltyProgramSettings,
    LoyaltyScanResult,
    LoyaltyVisit,
    WalletRegistration,
)
from src.services import apple_wallet, loyalty_logo, loyalty_service
from src.services.customer_service import known_contact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/loyalty", tags=["loyalty"])
public_router = APIRouter(prefix="/public/loyalty", tags=["loyalty"])
wallet_router = APIRouter(prefix=apple_wallet.WEB_SERVICE_PATH + "/v1", tags=["apple-wallet"])

EPOCH = datetime(1970, 1, 1)


def _config_or_503() -> apple_wallet.WalletConfig:
    try:
        return apple_wallet.wallet_config()
    except apple_wallet.WalletNotConfigured as exc:
        logger.warning(str(exc))
        raise HTTPException(
            status_code=503,
            detail="Apple Wallet no está configurado en el servidor (faltan " + ", ".join(exc.missing) + ")",
        )


def _program_public(program: LoyaltyProgram | None) -> LoyaltyProgramPublic:
    settings = LoyaltyProgramSettings.model_validate(program, from_attributes=True) if program else LoyaltyProgramSettings()
    return LoyaltyProgramPublic(
        **settings.model_dump(),
        apple_wallet_available=apple_wallet.wallet_available(),
        logo_url=program.logo_url if program else None,
    )


def _card_public(session: Session, card: LoyaltyCard, program: LoyaltyProgram | None) -> LoyaltyCardPublic:
    required = program.stamps_required if program else LoyaltyProgramSettings().stamps_required
    stamps = loyalty_service.current_stamps(session, card.id)  # type: ignore[arg-type]
    return LoyaltyCardPublic(
        serial_number=card.serial_number,
        customer_name=card.customer_name,
        customer_email=card.customer_email,
        stamps=stamps,
        stamps_required=required,
        reward=program.reward if program else LoyaltyProgramSettings().reward,
        reward_ready=stamps >= required,
        visits=[
            LoyaltyVisit(visit_date=s.visit_date, service=s.service, redeemed=s.redeemed_at is not None)
            for s in loyalty_service.card_stamps(session, card.id)  # type: ignore[arg-type]
        ],
        pass_url=loyalty_service.pass_url(card),
    )


# ---------------------------------------------------------------------------
# Negocio
# ---------------------------------------------------------------------------

@router.get("/program", response_model=LoyaltyProgramPublic)
def get_program(session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Regla de la tarjeta; sin configurar, desactivada con 10 sellos → «Corte gratis»."""
    return _program_public(loyalty_service.get_program(session, auth.business_id))


@router.put("/program", response_model=LoyaltyProgramPublic)
def update_program(body: LoyaltyProgramSettings, session: SessionDep, auth: AuthContext = Depends(require_owner)):
    """Activa la tarjeta y fija la regla y los colores. Los pases ya emitidos se actualizan."""
    program = loyalty_service.get_program(session, auth.business_id) or LoyaltyProgram(business_id=auth.business_id)
    program.sqlmodel_update(body.model_dump())
    program.updated_at = datetime.utcnow()
    if program.enabled and program.enabled_at is None:
        program.enabled_at = program.updated_at  # las visitas de antes no sellan
    session.add(program)
    session.commit()
    session.refresh(program)
    loyalty_service.touch_all_cards(session, auth.business_id)
    return _program_public(program)


def _resolve_email(session: Session, business_id: str, email: str | None, name: str | None) -> str | None:
    if email:
        return email
    if name:
        return known_contact(session, business_id, name)[1]
    return None


@router.get("/cards", response_model=LoyaltyCardPublic)
def get_card(
    session: SessionDep,
    email: str | None = None,
    name: str | None = None,
    auth: AuthContext = Depends(require_subscription),
):
    """Tarjeta del cliente por email o, en su defecto, por nombre (el email de sus reservas)."""
    card = loyalty_service.find_card(session, auth.business_id, _resolve_email(session, auth.business_id, email, name))
    if not card:
        raise HTTPException(status_code=404, detail="Este cliente aún no tiene tarjeta de fidelidad")
    return _card_public(session, card, loyalty_service.get_program(session, auth.business_id))


@router.post("/cards", response_model=LoyaltyCardPublic)
def create_card(body: LoyaltyCardCreate, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Crea (o devuelve) la tarjeta de un cliente, p. ej. para enseñarle el enlace del pase en el local."""
    program = loyalty_service.get_program(session, auth.business_id)
    if not program or not program.enabled:
        raise HTTPException(status_code=409, detail="Activa la tarjeta de fidelidad antes de crear tarjetas")
    email = loyalty_service.normalize_email(body.customer_email)
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Email no válido")
    card = loyalty_service.get_or_create_card(session, auth.business_id, email, body.customer_name)
    return _card_public(session, card, program)


def _own_card(session: Session, business_id: str, serial_number: str) -> LoyaltyCard:
    card = session.exec(select(LoyaltyCard).where(LoyaltyCard.serial_number == serial_number)).first()
    if not card or card.business_id != business_id:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return card


@router.post("/cards/{serial_number}/redeem", response_model=LoyaltyCardPublic)
def redeem_card(serial_number: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Entrega el premio: gasta los sellos más antiguos que pide la regla."""
    card = _own_card(session, auth.business_id, serial_number)
    program = loyalty_service.get_program(session, auth.business_id)
    required = program.stamps_required if program else LoyaltyProgramSettings().stamps_required
    if not loyalty_service.redeem(session, card, required):
        raise HTTPException(status_code=409, detail="Aún no tiene sellos suficientes para el premio")
    return _card_public(session, card, program)


@router.post("/cards/{serial_number}/send-pass", response_model=LoyaltyPassSent)
def send_pass(serial_number: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """
    (Re)envía al email del cliente el enlace «Añadir a Apple Wallet». El enlace solo
    sale por email: así se demuestra que el email es suyo. Devuelve también el
    enlace para enseñarlo en el local (el barbero ya ve la tarjeta).
    """
    card = _own_card(session, auth.business_id, serial_number)
    _config_or_503()
    link = loyalty_service.pass_url(card)
    if not link or not loyalty_service.send_pass_email(session, card, link):
        raise HTTPException(status_code=502, detail="No se pudo enviar el email. Inténtalo de nuevo más tarde")
    return LoyaltyPassSent(sent_to=card.customer_email, pass_url=link)


# ---------------------------------------------------------------------------
# Escaneo del QR del pase en el local
# ---------------------------------------------------------------------------

def _scanned_card(session: Session, business_id: str, token: str) -> LoyaltyCard:
    """404 tanto si el token no existe como si es de otro negocio: no se distingue."""
    card = loyalty_service.find_card_by_scan_token(session, token)
    if not card or card.business_id != business_id:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return card


def _scan_result(session: Session, card: LoyaltyCard, program: LoyaltyProgram | None, added: bool) -> LoyaltyScanResult:
    return LoyaltyScanResult(
        **_card_public(session, card, program).model_dump(),
        stamped_today=loyalty_service.stamped_on(session, card.id, loyalty_service.madrid_now().date()),  # type: ignore[arg-type]
        stamp_added=added,
    )


@router.get("/scan/{token}", response_model=LoyaltyScanResult)
def scan_card(token: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Tarjeta del QR del pase: cliente, sellos, si tiene el premio, últimas visitas y si ya se selló hoy."""
    card = _scanned_card(session, auth.business_id, token)
    return _scan_result(session, card, loyalty_service.get_program(session, auth.business_id), added=False)


@router.post("/scan/{token}/stamp", response_model=LoyaltyScanResult)
def stamp_scanned_card(token: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """
    Sello manual para un walk-in (visita sin reserva). Idempotente por día: si la
    tarjeta ya tiene un sello de hoy (manual o de reserva), no suma y
    `stamp_added` es false.
    """
    card = _scanned_card(session, auth.business_id, token)
    program = loyalty_service.get_program(session, auth.business_id)
    if not program or not program.enabled:
        raise HTTPException(status_code=409, detail="La tarjeta de fidelidad no está activa")
    added = loyalty_service.add_manual_stamp(session, card)
    session.refresh(card)
    return _scan_result(session, card, program, added=added)


# ---------------------------------------------------------------------------
# Logo del pase (opcional; sin el, el de Businext)
# ---------------------------------------------------------------------------

@router.put("/program/logo", response_model=LoyaltyProgramPublic)
def upload_program_logo(
    session: SessionDep,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_owner),
):
    """Sube el logo del negocio para el pase (PNG, JPG o WebP, hasta 2 MB). Se adapta a los tamaños de Apple."""
    raw = file.file.read(loyalty_logo.MAX_UPLOAD_BYTES + 1)
    try:
        images = loyalty_logo.resize_logo(raw)
    except loyalty_logo.InvalidLogo as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        logo_url = loyalty_logo.upload_logo(auth.business_id, images)
    except loyalty_logo.StorageNotConfigured as exc:
        logger.error("Logo del pase: %s", exc)
        raise HTTPException(status_code=503, detail="El almacenamiento de logos no está configurado en el servidor")
    except loyalty_logo.StorageError as exc:
        logger.error("Logo del pase: %s", exc)
        raise HTTPException(status_code=502, detail="No se pudo guardar el logo. Inténtalo de nuevo")

    program = loyalty_service.get_program(session, auth.business_id) or LoyaltyProgram(business_id=auth.business_id)
    previous = program.logo_url
    program.logo_url = logo_url
    program.updated_at = datetime.utcnow()
    session.add(program)
    session.commit()
    session.refresh(program)
    loyalty_logo.delete_logo(previous)
    loyalty_service.touch_all_cards(session, auth.business_id)
    return _program_public(program)


@router.delete("/program/logo", response_model=LoyaltyProgramPublic)
def delete_program_logo(session: SessionDep, auth: AuthContext = Depends(require_owner)):
    """Quita el logo propio: los pases vuelven a llevar el de Businext."""
    program = loyalty_service.get_program(session, auth.business_id)
    if program and program.logo_url:
        previous = program.logo_url
        program.logo_url = None
        program.updated_at = datetime.utcnow()
        session.add(program)
        session.commit()
        session.refresh(program)
        loyalty_logo.delete_logo(previous)
        loyalty_service.touch_all_cards(session, auth.business_id)
    return _program_public(program)


# ---------------------------------------------------------------------------
# Publico
# ---------------------------------------------------------------------------

class PublicLoyaltyInfo(BaseModel):
    enabled: bool
    stamps_required: int
    reward: str
    apple_wallet_available: bool


@public_router.get("/{business_id}", response_model=PublicLoyaltyInfo)
def get_public_program(business_id: str, session: SessionDep):
    """Para la página de reservas: si mostrar la tarjeta y el botón «Añadir a Apple Wallet»."""
    business = session.exec(select(BusinessConfiguration).where(BusinessConfiguration.business_id == business_id)).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    return _program_public(loyalty_service.get_program(session, business_id))


def _pkpass_response(session: Session, card: LoyaltyCard, config: apple_wallet.WalletConfig) -> Response:
    content = loyalty_service.render_pass(session, card, config)
    return Response(
        content=content,
        media_type=apple_wallet.PKPASS_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="fidelidad.pkpass"',
            "Last-Modified": _http_date(card.updated_at),
            "Cache-Control": "no-store",
        },
    )


@public_router.get("/passes/{serial_number}.pkpass")
def download_pass(serial_number: str, token: str, session: SessionDep):
    """Descarga del pase con el enlace que se da al cliente (serial + token del pase)."""
    config = _config_or_503()
    card = session.exec(select(LoyaltyCard).where(LoyaltyCard.serial_number == serial_number)).first()
    if not card or not hmac.compare_digest(card.auth_token, token):
        raise HTTPException(status_code=404, detail="Pase no encontrado")
    return _pkpass_response(session, card, config)


# ---------------------------------------------------------------------------
# Web service de PassKit (lo llama Wallet)
# https://developer.apple.com/documentation/walletpasses/adding-a-web-service-to-update-passes
# ---------------------------------------------------------------------------

def _http_date(moment: datetime) -> str:
    return format_datetime(moment.replace(microsecond=0, tzinfo=timezone.utc), usegmt=True)


def _update_tag(moment: datetime) -> str:
    """Marca opaca para `passesUpdatedSince`: segundos desde 1970 de `updated_at` (UTC, ver `touch`)."""
    return str(int((moment - EPOCH).total_seconds()))


def _authorized_card(
    session: Session, config: apple_wallet.WalletConfig, pass_type_id: str, serial_number: str, authorization: str | None
) -> LoyaltyCard:
    scheme, _, token = (authorization or "").partition(" ")
    card = session.exec(select(LoyaltyCard).where(LoyaltyCard.serial_number == serial_number)).first()
    if (
        pass_type_id != config.pass_type_id
        or scheme != "ApplePass"
        or not card
        or not hmac.compare_digest(card.auth_token, token.strip())
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return card


class RegistrationBody(BaseModel):
    pushToken: str


@wallet_router.post("/devices/{device_id}/registrations/{pass_type_id}/{serial_number}")
def register_device(
    device_id: str,
    pass_type_id: str,
    serial_number: str,
    body: RegistrationBody,
    session: SessionDep,
    authorization: str | None = Header(default=None),
):
    """201 si el dispositivo se registra para este pase; 200 si ya lo estaba (se actualiza el push token)."""
    config = _config_or_503()
    _authorized_card(session, config, pass_type_id, serial_number, authorization)
    registration = session.exec(select(WalletRegistration).where(
        WalletRegistration.device_library_identifier == device_id,
        WalletRegistration.pass_type_identifier == pass_type_id,
        WalletRegistration.serial_number == serial_number,
    )).first()
    status = 200
    if registration:
        registration.push_token = body.pushToken
    else:
        registration = WalletRegistration(
            device_library_identifier=device_id,
            pass_type_identifier=pass_type_id,
            serial_number=serial_number,
            push_token=body.pushToken,
        )
        status = 201
    session.add(registration)
    session.commit()
    return Response(status_code=status)


@wallet_router.get("/devices/{device_id}/registrations/{pass_type_id}")
def updated_serials(device_id: str, pass_type_id: str, session: SessionDep, passesUpdatedSince: str | None = None):
    """Seriales de los pases de este dispositivo cambiados desde la marca; 204 si ninguno."""
    query = (
        select(LoyaltyCard)
        .join(WalletRegistration, col(WalletRegistration.serial_number) == col(LoyaltyCard.serial_number))
        .where(
            WalletRegistration.device_library_identifier == device_id,
            WalletRegistration.pass_type_identifier == pass_type_id,
        )
    )
    cards = session.exec(query).all()
    if passesUpdatedSince and passesUpdatedSince.isdigit():
        since = int(passesUpdatedSince)
        cards = [c for c in cards if int(_update_tag(c.updated_at)) > since]
    if not cards:
        return Response(status_code=204)
    return {
        "serialNumbers": [c.serial_number for c in cards],
        "lastUpdated": _update_tag(max(c.updated_at for c in cards)),
    }


@wallet_router.delete("/devices/{device_id}/registrations/{pass_type_id}/{serial_number}")
def unregister_device(
    device_id: str,
    pass_type_id: str,
    serial_number: str,
    session: SessionDep,
    authorization: str | None = Header(default=None),
):
    config = _config_or_503()
    _authorized_card(session, config, pass_type_id, serial_number, authorization)
    registrations = session.exec(select(WalletRegistration).where(
        WalletRegistration.device_library_identifier == device_id,
        WalletRegistration.pass_type_identifier == pass_type_id,
        WalletRegistration.serial_number == serial_number,
    )).all()
    for registration in registrations:
        session.delete(registration)
    session.commit()
    return Response(status_code=200)


@wallet_router.get("/passes/{pass_type_id}/{serial_number}")
def latest_pass(
    pass_type_id: str,
    serial_number: str,
    session: SessionDep,
    authorization: str | None = Header(default=None),
    if_modified_since: str | None = Header(default=None),
):
    """El `.pkpass` actual; 304 si no ha cambiado desde `If-Modified-Since`."""
    config = _config_or_503()
    card = _authorized_card(session, config, pass_type_id, serial_number, authorization)
    if if_modified_since:
        try:
            since = parsedate_to_datetime(if_modified_since).astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            since = None
        if since and card.updated_at <= since:
            return Response(status_code=304)
    return _pkpass_response(session, card, config)


class LogBody(BaseModel):
    logs: list[str] = []


@wallet_router.post("/log")
async def wallet_log(request: Request):
    """Errores que Wallet reporta del web service (p. ej. un pase mal firmado)."""
    try:
        body = LogBody.model_validate(await request.json())
    except Exception:
        return Response(status_code=200)
    for line in body.logs[:20]:
        logger.warning("Apple Wallet log: %s", line[:500])
    return Response(status_code=200)
