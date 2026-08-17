from datetime import datetime

from sqlmodel import Field, SQLModel


class PushToken(SQLModel, table=True):
    """
    Expo push token for one device, owned by a user.

    A user may have several tokens (one per device), but a token is never
    shared between users: re-registering reassigns it.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    token: str = Field(nullable=False, unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PushTokenCreate(SQLModel):
    token: str


class PushTokenPublic(SQLModel):
    id: int
    user_id: str
    token: str
    created_at: datetime
