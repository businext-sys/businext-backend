from datetime import datetime

from sqlmodel import Field, SQLModel


class GoogleReviewBase(SQLModel):
    review_id: str = Field(unique=True)
    author_title: str | None = None
    author_image: str | None = None
    review_text: str | None = None
    review_rating: int
    review_timestamp: int
    review_datetime_utc: str | None = None
    review_link: str | None = None
    owner_answer: str | None = None
    owner_answer_timestamp: int | None = None


class GoogleReview(GoogleReviewBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    profile_id: int = Field(foreign_key="googlebusinessprofile.id", nullable=False, index=True)
    ai_generated_response: str | None = None
    ai_response_generated_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GoogleReviewPublic(GoogleReviewBase):
    id: int
    profile_id: int
    ai_generated_response: str | None = None
    ai_response_generated_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GoogleReviewUpdate(SQLModel):
    ai_generated_response: str | None = None
    ai_response_generated_at: datetime | None = None
