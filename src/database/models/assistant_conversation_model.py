from datetime import datetime

from sqlmodel import Field, SQLModel


class AssistantConversation(SQLModel, table=True):
    """Conversacion del asistente de la app movil (businext-backend#24)."""

    id: str = Field(primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    user_id: str = Field(index=True, nullable=False)
    title: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class AssistantMessage(SQLModel, table=True):
    """Mensaje de una conversacion; `blocks` es la lista de bloques del contrato en JSON."""

    id: str = Field(primary_key=True)
    conversation_id: str = Field(foreign_key="assistantconversation.id", index=True, nullable=False)
    role: str
    blocks: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
