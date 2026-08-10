from datetime import datetime

from sqlmodel import Field, SQLModel


class PushToken(SQLModel, table=True):
    """
    Token de Expo Push Notifications de un dispositivo movil, asociado a
    un usuario (issue #031 de businext-sys/businext, Fase 5).

    Un usuario puede tener varios tokens (varios dispositivos). El mismo
    token nunca deberia repetirse para dos usuarios distintos (si un
    dispositivo se re-registra con otro usuario, se reasigna).
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
