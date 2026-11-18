"""
Herramientas de escritura del asistente (businext-backend#22).

Casi ninguna escribe al llamarse: validan, guardan una accion pendiente y
devuelven un bloque `confirmation`. Solo se ejecutan desde
POST /assistant/actions/{action_id}/confirm, llamando a las mismas funciones
que los endpoints (mismas validaciones y efectos, p. ej. el email al cliente
al aceptar una solicitud), o desde confirm_pending_action cuando el usuario
confirma por escrito. Las excepciones son cambiar la hora de una reserva (se
deshace moviendola de vuelta a su hora anterior) y guardar el contacto del
cliente: se aplican al momento.
"""

import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import select

from src.database.models.booking_request_model import BookingRequest
from src.database.models.member_model import BusinessMember
from src.database.models.finances_model import FinancesBase
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import ReservationBase, ReservationUpdate, clean_phone
from src.database.models.working_hours_model import WorkingHours
from src.services.availability_service import _get_hours_for_employee, service_duration
from src.services.customer_service import known_contact
from src.services.assistant import actions
from src.services.assistant.tools import (
    MANAGEMENT_ROLES,
    OWNER_ONLY,
    Tool,
    ToolContext,
    ToolResult,
    own_name,
    register,
)

from . import booking_request, finances, reservation

DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ACTIVE_STATUSES = ("PENDING", "COMPLETED")
WALK_IN_WINDOW = timedelta(minutes=15)
EMAIL = TypeAdapter(EmailStr)


def _when(moment: datetime) -> str:
    return f"el {DAY_NAMES[moment.weekday()]} {moment:%d/%m} a las {moment:%H:%M}"


def _propose(ctx: ToolContext, title: str, description: str, execute, extra: dict | None = None) -> ToolResult:
    action = actions.add(actions.PendingAction(
        business_id=ctx.auth.business_id,
        user_id=ctx.auth.user_id,
        conversation_id=ctx.conversation_id,
        title=title,
        description=description,
        execute=execute,
    ))
    return ToolResult(
        data={
            "pending_confirmation": True,
            "action_id": action.id,
            "summary": description,
            "note": "No se ha hecho nada todavía: el usuario debe confirmar en la tarjeta o decir que sí.",
            **(extra or {}),
        },
        blocks=[action.block()],
    )


def _error(message: str) -> ToolResult:
    return ToolResult(data={"error": message})


def _find_reservation(ctx: ToolContext, reservation_id) -> object | None:
    try:
        found = reservation.get_reservation_by_id(int(reservation_id), ctx.session, ctx.auth)
    except HTTPException:
        return None
    if ctx.auth.role == "employee" and (found.in_charge or "").lower() != (own_name(ctx) or "").lower():
        return None
    return found


def _conflict(
    ctx: ToolContext, start: datetime, in_charge: str, duration: int, exclude_id: int | None = None
) -> str | None:
    """Misma regla que `detectReservationConflict` de shared-core: mismo encargado, horarios solapados."""
    end = start + timedelta(minutes=duration)
    try:
        existing = reservation.get_reservations(ctx.session, ctx.auth)
    except HTTPException:
        return None
    for r in existing:
        if r.id == exclude_id or r.in_charge != in_charge or r.status not in ACTIVE_STATUSES:
            continue
        if start < r.reservation_end_date and end > r.reservation_start_date:
            return (
                f"{in_charge} ya tiene una reserva de {r.customer_name} ({r.service}) "
                f"de {r.reservation_start_date:%H:%M} a {r.reservation_end_date:%H:%M}."
            )
    return None


def _member_user_id(ctx: ToolContext, name: str) -> str | None:
    return ctx.session.exec(
        select(BusinessMember.member_user_id)
        .join(Profile, Profile.id == BusinessMember.member_user_id)
        .where(BusinessMember.business_id == ctx.auth.business_id, Profile.display_name == name)
    ).first()


def _closed(ctx: ToolContext, start: datetime, in_charge: str, duration: int, walk_in: bool = False) -> str | None:
    has_hours = ctx.session.exec(
        select(WorkingHours.id).where(WorkingHours.business_id == ctx.auth.business_id)
    ).first()
    if has_hours is None:
        return None
    end = start + timedelta(minutes=duration)
    blocks = _get_hours_for_employee(
        ctx.session, ctx.auth.business_id, start.weekday(), _member_user_id(ctx, in_charge)
    )
    begins, ends = f"{start:%H:%M}", f"{end:%H:%M}"
    if any(b.start_time <= begins and (ends <= b.end_time or walk_in and begins < b.end_time) for b in blocks):
        return None
    if not blocks:
        return f"{in_charge} no trabaja el {DAY_NAMES[start.weekday()]}."
    hours = ", ".join(f"{b.start_time}-{b.end_time}" for b in blocks)
    return f"A esa hora no se trabaja: el horario de {in_charge} ese día es {hours}."


def _creator_name(ctx: ToolContext) -> str:
    return own_name(ctx) or "Owner"


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------

def _valid_email(value: str | None) -> str | None:
    try:
        return str(EMAIL.validate_python(value)) if value else None
    except ValidationError:
        return None


def _customer_contact(ctx: ToolContext, customer: str, args: dict) -> tuple[str | None, str | None]:
    phone = clean_phone(args.get("customer_phone"))
    email = (args.get("customer_email") or "").strip() or None
    if not (phone and email):
        known_phone, known_email = known_contact(ctx.session, ctx.auth.business_id, customer)
        phone = phone or known_phone
        email = email or _valid_email(known_email)
    return phone, email


def _mark_completed(exec_ctx: ToolContext, snapshot) -> None:
    reservation.update_reservation(
        snapshot.id,
        ReservationUpdate(status="COMPLETED", time_per_reservation=snapshot.time_per_reservation),
        exec_ctx.session,
        exec_ctx.auth,
    )
    product = exec_ctx.session.exec(
        select(Product).where(
            Product.business_id == exec_ctx.auth.business_id,
            Product.name == snapshot.service,
        )
    ).first()
    finances.create_finances(
        FinancesBase(
            concept=snapshot.service,
            amount=product.price if product else 0,
            type="INCOME",
            creator=snapshot.in_charge or "",
            reservation_id=snapshot.id,
            product_id=product.id if product else None,
            customer_name=snapshot.customer_name,
        ),
        exec_ctx.session,
        exec_ctx.auth,
    )


def _create_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    start = datetime.fromisoformat(args["start"])
    in_charge = args.get("in_charge") or _creator_name(ctx)
    if ctx.auth.role == "employee":
        in_charge = _creator_name(ctx)
    customer, service = args["customer_name"].strip(), args["service"]
    completed = bool(args.get("completed"))
    walk_in = ctx.now is not None and abs(start - ctx.now) <= WALK_IN_WINDOW
    duration = service_duration(ctx.session, ctx.auth.business_id, service)
    closed = _closed(ctx, start, in_charge, duration, walk_in=walk_in)
    if closed:
        return _error(closed)
    conflict = _conflict(ctx, start, in_charge, duration)
    if conflict:
        return _error(f"Hay un conflicto de horario: {conflict}")
    phone, email = _customer_contact(ctx, customer, args)
    try:
        new_reservation = ReservationBase(
            customer_name=customer,
            in_charge=in_charge,
            reservation_start_date=start,
            time_per_reservation=duration,
            status="PENDING",
            service=service,
            customer_phone=phone,
            customer_email=email,
        )
    except ValidationError:
        return _error("El email del cliente no es válido.")

    def execute(exec_ctx: ToolContext) -> str:
        if _conflict(exec_ctx, start, in_charge, duration):
            raise HTTPException(status_code=409, detail="Ese hueco ya se ha ocupado.")
        created = reservation.create_reservation(new_reservation, exec_ctx.session, exec_ctx.auth)
        if completed:
            _mark_completed(exec_ctx, created)
            return f"Hecho: reserva de {customer} creada {_when(start)}, completada y con el cobro registrado."
        return f"Hecho: reserva de {customer} creada {_when(start)}."

    contact = " · ".join(c for c in (phone, email) if c)
    description = f"{customer} · {service} con {in_charge}, {_when(start)}" + (f" · {contact}" if contact else "")
    extra = None
    if not contact and not completed:
        extra = {"missing_contact": "El cliente no tiene email ni teléfono: pide en una frase su email para enviarle recordatorios."}
    if completed:
        return _propose(ctx, "Crear reserva completada", f"{description}. Se registra el cobro del servicio.", execute, extra)
    return _propose(ctx, "Crear reserva", description, execute, extra)


def _reschedule_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    start = datetime.fromisoformat(args["new_start"])
    previous = found.reservation_start_date
    closed = _closed(ctx, start, found.in_charge, found.time_per_reservation)
    if closed:
        return _error(closed)
    conflict = _conflict(ctx, start, found.in_charge, found.time_per_reservation, exclude_id=found.id)
    if conflict:
        return _error(f"Hay un conflicto de horario: {conflict}")
    reservation.update_reservation(
        found.id,
        ReservationUpdate(reservation_start_date=start),
        ctx.session,
        ctx.auth,
    )
    return ToolResult(
        data={
            "done": True,
            "summary": f"La reserva de {found.customer_name} pasa a {_when(start)}.",
            "previous_start": f"{previous:%Y-%m-%dT%H:%M}",
            "note": "Ya está hecho. Si el usuario quiere deshacerlo, vuelve a moverla a previous_start.",
        },
        blocks=[{
            "type": "confirmation",
            "action_id": str(uuid.uuid4()),
            "title": "Reserva movida",
            "description": f"{found.customer_name} · {found.service}: de {_when(previous)} a {_when(start)}",
            "status": "confirmed",
        }],
    )


def _cancel_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    reservation_id, customer = found.id, found.customer_name

    def execute(exec_ctx: ToolContext) -> str:
        reservation.delete_reservation(reservation_id, exec_ctx.session, exec_ctx.auth)
        return f"Hecho: la reserva de {customer} está cancelada."

    return _propose(
        ctx,
        "Cancelar reserva",
        f"{customer} · {found.service}, {_when(found.reservation_start_date)}",
        execute,
    )


def _complete_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    snapshot = found.model_copy()

    def execute(exec_ctx: ToolContext) -> str:
        _mark_completed(exec_ctx, snapshot)
        return f"Hecho: reserva de {snapshot.customer_name} completada y cobro registrado."

    return _propose(
        ctx,
        "Completar reserva",
        f"{found.customer_name} · {found.service}, {_when(found.reservation_start_date)}. Se registra el cobro del servicio.",
        execute,
    )


def _revert_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    reservation_id, customer = found.id, found.customer_name

    def execute(exec_ctx: ToolContext) -> str:
        reservation.revert_reservation(reservation_id, exec_ctx.session, exec_ctx.auth)
        return f"Hecho: la reserva de {customer} vuelve a estar pendiente y se ha quitado su cobro."

    return _propose(
        ctx,
        "Revertir reserva completada",
        f"{customer} · {found.service}, {_when(found.reservation_start_date)}. Se borra el cobro asociado.",
        execute,
    )


def _update_reservation_contact(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    changes = {
        key: args[key] for key in ("customer_phone", "customer_email") if (args.get(key) or "").strip()
    }
    if not changes:
        return _error("Falta el teléfono o el email.")
    try:
        update = ReservationUpdate(time_per_reservation=found.time_per_reservation, **changes)
    except ValidationError:
        return _error("El email no es válido.")
    try:
        reservation.update_reservation(found.id, update, ctx.session, ctx.auth)
    except HTTPException as error:
        ctx.session.rollback()
        return _error(str(error.detail))
    return ToolResult(data={"done": True, "summary": f"Contacto de {found.customer_name} guardado."})


def _mark_no_show(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["reservation_id"])
    if found is None:
        return _error("No existe esa reserva.")
    undo = bool(args.get("undo"))
    try:
        reservation.update_reservation(
            found.id,
            ReservationUpdate(status="PENDING" if undo else "NO_SHOW", time_per_reservation=found.time_per_reservation),
            ctx.session,
            ctx.auth,
        )
    except HTTPException as error:
        ctx.session.rollback()
        return _error(str(error.detail))
    if undo:
        return ToolResult(data={"done": True, "summary": f"La reserva de {found.customer_name} vuelve a estar pendiente."})
    return ToolResult(
        data={
            "done": True,
            "summary": f"{found.customer_name} marcado como «no vino».",
            "note": "Ya está hecho. Si el usuario quiere deshacerlo, vuelve a llamar con undo=true.",
        },
        blocks=[{
            "type": "confirmation",
            "action_id": str(uuid.uuid4()),
            "title": "No vino",
            "description": f"{found.customer_name} · {found.service}, {_when(found.reservation_start_date)}",
            "status": "confirmed",
        }],
    )


# ---------------------------------------------------------------------------
# Confirmar por texto
# ---------------------------------------------------------------------------

def _confirm_pending_action(ctx: ToolContext, args: dict) -> ToolResult:
    owner = (ctx.auth.business_id, ctx.auth.user_id, ctx.conversation_id)
    action_id = args.get("action_id")
    if not action_id:
        pending = actions.latest(*owner)
        action_id = pending.id if pending else None
    action = actions.take(action_id, *owner) if action_id else None
    if action is None:
        return _error("No hay ninguna acción pendiente de confirmar: ha caducado o ya se respondió. Propónla de nuevo.")
    done, text = actions.run(action, ctx)
    if not done:
        return _error(text)
    return ToolResult(data={"done": True, "summary": text}, blocks=[action.block("confirmed")])


# ---------------------------------------------------------------------------
# Solicitudes de reserva
# ---------------------------------------------------------------------------

def _find_request(ctx: ToolContext, request_id) -> BookingRequest | None:
    return ctx.session.exec(
        select(BookingRequest).where(
            BookingRequest.id == int(request_id),
            BookingRequest.business_id == ctx.auth.business_id,
        )
    ).first()


def _answer_booking_request(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_request(ctx, args["id"])
    if found is None:
        return _error("No existe esa solicitud.")
    accept = args["decision"] == "accept"
    request_id, client = found.id, found.client_name
    reason = args.get("reason")

    def execute(exec_ctx: ToolContext) -> str:
        if accept:
            booking_request.accept_booking_request(request_id, exec_ctx.session, exec_ctx.auth)
            return f"Hecho: solicitud de {client} aceptada; ya está en la agenda y se le ha avisado."
        booking_request.reject_booking_request(request_id, exec_ctx.session, exec_ctx.auth, reason)
        return f"Hecho: solicitud de {client} rechazada; se le ha avisado."

    return _propose(
        ctx,
        "Aceptar solicitud" if accept else "Rechazar solicitud",
        f"{client} · {found.service}, {_when(found.requested_date)}",
        execute,
    )


# ---------------------------------------------------------------------------
# Caja
# ---------------------------------------------------------------------------

def _register_cash_movement(ctx: ToolContext, args: dict) -> ToolResult:
    movement_type = "EXPENSE" if args["type"] == "expense" else "INCOME"
    amount = round(float(args["amount"]), 2)
    if amount <= 0:
        return _error("El importe tiene que ser mayor que cero.")
    concept = args["concept"]
    creator = _creator_name(ctx)
    label = "Gasto" if movement_type == "EXPENSE" else "Ingreso"

    def execute(exec_ctx: ToolContext) -> str:
        finances.create_finances(
            FinancesBase(concept=concept, amount=amount, type=movement_type, creator=creator),
            exec_ctx.session,
            exec_ctx.auth,
        )
        return f"Hecho: {label.lower()} de {amount:.2f} € registrado ({concept})."

    return _propose(ctx, f"Registrar {label.lower()}", f"{label} de {amount:.2f} € · {concept}", execute)


ID_PARAM = {"type": "integer", "description": "Id de la reserva (de get_reservations)"}
DATETIME_PARAM = {"type": "string", "description": "Fecha y hora local, formato YYYY-MM-DDTHH:MM"}

register(Tool(
    name="create_reservation",
    description=(
        "Propone crear una reserva. No la crea hasta que el usuario confirme. "
        "Si no dicen hora concreta, usa la primera de get_free_slots; si es para ahora, la hora actual. "
        "Sin teléfono ni email, usa los de la última reserva del mismo cliente."
    ),
    parameters={
        "type": "object",
        "properties": {
            "customer_name": {"type": "string"},
            "service": {"type": "string", "description": "Nombre del servicio (de get_products)"},
            "start": DATETIME_PARAM,
            "in_charge": {"type": "string", "description": "Profesional; por defecto, el usuario"},
            "customer_phone": {"type": "string", "description": "Teléfono del cliente, opcional"},
            "customer_email": {"type": "string", "description": "Email del cliente, opcional (para los recordatorios)"},
            "completed": {
                "type": "boolean",
                "description": "true para crearla ya completada y registrar su cobro (p. ej. un cliente atendido ahora)",
            },
        },
        "required": ["customer_name", "service", "start"],
    },
    status_label="Preparando la reserva…",
    handler=_create_reservation,
))

register(Tool(
    name="reschedule_reservation",
    description="Cambia al momento la fecha u hora de una reserva pendiente, sin pedir confirmación.",
    parameters={
        "type": "object",
        "properties": {"id": ID_PARAM, "new_start": DATETIME_PARAM},
        "required": ["id", "new_start"],
    },
    status_label="Moviendo la reserva…",
    handler=_reschedule_reservation,
))

register(Tool(
    name="cancel_reservation",
    description="Propone cancelar (eliminar) una reserva pendiente.",
    parameters={"type": "object", "properties": {"id": ID_PARAM}, "required": ["id"]},
    status_label="Preparando la cancelación…",
    handler=_cancel_reservation,
))

register(Tool(
    name="complete_reservation",
    description="Propone marcar una reserva como completada y registrar su cobro.",
    parameters={"type": "object", "properties": {"id": ID_PARAM}, "required": ["id"]},
    status_label="Preparando la reserva…",
    handler=_complete_reservation,
))

register(Tool(
    name="revert_reservation",
    description="Propone devolver a pendiente una reserva completada (borra su cobro). Solo el dueño.",
    parameters={"type": "object", "properties": {"id": ID_PARAM}, "required": ["id"]},
    status_label="Preparando la reserva…",
    handler=_revert_reservation,
    roles=OWNER_ONLY,
))

register(Tool(
    name="update_reservation_contact",
    description="Guarda al momento el teléfono o el email del cliente de una reserva pendiente.",
    parameters={
        "type": "object",
        "properties": {
            "id": ID_PARAM,
            "customer_phone": {"type": "string"},
            "customer_email": {"type": "string"},
        },
        "required": ["id"],
    },
    status_label="Guardando el contacto…",
    handler=_update_reservation_contact,
))

register(Tool(
    name="mark_no_show",
    description="Marca al momento que el cliente de una reserva pendiente no vino, sin pedir confirmación. Con undo=true la devuelve a pendiente.",
    parameters={
        "type": "object",
        "properties": {
            "reservation_id": ID_PARAM,
            "undo": {"type": "boolean", "description": "true para deshacer un «no vino»"},
        },
        "required": ["reservation_id"],
    },
    status_label="Marcando la reserva…",
    handler=_mark_no_show,
))

register(Tool(
    name="confirm_pending_action",
    description=(
        "Ejecuta una acción pendiente de esta conversación cuando el usuario la confirma por escrito "
        "(«sí», «confirmo», «vale»). Sin action_id, la última pendiente."
    ),
    parameters={
        "type": "object",
        "properties": {"action_id": {"type": "string", "description": "Id de la tarjeta pendiente"}},
    },
    status_label="Haciéndolo…",
    handler=_confirm_pending_action,
))

register(Tool(
    name="answer_booking_request",
    description="Propone aceptar o rechazar una solicitud de reserva de un cliente.",
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Id de la solicitud (de get_booking_requests)"},
            "decision": {"type": "string", "enum": ["accept", "reject"]},
            "reason": {"type": "string", "description": "Motivo del rechazo, opcional"},
        },
        "required": ["id", "decision"],
    },
    status_label="Preparando la respuesta…",
    handler=_answer_booking_request,
))

register(Tool(
    name="register_cash_movement",
    description="Propone registrar un ingreso o un gasto en la caja.",
    parameters={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["income", "expense"]},
            "amount": {"type": "number", "description": "Importe en euros"},
            "concept": {"type": "string"},
        },
        "required": ["type", "amount", "concept"],
    },
    status_label="Preparando el movimiento…",
    handler=_register_cash_movement,
    roles=MANAGEMENT_ROLES,
))
