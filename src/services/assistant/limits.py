"""
Limites de uso del asistente (businext-backend#25).

Contadores en memoria (un solo worker): protegen el coste, no son una cuota
exacta, y se reinician con cada despliegue.
"""

import threading
import time
from collections import defaultdict, deque

USER_LIMIT, USER_WINDOW_SECONDS = 30, 3600
BUSINESS_LIMIT, BUSINESS_WINDOW_SECONDS = 300, 86400

LIMIT_MESSAGE = (
    "Has llegado al límite de mensajes del asistente por ahora. "
    "Vuelve a intentarlo en un rato; mientras, tienes la agenda y la caja en el menú."
)

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _allow(key: str, limit: int, window: int, now: float) -> bool:
    hits = _hits[key]
    while hits and hits[0] <= now - window:
        hits.popleft()
    return len(hits) < limit


def allow_message(business_id: str, user_id: str) -> bool:
    """Registra un mensaje si cabe en los limites del usuario y del negocio."""
    now = time.monotonic()
    user_key, business_key = f"user:{user_id}", f"business:{business_id}"
    with _lock:
        if not (
            _allow(user_key, USER_LIMIT, USER_WINDOW_SECONDS, now)
            and _allow(business_key, BUSINESS_LIMIT, BUSINESS_WINDOW_SECONDS, now)
        ):
            return False
        _hits[user_key].append(now)
        _hits[business_key].append(now)
        return True


def reset() -> None:
    with _lock:
        _hits.clear()
