"""
Fuentes del radar de tendencias (businext-sys/businext#164).

La ingesta diaria pide a Claude, con el conector MCP de vidIQ, las
tendencias de Instagram Reels y TikTok de cada (nicho, idioma, pais) con
negocios activos, y las guarda en `trendsnapshot`. Una consulta a vidIQ cuesta
5 creditos: una combinacion que ya tiene snapshot hoy no se vuelve a consultar.
"""
import json
import logging
import os
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from src.database.models.trends_model import ContentPreferences, TrendSnapshot

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TRENDS_MCP_VIDIQ_URL = os.getenv("TRENDS_MCP_VIDIQ_URL", "")
TRENDS_MCP_VIDIQ_TOKEN = os.getenv("TRENDS_MCP_VIDIQ_TOKEN", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

MODEL = "claude-opus-5-5"
SOURCE = "vidiq"
VIDIQ_TOOL = "vidiq_instagram_tiktok_outlier_search"
BUSINESS_TIMEZONE = ZoneInfo("Europe/Madrid")

SYSTEM_PROMPT = (
    "Extraes tendencias reales de Instagram Reels y TikTok para negocios locales. "
    f"Haz una sola consulta con {VIDIQ_TOOL} (descriptionLanguage con el idioma indicado, "
    "audienceQuery con el pais indicado, ultimos 7 dias) y devuelve los videos que encuentre. "
    "Usa solo lo que devuelva la herramienta: no inventes videos, URLs ni cifras. "
    "Si no hay datos, devuelve una lista vacia."
)

NULLABLE_STRING = {"type": ["string", "null"]}
TRENDS_SCHEMA = {
    "type": "object",
    "properties": {
        "trends": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "network": {"type": "string", "enum": ["instagram", "tiktok"]},
                    "url": {"type": "string"},
                    "views": {"type": ["integer", "null"]},
                    "outlier_score": {"type": ["number", "null"]},
                    "hook": NULLABLE_STRING,
                    "format": NULLABLE_STRING,
                    "audio": NULLABLE_STRING,
                    "effort": NULLABLE_STRING,
                },
                "required": ["title", "network", "url", "views", "outlier_score", "hook", "format", "audio", "effort"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["trends"],
    "additionalProperties": False,
}


class TrendItem(BaseModel):
    title: str
    network: Literal["instagram", "tiktok"]
    url: str
    views: int | None
    outlier_score: float | None
    hook: str | None
    format: str | None
    audio: str | None
    effort: str | None


class TrendSourceError(Exception):
    pass


def trends_enabled() -> bool:
    return os.getenv("TRENDS_ENABLED", "").lower() == "true"


def fetch_vidiq_trends(niche: str, language: str, country: str) -> list[TrendItem]:
    """Tendencias de vidIQ para un nicho, idioma y pais. Lista vacia si vidIQ no devuelve nada."""
    if not client or not TRENDS_MCP_VIDIQ_URL or not TRENDS_MCP_VIDIQ_TOKEN:
        raise TrendSourceError("Falta ANTHROPIC_API_KEY o la configuracion del MCP de vidIQ")

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["mcp-client-2025-11-20", "server-side-fallback-2026-07-01"],
            fallbacks="default",
            mcp_servers=[
                {
                    "type": "url",
                    "url": TRENDS_MCP_VIDIQ_URL,
                    "name": "vidiq",
                    "authorization_token": TRENDS_MCP_VIDIQ_TOKEN,
                }
            ],
            tools=[
                {
                    "type": "mcp_toolset",
                    "mcp_server_name": "vidiq",
                    "default_config": {"enabled": False},
                    "configs": {VIDIQ_TOOL: {"enabled": True}},
                }
            ],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": TRENDS_SCHEMA}},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Nicho: {niche}. Idioma: {language}. Pais: {country}."}],
        )
    except anthropic.RateLimitError as e:
        raise TrendSourceError(f"Limite de la API de Claude: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise TrendSourceError(f"La API de Claude respondio {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise TrendSourceError("No se pudo conectar con la API de Claude") from e

    if response.stop_reason != "end_turn":
        raise TrendSourceError(f"Respuesta incompleta de Claude (stop_reason={response.stop_reason})")

    texts = [block.text for block in response.content if block.type == "text"]
    try:
        trends = [TrendItem.model_validate(item) for item in json.loads(texts[-1])["trends"]]
    except (IndexError, KeyError, json.JSONDecodeError, ValidationError) as e:
        raise TrendSourceError("La respuesta de Claude no tiene el formato esperado") from e

    logger.info(
        "trends_fetch niche=%s language=%s country=%s trends=%d input_tokens=%d output_tokens=%d",
        niche, language, country, len(trends), response.usage.input_tokens, response.usage.output_tokens,
    )
    return [trend for trend in trends if trend.url.startswith("http")]


def ingest_trends(session: Session) -> dict:
    """Guarda el snapshot del dia de cada (nicho, idioma, pais) con negocios activos."""
    result = {"enabled": trends_enabled(), "created": 0, "skipped": 0, "failed": 0}
    if not result["enabled"]:
        return result

    today = datetime.now(BUSINESS_TIMEZONE).date()
    combinations = session.exec(
        select(ContentPreferences.niche, ContentPreferences.language, ContentPreferences.country)
        .where(ContentPreferences.enabled)
        .distinct()
    ).all()

    for niche, language, country in combinations:
        already_captured = session.exec(
            select(TrendSnapshot.id).where(
                TrendSnapshot.niche == niche,
                TrendSnapshot.language == language,
                TrendSnapshot.country == country,
                TrendSnapshot.source == SOURCE,
                TrendSnapshot.day == today,
            )
        ).first()
        if already_captured:
            result["skipped"] += 1
            continue

        try:
            trends = fetch_vidiq_trends(niche, language, country)
        except TrendSourceError:
            logger.exception("trends_fetch_failed niche=%s language=%s country=%s", niche, language, country)
            result["failed"] += 1
            continue

        session.add(
            TrendSnapshot(
                niche=niche,
                language=language,
                country=country,
                source=SOURCE,
                day=today,
                payload=json.dumps([trend.model_dump() for trend in trends]),
            )
        )
        session.commit()
        result["created"] += 1

    return result
