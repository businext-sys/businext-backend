"""
Evals del asistente (businext-backend#26).

    python -m evals.run [--cases evals/cases.json] [--report evals/report.json]

Ejecuta cada caso contra el LLM real (OPENAI_API_KEY) con las herramientas
simuladas: mismos nombres, descripciones y roles que las reales, pero con
datos fijos y sin base de datos. Mide la herramienta elegida, sus
parametros, el respeto de permisos y que el asistente nunca de por hecha una
escritura (solo se ejecutan al confirmar en la tarjeta).
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from src.api.auth import AccessCapabilities, AuthContext
from src.services.assistant.llm import get_assistant_llm
from src.services.assistant.orchestrator import TurnStats, run_turn
from src.services.assistant.tools import TOOLS, Tool, ToolContext, ToolResult

TODAY = date(2026, 10, 22)  # miercoles
MIN_PRECISION = 0.8
WRITE_TOOLS = {
    "create_reservation",
    "reschedule_reservation",
    "cancel_reservation",
    "complete_reservation",
    "revert_reservation",
    "answer_booking_request",
    "register_cash_movement",
}
DONE_CLAIM = re.compile(
    r"\b(ya (est[aá]|lo he)|he (cancelado|creado|registrado|movido|cambiado|completado|aceptado|rechazado|anotado|apuntado)|"
    r"(reserva|cita|solicitud|gasto|ingreso) (cancelada|creada|registrad[ao]|movida|completada|aceptada|rechazada))\b",
    re.IGNORECASE,
)

PLACEHOLDERS = {
    "today": TODAY.isoformat(),
    "tomorrow": (TODAY + timedelta(days=1)).isoformat(),
    "day_after_tomorrow": (TODAY + timedelta(days=2)).isoformat(),
    "friday": (TODAY + timedelta(days=(4 - TODAY.weekday()) % 7)).isoformat(),
}


def _fill(value):
    if isinstance(value, str):
        return value.format(**PLACEHOLDERS)
    return value


CANNED = {
    "get_reservations": {
        "count": 2,
        "reservations": [
            {"id": 12, "client_name": "Juan", "service": "Corte", "start_time": f"{TODAY}T17:00:00",
             "employee_name": "Marta", "status": "PENDING"},
            {"id": 13, "client_name": "Lucía", "service": "Tinte", "start_time": f"{TODAY}T10:00:00",
             "employee_name": "Pedro", "status": "COMPLETED"},
        ],
    },
    "get_reservation": {"id": 12, "client_name": "Juan", "service": "Corte", "start_time": f"{TODAY}T17:00:00",
                        "employee_name": "Marta", "status": "PENDING"},
    "get_cash_summary": {"period_label": "Hoy", "total_income": 180, "total_expense": 20, "balance": 160,
                         "previous_balance": 140, "currency": "EUR"},
    "get_booking_requests": {"count": 1, "requests": [
        {"id": 7, "client_name": "Sara", "service": "Tinte", "requested_date": f"{TODAY + timedelta(days=1)}T11:00:00",
         "employee_name": None, "status": "REQUESTED"},
    ]},
    "get_products": [{"name": "Corte", "price": 15, "type": "service"}, {"name": "Tinte", "price": 40, "type": "service"},
                     {"name": "Barba", "price": 10, "type": "service"}],
    "get_reviews": {"total": 2, "reviews": [
        {"author": "Ana", "rating": 5, "text": "Genial", "answered": False},
        {"author": "Luis", "rating": 2, "text": "Esperé mucho", "answered": False},
    ]},
    "get_weekly_summary": {"narrative": "Buena semana, más clientes que la anterior.", "kpis": {"total_income": 900}},
    "get_working_hours": [{"day": d, "open": d != "domingo", "start": "09:00", "end": "20:00"}
                          for d in ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]],
}


def _stub_tools(calls: list[tuple[str, dict]]) -> dict[str, Tool]:
    def handler_for(name: str):
        def handler(ctx: ToolContext, args: dict) -> ToolResult:
            calls.append((name, args))
            if name in WRITE_TOOLS:
                return ToolResult(data={
                    "pending_confirmation": True,
                    "note": "No se ha hecho nada todavía: el usuario debe confirmar en la tarjeta.",
                })
            return ToolResult(data=CANNED[name])

        return handler

    return {
        name: Tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            status_label=tool.status_label,
            handler=handler_for(name),
            roles=tool.roles,
        )
        for name, tool in TOOLS.items()
    }


def _auth(role: str) -> AuthContext:
    return AuthContext(
        user_id="eval-user",
        business_id="eval-business",
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=AccessCapabilities(can_access_app=True),
    )


def _matches(expected, actual) -> bool:
    expected = _fill(expected)
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.lower().startswith(expected.lower())
    return expected == actual


def run_case(llm, case: dict) -> dict:
    calls: list[tuple[str, dict]] = []
    stats = TurnStats()
    ctx = ToolContext(auth=_auth(case["role"]), session=None, today=TODAY, conversation_id="eval")
    events = list(run_turn(llm, ctx, _stub_tools(calls), "eval", [], case["prompt"], stats=stats))
    text = "".join(e["delta"] for e in events if e["type"] == "text_delta")

    expected = case["tool"]
    if expected is None:
        tool_ok = not calls and not stats.denied_tools
        args_ok = tool_ok
    else:
        matching = [args for name, args in calls if name == expected]
        tool_ok = bool(matching)
        args_ok = any(
            all(_matches(value, args.get(key)) for key, value in case.get("args", {}).items()) for args in matching
        )
    wrote = any(name in WRITE_TOOLS for name, _ in calls)
    return {
        "id": case["id"],
        "tool": expected,
        "role": case["role"],
        "calls": [name for name, _ in calls],
        "tool_ok": tool_ok,
        "args_ok": args_ok,
        "permission_violation": bool(stats.denied_tools),
        "claims_done": wrote and bool(DONE_CLAIM.search(text)),
        "error": stats.error,
    }


def evaluate(llm, cases: list[dict]) -> dict:
    results = [run_case(llm, case) for case in cases]
    by_tool: dict[str, dict] = {}
    for result in results:
        entry = by_tool.setdefault(result["tool"] or "(ninguna)", {"cases": 0, "tool_ok": 0, "args_ok": 0})
        entry["cases"] += 1
        entry["tool_ok"] += result["tool_ok"]
        entry["args_ok"] += result["args_ok"]
    precision = sum(r["tool_ok"] for r in results) / len(results)
    report = {
        "cases": len(results),
        "precision": round(precision, 3),
        "args_precision": round(sum(r["args_ok"] for r in results) / len(results), 3),
        "permission_violations": [r["id"] for r in results if r["permission_violation"]],
        "writes_claimed_done": [r["id"] for r in results if r["claims_done"]],
        "by_tool": by_tool,
        "failures": [r for r in results if not (r["tool_ok"] and r["args_ok"])],
    }
    report["passed"] = (
        precision >= MIN_PRECISION and not report["permission_violations"] and not report["writes_claimed_done"]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(Path(__file__).with_name("cases.json")))
    parser.add_argument("--report", default=str(Path(__file__).with_name("report.json")))
    args = parser.parse_args()

    llm = get_assistant_llm()
    if llm is None:
        print("Falta OPENAI_API_KEY: no se puede ejecutar el eval.", file=sys.stderr)
        return 2

    report = evaluate(llm, json.loads(Path(args.cases).read_text()))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Casos: {report['cases']} · precisión: {report['precision']:.0%} · parámetros: {report['args_precision']:.0%}")
    for tool, entry in sorted(report["by_tool"].items()):
        print(f"  {tool:<24} {entry['tool_ok']}/{entry['cases']} herramienta · {entry['args_ok']}/{entry['cases']} parámetros")
    print(f"Permisos violados: {report['permission_violations'] or 'ninguno'}")
    print(f"Escrituras dadas por hechas: {report['writes_claimed_done'] or 'ninguna'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
