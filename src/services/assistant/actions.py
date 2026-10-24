"""
Acciones de escritura pendientes de confirmar (businext-backend#22).

Viven en memoria: el backend corre con un solo worker (ver README). Un
reinicio las invalida; el usuario vuelve a pedir la accion.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .tools import ToolContext

ACTION_TTL = timedelta(minutes=15)


@dataclass
class PendingAction:
    business_id: str
    user_id: str
    conversation_id: str
    title: str
    description: str
    execute: Callable[[ToolContext], str]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + ACTION_TTL)

    def block(self, status: str = "pending") -> dict:
        return {
            "type": "confirmation",
            "action_id": self.id,
            "title": self.title,
            "description": self.description,
            "status": status,
        }


_pending: dict[str, PendingAction] = {}
_lock = threading.Lock()


def add(action: PendingAction) -> PendingAction:
    with _lock:
        now = datetime.now(timezone.utc)
        for action_id in [k for k, v in _pending.items() if v.expires_at <= now]:
            del _pending[action_id]
        _pending[action.id] = action
    return action


def take(action_id: str, business_id: str, user_id: str, conversation_id: str) -> PendingAction | None:
    """Saca la accion si pertenece a ese usuario y conversacion y no ha caducado."""
    with _lock:
        action = _pending.get(action_id)
        if (
            action is None
            or action.business_id != business_id
            or action.user_id != user_id
            or action.conversation_id != conversation_id
        ):
            return None
        del _pending[action_id]
    return action if action.expires_at > datetime.now(timezone.utc) else None
