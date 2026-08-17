from datetime import datetime

from sqlmodel import Field, SQLModel


class ProductBase(SQLModel):
    name: str
    price: float = Field(ge=0)
    type: str | None = None
    image_url: str | None = None
    seller: str | None = None


class Product(ProductBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ProductPublic(ProductBase):
    id: int


class ProductUpdate(ProductBase):
    name: str | None = None
    price: float | None = Field(default=None, ge=0)
    type: str | None = None
    image_url: str | None = None
    seller: str | None = None
