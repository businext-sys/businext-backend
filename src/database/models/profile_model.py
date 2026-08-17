from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Profile(SQLModel, table=True):
    """
    Maps to public.profile — auto-populated by a Supabase trigger on auth.users.
    Do NOT write to this table manually; let the trigger keep it in sync.
    See supabase_trigger.sql at the repo root for trigger setup.
    """

    id: str = Field(primary_key=True)  # UUID matching auth.users.id
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    provider_type: str | None = None
    providers: str | None = None  # JSON array stored as text
    created_at: datetime | None = None
    last_sign_in_at: datetime | None = None
    updated_at: datetime | None = None
    status: str = Field(default="pending")  # "pending" | "onboarded"
    tour_state: dict[str, Any] = Field(
        default_factory=dict,
        # JSONB on Postgres, plain JSON elsewhere (SQLite in tests).
        sa_column=Column(
            JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
            server_default="{}",
        ),
    )


class ProfilePublic(SQLModel):
    id: str
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    provider_type: str | None = None
    status: str


class ProfileUpdate(SQLModel):
    display_name: str | None = None
    phone: str | None = None
    status: str | None = None
