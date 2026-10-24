"""Bucle de un turno del asistente: LLM + herramientas → eventos del contrato."""

import json
import logging
import uuid
from collections.abc import Iterator

from .llm import AssistantLLM
from .tools import Tool, ToolContext

logger = logging.getLogger(__name__)

MAX_STEPS = 5

WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

LLM_ERROR_MESSAGE = "El asistente no está disponible ahora mismo. Inténtalo de nuevo en un momento."
MAX_STEPS_MESSAGE = "No he podido terminar esta consulta. ¿Puedes reformularla?"


def system_prompt(ctx: ToolContext) -> str:
    return (
        "Eres el asistente de Businext, la app de gestión de un negocio local de servicios "
        "(barbería, peluquería...). Hablas en español, de tú, con frases cortas y claras. "
        f"Hoy es {ctx.today.isoformat()} ({WEEKDAYS[ctx.today.weekday()]}). El usuario tiene el rol «{ctx.auth.role}» en el negocio. "
        "Usa siempre las herramientas para obtener datos del negocio: nunca inventes reservas, "
        "importes ni nombres. Si no tienes una herramienta para lo que te piden, dilo. "
        "Cuando una herramienta devuelve una tarjeta, no repitas su contenido entero: resúmelo "
        "en una frase. Las herramientas que cambian datos solo preparan la acción: di al usuario "
        "que la confirme en la tarjeta y nunca afirmes que ya está hecha."
    )


def _run_tool(tool: Tool | None, name: str, raw_args: str, ctx: ToolContext):
    if tool is None:
        return {"error": f"Herramienta desconocida: {name}"}, []
    try:
        args = json.loads(raw_args or "{}")
        result = tool.handler(ctx, args)
        return result.data, result.blocks
    except Exception:
        logger.exception("assistant tool %s failed", name)
        return {"error": "La herramienta ha fallado."}, []


def run_turn(
    llm: AssistantLLM,
    ctx: ToolContext,
    tools: dict[str, Tool],
    conversation_id: str,
    history: list[dict],
    user_message: str,
) -> Iterator[dict]:
    message_id = str(uuid.uuid4())
    yield {"type": "message_start", "conversation_id": conversation_id, "message_id": message_id}

    messages = [
        {"role": "system", "content": system_prompt(ctx)},
        *history,
        {"role": "user", "content": user_message},
    ]
    specs = [tool.spec() for tool in tools.values()]

    try:
        for _ in range(MAX_STEPS):
            calls: dict[int, dict] = {}
            text = ""
            for delta in llm.stream(messages, specs):
                if delta.text:
                    text += delta.text
                    yield {"type": "text_delta", "message_id": message_id, "delta": delta.text}
                for call in delta.tool_calls:
                    current = calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                    current["id"] = call.id or current["id"]
                    current["name"] = call.name or current["name"]
                    current["arguments"] += call.arguments

            if not calls:
                break

            messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for call in calls.values()
                ],
            })
            for call in calls.values():
                tool = tools.get(call["name"])
                if tool:
                    yield {"type": "tool_status", "message_id": message_id, "label": tool.status_label}
                data, blocks = _run_tool(tool, call["name"], call["arguments"], ctx)
                for block in blocks:
                    yield {"type": "block", "message_id": message_id, "block": block}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(data, ensure_ascii=False, default=str),
                })
            yield {"type": "tool_status", "message_id": message_id, "label": None}
        else:
            yield {"type": "text_delta", "message_id": message_id, "delta": MAX_STEPS_MESSAGE}
    except Exception:
        logger.exception("assistant LLM call failed")
        yield {"type": "error", "code": "llm_error", "message": LLM_ERROR_MESSAGE}
        return

    yield {"type": "message_end", "message_id": message_id}
