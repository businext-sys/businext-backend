"""
Analisis del radar de tendencias por negocio (businext-sys/businext#165).

Cruza el `trendsnapshot` del dia con agregados del negocio (nunca datos de
clientes) y guarda un `trendinsight` y 1-3 `contentsuggestion`. La hora de
publicacion la calcula el codigo a partir de las reservas, el horario del
negocio y el horario silencioso, no el modelo.
"""
import json
import logging
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.finances_model import Finances
from src.database.models.product_model import Product
from src.database.models.reservation_model import Reservation
from src.database.models.trends_model import (
    ContentPreferences,
    ContentSuggestion,
    TrendInsight,
    TrendSnapshot,
)
from src.database.models.working_hours_model import WorkingHours
from src.services import trend_sources

logger = logging.getLogger(__name__)

WINDOW_DAYS = 28
SCHEDULE_DAYS = 8
MIN_LEAD = timedelta(hours=1)
MAX_SUGGESTIONS = 3
DAY_NAMES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

SYSTEM_PROMPT = (
    "Eres estratega de contenido para negocios locales de servicios. Con las tendencias de hoy "
    "y los datos agregados de un negocio, escribe un analisis breve para su dueno (que tendencias "
    "encajan con sus servicios y por que) y de 1 a 3 ideas de video que pueda grabar el mismo. "
    "Nombra los servicios reales del negocio. Cada idea lleva un guion de exactamente 3 pasos, "
    "un texto para la publicacion y, si aplica, el audio. En source_trend_urls pon solo URLs de "
    "la lista de tendencias que justifiquen la idea. No inventes tendencias, cifras ni servicios."
)

SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "network": {"type": "string", "enum": ["instagram", "tiktok"]},
        "format": {"type": "string", "enum": ["reel", "short", "carrusel", "story"]},
        "idea": {"type": "string"},
        "script": {"type": "array", "items": {"type": "string"}},
        "caption_hint": {"type": "string"},
        "audio_hint": {"type": ["string", "null"]},
        "source_trend_urls": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["network", "format", "idea", "script", "caption_hint", "audio_hint", "source_trend_urls"],
    "additionalProperties": False,
}
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "matched_services": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": SUGGESTION_SCHEMA},
    },
    "required": ["analysis", "matched_services", "suggestions"],
    "additionalProperties": False,
}


class SuggestionDraft(BaseModel):
    network: str
    format: str
    idea: str
    script: list[str]
    caption_hint: str
    audio_hint: str | None
    source_trend_urls: list[str]


class AnalysisResult(BaseModel):
    analysis: str
    matched_services: list[str]
    suggestions: list[SuggestionDraft]


class TrendAnalysisError(Exception):
    pass


def today_snapshot(session: Session, preferences: ContentPreferences, today: date) -> TrendSnapshot | None:
    return session.exec(
        select(TrendSnapshot).where(
            TrendSnapshot.niche == preferences.niche,
            TrendSnapshot.language == preferences.language,
            TrendSnapshot.country == preferences.country,
            TrendSnapshot.source == trend_sources.SOURCE,
            TrendSnapshot.day == today,
        )
    ).first()


def business_aggregates(session: Session, business_id: str, today: date) -> dict:
    """Agregados de los ultimos 28 dias. Las horas de las reservas son hora local del negocio."""
    since = datetime.combine(today - timedelta(days=WINDOW_DAYS), time.min)
    until = datetime.combine(today + timedelta(days=1), time.min)
    last_week = datetime.combine(today - timedelta(days=7), time.min)

    reservations = session.exec(
        select(Reservation).where(
            Reservation.business_id == business_id,
            Reservation.reservation_start_date >= since,
            Reservation.reservation_start_date < until,
        )
    ).all()
    incomes = session.exec(
        select(Finances).where(
            Finances.business_id == business_id,
            Finances.type == "INCOME",
            Finances.created_at >= since,
            Finances.created_at < until,
        )
    ).all()
    services = session.exec(
        select(Product).where(Product.business_id == business_id, Product.type == "servicio")
    ).all()

    reservation_ids = {f.reservation_id for f in incomes if f.reservation_id}
    service_by_reservation = dict(
        session.exec(select(Reservation.id, Reservation.service).where(Reservation.id.in_(reservation_ids))).all()
    ) if reservation_ids else {}
    income_by_service = Counter()
    for income in incomes:
        if income.reservation_id in service_by_reservation:
            income_by_service[service_by_reservation[income.reservation_id]] += income.amount

    return {
        "services": [{"name": s.name, "price": s.price} for s in services],
        "reservations_by_service": dict(Counter(r.service for r in reservations).most_common(5)),
        "income_by_service": {name: round(total, 2) for name, total in income_by_service.most_common(5)},
        "reservations_by_hour": dict(sorted(Counter(r.reservation_start_date.hour for r in reservations).items())),
        "reservations_by_weekday": {
            DAY_NAMES[day]: n for day, n in sorted(Counter(r.reservation_start_date.weekday() for r in reservations).items())
        },
        "average_ticket": round(sum(f.amount for f in incomes) / len(incomes), 2) if incomes else None,
        "reservations_last_7_days": sum(1 for r in reservations if r.reservation_start_date >= last_week),
        "reservations_previous_7_days": sum(
            1 for r in reservations if last_week - timedelta(days=7) <= r.reservation_start_date < last_week
        ),
    }


def is_quiet(preferences: ContentPreferences, moment: time) -> bool:
    start, end = preferences.quiet_from, preferences.quiet_to
    if start > end:
        return moment >= start or moment < end
    return start <= moment < end


def _is_open(working_hours: list[WorkingHours], weekday: int, hour: int) -> bool:
    if not working_hours:
        return True
    moment = f"{hour:02d}:00"
    return any(w.day_of_week == weekday and w.start_time <= moment < w.end_time for w in working_hours)


def publish_slots(
    session: Session, preferences: ContentPreferences, hour_counts: Counter, needed: int, now: datetime
) -> list[datetime]:
    """
    Horas de publicacion (UTC sin zona) para `needed` sugerencias: dentro del
    horario general del negocio, fuera del silencioso, al menos una hora despues
    de `now` y sin pasar de `max_per_day` por dia. Gana la hora con mas reservas.
    """
    tz = ZoneInfo(preferences.timezone)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    working_hours = session.exec(
        select(WorkingHours).where(
            WorkingHours.business_id == preferences.business_id,
            WorkingHours.member_user_id.is_(None),
            WorkingHours.enabled,
        )
    ).all()
    scheduled = session.exec(
        select(ContentSuggestion.publish_at).where(
            ContentSuggestion.business_id == preferences.business_id,
            ContentSuggestion.publish_at >= now - timedelta(days=1),
        )
    ).all()
    taken = Counter(at.replace(tzinfo=timezone.utc).astimezone(tz).date() for at in scheduled)

    slots = []
    for offset in range(SCHEDULE_DAYS):
        day = local_now.date() + timedelta(days=offset)
        free = preferences.max_per_day - taken[day]
        hours = [
            hour
            for hour in range(24)
            if _is_open(working_hours, day.weekday(), hour)
            and not is_quiet(preferences, time(hour))
            and datetime.combine(day, time(hour), tz) >= local_now + MIN_LEAD
        ]
        best = sorted(hours, key=lambda hour: (-hour_counts[hour], hour))
        for hour in best[: max(0, min(free, needed - len(slots)))]:
            slots.append(datetime.combine(day, time(hour), tz).astimezone(timezone.utc).replace(tzinfo=None))
        if len(slots) == needed:
            break
    return sorted(slots)


def _request_analysis(preferences: ContentPreferences, trends: list[dict], business: dict) -> anthropic.types.beta.BetaMessage:
    if not trend_sources.client:
        raise TrendAnalysisError("Falta ANTHROPIC_API_KEY")
    trends_block = (
        f"Tendencias de hoy (nicho {preferences.niche}, idioma {preferences.language}, "
        f"pais {preferences.country}). Responde en ese idioma.\n{json.dumps(trends, ensure_ascii=False)}"
    )
    try:
        return trend_sources.client.beta.messages.create(
            model=trend_sources.MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
            system=[
                {"type": "text", "text": SYSTEM_PROMPT},
                {"type": "text", "text": trends_block, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": f"Datos agregados del negocio:\n{json.dumps(business, ensure_ascii=False)}"}],
        )
    except anthropic.RateLimitError as e:
        raise TrendAnalysisError(f"Limite de la API de Claude: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise TrendAnalysisError(f"La API de Claude respondio {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise TrendAnalysisError("No se pudo conectar con la API de Claude") from e


def analyze_business(
    session: Session, preferences: ContentPreferences, snapshot: TrendSnapshot, now: datetime, refresh: bool = False
) -> TrendInsight:
    """
    Genera (o con `refresh`, regenera) el analisis de hoy del negocio y sus
    sugerencias. Al regenerar se sustituyen las sugerencias aun `pending`.
    """
    trends = json.loads(snapshot.payload)
    if not trends:
        raise TrendAnalysisError("No hay tendencias de hoy")
    today = snapshot.day
    business_id = preferences.business_id
    config = session.exec(select(BusinessConfiguration).where(BusinessConfiguration.business_id == business_id)).first()
    business = {
        "name": config.business_name if config else None,
        "networks": json.loads(preferences.networks),
        **business_aggregates(session, business_id, today),
    }

    response = _request_analysis(preferences, trends, business)
    if response.stop_reason != "end_turn":
        raise TrendAnalysisError(f"Respuesta incompleta de Claude (stop_reason={response.stop_reason})")
    texts = [block.text for block in response.content if block.type == "text"]
    try:
        result = AnalysisResult.model_validate_json(texts[-1])
    except (IndexError, ValidationError) as e:
        raise TrendAnalysisError("La respuesta de Claude no tiene el formato esperado") from e

    trends_by_url = {trend["url"]: trend for trend in trends}
    drafts = []
    for draft in result.suggestions:
        sources = [url for url in draft.source_trend_urls if url in trends_by_url]
        if sources and draft.script and draft.network in business["networks"]:
            drafts.append(draft.model_copy(update={"source_trend_urls": sources, "script": draft.script[:3]}))
    drafts = drafts[:MAX_SUGGESTIONS]

    insight = session.exec(
        select(TrendInsight).where(TrendInsight.business_id == business_id, TrendInsight.day == today)
    ).first() or TrendInsight(business_id=business_id, day=today)
    if insight.id is not None:
        for pending in session.exec(
            select(ContentSuggestion).where(
                ContentSuggestion.insight_id == insight.id, ContentSuggestion.status == "pending"
            )
        ).all():
            session.delete(pending)
        session.flush()

    used_urls = list(dict.fromkeys(url for draft in drafts for url in draft.source_trend_urls))
    usage = response.usage
    insight.sqlmodel_update(
        {
            "analysis": result.analysis,
            "matched_services": json.dumps(result.matched_services, ensure_ascii=False),
            "trends_used": json.dumps(
                [{key: trends_by_url[url][key] for key in ("title", "network", "url")} for url in used_urls],
                ensure_ascii=False,
            ),
            "model": response.model,
            "input_tokens": usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0),
            "output_tokens": usage.output_tokens,
        }
    )
    if refresh:
        insight.refreshed_at = now
    session.add(insight)
    session.flush()

    hour_counts = Counter({int(hour): n for hour, n in business["reservations_by_hour"].items()})
    for draft, publish_at in zip(drafts, publish_slots(session, preferences, hour_counts, len(drafts), now)):
        session.add(
            ContentSuggestion(
                business_id=business_id,
                insight_id=insight.id,
                network=draft.network,
                format=draft.format,
                idea=draft.idea,
                script=json.dumps(draft.script, ensure_ascii=False),
                caption_hint=draft.caption_hint,
                audio_hint=draft.audio_hint,
                publish_at=publish_at,
                source_trend_ids=json.dumps(draft.source_trend_urls),
            )
        )
    session.commit()
    session.refresh(insight)
    logger.info(
        "trends_analysis business_id=%s suggestions=%d input_tokens=%d output_tokens=%d",
        business_id, len(drafts), insight.input_tokens, insight.output_tokens,
    )
    return insight


def analyze_trends(session: Session) -> dict:
    """Analiza cada negocio activado que tenga tendencias de hoy y aun no tenga analisis."""
    result = {"enabled": trend_sources.trends_enabled(), "created": 0, "skipped": 0, "failed": 0}
    if not result["enabled"]:
        return result

    now = datetime.utcnow()
    today = datetime.now(trend_sources.BUSINESS_TIMEZONE).date()
    for preferences in session.exec(select(ContentPreferences).where(ContentPreferences.enabled)).all():
        already_analyzed = session.exec(
            select(TrendInsight.id).where(
                TrendInsight.business_id == preferences.business_id, TrendInsight.day == today
            )
        ).first()
        snapshot = today_snapshot(session, preferences, today)
        if already_analyzed or not snapshot or snapshot.payload == "[]":
            result["skipped"] += 1
            continue
        try:
            analyze_business(session, preferences, snapshot, now)
        except TrendAnalysisError:
            session.rollback()
            logger.exception("trends_analysis_failed business_id=%s", preferences.business_id)
            result["failed"] += 1
            continue
        result["created"] += 1
    return result
