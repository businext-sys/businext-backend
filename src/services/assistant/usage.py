"""
Uso y coste diario del asistente por negocio (businext-backend#25).

La tabla se crea a mano con docs/sql/025-assistant-usage.sql; sin ella, el
uso solo queda en los logs `assistant_turn`.
"""

import logging
from datetime import date, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from src.database.models.assistant_usage_model import AssistantUsage

logger = logging.getLogger(__name__)


class UsageUnavailable(Exception):
    pass


def record(session: Session, business_id: str, day: date, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
    try:
        usage = session.exec(
            select(AssistantUsage).where(AssistantUsage.business_id == business_id, AssistantUsage.day == day)
        ).first() or AssistantUsage(business_id=business_id, day=day)
        usage.turns += 1
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.cost_usd = round(usage.cost_usd + cost_usd, 6)
        session.add(usage)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        logger.warning("assistant usage not recorded: %s", error)


def daily(session: Session, business_id: str, today: date, days: int) -> list[dict]:
    try:
        rows = session.exec(
            select(AssistantUsage)
            .where(AssistantUsage.business_id == business_id, AssistantUsage.day > today - timedelta(days=days))
            .order_by(AssistantUsage.day.desc())  # type: ignore[union-attr]
        ).all()
    except SQLAlchemyError as error:
        session.rollback()
        raise UsageUnavailable from error
    return [
        {
            "day": row.day.isoformat(),
            "turns": row.turns,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "cost_usd": row.cost_usd,
        }
        for row in rows
    ]
