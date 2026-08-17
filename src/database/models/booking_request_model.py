from datetime import datetime, timedelta
from uuid import uuid4

from sqlmodel import Field, SQLModel


class BookingRequestBase(SQLModel):
    client_name: str
    client_email: str
    client_phone: str
    employee_name: str | None = None
    service: str
    requested_date: datetime
    proposed_date: datetime | None = None
    status: str = Field(default="REQUESTED")


class BookingRequest(BookingRequestBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    location_id: int | None = Field(default=None, foreign_key="location.id")
    response_token: str = Field(
        default_factory=lambda: str(uuid4()), index=True, unique=True
    )
    responded_at: datetime | None = None
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(hours=48)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BookingRequestPublic(BookingRequestBase):
    id: int
    business_id: str
    location_id: int | None = None
    responded_at: datetime | None = None
    expires_at: datetime
    created_at: datetime


class BookingRequestCreate(SQLModel):
    client_name: str
    client_email: str
    client_phone: str
    employee_name: str | None = None
    service: str
    requested_date: datetime
    location_id: int | None = None
