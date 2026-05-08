from typing import Optional
from sqlmodel import Field, SQLModel, UniqueConstraint
from datetime import datetime


class WorkingHoursBase(SQLModel):
    day_of_week: int  # 0=Monday, 1=Tuesday, ..., 6=Sunday
    start_time: str  # HH:MM format, e.g. "09:00"
    end_time: str  # HH:MM format, e.g. "18:00"
    enabled: bool = True
    employee_name: Optional[str] = None  # If null, applies to all employees


class WorkingHours(WorkingHoursBase, table=True):
    __table_args__ = (
        UniqueConstraint(
            "business_id", "day_of_week", "employee_name",
            name="uq_business_day_employee",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkingHoursPublic(WorkingHoursBase):
    id: int


class WorkingHoursUpdate(WorkingHoursBase):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    enabled: Optional[bool] = None
    employee_name: Optional[str] = None
