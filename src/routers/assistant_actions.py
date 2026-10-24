"""
Herramientas de escritura del asistente (businext-backend#22).

Ninguna escribe al llamarse: validan, guardan una accion pendiente y
devuelven un bloque `confirmation`. Solo se ejecutan desde
POST /assistant/actions/{action_id}/confirm, llamando a las mismas funciones
que los endpoints (mismas validaciones y efectos, p. ej. el email al cliente
al aceptar una solicitud).
"""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import select

from src.database.models.booking_request_model import BookingRequest
from src.database.models.finances_model import FinancesBase
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.reservation_model import ReservationBase, ReservationUpdate
from src.services.assistant import actions
from src.services.assistant.tools import Tool, ToolContext, ToolResult, register

from . import booking_request, finances, reservation

DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ACTIVE_STATUSES = ("PENDING", "COMPLETED")


def _when(moment: datetime) -> str:
    return f"el {DAY_NAMES[moment.weekday()]} {moment:%d/%m} a las {moment:%H:%M}"


def _propose(ctx: ToolContext, title: str, description: str, execute) -> ToolResult:
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
            "summary": description,
            "note": "No se ha hecho nada todavía: el usuario debe confirmar en la tarjeta.",
        },
        blocks=[action.block()],
    )


def _error(message: str) -> ToolResult:
    return ToolResult(data={"error": message})


def _find_reservation(ctx: ToolContext, reservation_id) -> object | None:
    try:
        return reservation.get_reservation_by_id(int(reservation_id), ctx.session, ctx.auth)
    except HTTPException:
        return None


def _conflict(ctx: ToolContext, start: datetime, in_charge: str, exclude_id: int | None = None) -> str | None:
    """Misma regla que `detectReservationConflict` de shared-core: mismo encargado, horarios solapados."""
    end = start + timedelta(minutes=reservation.FIXED_DURATION_MINUTES)
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


def _creator_name(ctx: ToolContext) -> str:
    profile = ctx.session.get(Profile, ctx.auth.user_id)
    return profile.display_name if profile and profile.display_name else "Owner"


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------

def _create_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    start = datetime.fromisoformat(args["start"])
    in_charge = args.get("in_charge") or _creator_name(ctx)
    customer, service = args["customer_name"], args["service"]
    conflict = _conflict(ctx, start, in_charge)
    if conflict:
        return _error(f"Hay un conflicto de horario: {conflict}")

    def execute(exec_ctx: ToolContext) -> str:
        if _conflict(exec_ctx, start, in_charge):
            raise HTTPException(status_code=409, detail="Ese hueco ya se ha ocupado.")
        reservation.create_reservation(
            ReservationBase(
                customer_name=customer,
                in_charge=in_charge,
                reservation_start_date=start,
                reservation_end_date=start,
                time_per_reservation=reservation.FIXED_DURATION_MINUTES,
                status="PENDING",
                service=service,
            ),
            exec_ctx.session,
            exec_ctx.auth,
        )
        return f"Hecho: reserva de {customer} creada {_when(start)}."

    return _propose(
        ctx,
        "Crear reserva",
        f"{customer} · {service} con {in_charge}, {_when(start)}",
        execute,
    )


def _reschedule_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _find_reservation(ctx, args["id"])
    if found is None:
        return _error("No existe esa reserva.")
    start = datetime.fromisoformat(args["new_start"])
    conflict = _conflict(ctx, start, found.in_charge, exclude_id=found.id)
    if conflict:
        return _error(f"Hay un conflicto de horario: {conflict}")
    reservation_id, customer = found.id, found.customer_name

    def execute(exec_ctx: ToolContext) -> str:
        reservation.update_reservation(
            reservation_id,
            ReservationUpdate(
                reservation_start_date=start,
                reservation_end_date=start + timedelta(minutes=reservation.FIXED_DURATION_MINUTES),
                time_per_reservation=reservation.FIXED_DURATION_MINUTES,
            ),
            exec_ctx.session,
            exec_ctx.auth,
        )
        return f"Hecho: la reserva de {customer} pasa a {_when(start)}."

    return _propose(
        ctx,
        "Cambiar la hora de una reserva",
        f"{customer} · {found.service}: de {_when(found.reservation_start_date)} a {_when(start)}",
        execute,
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
        return f"Hecho: reserva de {snapshot.customer_name} completada y cobro registrado."

    return _propose(
        ctx,
        "Completar reserva",
        f"{found.customer_name} · {found.service}, {_when(found.reservation_start_date)}. Se registra el cobro del servicio.",
        execute,
    )


def _revert_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    if ctx.auth.role != "owner":
        return _error("Solo el dueño del negocio puede revertir una reserva completada.")
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
    description="Propone crear una reserva. No la crea hasta que el usuario confirme en la tarjeta.",
    parameters={
        "type": "object",
        "properties": {
            "customer_name": {"type": "string"},
            "service": {"type": "string", "description": "Nombre del servicio (de get_products)"},
            "start": DATETIME_PARAM,
            "in_charge": {"type": "string", "description": "Profesional; por defecto, el usuario"},
        },
        "required": ["customer_name", "service", "start"],
    },
    status_label="Preparando la reserva…",
    handler=_create_reservation,
))

register(Tool(
    name="reschedule_reservation",
    description="Propone cambiar la fecha u hora de una reserva pendiente.",
    parameters={
        "type": "object",
        "properties": {"id": ID_PARAM, "new_start": DATETIME_PARAM},
        "required": ["id", "new_start"],
    },
    status_label="Preparando el cambio…",
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
))
