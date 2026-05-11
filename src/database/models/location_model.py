from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime


class LocationBase(SQLModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    maps_link: Optional[str] = None


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
    address: Optional[str] = None
    phone: Optional[str] = None
    maps_link: Optional[str] = None


class LocationUpdate(SQLModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    maps_link: Optional[str] = None
    is_active: Optional[bool] = None
