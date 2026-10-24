"""
LLM simulado para los E2E del chat (businext-sys/businext#101).

Responde de forma determinista a los prompts de los flows Maestro
(businext/apps/mobile/.maestro/flows/) llamando a las herramientas reales,
asi que las tarjetas salen de los datos de la base. Nunca en produccion:
ver get_assistant_llm.
"""

import json
import unicodedata
from collections.abc import Iterator

from .llm import LLMDelta, ToolCallDelta

FALLBACK_TEXT = "Soy el asistente en modo de prueba."


def _normalize(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def _turn(messages: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """Ultimo mensaje del usuario y (herramienta, resultado) de este turno, en orden."""
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    names = {
        call["id"]: call["function"]["name"]
        for m in messages[last_user:]
        if m["role"] == "assistant"
        for call in m.get("tool_calls") or []
    }
    results = [
        (names.get(m["tool_call_id"], ""), m["content"])
        for m in messages[last_user:]
        if m["role"] == "tool"
    ]
    return _normalize(messages[last_user]["content"]), results


def _call(name: str, args: dict) -> LLMDelta:
    return LLMDelta(tool_calls=[ToolCallDelta(index=0, id=f"mock-{name}", name=name, arguments=json.dumps(args))])


def _next_pending_reservation(result: str) -> int | None:
    data = json.loads(result)
    for reservation in data.get("reservations", []):
        if reservation["status"] == "PENDING":
            return reservation["id"]
    return None


class MockAssistantLLM:
    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMDelta]:
        prompt, done = _turn(messages)
        called = [name for name, _ in done]

        if "cancela" in prompt:
            if not called:
                yield _call("get_reservations", {})
                return
            if called == ["get_reservations"]:
                reservation_id = _next_pending_reservation(done[0][1])
                if reservation_id is not None:
                    yield _call("cancel_reservation", {"id": reservation_id})
                    return
                yield LLMDelta(text="No tienes reservas pendientes hoy.")
                return
            yield LLMDelta(text="Confírmalo en la tarjeta.")
            return

        for keyword, tool, args, text in (
            ("solicitud", "get_booking_requests", {}, "Estas son tus solicitudes pendientes."),
            ("caja", "get_cash_summary", {"period": "today"}, "Así va la caja hoy."),
            ("reserva", "get_reservations", {}, "Estas son tus reservas de hoy."),
        ):
            if keyword in prompt:
                yield _call(tool, args) if not called else LLMDelta(text=text)
                return

        yield LLMDelta(text=FALLBACK_TEXT)
