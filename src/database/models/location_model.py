from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime


class LocationBase(SQLModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    maps_link: Optional[str] = None
    # Para que iOS sugiera el pase de fidelidad cerca del local (docs/adr/0003).
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)


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
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)


class LocationUpdate(SQLModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    maps_link: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    is_active: Optional[bool] = None
