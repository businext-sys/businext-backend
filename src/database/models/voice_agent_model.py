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


CallStatus = Literal["rejected", "in_progress", "ended"]


class VoiceCall(SQLModel, table=True):
    """
    Llamada atendida (o rechazada) por el agente de voz de un negocio. La
    crean el webhook de llamada entrante y los eventos de Retell
    (`call_started`, `call_ended`, `call_analyzed`), y `book_appointment`
    le apunta la reserva que haya hecho. Es lo que ve el owner en la pestaña
    del agente de voz.

    `status`: "rejected" (el agente estaba desactivado), "in_progress" o
    "ended". `booking_mode` + `booking_id`: la solicitud ("request") o la
    reserva ("confirm") que creo la llamada, si creo alguna.
    """

    id: int | None = Field(default=None, primary_key=True)
    call_id: str = Field(nullable=False, unique=True, index=True)  # id de Retell
    business_id: str = Field(index=True, nullable=False)
    from_number: str | None = None
    to_number: str | None = None
    status: str = Field(default="in_progress", nullable=False)
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    disconnection_reason: str | None = None
    summary: str | None = None
    sentiment: str | None = None  # Positive / Neutral / Negative / Unknown (Retell)
    successful: bool | None = None
    transcript: str | None = None
    recording_url: str | None = None
    booking_mode: str | None = None
    booking_id: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class VoiceCallPublic(SQLModel):
    id: int
    from_number: str | None
    status: CallStatus
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    disconnection_reason: str | None
    summary: str | None
    sentiment: str | None
    successful: bool | None
    booking_mode: BookingMode | None
    booking_id: int | None


class VoiceCallDetail(VoiceCallPublic):
    transcript: str | None
    recording_url: str | None


class VoiceCallPage(SQLModel):
    items: list[VoiceCallPublic]
    total: int


class VoiceCallDay(SQLModel):
    date: str  # YYYY-MM-DD, hora de Madrid
    calls: int
    bookings: int


class VoiceCallStats(SQLModel):
    days: int
    total_calls: int
    answered_calls: int
    rejected_calls: int
    bookings: int
    booking_rate: float  # reservas / llamadas atendidas, 0..1
    total_minutes: float
    avg_duration_seconds: int
    positive_calls: int
    negative_calls: int
    daily: list[VoiceCallDay]
