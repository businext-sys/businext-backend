from typing import Optional
from datetime import datetime, timedelta
from uuid import uuid4
from sqlmodel import Field, SQLModel


class BookingRequestBase(SQLModel):
    client_name: str
    client_email: str
    client_phone: str
    employee_name: Optional[str] = None
    service: str
    requested_date: datetime
    proposed_date: Optional[datetime] = None
    status: str = Field(default="REQUESTED")


class BookingRequest(BookingRequestBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    response_token: str = Field(
        default_factory=lambda: str(uuid4()), index=True, unique=True
    )
    responded_at: Optional[datetime] = None
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(hours=48)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BookingRequestPublic(BookingRequestBase):
    id: int
    business_id: str
    responded_at: Optional[datetime] = None
    expires_at: datetime
    created_at: datetime


class BookingRequestCreate(SQLModel):
    client_name: str
    client_email: str
    client_phone: str
    employee_name: Optional[str] = None
    service: str
    requested_date: datetime
