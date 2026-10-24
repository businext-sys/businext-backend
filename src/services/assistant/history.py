"""
Historial de conversaciones del asistente (businext-backend#24).

Si las tablas aun no existen en la base de datos (se crean a mano con
docs/sql/024-assistant-conversations.sql), el chat sigue funcionando sin
historial: las operaciones lanzan HistoryUnavailable y se registra un aviso.
"""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from src.api.auth import AuthContext
from src.database.models.assistant_conversation_model import AssistantConversation, AssistantMessage

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20
TITLE_LENGTH = 60
PREVIEW_LENGTH = 100
BLOCK_CONTEXT_LENGTH = 500


class HistoryUnavailable(Exception):
    pass


def _guard(session: Session, operation):
    try:
        return operation()
    except SQLAlchemyError as error:
        session.rollback()
        logger.warning("assistant history unavailable: %s", error)
        raise HistoryUnavailable from error


def _owned(session: Session, auth: AuthContext, conversation_id: str) -> AssistantConversation | None:
    conversation = session.get(AssistantConversation, conversation_id)
    if conversation and conversation.business_id == auth.business_id and conversation.user_id == auth.user_id:
        return conversation
    return None


def _text_of(blocks: list[dict]) -> str:
    return " ".join(b["text"] for b in blocks if b.get("type") == "text").strip()


def open_conversation(
    session: Session, auth: AuthContext, conversation_id: str | None, first_message: str
) -> AssistantConversation:
    """La conversacion del usuario, o una nueva si no existe o es de otro."""

    def operation():
        if conversation_id:
            existing = _owned(session, auth, conversation_id)
            if existing:
                return existing
            taken = session.get(AssistantConversation, conversation_id) is not None
        else:
            taken = True
        title = first_message.strip()
        conversation = AssistantConversation(
            id=str(uuid.uuid4()) if taken else conversation_id,
            business_id=auth.business_id,
            user_id=auth.user_id,
            title=title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1].rstrip() + "…",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation

    return _guard(session, operation)


def start_conversation(
    session: Session, business_id: str, user_id: str, title: str, blocks: list[dict]
) -> AssistantConversation:
    """Conversacion nueva que empieza con un mensaje del asistente (p. ej. al llegar una push)."""

    def operation():
        conversation = AssistantConversation(
            id=str(uuid.uuid4()), business_id=business_id, user_id=user_id, title=title[:TITLE_LENGTH]
        )
        session.add(conversation)
        session.add(AssistantMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            blocks=json.dumps(blocks, ensure_ascii=False),
        ))
        session.commit()
        session.refresh(conversation)
        return conversation

    return _guard(session, operation)


def llm_history(session: Session, conversation: AssistantConversation) -> list[dict]:
    """Los ultimos mensajes como contexto para el LLM; las tarjetas, resumidas."""

    def operation():
        messages = session.exec(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.created_at.desc())  # type: ignore[union-attr]
            .limit(HISTORY_LIMIT)
        ).all()
        history = []
        for message in reversed(messages):
            parts = []
            for block in json.loads(message.blocks):
                if block.get("type") == "text":
                    parts.append(block["text"])
                else:
                    parts.append(f"[tarjeta mostrada al usuario: {json.dumps(block, ensure_ascii=False)[:BLOCK_CONTEXT_LENGTH]}]")
            history.append({"role": message.role, "content": "\n".join(parts)})
        return history

    return _guard(session, operation)


def save_message(
    session: Session,
    conversation: AssistantConversation,
    role: str,
    blocks: list[dict],
    message_id: str | None = None,
) -> None:
    def operation():
        now = datetime.utcnow()
        session.add(AssistantMessage(
            id=message_id or str(uuid.uuid4()),
            conversation_id=conversation.id,
            role=role,
            blocks=json.dumps(blocks, ensure_ascii=False),
            created_at=now,
        ))
        conversation.updated_at = now
        session.add(conversation)
        session.commit()

    _guard(session, operation)


def find_conversation(session: Session, auth: AuthContext, conversation_id: str) -> AssistantConversation | None:
    return _guard(session, lambda: _owned(session, auth, conversation_id))


def list_conversations(session: Session, auth: AuthContext) -> list[dict]:
    def operation():
        conversations = session.exec(
            select(AssistantConversation)
            .where(
                AssistantConversation.business_id == auth.business_id,
                AssistantConversation.user_id == auth.user_id,
            )
            .order_by(AssistantConversation.updated_at.desc())  # type: ignore[union-attr]
        ).all()
        summaries = []
        for conversation in conversations:
            last = session.exec(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation.id)
                .order_by(AssistantMessage.created_at.desc())  # type: ignore[union-attr]
            ).first()
            preview = _text_of(json.loads(last.blocks))[:PREVIEW_LENGTH] if last else ""
            summaries.append({
                "id": conversation.id,
                "title": conversation.title,
                "last_message_preview": preview or None,
                "updated_at": conversation.updated_at.isoformat(),
            })
        return summaries

    return _guard(session, operation)


def get_conversation(session: Session, auth: AuthContext, conversation_id: str) -> dict | None:
    def operation():
        conversation = _owned(session, auth, conversation_id)
        if conversation is None:
            return None
        messages = session.exec(
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation.id)
            .order_by(AssistantMessage.created_at)  # type: ignore[arg-type]
        ).all()
        return {
            "id": conversation.id,
            "title": conversation.title,
            "updated_at": conversation.updated_at.isoformat(),
            "messages": [
                {
                    "id": m.id,
                    "conversation_id": m.conversation_id,
                    "role": m.role,
                    "blocks": json.loads(m.blocks),
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }

    return _guard(session, operation)


def delete_conversation(session: Session, auth: AuthContext, conversation_id: str) -> bool:
    def operation():
        conversation = _owned(session, auth, conversation_id)
        if conversation is None:
            return False
        for message in session.exec(
            select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id)
        ).all():
            session.delete(message)
        session.delete(conversation)
        session.commit()
        return True

    return _guard(session, operation)
