"""Asistente conversacional de la app movil (Fase 7, ADR 0001)."""

import json
import uuid
from collections.abc import Iterator
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.auth import AuthContext, require_subscription
from src.database.database import SessionDep
from src.services.assistant.llm import AssistantLLM, get_assistant_llm
from src.services.assistant.orchestrator import run_turn
from src.services.assistant.tools import TOOLS, ToolContext

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)


def require_authorization_header(authorization: str | None = Header(default=None)) -> None:
    # Sin esto, la falta de cabecera daria 422 (Header(...) en get_auth_context).
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


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

    events = run_turn(
        llm=llm,
        ctx=ToolContext(auth=auth, session=session),
        tools=TOOLS,
        conversation_id=body.conversation_id or str(uuid.uuid4()),
        history=[],
        user_message=body.message,
        today=date.today(),
    )
    return StreamingResponse(_sse(events), media_type="text/event-stream", headers=SSE_HEADERS)
