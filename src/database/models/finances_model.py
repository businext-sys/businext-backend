from datetime import datetime

from sqlmodel import Field, SQLModel


class FinancesBase(SQLModel):
    concept: str
    amount: float
    type: str
    creator: str
    reservation_id: int | None = None
    customer_name: str | None = None
    product_id: int | None = None


class Finances(FinancesBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reservation_id: int | None = Field(default=None, foreign_key="reservation.id")
    product_id: int | None = Field(default=None, foreign_key="product.id")
    customer_name: str | None = Field(default=None)
    commission_rate: float | None = Field(default=0)
    commission_amount: float | None = Field(default=0)


class FinancesPublic(FinancesBase):
    id: int
    created_at: datetime
    reservation_id: int | None
    product_id: int | None = None
    customer_name: str | None = None
    commission_rate: float | None = 0
    commission_amount: float | None = 0


class FinancesUpdate(FinancesBase):
    concept: str | None = None
    amount: float | None = None
    type: str | None = None
    creator: str | None = None
    product_id: int | None = None
    customer_name: str | None = None
