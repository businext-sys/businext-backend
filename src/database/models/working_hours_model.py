from typing import Optional
from sqlmodel import Field, SQLModel, UniqueConstraint
from datetime import datetime


class WorkingHoursBase(SQLModel):
    day_of_week: int  # 0=Monday, 1=Tuesday, ..., 6=Sunday
    start_time: str  # HH:MM format, e.g. "09:00"
    end_time: str  # HH:MM format, e.g. "18:00"
    enabled: bool = True
    member_user_id: Optional[str] = None  # If null, applies to all employees (business-wide)


class WorkingHours(WorkingHoursBase, table=True):
    __table_args__ = (
        UniqueConstraint(
            "business_id", "day_of_week", "member_user_id", "start_time",
            name="uq_business_day_member_start",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkingHoursPublic(SQLModel):
    id: int
    day_of_week: int
    start_time: str
    end_time: str
    enabled: bool
    member_user_id: Optional[str] = None


class WorkingHoursUpdate(SQLModel):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    enabled: Optional[bool] = None
    member_user_id: Optional[str] = None


class WorkingHoursInput(SQLModel):
    """Input schema for creating/updating working hours blocks."""
    day_of_week: int
    start_time: str
    end_time: str
    enabled: bool = True
