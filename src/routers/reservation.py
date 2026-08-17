from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import and_, or_, select

from src.api.auth import AuthContext, require_owner, require_subscription

from ..database.database import SessionDep
from ..database.models.finances_model import Finances
from ..database.models.member_model import BusinessMember
from ..database.models.profile_model import Profile
from ..database.models.reservation_model import (
    Reservation,
    ReservationBase,
    ReservationPublic,
    ReservationUpdate,
)

router = APIRouter(
    prefix="/reservations",
    tags=["reservations"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", response_model=list[ReservationPublic])
def get_reservations(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Return PENDING reservations from the last 7 days onward + COMPLETED from today."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today_start - timedelta(days=7)

    reservations = session.exec(
        select(Reservation)
        .where(Reservation.business_id == auth.business_id)
        .where(
            or_(
                and_(
                    Reservation.status == "PENDING",
                    Reservation.reservation_start_date >= week_ago,
                ),
                and_(
                    Reservation.status == "COMPLETED",
                    Reservation.reservation_start_date >= today_start,
                ),
            )
        )
        .order_by(Reservation.reservation_start_date.desc())
    ).all()
    if not reservations:
        raise HTTPException(status_code=404, detail="No reservations found")
    return reservations


@router.get("/{reservation_id}", response_model=ReservationPublic)
def get_reservation_by_id(
    reservation_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    reservation = session.get(Reservation, reservation_id)
    if not reservation or reservation.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


FIXED_DURATION_MINUTES = 30


def _get_valid_in_charge_names(session, business_id: str) -> set[str]:
    """Return display names of the owner + all active members for a business."""
    # Owner's display name (business_id == owner's user_id)
    owner_profile = session.get(Profile, business_id)
    names: set[str] = set()
    if owner_profile and owner_profile.display_name:
        names.add(owner_profile.display_name)

    # Active members' display names
    members = session.exec(
        select(BusinessMember)
        .where(BusinessMember.business_id == business_id)
        .where(BusinessMember.status == "active")
    ).all()
    member_ids = [m.member_user_id for m in members]
    if member_ids:
        profiles = session.exec(
            select(Profile).where(Profile.id.in_(member_ids))
        ).all()
        for p in profiles:
            if p.display_name:
                names.add(p.display_name)

    return names


@router.post("/", response_model=ReservationPublic)
def create_reservation(
    reservation: ReservationBase,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    reservation_data = reservation.model_dump()
    reservation_data["business_id"] = auth.business_id
    reservation_data["time_per_reservation"] = FIXED_DURATION_MINUTES
    reservation_data["reservation_end_date"] = reservation.reservation_start_date + timedelta(minutes=FIXED_DURATION_MINUTES)

    # Validate in_charge against active team members
    if reservation.in_charge:
        valid_names = _get_valid_in_charge_names(session, auth.business_id)
        if reservation.in_charge not in valid_names:
            raise HTTPException(
                status_code=422,
                detail=f"El encargado '{reservation.in_charge}' no es un miembro activo del negocio",
            )

    reservation_obj = Reservation(**reservation_data)
    session.add(reservation_obj)
    session.commit()
    session.refresh(reservation_obj)
    return reservation_obj


@router.patch("/{reservation_id}", response_model=ReservationPublic)
def update_reservation(
    reservation_id: int,
    reservation: ReservationUpdate,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    reservation_db = session.get(Reservation, reservation_id)
    if not reservation_db or reservation_db.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Reservation not found")

    # Block modifications on completed reservations (except completing a pending one)
    if reservation_db.status == "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="No se puede modificar una reserva ya completada",
        )

    # Prevent double-completing: only allow PENDING → COMPLETED
    reservation_data = reservation.model_dump(exclude_unset=True)
    new_status = reservation_data.get("status")
    if new_status == "COMPLETED" and reservation_db.status != "PENDING":
        raise HTTPException(
            status_code=409,
            detail="Solo se pueden completar reservas pendientes",
        )

    reservation_db.sqlmodel_update(reservation_data)
    session.add(reservation_db)
    session.commit()
    session.refresh(reservation_db)
    return reservation_db


@router.delete("/{reservation_id}")
def delete_reservation(
    reservation_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    reservation = session.get(Reservation, reservation_id)
    if not reservation or reservation.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if reservation.status == "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar una reserva ya completada",
        )

    session.delete(reservation)
    session.commit()
    return {"Reservation deleted": True}


@router.post("/{reservation_id}/revert", response_model=ReservationPublic)
def revert_reservation(
    reservation_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    """Revert a COMPLETED reservation back to PENDING and delete its linked finance."""
    reservation = session.get(Reservation, reservation_id)
    if not reservation or reservation.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if reservation.status != "COMPLETED":
        raise HTTPException(
            status_code=409,
            detail="Solo se pueden revertir reservas completadas",
        )

    # Delete linked finance record
    linked_finance = session.exec(
        select(Finances).where(Finances.reservation_id == reservation_id)
    ).first()
    if linked_finance:
        session.delete(linked_finance)

    reservation.status = "PENDING"
    session.add(reservation)
    session.commit()
    session.refresh(reservation)
    return reservation
