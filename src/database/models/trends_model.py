from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from sqlmodel import Field, SQLModel, UniqueConstraint

Network = Literal["instagram", "tiktok"]

DEFAULT_NETWORKS = '["instagram", "tiktok"]'


class TrendSnapshot(SQLModel, table=True):
    """
    Tendencias de un nicho, idioma y pais capturadas de una fuente en un dia
    (businext-sys/businext#161). La ingesta diaria escribe una fila por
    combinacion y el analisis de cada negocio lee su `summary`.
    """

    __table_args__ = (
        UniqueConstraint("niche", "language", "country", "source", "day", name="uq_trend_snapshot_day"),
    )

    id: int | None = Field(default=None, primary_key=True)
    niche: str = Field(nullable=False)
    language: str = Field(nullable=False)
    country: str = Field(nullable=False)
    source: str = Field(nullable=False)  # p. ej. "vidiq"
    day: date
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    payload: str  # JSON string: lista de TrendItem
    summary: str | None = None


class TrendInsight(SQLModel, table=True):
    """Analisis diario de un negocio: que tendencias encajan con sus datos y por que."""

    __table_args__ = (UniqueConstraint("business_id", "day", name="uq_trend_insight_business_day"),)

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    day: date
    trends_used: str  # JSON string: ids de las tendencias usadas
    analysis: str
    matched_services: str  # JSON string
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContentSuggestion(SQLModel, table=True):
    """
    Idea de publicacion que se avisa al negocio en `publish_at`.

    `format`: reel, short, carrusel o story. `status`: pending, sent, opened,
    dismissed o published.
    """

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    insight_id: int = Field(index=True, nullable=False)
    network: str = Field(nullable=False)
    format: str = Field(nullable=False)
    idea: str
    caption_hint: str
    audio_hint: str | None = None
    publish_at: datetime = Field(index=True)
    source_trend_ids: str  # JSON string
    status: str = Field(default="pending", nullable=False, index=True)
    sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContentPreferences(SQLModel, table=True):
    """
    Preferencias del radar de tendencias de un negocio. Sin fila, valen los
    valores por defecto (desactivado, barberia en espanol de Espana).
    """

    business_id: str = Field(primary_key=True)
    enabled: bool = Field(default=False, nullable=False)
    networks: str = Field(default=DEFAULT_NETWORKS, nullable=False)  # JSON string
    max_per_day: int = Field(default=1, nullable=False)
    quiet_from: time = Field(default=time(21, 0), nullable=False)
    quiet_to: time = Field(default=time(9, 0), nullable=False)
    timezone: str = Field(default="Europe/Madrid", nullable=False)
    niche: str = Field(default="barberia", nullable=False)
    language: str = Field(default="es", nullable=False)
    country: str = Field(default="ES", nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ContentPreferencesPublic(SQLModel):
    enabled: bool
    networks: list[Network]
    max_per_day: int = Field(ge=1, le=10)
    quiet_from: time
    quiet_to: time
    timezone: str
    niche: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=5)
    country: str = Field(min_length=2, max_length=2)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone")
        return value
