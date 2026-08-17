from datetime import datetime

from sqlmodel import Field, SQLModel


class Subscription(SQLModel, table=True):
    """Maps to public.subscriptions — managed by Stripe webhooks."""

    __tablename__ = "subscriptions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    stripe_subscription_id: str = Field(nullable=False)
    status: str = Field(default="inactive")  # "active" | "canceled" | "past_due" | etc.
    updated_at: datetime | None = None
