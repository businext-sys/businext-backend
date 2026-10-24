"""
Agente de voz Retell multitenant (businext-sys/businext#117).

Un unico agente plantilla atiende las llamadas de todas las barberias:

- `POST /retell/inbound`: webhook de llamada entrante. Resuelve el numero
  marcado (`to_number`) a su negocio y devuelve sus datos como variables
  dinamicas, o rechaza la llamada si el numero no es de ningun negocio o su
  agente esta desactivado.
- `POST /retell/functions/{name}`: funciones del agente sobre la reserva
  publica (`public_booking.py`). El negocio sale de la variable `business_id`
  que fijo el webhook, nunca de los argumentos del modelo.
- `GET`/`PATCH /voice-agent/config`: configuracion del agente (solo owner).

Todas las rutas de `/retell` exigen la firma de Retell (401 sin ella).
"""

import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from src.api.auth import AuthContext, require_owner
from src.database.database import SessionDep
from src.database.models.booking_request_model import BookingRequestCreate
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.reservation_model import Reservation
from src.database.models.voice_agent_model import (
    VoiceAgentConfig,
    VoiceAgentConfigPublic,
    VoiceAgentConfigUpdate,
    VoicePhoneNumber,
)
from src.services.availability_service import SLOT_DURATION_MINUTES, get_available_slots
from src.services.email_service import email_request_accepted, send_email
from src.services.retell import require_retell_signature

from . import public_booking
from .assistant import BUSINESS_TIMEZONE, require_authorization_header

logger = logging.getLogger(__name__)

DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ANY_EMPLOYEE = "Cualquiera"  # el `employee_name` de los huecos sin empleado

router = APIRouter(prefix="/voice-agent", tags=["voice-agent"])
retell_router = APIRouter(prefix="/retell", tags=["retell"], dependencies=[Depends(require_retell_signature)])


def _config(session, business_id: str) -> VoiceAgentConfig:
    config = session.exec(select(VoiceAgentConfig).where(VoiceAgentConfig.business_id == business_id)).first()
    return config or VoiceAgentConfig(business_id=business_id)


def _business_for_number(session, phone_number: str | None) -> str | None:
    if not phone_number:
        return None
    return session.exec(
        select(VoicePhoneNumber.business_id).where(VoicePhoneNumber.phone_number == phone_number)
    ).first()


# ---------------------------------------------------------------------------
# Configuracion del agente (UI del barbero)
# ---------------------------------------------------------------------------

CONFIG_UNAVAILABLE = HTTPException(status_code=503, detail="El agente de voz no está disponible")


def _public(session, config: VoiceAgentConfig) -> VoiceAgentConfigPublic:
    numbers = session.exec(
        select(VoicePhoneNumber.phone_number)
        .where(VoicePhoneNumber.business_id == config.business_id)
        .order_by(VoicePhoneNumber.phone_number)
    ).all()
    return VoiceAgentConfigPublic(enabled=config.enabled, booking_mode=config.booking_mode, phone_numbers=list(numbers))


@router.get("/config", response_model=VoiceAgentConfigPublic, dependencies=[Depends(require_authorization_header)])
def get_voice_agent_config(session: SessionDep, auth: AuthContext = Depends(require_owner)):
    """Configuracion del agente de voz del negocio y sus numeros (sin fila: desactivado, modo solicitud)."""
    try:
        return _public(session, _config(session, auth.business_id))
    except SQLAlchemyError:
        session.rollback()
        raise CONFIG_UNAVAILABLE


@router.patch("/config", response_model=VoiceAgentConfigPublic, dependencies=[Depends(require_authorization_header)])
def update_voice_agent_config(
    body: VoiceAgentConfigUpdate, session: SessionDep, auth: AuthContext = Depends(require_owner)
):
    """Activa o desactiva el agente y cambia su modo de reserva (`request` o `confirm`)."""
    try:
        config = _config(session, auth.business_id)
        config.sqlmodel_update(body.model_dump(exclude_unset=True, exclude_none=True))
        config.updated_at = datetime.utcnow()
        session.add(config)
        session.commit()
        session.refresh(config)
        return _public(session, config)
    except SQLAlchemyError:
        session.rollback()
        raise CONFIG_UNAVAILABLE


# ---------------------------------------------------------------------------
# Webhook de llamada entrante
# ---------------------------------------------------------------------------

class CallInbound(BaseModel):
    call_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None


class InboundWebhook(BaseModel):
    event: str
    call_inbound: CallInbound | None = None


REJECT = {"call_inbound": {"reject": True}}


def _locations_text(session, business_id: str) -> str:
    locations = session.exec(
        select(Location).where(Location.business_id == business_id, Location.is_active == True)  # noqa: E712
    ).all()
    return "; ".join(
        f"{loc.name} (id {loc.id}): {loc.address}" if loc.address else f"{loc.name} (id {loc.id})"
        for loc in locations
    )


@retell_router.post("/inbound")
def inbound_call(body: InboundWebhook, session: SessionDep):
    """Resuelve el numero marcado a su negocio; rechaza si no hay negocio o el agente esta desactivado."""
    if body.event != "call_inbound" or body.call_inbound is None:
        return {}
    call = body.call_inbound
    business_id = _business_for_number(session, call.to_number)
    config = _config(session, business_id) if business_id else None
    business = session.exec(
        select(BusinessConfiguration).where(BusinessConfiguration.business_id == business_id)
    ).first() if business_id else None
    if not (config and config.enabled and business):
        logger.info("retell_inbound_rejected to=%s call=%s", call.to_number, call.call_id)
        return REJECT

    today = datetime.now(BUSINESS_TIMEZONE).date()
    # Retell solo admite variables dinamicas de tipo string.
    variables = {
        "business_id": business_id,
        "business_name": business.business_name,
        "business_phone": business.business_phone or "",
        "business_email": business.business_email or "",
        "booking_mode": config.booking_mode,
        "locations": _locations_text(session, business_id),
        "today": today.isoformat(),
        "today_text": f"{DAY_NAMES[today.weekday()]} {today:%d/%m/%Y}",
        "caller_number": call.from_number or "",
    }
    response = {"dynamic_variables": variables, "metadata": {"business_id": business_id}}
    agent_id = os.getenv("RETELL_AGENT_ID")
    if agent_id:
        response["override_agent_id"] = agent_id
    logger.info("retell_inbound_accepted business=%s call=%s", business_id, call.call_id)
    return {"call_inbound": response}


# ---------------------------------------------------------------------------
# Funciones del agente
# ---------------------------------------------------------------------------

class RetellCall(BaseModel):
    call_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    retell_llm_dynamic_variables: dict[str, str] = Field(default_factory=dict)


class FunctionCall(BaseModel):
    name: str | None = None
    call: RetellCall
    args: dict = Field(default_factory=dict)


class ServicesArgs(BaseModel):
    location_id: int | None = None


class AvailabilityArgs(BaseModel):
    date: date
    employee_name: str | None = None


class BookArgs(BaseModel):
    client_name: str
    client_email: str
    client_phone: str | None = None
    service: str
    date: date
    time: time
    employee_name: str | None = None
    location_id: int | None = None


class FunctionError(Exception):
    """Error de negocio: vuelve al agente como texto para que lo explique al cliente."""


def _employee(name: str | None) -> str | None:
    name = (name or "").strip()
    return None if not name or name.lower() == ANY_EMPLOYEE.lower() else name


def _business_of_call(session, call: RetellCall) -> tuple[str, VoiceAgentConfig]:
    business_id = call.retell_llm_dynamic_variables.get("business_id")
    if not business_id:
        raise HTTPException(status_code=403, detail="Call without business")
    # Si es una llamada telefonica, el numero marcado tiene que ser de ese negocio.
    if call.to_number and _business_for_number(session, call.to_number) != business_id:
        raise HTTPException(status_code=403, detail="Number does not belong to the business")
    config = _config(session, business_id)
    if not config.enabled:
        raise HTTPException(status_code=403, detail="Voice agent disabled")
    return business_id, config


def _get_business_info(session, business_id: str, config, call: RetellCall, args: dict) -> dict:
    return public_booking.get_locations(business_id, session)


def _get_services(session, business_id: str, config, call: RetellCall, args: dict) -> dict:
    parsed = ServicesArgs(**args)
    data = public_booking.get_services(business_id, session, location_id=parsed.location_id)
    data["services"] = [{k: v for k, v in s.items() if k != "image_url"} for s in data["services"]]
    data["employees"] = [e["name"] for e in data["employees"]]
    return data


def _check_availability(session, business_id: str, config, call: RetellCall, args: dict) -> dict:
    parsed = AvailabilityArgs(**args)
    data = public_booking.get_availability(
        business_id, session, date_param=parsed.date.isoformat(), employee_name=_employee(parsed.employee_name)
    )
    # Agrupado por empleado: mas corto para el modelo que la lista de huecos.
    by_employee: dict[str, list[str]] = {}
    for slot in data["slots"]:
        by_employee.setdefault(slot["employee_name"], []).append(slot["time"])
    return {"date": data["date"], "slots": by_employee}


def _free_slot(session, business_id: str, start: datetime, employee_name: str | None) -> dict:
    if start.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise FunctionError("La fecha solicitada debe ser futura")
    wanted = start.strftime("%H:%M")
    for slot in get_available_slots(session, business_id, start.date(), employee_name):
        if slot["time"] == wanted:
            return slot
    raise FunctionError("Esa hora no está disponible. Consulta la disponibilidad y ofrece otra.")


def _book_appointment(session, business_id: str, config, call: RetellCall, args: dict) -> dict:
    parsed = BookArgs(**args)
    start = datetime.combine(parsed.date, parsed.time.replace(second=0, microsecond=0))
    client_phone = parsed.client_phone or call.from_number or ""
    slot = _free_slot(session, business_id, start, _employee(parsed.employee_name))
    # Si al cliente le da igual, la cita va al empleado del hueco libre: una
    # solicitud sin empleado no ocupa el hueco de nadie en la disponibilidad.
    employee_name = _employee(slot["employee_name"])

    if config.booking_mode != "confirm":
        result = public_booking.create_booking_request(
            business_id,
            BookingRequestCreate(
                client_name=parsed.client_name,
                client_email=parsed.client_email,
                client_phone=client_phone,
                employee_name=employee_name,
                service=parsed.service,
                requested_date=start,
                location_id=parsed.location_id,
            ),
            session,
        )
        return {
            "ok": True,
            "mode": "request",
            "request_id": result["id"],
            "message": "Solicitud enviada. El negocio la confirmará y el cliente recibirá un email.",
        }
    return _confirm_reservation(session, business_id, parsed, start, employee_name)


def _confirm_reservation(session, business_id: str, parsed: BookArgs, start: datetime, in_charge: str | None) -> dict:
    """
    Modo «confirmar directamente»: la misma reserva que crea aceptar una
    solicitud (`booking_request.accept_booking_request`) y el mismo email de
    reserva confirmada al cliente.
    """
    reservation = Reservation(
        business_id=business_id,
        customer_name=parsed.client_name,
        in_charge=in_charge or "Owner",
        reservation_start_date=start,
        reservation_end_date=start + timedelta(minutes=SLOT_DURATION_MINUTES),
        time_per_reservation=SLOT_DURATION_MINUTES,
        status="PENDING",
        service=parsed.service,
    )
    session.add(reservation)
    session.commit()
    session.refresh(reservation)

    biz = public_booking._get_business_or_404(session, business_id)
    location = session.get(Location, parsed.location_id) if parsed.location_id else None
    subject, html = email_request_accepted(
        parsed.client_name, parsed.service, start.strftime("%d/%m/%Y %H:%M"), biz.business_name, in_charge,
        business_phone=biz.business_phone, business_email=biz.business_email,
        location_name=location.name if location else None,
        location_address=location.address if location else None,
    )
    send_email(parsed.client_email, subject, html)
    return {
        "ok": True,
        "mode": "confirm",
        "reservation_id": reservation.id,
        "employee_name": in_charge or ANY_EMPLOYEE,
        "message": "Reserva confirmada. El cliente recibirá un email.",
    }


FUNCTIONS = {
    "get_business_info": _get_business_info,
    "get_services": _get_services,
    "check_availability": _check_availability,
    "book_appointment": _book_appointment,
}


@retell_router.post("/functions/{function_name}")
def run_function(function_name: str, body: FunctionCall, session: SessionDep):
    """
    Ejecuta una funcion del agente para el negocio de la llamada. Los errores
    de negocio responden 200 con `ok: false` y el motivo, para que el agente se
    lo diga al cliente; un fallo de Retell (no 2xx) solo le diria que algo fallo.
    """
    handler = FUNCTIONS.get(function_name)
    if handler is None:
        raise HTTPException(status_code=404, detail="Unknown function")
    business_id, config = _business_of_call(session, body.call)
    try:
        return handler(session, business_id, config, body.call, body.args)
    except ValidationError as error:
        return {"ok": False, "error": f"Argumentos no válidos: {error.errors()[0]['msg']}"}
    except FunctionError as error:
        return {"ok": False, "error": str(error)}
    except HTTPException as error:
        return {"ok": False, "error": error.detail}
