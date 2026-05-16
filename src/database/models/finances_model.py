from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime


class FinancesBase(SQLModel):
    concept: str
    amount: float
    type: str
    creator: str
    reservation_id: Optional[int] = None
    customer_name: Optional[str] = None
    product_id: Optional[int] = None


class Finances(FinancesBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reservation_id: Optional[int] = Field(default=None, foreign_key="reservation.id")
    customer_name: Optional[str] = Field(default=None)
    product_id: Optional[int] = Field(default=None, foreign_key="product.id")


class FinancesPublic(FinancesBase):
    id: int
    created_at: datetime
    reservation_id: Optional[int] = None
    customer_name: Optional[str] = None
    product_id: Optional[int] = None


class FinancesUpdate(FinancesBase):
    concept: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    creator: Optional[str] = None
    customer_name: Optional[str] = None
    product_id: Optional[int] = None
