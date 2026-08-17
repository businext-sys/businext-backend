from datetime import datetime

from sqlmodel import Field, SQLModel


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
    location_id: int | None = Field(default=None, foreign_key="location.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BusinessMemberPublic(SQLModel):
    id: int
    business_id: str
    member_user_id: str
    role: str
    status: str
    location_id: int | None = None
    created_at: datetime


class BusinessMemberCreate(SQLModel):
    member_user_id: str
    role: str = "employee"


class BusinessMemberUpdate(SQLModel):
    role: str | None = None
    status: str | None = None
    location_id: int | None = None
