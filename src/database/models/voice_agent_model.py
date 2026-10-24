from datetime import datetime
from typing import Literal

from sqlmodel import Field, SQLModel

BookingMode = Literal["request", "confirm"]


class VoicePhoneNumber(SQLModel, table=True):
    """
    Numero de telefono de Retell asignado a un negocio
    (businext-sys/businext#117). El webhook de llamada entrante resuelve el
    `to_number` de la llamada a su negocio con esta tabla. Los asigna Businext
    (no hay compra de numeros desde la UI), por eso no hay endpoint para
    cambiarlos.
    """

    id: int | None = Field(default=None, primary_key=True)
    phone_number: str = Field(nullable=False, unique=True, index=True)  # E.164, p. ej. +34910000000
    business_id: str = Field(index=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class VoiceAgentConfig(SQLModel, table=True):
    """
    Configuracion del agente de voz de un negocio. Sin fila, el agente esta
    desactivado y en modo solicitud.

    `booking_mode`: "request" crea una solicitud de reserva (como la pagina
    publica); "confirm" crea la reserva directamente en la agenda.
    """

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False, unique=True)
    enabled: bool = Field(default=False, nullable=False)
    booking_mode: str = Field(default="request", nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class VoiceAgentConfigPublic(SQLModel):
    enabled: bool
    booking_mode: BookingMode
    phone_numbers: list[str]


class VoiceAgentConfigUpdate(SQLModel):
    enabled: bool | None = None
    booking_mode: BookingMode | None = None
