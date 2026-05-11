from typing import Optional
from sqlmodel import Field, SQLModel
from datetime import datetime


class BusinessMember(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(
        index=True,
        nullable=False,
        foreign_key="businessconfiguration.business_id",
    )
    member_user_id: str = Field(index=True, nullable=False)
    role: str = Field(default="employee")  # "manager" | "employee"
    status: str = Field(default="active")  # "pending" | "active" | "inactive"
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BusinessMemberPublic(SQLModel):
    id: int
    business_id: str
    member_user_id: str
    role: str
    status: str
    location_id: Optional[int] = None
    created_at: datetime


class BusinessMemberCreate(SQLModel):
    member_user_id: str
    role: str = "employee"


class BusinessMemberUpdate(SQLModel):
    role: Optional[str] = None
    status: Optional[str] = None
    location_id: Optional[int] = None
