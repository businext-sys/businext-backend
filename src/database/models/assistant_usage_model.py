from datetime import date

from sqlmodel import Field, SQLModel, UniqueConstraint


class AssistantUsage(SQLModel, table=True):
    """Tokens y coste del asistente por negocio y dia (businext-backend#25)."""

    __table_args__ = (UniqueConstraint("business_id", "day", name="uq_assistant_usage_business_day"),)

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    day: date = Field(index=True, nullable=False)
    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0
