"""
Avisos push al equipo del negocio (businext-sys/businext#220).

- Proxima cita: al encargado (o al owner), `upcoming_minutes` antes.
- Resumen de la manana: a cada miembro a su `daily_summary_hour`, si tiene citas hoy.
- Cambios de reservas: al encargado y al owner, salvo a quien hizo el cambio.
- Cierre del dia: al owner y a los managers si quedan citas de hoy pendientes,
  30 min despues del cierre del horario general (21:00 sin horario).

Las horas son de pared de Europe/Madrid, como las reservas. Los programados
los lanza cada minuto `notifications_loop` (lifespan de main.py); cada envio
deja una fila en `notificationlog` (unica por usuario, tipo y clave) antes de
salir, asi que nunca se repite aunque corran varias instancias.
"""

import asyncio
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from src.database.database import get_engine
from src.database.models.member_model import BusinessMember
from src.database.models.notification_model import NotificationLog, NotificationPreferences
from src.database.models.profile_model import Profile
from src.database.models.push_token_model import PushToken
from src.database.models.reservation_model import Reservation
from src.database.models.working_hours_model import WorkingHours
from src.services.push_notification_service import send_push_notifications
from src.services.trend_sources import BUSINESS_TIMEZONE

logger = logging.getLogger(__name__)

DAY_NAMES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MAX_UPCOMING_MINUTES = 120
DAY_CLOSE_DELAY = timedelta(minutes=30)
DEFAULT_DAY_CLOSE = time(21, 0)
LOOP_SECONDS = 60
ADVISORY_LOCK_KEY = 220_001
VISIBLE_STATUSES = ("PENDING", "COMPLETED")


@dataclass
class Member:
    user_id: str
    role: str
    name: str | None


def _team(session: Session, business_id: str) -> list[Member]:
    """El owner (su user_id es el business_id) y los miembros activos, con su nombre visible."""
    rows = session.exec(
        select(BusinessMember.member_user_id, BusinessMember.role).where(
            BusinessMember.business_id == business_id, BusinessMember.status == "active"
        )
    ).all()
    roles = {business_id: "owner", **{user_id: role for user_id, role in rows}}
    names = dict(session.exec(select(Profile.id, Profile.display_name).where(Profile.id.in_(list(roles)))).all())
    return [Member(user_id, role, names.get(user_id)) for user_id, role in roles.items()]


def _in_charge(team: list[Member], business_id: str, in_charge: str | None) -> str:
    match = next((m for m in team if in_charge and m.name == in_charge), None)
    return match.user_id if match else business_id


def _tokens(session: Session, user_ids: Iterable[str]) -> dict[str, list[str]]:
    tokens: dict[str, list[str]] = {}
    for user_id, token in session.exec(
        select(PushToken.user_id, PushToken.token).where(PushToken.user_id.in_(list(user_ids)))
    ).all():
        tokens.setdefault(user_id, []).append(token)
    return tokens


def preferences(session: Session, user_id: str) -> NotificationPreferences:
    try:
        return session.get(NotificationPreferences, user_id) or NotificationPreferences(user_id=user_id)
    except SQLAlchemyError:
        session.rollback()
        return NotificationPreferences(user_id=user_id)


def _when(moment: datetime) -> str:
    return f"{DAY_NAMES[moment.weekday()]} {moment:%d/%m} a las {moment:%H:%M}"


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


# ---------------------------------------------------------------------------
# Cambios de reservas (al momento)
# ---------------------------------------------------------------------------

CHANGE_TITLES = {"created": "Nueva cita", "cancelled": "Cita cancelada", "moved": "Cita cambiada"}


def _send_in_background(tokens: list[str], title: str, body: str, data: dict) -> None:
    # La peticion que cambia la reserva no espera a Expo.
    threading.Thread(target=send_push_notifications, args=(tokens, title, body, data), daemon=True).start()


def notify_reservation_change(session: Session, reservation: Reservation, change: str, actor_user_id: str | None) -> None:
    """Avisa al encargado y al owner de una reserva creada, cancelada o cambiada. Nunca falla."""
    try:
        business_id = reservation.business_id
        team = _team(session, business_id)
        recipients = {_in_charge(team, business_id, reservation.in_charge), business_id} - {actor_user_id}
        recipients = {u for u in recipients if preferences(session, u).reservation_changes}
        tokens = [t for user_tokens in _tokens(session, recipients).values() for t in user_tokens]
        if not tokens:
            return
        start = reservation.reservation_start_date
        _send_in_background(
            tokens,
            f"{CHANGE_TITLES[change]}: {reservation.customer_name}",
            f"{reservation.service}, {_when(start)}",
            {"type": "reservation_changed", "reservation_id": reservation.id, "date": start.date().isoformat()},
        )
    except Exception:
        session.rollback()
        logger.exception("team notification for reservation change failed")


# ---------------------------------------------------------------------------
# Programados (cada minuto)
# ---------------------------------------------------------------------------

def _claim(session: Session, user_id: str, kind: str, key: str) -> bool:
    """Reserva el envio; False si ya se hizo (aqui o en otra instancia)."""
    session.add(NotificationLog(user_id=user_id, kind=kind, key=key))
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def _send_once(session: Session, tokens: list[str], user_id: str, kind: str, key: str, title: str, body: str, data: dict) -> bool:
    if not tokens or not _claim(session, user_id, kind, key):
        return False
    send_push_notifications(tokens, title, body, data)
    return True


def _upcoming(session: Session, now: datetime) -> int:
    due = session.exec(
        select(Reservation).where(
            Reservation.status == "PENDING",
            Reservation.reservation_start_date > now,
            Reservation.reservation_start_date <= now + timedelta(minutes=MAX_UPCOMING_MINUTES),
        )
    ).all()
    sent = 0
    teams: dict[str, list[Member]] = {}
    for r in due:
        team = teams.setdefault(r.business_id, _team(session, r.business_id))
        user_id = _in_charge(team, r.business_id, r.in_charge)
        prefs = preferences(session, user_id)
        if not prefs.upcoming or r.reservation_start_date - now > timedelta(minutes=prefs.upcoming_minutes):
            continue
        start = r.reservation_start_date
        sent += _send_once(
            session, _tokens(session, [user_id]).get(user_id, []), user_id, "upcoming", f"{r.id}:{start:%Y-%m-%dT%H:%M}",
            f"{start:%H:%M} · {r.customer_name}", r.service, {"type": "upcoming", "reservation_id": r.id},
        )
    return sent


def _today(session: Session, today: date, statuses: Iterable[str]) -> dict[str, list[Reservation]]:
    by_business: dict[str, list[Reservation]] = {}
    for r in session.exec(
        select(Reservation)
        .where(
            Reservation.status.in_(list(statuses)),
            Reservation.reservation_start_date >= datetime.combine(today, time.min),
            Reservation.reservation_start_date < datetime.combine(today + timedelta(days=1), time.min),
        )
        .order_by(Reservation.reservation_start_date)
    ).all():
        by_business.setdefault(r.business_id, []).append(r)
    return by_business


def _daily_summary(session: Session, now: datetime) -> int:
    sent = 0
    today = now.date()
    for business_id, reservations in _today(session, today, VISIBLE_STATUSES).items():
        team = _team(session, business_id)
        tokens = _tokens(session, [m.user_id for m in team])
        for member in team:
            prefs = preferences(session, member.user_id)
            if member.user_id not in tokens or not prefs.daily_summary or now.hour != prefs.daily_summary_hour:
                continue
            mine = (
                [r for r in reservations if _in_charge(team, business_id, r.in_charge) == member.user_id]
                if member.role == "employee"
                else reservations
            )
            if not mine:
                continue
            sent += _send_once(
                session, tokens[member.user_id], member.user_id, "daily_summary", today.isoformat(),
                "Resumen de hoy",
                f"Hoy {_plural(len(mine), 'cita', 'citas')}, primera a las {mine[0].reservation_start_date:%H:%M}",
                {"type": "daily_summary", "date": today.isoformat()},
            )
    return sent


def _day_close_time(session: Session, business_id: str, today: date) -> datetime:
    ends = session.exec(
        select(WorkingHours.end_time).where(
            WorkingHours.business_id == business_id,
            WorkingHours.day_of_week == today.weekday(),
            WorkingHours.enabled == True,  # noqa: E712
            WorkingHours.member_user_id == None,  # noqa: E711
        )
    ).all()
    if not ends:
        return datetime.combine(today, DEFAULT_DAY_CLOSE)
    return datetime.combine(today, time.fromisoformat(max(ends))) + DAY_CLOSE_DELAY


def _day_close(session: Session, now: datetime) -> int:
    sent = 0
    today = now.date()
    for business_id, pending in _today(session, today, ["PENDING"]).items():
        if now < _day_close_time(session, business_id, today):
            continue
        managers = [m for m in _team(session, business_id) if m.role in ("owner", "manager")]
        tokens = _tokens(session, [m.user_id for m in managers])
        count = len(pending)
        body = (
            "1 cita de hoy sigue pendiente. Márcala completada para cuadrar la caja."
            if count == 1
            else f"{count} citas de hoy siguen pendientes. Márcalas completadas para cuadrar la caja."
        )
        for member in managers:
            if not preferences(session, member.user_id).day_close:
                continue
            sent += _send_once(
                session, tokens.get(member.user_id, []), member.user_id, "day_close", today.isoformat(),
                "Cierre del día", body, {"type": "day_close", "date": today.isoformat()},
            )
    return sent


def run_scheduled(session: Session, now: datetime) -> dict:
    """Envia los avisos programados que tocan. `now` es la hora de pared del negocio."""
    return {
        "upcoming": _upcoming(session, now),
        "daily_summary": _daily_summary(session, now),
        "day_close": _day_close(session, now),
    }


def _run_once() -> None:
    engine = get_engine()
    now = datetime.now(BUSINESS_TIMEZONE).replace(tzinfo=None)
    if engine.dialect.name != "postgresql":
        with Session(engine) as session:
            run_scheduled(session, now)
        return
    with engine.connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar():
            return
        lock.commit()
        try:
            with Session(engine) as session:
                result = run_scheduled(session, now)
            if any(result.values()):
                logger.info("team_notifications %s", result)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
            lock.commit()


async def notifications_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_run_once)
        except Exception:
            logger.exception("team notifications run failed")
        await asyncio.sleep(LOOP_SECONDS)
