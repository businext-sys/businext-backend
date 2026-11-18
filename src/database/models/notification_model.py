from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


class NotificationPreferencesPublic(SQLModel):
    upcoming: bool = True
    upcoming_minutes: int = Field(default=15, ge=5, le=120)
    daily_summary: bool = True
    daily_summary_hour: int = Field(default=9, ge=0, le=23)
    reservation_changes: bool = True
    day_close: bool = True


class NotificationPreferences(SQLModel, table=True):
    """Avisos push al equipo que quiere recibir cada usuario (sin fila: todos, con los valores por defecto)."""

    user_id: str = Field(primary_key=True)
    upcoming: bool = True
    upcoming_minutes: int = 15
    daily_summary: bool = True
    daily_summary_hour: int = 9
    reservation_changes: bool = True
    day_close: bool = True
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationLog(SQLModel, table=True):
    """Un aviso programado ya enviado: la fila unica evita repetirlo, tambien entre instancias."""

    __table_args__ = (UniqueConstraint("user_id", "kind", "key"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(nullable=False)
    kind: str = Field(nullable=False)
    key: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
