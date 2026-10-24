"""Asistente conversacional de la app movil (Fase 7, ADR 0001)."""

import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src.api.auth import AuthContext, require_owner, require_subscription
from src.database.database import SessionDep
from src.services.assistant import actions, history, limits, usage
from src.services.assistant.history import HistoryUnavailable
from src.services.assistant.llm import AssistantLLM, cost_usd, get_assistant_llm
from src.services.assistant.orchestrator import TurnStats, run_turn
from src.services.assistant.usage import UsageUnavailable
from src.services.assistant.tools import TOOLS, ToolContext

from . import assistant_actions, assistant_tools  # noqa: F401  (registran las herramientas en TOOLS)

router = APIRouter(prefix="/assistant", tags=["assistant"])
logger = logging.getLogger(__name__)

# No hay zona horaria por negocio; todos operan en Espana (importes en EUR).
BUSINESS_TIMEZONE = ZoneInfo("Europe/Madrid")


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)


def require_authorization_header(authorization: str | None = Header(default=None)) -> None:
    # Sin esto, la falta de cabecera daria 422 (Header(...) en get_auth_context).
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")


def _sse(events: Iterator[dict], session) -> Iterator[str]:
    # El stream sigue despues de que FastAPI cierre la dependencia de sesion.
    try:
        for event in events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        session.close()


def _recorded(events: Iterator[dict], session, conversation) -> Iterator[dict]:
    """Deja pasar los eventos y guarda la respuesta del asistente al terminar."""
    blocks: list[dict] = []
    for event in events:
        if event["type"] == "text_delta":
            if blocks and blocks[-1]["type"] == "text":
                blocks[-1]["text"] += event["delta"]
            else:
                blocks.append({"type": "text", "text": event["delta"]})
        elif event["type"] == "block":
            blocks.append(event["block"])
        elif event["type"] == "message_end" and conversation is not None and blocks:
            try:
                history.save_message(session, conversation, "assistant", blocks, event["message_id"])
            except HistoryUnavailable:
                pass
        yield event


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _observed(
    events: Iterator[dict], session, auth: AuthContext, conversation_id: str, stats: TurnStats
) -> Iterator[dict]:
    """Al terminar el turno, registra un log estructurado y el uso del dia."""
    started = time.monotonic()
    try:
        yield from events
    finally:
        cost = cost_usd(stats.prompt_tokens, stats.completion_tokens)
        logger.info(
            "assistant_turn %s",
            json.dumps({
                "business_id": auth.business_id,
                "user_id": auth.user_id,
                "role": auth.role,
                "conversation_id": conversation_id,
                "tools": stats.tools,
                "denied_tools": stats.denied_tools,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "prompt_tokens": stats.prompt_tokens,
                "completion_tokens": stats.completion_tokens,
                "cost_usd": round(cost, 6),
                "error": stats.error,
            }),
        )
        usage.record(
            session,
            auth.business_id,
            datetime.now(BUSINESS_TIMEZONE).date(),
            stats.prompt_tokens,
            stats.completion_tokens,
            cost,
        )


def _limit_reached(conversation_id: str) -> Iterator[dict]:
    message_id = str(uuid.uuid4())
    yield {"type": "message_start", "conversation_id": conversation_id, "message_id": message_id}
    yield {"type": "text_delta", "message_id": message_id, "delta": limits.LIMIT_MESSAGE}
    yield {"type": "message_end", "message_id": message_id}


@router.post("/chat", dependencies=[Depends(require_authorization_header)])
def chat(
    body: ChatRequest,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
    llm: AssistantLLM | None = Depends(get_assistant_llm),
):
    """Un turno del chat, respondido en streaming SSE (eventos del contrato de shared-core)."""
    if llm is None:
        raise HTTPException(status_code=503, detail="Assistant is not configured")
    if not limits.allow_message(auth.business_id, auth.user_id):
        logger.info("assistant_rate_limited %s", json.dumps({"business_id": auth.business_id, "user_id": auth.user_id}))
        events = _limit_reached(body.conversation_id or str(uuid.uuid4()))
        return StreamingResponse(_sse(events, session), media_type="text/event-stream", headers=SSE_HEADERS)

    try:
        conversation = history.open_conversation(session, auth, body.conversation_id, body.message)
        past = history.llm_history(session, conversation)
        history.save_message(session, conversation, "user", [{"type": "text", "text": body.message}])
        conversation_id = conversation.id
    except HistoryUnavailable:
        conversation, past = None, []
        conversation_id = body.conversation_id or str(uuid.uuid4())

    stats = TurnStats()
    events = run_turn(
        llm=llm,
        ctx=_context(auth, session, conversation_id),
        tools=TOOLS,
        conversation_id=conversation_id,
        history=past,
        user_message=body.message,
        stats=stats,
    )
    return StreamingResponse(
        _sse(_observed(_recorded(events, session, conversation), session, auth, conversation_id, stats), session),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


class ConfirmActionRequest(BaseModel):
    conversation_id: str
    decision: Literal["confirm", "cancel"]


ACTION_UNAVAILABLE = "Esta acción ya no está disponible (ha caducado o ya se respondió). Pídemela de nuevo si la necesitas."


def _confirm_events(action_id: str, body: ConfirmActionRequest, ctx: ToolContext) -> Iterator[dict]:
    message_id = str(uuid.uuid4())
    yield {"type": "message_start", "conversation_id": body.conversation_id, "message_id": message_id}
    action = actions.take(action_id, ctx.auth.business_id, ctx.auth.user_id, body.conversation_id)
    if action is None:
        text = ACTION_UNAVAILABLE
    elif body.decision == "cancel":
        text = "Vale, no he hecho nada."
    else:
        try:
            text = action.execute(ctx)
        except HTTPException as error:
            ctx.session.rollback()
            text = f"No se ha podido hacer: {error.detail}"
    yield {"type": "text_delta", "message_id": message_id, "delta": text}
    yield {"type": "message_end", "message_id": message_id}


@router.post("/actions/{action_id}/confirm", dependencies=[Depends(require_authorization_header)])
def confirm_action(
    action_id: str,
    body: ConfirmActionRequest,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Ejecuta (o descarta) una accion propuesta por el asistente. Responde en SSE."""
    try:
        conversation = history.find_conversation(session, auth, body.conversation_id)
    except HistoryUnavailable:
        conversation = None
    events = _confirm_events(action_id, body, _context(auth, session, body.conversation_id))
    return StreamingResponse(
        _sse(_recorded(events, session, conversation), session),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/usage", dependencies=[Depends(require_authorization_header)])
def get_usage(session: SessionDep, auth: AuthContext = Depends(require_owner), days: int = 30):
    """Coste diario del asistente para el negocio (solo owner), el dia mas reciente primero."""
    try:
        return usage.daily(session, auth.business_id, datetime.now(BUSINESS_TIMEZONE).date(), min(max(days, 1), 365))
    except UsageUnavailable:
        raise HTTPException(status_code=503, detail="El registro de uso no está disponible")


HISTORY_UNAVAILABLE = HTTPException(status_code=503, detail="El historial de conversaciones no está disponible")


@router.get("/conversations", dependencies=[Depends(require_authorization_header)])
def list_conversations(session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Conversaciones del usuario, la mas reciente primero (`ConversationSummary` de shared-core)."""
    try:
        return history.list_conversations(session, auth)
    except HistoryUnavailable:
        raise HISTORY_UNAVAILABLE


@router.get("/conversations/{conversation_id}", dependencies=[Depends(require_authorization_header)])
def get_conversation(
    conversation_id: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)
):
    """Una conversacion con sus mensajes y bloques (`Conversation` de shared-core)."""
    try:
        conversation = history.get_conversation(session, auth, conversation_id)
    except HistoryUnavailable:
        raise HISTORY_UNAVAILABLE
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204, dependencies=[Depends(require_authorization_header)])
def delete_conversation(
    conversation_id: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)
):
    try:
        deleted = history.delete_conversation(session, auth, conversation_id)
    except HistoryUnavailable:
        raise HISTORY_UNAVAILABLE
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    return Response(status_code=204)


def _context(auth: AuthContext, session, conversation_id: str) -> ToolContext:
    return ToolContext(
        auth=auth,
        session=session,
        today=datetime.now(BUSINESS_TIMEZONE).date(),
        conversation_id=conversation_id,
    )
