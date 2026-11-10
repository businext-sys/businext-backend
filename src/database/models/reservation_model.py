from typing import Optional
from pydantic import EmailStr, field_validator
from sqlmodel import Field, SQLModel
from datetime import datetime


def clean_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    return "".join(phone.split()) or None


class ReservationBase(SQLModel):
    customer_name: str
    in_charge: str | None
    reservation_start_date: datetime = Field(index=True)
    reservation_end_date: datetime
    time_per_reservation: int  # in minutes
    status: str
    service: str
    customer_phone: str | None = None
    customer_email: EmailStr | None = None

    @field_validator("customer_phone")
    @classmethod
    def _clean_phone(cls, value: str | None) -> str | None:
        return clean_phone(value)


class Reservation(ReservationBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    reminder_sent_at: datetime | None = None


class ReservationPublic(ReservationBase):
    id: int
    # Solicitudes antiguas pueden traer emails no válidos: se validan al entrar, no al leer.
    customer_email: str | None = None


class ReservationUpdate(ReservationBase):
    customer_name: Optional[str] = None
    in_charge: Optional[str] = None
    reservation_start_date: Optional[datetime] = None
    reservation_end_date: Optional[datetime] = None
    time_per_reservation: int  # in minutes
    status: Optional[str] = None
    service: Optional[str] = None
