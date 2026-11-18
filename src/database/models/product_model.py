from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime


class ProductBase(SQLModel):
    name: str
    price: float = Field(ge=0)
    type: Optional[str] = None
    image_url: Optional[str] = None
    seller: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=30, ge=5, le=480)


class Product(ProductBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProductPublic(ProductBase):
    id: int


class ProductUpdate(ProductBase):
    name: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    type: Optional[str] = None
    image_url: Optional[str] = None
    seller: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=480)
