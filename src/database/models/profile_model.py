from typing import Any, Optional
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel
from datetime import datetime


class Profile(SQLModel, table=True):
    """
    Maps to public.profile — auto-populated by a Supabase trigger on auth.users.
    Do NOT write to this table manually; let the trigger keep it in sync.
    See supabase_trigger.sql at the repo root for trigger setup.
    """

    id: str = Field(primary_key=True)  # UUID matching auth.users.id
    display_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    provider_type: Optional[str] = None
    providers: Optional[str] = None  # JSON array stored as text
    created_at: Optional[datetime] = None
    last_sign_in_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    status: str = Field(default="pending")  # "pending" | "onboarded"
    tour_state: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )


class ProfilePublic(SQLModel):
    id: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    provider_type: Optional[str] = None
    status: str


class ProfileUpdate(SQLModel):
    display_name: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None
