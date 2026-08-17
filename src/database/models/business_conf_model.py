from datetime import datetime

from sqlmodel import Field, SQLModel


class BusinessConfigurationBase(SQLModel):
    business_name: str
    business_phone: str | None = None
    business_email: str | None = None
    commission_product: float | None = Field(default=None, ge=0, le=100)
    commission_service: float | None = Field(default=None, ge=0, le=100)


class BusinessConfiguration(BusinessConfigurationBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BusinessConfigurationPublic(BusinessConfigurationBase):
    id: int


class BusinessConfigurationUpdate(BusinessConfigurationBase):
    business_name: str | None = None
    business_phone: str | None = None
    business_email: str | None = None
    commission_product: float | None = Field(default=None, ge=0, le=100)
    commission_service: float | None = Field(default=None, ge=0, le=100)
