from datetime import datetime

from sqlmodel import Field, SQLModel


class ReservationBase(SQLModel):
    customer_name: str
    in_charge: str | None
    reservation_start_date: datetime = Field(index=True)
    reservation_end_date: datetime
    time_per_reservation: int  # in minutes
    status: str
    service: str


class Reservation(ReservationBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class ReservationPublic(ReservationBase):
    id: int


class ReservationUpdate(ReservationBase):
    customer_name: str | None = None
    in_charge: str | None = None
    reservation_start_date: datetime | None = None
    reservation_end_date: datetime | None = None
    time_per_reservation: int  # in minutes
    status: str | None = None
    service: str | None = None
