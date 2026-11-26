"""
Tarjeta de fidelidad: un sello por reserva completada. Ver docs/adr/0003.

El cliente es su email en minusculas (el de la reserva o, si no lo tiene, el
mas reciente de sus reservas con el mismo nombre). Sin email no hay tarjeta.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, func, select

from src.database.database import get_engine
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.loyalty_model import (
    LoyaltyCard,
    LoyaltyProgram,
    LoyaltyStamp,
    WalletRegistration,
)
from src.database.models.reservation_model import Reservation
from src.services import apple_wallet
from src.services.customer_service import known_contact
from src.services.email_service import APP_URL
from src.services.trend_sources import BUSINESS_TIMEZONE

logger = logging.getLogger(__name__)

CARD_VISITS = 20


def normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


def _now() -> datetime:
    return datetime.utcnow()


def get_program(session: Session, business_id: str) -> LoyaltyProgram | None:
    return session.exec(select(LoyaltyProgram).where(LoyaltyProgram.business_id == business_id)).first()


def find_card(session: Session, business_id: str, email: str | None) -> LoyaltyCard | None:
    email = normalize_email(email)
    if not email:
        return None
    return session.exec(
        select(LoyaltyCard).where(LoyaltyCard.business_id == business_id, LoyaltyCard.customer_email == email)
    ).first()


def get_or_create_card(session: Session, business_id: str, email: str, name: str) -> LoyaltyCard:
    """Sin commit si ya existe; si no, la crea y hace commit (unica por negocio y email)."""
    card = find_card(session, business_id, email)
    if card:
        return card
    card = LoyaltyCard(business_id=business_id, customer_email=normalize_email(email), customer_name=name.strip())
    session.add(card)
    try:
        session.commit()
    except IntegrityError:  # otra peticion la creo a la vez
        session.rollback()
        return find_card(session, business_id, email)  # type: ignore[return-value]
    session.refresh(card)
    return card


def current_stamps(session: Session, card_id: int) -> int:
    return session.exec(
        select(func.count()).select_from(LoyaltyStamp).where(
            LoyaltyStamp.card_id == card_id, LoyaltyStamp.redeemed_at.is_(None)  # type: ignore[union-attr]
        )
    ).one()


def card_stamps(session: Session, card_id: int, limit: int = CARD_VISITS) -> list[LoyaltyStamp]:
    return list(session.exec(
        select(LoyaltyStamp).where(LoyaltyStamp.card_id == card_id)
        .order_by(col(LoyaltyStamp.visit_date).desc()).limit(limit)
    ).all())


def touch(card: LoyaltyCard) -> None:
    """
    Marca la tarjeta como cambiada (`passesUpdatedSince`, `Last-Modified`). Siempre
    avanza al menos un segundo entero: las cabeceras HTTP no tienen mas precision y
    dos cambios en el mismo segundo no pueden compartir marca.
    """
    now = _now()
    next_second = now.replace(microsecond=0) + timedelta(seconds=1 if now.microsecond else 0)
    previous = card.updated_at.replace(microsecond=0) if card.updated_at else None
    card.updated_at = next_second if previous is None or next_second > previous else previous + timedelta(seconds=1)


def award_stamp(session: Session, card: LoyaltyCard, reservation: Reservation) -> bool:
    """Sella la reserva si no lo estaba. Idempotente: True solo si anade un sello."""
    exists = session.exec(select(LoyaltyStamp.id).where(LoyaltyStamp.reservation_id == reservation.id)).first()
    if exists:
        return False
    session.add(LoyaltyStamp(
        card_id=card.id,
        reservation_id=reservation.id,
        visit_date=reservation.reservation_start_date,
        service=reservation.service,
    ))
    try:
        session.flush()
    except IntegrityError:  # sellada en paralelo
        session.rollback()
        return False
    return True


def remove_stamp(session: Session, reservation_id: int) -> LoyaltyCard | None:
    """Quita el sello de una reserva que deja de estar completada, si aun no se canjeo."""
    stamp = session.exec(select(LoyaltyStamp).where(LoyaltyStamp.reservation_id == reservation_id)).first()
    if not stamp:
        return None
    if stamp.redeemed_at is not None:
        logger.info("reserva %s revertida con su sello ya canjeado: se mantiene", reservation_id)
        return None
    card = session.get(LoyaltyCard, stamp.card_id)
    session.delete(stamp)
    return card


def redeem(session: Session, card: LoyaltyCard, stamps_required: int) -> bool:
    """Canjea el premio gastando los `stamps_required` sellos mas antiguos. False si no llegan."""
    stamps = session.exec(
        select(LoyaltyStamp)
        .where(LoyaltyStamp.card_id == card.id, LoyaltyStamp.redeemed_at.is_(None))  # type: ignore[union-attr]
        .order_by(col(LoyaltyStamp.visit_date).asc())
        .limit(stamps_required)
    ).all()
    if len(stamps) < stamps_required:
        return False
    now = _now()
    for stamp in stamps:
        stamp.redeemed_at = now
        session.add(stamp)
    touch(card)
    session.add(card)
    session.commit()
    notify_devices(session, [card])
    return True


def _reservation_email(session: Session, reservation: Reservation) -> str | None:
    email = normalize_email(reservation.customer_email)
    if email:
        return email
    _, known = known_contact(session, reservation.business_id, reservation.customer_name)
    return normalize_email(known)


def sync_reservation(session: Session, reservation: Reservation) -> None:
    """
    Llamar despues de crear, cambiar, borrar o revertir una reserva (ya con commit).
    Completada → sello (idempotente); deja de estarlo → se quita el sello. En
    todo caso la tarjeta del cliente se marca cambiada (puede cambiar su
    proxima cita) y se avisa a sus dispositivos. Nunca falla.
    """
    try:
        cards: list[LoyaltyCard] = []
        if reservation.status != "COMPLETED" and reservation.id is not None:
            reverted = remove_stamp(session, reservation.id)
            if reverted:
                cards.append(reverted)

        program = get_program(session, reservation.business_id)
        if program and program.enabled:
            email = _reservation_email(session, reservation)
            card = find_card(session, reservation.business_id, email)
            if reservation.status == "COMPLETED" and email:
                card = card or get_or_create_card(session, reservation.business_id, email, reservation.customer_name)
                award_stamp(session, card, reservation)
            if card and card not in cards:
                cards.append(card)

        if not cards:
            session.commit()
            return
        for card in cards:
            touch(card)
            session.add(card)
        session.commit()
        notify_devices(session, cards)
    except Exception:
        session.rollback()
        logger.exception("loyalty: no se pudo sincronizar la reserva %s", reservation.id)


def touch_all_cards(session: Session, business_id: str) -> None:
    """La regla o los colores cambiaron: todos los pases del negocio se regeneran."""
    cards = session.exec(select(LoyaltyCard).where(LoyaltyCard.business_id == business_id)).all()
    for card in cards:
        touch(card)
        session.add(card)
    session.commit()
    notify_devices(session, list(cards))


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------

def _delete_dead_tokens(tokens: list[str]) -> None:
    with Session(get_engine()) as session:
        session.exec(delete(WalletRegistration).where(col(WalletRegistration.push_token).in_(tokens)))  # type: ignore[call-overload]
        session.commit()


def notify_devices(session: Session, cards: list[LoyaltyCard]) -> None:
    serials = [c.serial_number for c in cards]
    if not serials:
        return
    tokens = session.exec(
        select(WalletRegistration.push_token).where(col(WalletRegistration.serial_number).in_(serials))
    ).all()
    apple_wallet.push_updates(list(tokens), on_dead_tokens=_delete_dead_tokens)


def pass_url(card: LoyaltyCard) -> str | None:
    """Enlace de descarga del `.pkpass`, o None si Wallet no esta configurado."""
    try:
        config = apple_wallet.wallet_config()
    except apple_wallet.WalletNotConfigured:
        return None
    return f"{config.public_api_url}/public/loyalty/passes/{card.serial_number}.pkpass?token={card.auth_token}"


def booking_url(business_id: str) -> str:
    return f"{APP_URL}/book/{business_id}"


def pass_data(session: Session, card: LoyaltyCard, program: LoyaltyProgram | None) -> apple_wallet.PassData:
    business = session.exec(
        select(BusinessConfiguration).where(BusinessConfiguration.business_id == card.business_id)
    ).first()
    locations = session.exec(
        select(Location)
        .where(Location.business_id == card.business_id, Location.is_active == True)  # noqa: E712
        .order_by(col(Location.created_at).asc())
    ).all()
    now = datetime.now(BUSINESS_TIMEZONE).replace(tzinfo=None)
    next_reservation = session.exec(
        select(Reservation)
        .where(
            Reservation.business_id == card.business_id,
            func.lower(Reservation.customer_email) == card.customer_email,
            Reservation.status == "PENDING",
            Reservation.reservation_start_date >= now,
        )
        .order_by(col(Reservation.reservation_start_date).asc())
    ).first()
    coordinates = [
        apple_wallet.PassLocation(loc.latitude, loc.longitude)
        for loc in locations if loc.latitude is not None and loc.longitude is not None
    ]
    first = locations[0] if locations else None
    return apple_wallet.PassData(
        serial_number=card.serial_number,
        auth_token=card.auth_token,
        business_name=business.business_name if business else "Businext",
        customer_name=card.customer_name,
        stamps=current_stamps(session, card.id),  # type: ignore[arg-type]
        stamps_required=program.stamps_required if program else 10,
        reward=program.reward if program else "",
        next_appointment=next_reservation.reservation_start_date if next_reservation else None,
        last_visits=[(s.visit_date, s.service) for s in card_stamps(session, card.id, apple_wallet.LAST_VISITS)],  # type: ignore[arg-type]
        booking_url=booking_url(card.business_id),
        directions_url=apple_wallet.directions_url(
            first.address if first else None,
            first.maps_link if first else None,
            coordinates[0] if coordinates else None,
        ),
        locations=coordinates,
        background_color=program.background_color if program else None,
        foreground_color=program.foreground_color if program else None,
        label_color=program.label_color if program else None,
    )


def render_pass(session: Session, card: LoyaltyCard, config: apple_wallet.WalletConfig) -> bytes:
    data = pass_data(session, card, get_program(session, card.business_id))
    return apple_wallet.package_pass(apple_wallet.build_pass_json(data, config), config)
