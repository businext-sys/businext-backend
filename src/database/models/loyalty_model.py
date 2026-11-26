"""
Tarjeta de fidelidad (sellos) y pases de Apple Wallet. Ver docs/adr/0003.

Los datos viven aqui; el pase de Wallet es solo una vista de `LoyaltyCard`
que se regenera cuando cambia `updated_at`.
"""

import re
import secrets
from datetime import datetime
from uuid import uuid4

from pydantic import field_validator
from sqlmodel import Field, SQLModel, UniqueConstraint

DEFAULT_STAMPS_REQUIRED = 10
DEFAULT_REWARD = "Corte gratis"
HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class LoyaltyProgramSettings(SQLModel):
    enabled: bool = False
    stamps_required: int = Field(default=DEFAULT_STAMPS_REQUIRED, ge=1, le=50)
    reward: str = Field(default=DEFAULT_REWARD, min_length=1, max_length=40)
    # Colores del pase; sin ellos, los de Businext (DESIGN.md).
    background_color: str | None = None
    foreground_color: str | None = None
    label_color: str | None = None

    @field_validator("background_color", "foreground_color", "label_color")
    @classmethod
    def _hex_color(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(HEX_COLOR, value):
            raise ValueError("Usa un color #RRGGBB")
        return value


class LoyaltyProgram(LoyaltyProgramSettings, table=True):
    """Regla de la tarjeta de un negocio. Sin fila, el programa esta desactivado."""

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, unique=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LoyaltyCard(SQLModel, table=True):
    """
    Tarjeta de un cliente en un negocio, identificado por su email en minusculas
    (el dato que pide la reserva publica). `serial_number` y `auth_token` son los
    del pase de Wallet; `updated_at` es la marca que compara `passesUpdatedSince`.
    """

    __table_args__ = (UniqueConstraint("business_id", "customer_email"),)

    id: int | None = Field(default=None, primary_key=True)
    business_id: str = Field(index=True, nullable=False)
    customer_email: str = Field(nullable=False)
    customer_name: str = Field(nullable=False)
    serial_number: str = Field(default_factory=lambda: uuid4().hex, unique=True, index=True)
    auth_token: str = Field(default_factory=lambda: secrets.token_urlsafe(24), nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class LoyaltyStamp(SQLModel, table=True):
    """Un sello por reserva completada: `reservation_id` unico hace idempotente el sellado."""

    id: int | None = Field(default=None, primary_key=True)
    card_id: int = Field(foreign_key="loyaltycard.id", index=True, nullable=False)
    reservation_id: int = Field(unique=True, nullable=False)
    visit_date: datetime = Field(nullable=False)
    service: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    redeemed_at: datetime | None = None


class WalletRegistration(SQLModel, table=True):
    """Dispositivo que tiene un pase de Apple Wallet y recibe sus pushes (protocolo PassKit)."""

    __table_args__ = (
        UniqueConstraint("device_library_identifier", "pass_type_identifier", "serial_number"),
    )

    id: int | None = Field(default=None, primary_key=True)
    device_library_identifier: str = Field(index=True, nullable=False)
    pass_type_identifier: str = Field(nullable=False)
    serial_number: str = Field(index=True, nullable=False)
    push_token: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LoyaltyProgramPublic(LoyaltyProgramSettings):
    apple_wallet_available: bool


class LoyaltyVisit(SQLModel):
    visit_date: datetime
    service: str
    redeemed: bool


class LoyaltyCardPublic(SQLModel):
    serial_number: str
    customer_name: str
    customer_email: str
    stamps: int
    stamps_required: int
    reward: str
    reward_ready: bool
    visits: list[LoyaltyVisit]
    pass_url: str | None


class LoyaltyCardCreate(SQLModel):
    customer_name: str = Field(min_length=1, max_length=120)
    customer_email: str = Field(min_length=3, max_length=254)
