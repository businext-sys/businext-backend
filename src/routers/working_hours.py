from fastapi import APIRouter, HTTPException, Depends, Query
from sqlmodel import select
from typing import Optional
from ..database.database import SessionDep
from ..database.models.working_hours_model import (
    WorkingHours,
    WorkingHoursPublic,
    WorkingHoursInput,
)
from src.api.auth import AuthContext, require_active_member, require_owner

router = APIRouter(
    prefix="/working-hours",
    tags=["working-hours"],
    responses={404: {"description": "Not found"}},
)


@router.get("/", response_model=list[WorkingHoursPublic])
def get_working_hours(
    session: SessionDep,
    auth: AuthContext = Depends(require_active_member),
    member_user_id: Optional[str] = Query(None, description="Filter by member. Omit for business-wide."),
):
    """Get working hours. If member_user_id is provided, returns that member's hours.
    Otherwise returns business-wide (general) hours."""
    query = (
        select(WorkingHours)
        .where(WorkingHours.business_id == auth.business_id)
    )
    if member_user_id:
        query = query.where(WorkingHours.member_user_id == member_user_id)
    else:
        query = query.where(WorkingHours.member_user_id == None)  # noqa: E711

    hours = session.exec(query.order_by(WorkingHours.day_of_week, WorkingHours.start_time)).all()
    return hours


@router.put("/", response_model=list[WorkingHoursPublic])
def upsert_working_hours(
    entries: list[WorkingHoursInput],
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
    member_user_id: Optional[str] = Query(None, description="Set hours for a specific member. Omit for business-wide."),
):
    """Replace all working hour blocks for business-wide or a specific member.
    Supports multiple blocks per day."""
    # Validate entries
    for entry in entries:
        if entry.day_of_week < 0 or entry.day_of_week > 6:
            raise HTTPException(
                status_code=422,
                detail=f"day_of_week must be 0-6, got {entry.day_of_week}",
            )
        if entry.enabled and entry.start_time >= entry.end_time:
            raise HTTPException(
                status_code=422,
                detail=f"start_time must be before end_time for day {entry.day_of_week}",
            )

    # Validate no overlapping blocks on the same day
    by_day: dict[int, list[WorkingHoursInput]] = {}
    for entry in entries:
        by_day.setdefault(entry.day_of_week, []).append(entry)

    for day, blocks in by_day.items():
        enabled_blocks = sorted(
            [b for b in blocks if b.enabled], key=lambda b: b.start_time
        )
        for i in range(len(enabled_blocks) - 1):
            if enabled_blocks[i].end_time > enabled_blocks[i + 1].start_time:
                raise HTTPException(
                    status_code=422,
                    detail=f"Overlapping time blocks on day {day}",
                )

    # Delete existing hours for this scope
    existing = session.exec(
        select(WorkingHours).where(
            WorkingHours.business_id == auth.business_id,
            WorkingHours.member_user_id == member_user_id if member_user_id
            else WorkingHours.member_user_id == None,  # noqa: E711
        )
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()

    # Insert new entries
    for entry in entries:
        data = entry.model_dump()
        data["business_id"] = auth.business_id
        data["member_user_id"] = member_user_id
        new_entry = WorkingHours(**data)
        session.add(new_entry)

    session.commit()

    # Return updated list
    query = (
        select(WorkingHours)
        .where(WorkingHours.business_id == auth.business_id)
    )
    if member_user_id:
        query = query.where(WorkingHours.member_user_id == member_user_id)
    else:
        query = query.where(WorkingHours.member_user_id == None)  # noqa: E711

    hours = session.exec(query.order_by(WorkingHours.day_of_week, WorkingHours.start_time)).all()
    return hours
