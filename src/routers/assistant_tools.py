"""
Herramientas de lectura del asistente (businext-backend#21).

Viven junto a los routers porque llaman a las mismas funciones que los
endpoints: asi el asistente devuelve exactamente lo que ven la web y la app,
con las mismas reglas de acceso, sin duplicar queries.
"""

import json
from datetime import date, timedelta

from fastapi import HTTPException

from src.services.assistant.tools import Tool, ToolContext, ToolResult, register

from . import booking_request, finances, google_reviews, intelligence, product, reservation, working_hours

CURRENCY = "EUR"
MAX_BOOKING_REQUEST_BLOCKS = 5
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _or_empty(call, empty):
    try:
        return call()
    except HTTPException as error:
        if error.status_code == 404:
            return empty
        raise


def _parse_date(value: str | None, default: date) -> date:
    return date.fromisoformat(value) if value else default


def _day_label(day: date, today: date) -> str:
    if day == today:
        return "Reservas de hoy"
    if day == today + timedelta(days=1):
        return "Reservas de mañana"
    return f"Reservas del {DAY_NAMES[day.weekday()]} {day.day:02d}/{day.month:02d}"


def _reservation_item(r) -> dict:
    return {
        "id": r.id,
        "client_name": r.customer_name,
        "service": r.service,
        "start_time": r.reservation_start_date.isoformat(),
        "end_time": r.reservation_end_date.isoformat() if r.reservation_end_date else None,
        "employee_name": r.in_charge,
        "status": r.status,
    }


def _get_reservations(ctx: ToolContext, args: dict) -> ToolResult:
    start = _parse_date(args.get("date"), ctx.today)
    end = _parse_date(args.get("end_date"), start)
    employee = (args.get("employee") or "").strip().lower()

    all_reservations = _or_empty(lambda: reservation.get_reservations(ctx.session, ctx.auth), [])
    selected = sorted(
        (
            r
            for r in all_reservations
            if start <= r.reservation_start_date.date() <= end
            and (not employee or employee in (r.in_charge or "").lower())
        ),
        key=lambda r: r.reservation_start_date,
    )
    items = [_reservation_item(r) for r in selected]
    title = _day_label(start, ctx.today) if start == end else f"Reservas del {start:%d/%m} al {end:%d/%m}"
    return ToolResult(
        data={"from": start.isoformat(), "to": end.isoformat(), "count": len(items), "reservations": items},
        blocks=[{"type": "reservation_list", "title": title, "reservations": items}],
    )


def _get_reservation(ctx: ToolContext, args: dict) -> ToolResult:
    found = _or_empty(
        lambda: reservation.get_reservation_by_id(int(args["id"]), ctx.session, ctx.auth), None
    )
    if found is None:
        return ToolResult(data={"error": "No existe esa reserva."})
    item = _reservation_item(found)
    return ToolResult(
        data=item,
        blocks=[{"type": "reservation_list", "title": None, "reservations": [item]}],
    )


def _period_range(period: str, today: date) -> tuple[date, date, date, date, str]:
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end, start - timedelta(days=7), start - timedelta(days=1), "Esta semana"
    if period == "month":
        start = today.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        prev_end = start - timedelta(days=1)
        return start, next_month - timedelta(days=1), prev_end.replace(day=1), prev_end, "Este mes"
    yesterday = today - timedelta(days=1)
    return today, today, yesterday, yesterday, "Hoy"


def _totals(records, start: date, end: date, is_owner: bool) -> tuple[float, float]:
    in_period = [f for f in records if f.created_at and start <= f.created_at.date() <= end]
    income = [f for f in in_period if f.type == "INCOME"]
    gross = sum(f.amount for f in income)
    commission = sum(f.commission_amount or 0 for f in income)
    total_income = gross if is_owner else max(0, gross - commission)
    total_expense = sum(f.amount for f in in_period if f.type == "EXPENSE")
    return round(total_income, 2), round(total_expense, 2)


def _get_cash_summary(ctx: ToolContext, args: dict) -> ToolResult:
    period = args.get("period") or "today"
    start, end, prev_start, prev_end, label = _period_range(period, ctx.today)
    records = _or_empty(lambda: finances.get_finances(ctx.session, ctx.auth), [])
    is_owner = ctx.auth.role == "owner"

    income, expense = _totals(records, start, end, is_owner)
    prev_income, prev_expense = _totals(records, prev_start, prev_end, is_owner)
    block = {
        "type": "cash_summary",
        "period_label": label,
        "total_income": income,
        "total_expense": expense,
        "balance": round(income - expense, 2),
        "previous_balance": round(prev_income - prev_expense, 2),
        "currency": CURRENCY,
    }
    return ToolResult(
        data={**block, "from": start.isoformat(), "to": end.isoformat()},
        blocks=[block],
    )


def _get_booking_requests(ctx: ToolContext, args: dict) -> ToolResult:
    status = args.get("status") or "REQUESTED"
    requests = booking_request.list_booking_requests(ctx.session, ctx.auth, status)
    items = [
        {
            "id": r.id,
            "client_name": r.client_name,
            "service": r.service,
            "requested_date": r.requested_date.isoformat(),
            "employee_name": r.employee_name,
            "status": r.status,
        }
        for r in requests
    ]
    return ToolResult(
        data={"count": len(items), "requests": items},
        blocks=[{"type": "booking_request", "request": item} for item in items[:MAX_BOOKING_REQUEST_BLOCKS]],
    )


def _get_products(ctx: ToolContext, args: dict) -> ToolResult:
    products = _or_empty(lambda: product.get_products(ctx.session, ctx.auth, offset=0, limit=100), [])
    return ToolResult(data=[{"name": p.name, "price": p.price, "type": p.type} for p in products])


def _get_reviews(ctx: ToolContext, args: dict) -> ToolResult:
    page = google_reviews.get_reviews(
        ctx.session,
        ctx.auth,
        page=1,
        page_size=min(int(args.get("limit") or 5), 20),
        rating=args.get("rating"),
        sort="newest",
        search=None,
    )
    return ToolResult(
        data={
            "total": page.total,
            "reviews": [
                {
                    "author": r.author_title,
                    "rating": r.review_rating,
                    "text": r.review_text,
                    "date": r.review_datetime_utc,
                    "answered": bool(r.owner_answer),
                }
                for r in page.items
            ],
        }
    )


def _get_weekly_summary(ctx: ToolContext, args: dict) -> ToolResult:
    summary = _or_empty(lambda: intelligence.get_summary(ctx.session, ctx.auth), None)
    if summary is None:
        return ToolResult(data={"error": "Todavía no hay ningún resumen semanal."})
    return ToolResult(
        data={
            "week_start": summary.week_start.isoformat(),
            "week_end": summary.week_end.isoformat(),
            "narrative": summary.narrative,
            "client_narrative": summary.client_narrative,
            "kpis": json.loads(summary.kpis),
        }
    )


def _get_working_hours(ctx: ToolContext, args: dict) -> ToolResult:
    hours = working_hours.get_working_hours(ctx.session, ctx.auth, member_user_id=None)
    return ToolResult(
        data=[
            {
                "day": DAY_NAMES[h.day_of_week],
                "open": h.enabled,
                "start": h.start_time,
                "end": h.end_time,
            }
            for h in sorted(hours, key=lambda h: h.day_of_week)
        ]
    )


DATE_PARAM = {"type": "string", "description": "Fecha en formato YYYY-MM-DD"}

register(Tool(
    name="get_reservations",
    description=(
        "Reservas de un día o de un rango de días (por defecto, hoy). "
        "Úsala para «¿qué tengo hoy/mañana?» o las reservas de un profesional."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": DATE_PARAM,
            "end_date": {**DATE_PARAM, "description": "Último día del rango (YYYY-MM-DD), opcional"},
            "employee": {"type": "string", "description": "Nombre del profesional, opcional"},
        },
    },
    status_label="Consultando reservas…",
    handler=_get_reservations,
))

register(Tool(
    name="get_reservation",
    description="Detalle de una reserva por su id.",
    parameters={"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    status_label="Consultando la reserva…",
    handler=_get_reservation,
))

register(Tool(
    name="get_cash_summary",
    description="Caja del negocio: ingresos, gastos y balance de hoy, esta semana o este mes, comparado con el periodo anterior.",
    parameters={
        "type": "object",
        "properties": {"period": {"type": "string", "enum": ["today", "week", "month"]}},
    },
    status_label="Consultando la caja…",
    handler=_get_cash_summary,
))

register(Tool(
    name="get_booking_requests",
    description="Solicitudes de reserva de clientes. Por defecto, las pendientes de responder.",
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["REQUESTED", "ACCEPTED", "REJECTED", "EXPIRED"]}
        },
    },
    status_label="Consultando solicitudes…",
    handler=_get_booking_requests,
))

register(Tool(
    name="get_products",
    description="Servicios y productos del negocio con sus precios.",
    parameters={"type": "object", "properties": {}},
    status_label="Consultando servicios y precios…",
    handler=_get_products,
))

register(Tool(
    name="get_reviews",
    description="Reseñas de Google más recientes, opcionalmente filtradas por estrellas.",
    parameters={
        "type": "object",
        "properties": {
            "rating": {"type": "integer", "minimum": 1, "maximum": 5},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    },
    status_label="Consultando reseñas…",
    handler=_get_reviews,
))

register(Tool(
    name="get_weekly_summary",
    description="El último resumen semanal del negocio (ingresos, reservas, equipo y oportunidades).",
    parameters={"type": "object", "properties": {}},
    status_label="Consultando el resumen semanal…",
    handler=_get_weekly_summary,
))

register(Tool(
    name="get_working_hours",
    description="Horario general de apertura del negocio por día de la semana.",
    parameters={"type": "object", "properties": {}},
    status_label="Consultando el horario…",
    handler=_get_working_hours,
))
