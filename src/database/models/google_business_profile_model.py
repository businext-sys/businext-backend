from datetime import datetime

from sqlmodel import Field, SQLModel


class GoogleBusinessProfileBase(SQLModel):
    source_url: str
    google_id: str


class GoogleBusinessProfile(GoogleBusinessProfileBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    name: str | None = None
    address: str | None = None
    category: str | None = None
    phone: str | None = None
    rating: float | None = None
    total_reviews: int = Field(default=0)
    reviews_per_score: str | None = None  # JSON string: {"1": 5, "2": 3, ...}
    location_link: str | None = None
    validation_status: str = Field(default="pending")  # "pending" | "locked"
    validated_by: str | None = None
    validated_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_review_timestamp: int = Field(default=0)
    ai_summary: str | None = None  # JSON string
    ai_summary_generated_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GoogleBusinessProfilePublic(GoogleBusinessProfileBase):
    id: int
    name: str | None = None
    address: str | None = None
    category: str | None = None
    phone: str | None = None
    rating: float | None = None
    total_reviews: int = 0
    reviews_per_score: str | None = None
    location_link: str | None = None
    validation_status: str = "pending"
    validated_by: str | None = None
    validated_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_review_timestamp: int = 0
    ai_summary: str | None = None
    ai_summary_generated_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GoogleBusinessProfileUpdate(SQLModel):
    validation_status: str | None = None
    validated_by: str | None = None
    validated_at: datetime | None = None
    ai_summary: str | None = None
    ai_summary_generated_at: datetime | None = None
