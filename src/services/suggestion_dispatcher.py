"""
Avisos de las sugerencias de contenido (businext-sys/businext#166).

Cada 15 minutos, un cron job de Render llama a `POST /internal/trends/dispatch`,
que envia por Expo Push las sugerencias `pending` cuya hora de publicacion
esta a 45 minutos o menos, y las marca `sent`. Nunca dentro del horario
silencioso del negocio ni por encima de su `max_per_day`.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from src.database.models.member_model import BusinessMember
from src.database.models.push_token_model import PushToken
from src.database.models.trends_model import ContentPreferences, ContentSuggestion
from src.services.push_notification_service import send_push_notifications
from src.services.trend_analysis import is_quiet
from src.services.trend_sources import trends_enabled

NOTICE = timedelta(minutes=45)
CHANNEL_ID = "content"
TITLE = "Idea de contenido para hoy"
BODY_LIMIT = 150
NETWORK_NAMES = {"instagram": "Instagram", "tiktok": "TikTok"}


def _recipients(session: Session, business_id: str) -> list[str]:
    """Tokens del owner (su user_id es el business_id) y de los managers activos."""
    managers = select(BusinessMember.member_user_id).where(
        BusinessMember.business_id == business_id,
        BusinessMember.role == "manager",
        BusinessMember.status == "active",
    )
    return list(
        session.exec(
            select(PushToken.token).where(
                (PushToken.user_id == business_id) | PushToken.user_id.in_(managers)
            )
        ).all()
    )


def _body(suggestion: ContentSuggestion) -> str:
    text = f"{NETWORK_NAMES.get(suggestion.network, suggestion.network)}: {suggestion.idea}"
    return text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - 1].rstrip() + "…"


def dispatch_suggestions(session: Session, now: datetime) -> dict:
    """Envia las sugerencias que tocan. `now` es UTC sin zona, como `publish_at`."""
    result = {"enabled": trends_enabled(), "sent": 0, "skipped": 0}
    if not result["enabled"]:
        return result

    due = session.exec(
        select(ContentSuggestion)
        .join(ContentPreferences, ContentPreferences.business_id == ContentSuggestion.business_id)
        .where(
            ContentPreferences.enabled,
            ContentSuggestion.status == "pending",
            ContentSuggestion.publish_at <= now + NOTICE,
        )
        .order_by(ContentSuggestion.publish_at)
    ).all()
    by_business = defaultdict(list)
    for suggestion in due:
        by_business[suggestion.business_id].append(suggestion)

    for business_id, suggestions in by_business.items():
        preferences = session.get(ContentPreferences, business_id)
        tz = ZoneInfo(preferences.timezone)
        local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
        day_start = datetime.combine(local_now.date(), datetime.min.time(), tz).astimezone(timezone.utc)
        sent_today = len(
            session.exec(
                select(ContentSuggestion.id).where(
                    ContentSuggestion.business_id == business_id,
                    ContentSuggestion.sent_at >= day_start.replace(tzinfo=None),
                )
            ).all()
        )
        allowed = 0 if is_quiet(preferences, local_now.time()) else max(0, preferences.max_per_day - sent_today)
        result["skipped"] += len(suggestions[allowed:])
        if not suggestions[:allowed]:
            continue

        tokens = _recipients(session, business_id)
        for suggestion in suggestions[:allowed]:
            send_push_notifications(
                tokens=tokens,
                title=TITLE,
                body=_body(suggestion),
                data={"type": "content_suggestion", "id": suggestion.id},
                channel_id=CHANNEL_ID,
            )
            suggestion.status = "sent"
            suggestion.sent_at = now
            session.add(suggestion)
            session.commit()
            result["sent"] += 1
    return result
