from datetime import datetime

from sqlmodel import Field, SQLModel


class LocationBase(SQLModel):
    name: str
    address: str | None = None
    phone: str | None = None
    maps_link: str | None = None


class Location(LocationBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(
        index=True,
        nullable=False,
        foreign_key="businessconfiguration.business_id",
    )
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LocationPublic(LocationBase):
    id: int
    business_id: str
    is_active: bool
    created_at: datetime


class LocationCreate(SQLModel):
    name: str
    address: str | None = None
    phone: str | None = None
    maps_link: str | None = None


class LocationUpdate(SQLModel):
    name: str | None = None
    address: str | None = None
    phone: str | None = None
    maps_link: str | None = None
    is_active: bool | None = None
