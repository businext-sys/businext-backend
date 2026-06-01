from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


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
    product_id: Optional[int] = Field(default=None, foreign_key="product.id")
    customer_name: Optional[str] = Field(default=None)
    commission_rate: Optional[float] = Field(default=0)
    commission_amount: Optional[float] = Field(default=0)


class FinancesPublic(FinancesBase):
    id: int
    created_at: datetime
    reservation_id: Optional[int]
    product_id: Optional[int] = None
    customer_name: Optional[str] = None
    commission_rate: Optional[float] = 0
    commission_amount: Optional[float] = 0


class FinancesUpdate(FinancesBase):
    concept: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    creator: Optional[str] = None
    product_id: Optional[int] = None
    customer_name: Optional[str] = None
