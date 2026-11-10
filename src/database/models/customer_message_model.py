from datetime import datetime

from pydantic import EmailStr
from sqlmodel import Field, SQLModel


class CustomerMessage(SQLModel, table=True):
    """Email enviado por el negocio a un cliente que lleva tiempo sin venir."""

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    customer_email: str = Field(index=True, nullable=False)
    message: str = Field(nullable=False)
    sent_by: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class CustomerMessageCreate(SQLModel):
    customer_email: EmailStr
    message: str = Field(min_length=1, max_length=2000)


class CustomerMessageDraft(SQLModel):
    customer_email: str
    customer_name: str
    message: str


class CustomerMessagePublic(SQLModel):
    id: int
    customer_email: str
    message: str
    sent_by: str
    created_at: datetime
