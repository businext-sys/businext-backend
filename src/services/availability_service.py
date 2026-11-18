from datetime import datetime, date, timedelta
from sqlmodel import Session, func, select
from src.database.models.product_model import Product
from src.database.models.working_hours_model import WorkingHours
from src.database.models.reservation_model import Reservation
from src.database.models.booking_request_model import BookingRequest
from src.database.models.member_model import BusinessMember
from src.database.models.profile_model import Profile

SLOT_DURATION_MINUTES = 30
SLOT_STEP_MINUTES = 15


def service_duration(session: Session, business_id: str, service: str | None) -> int:
    """Duracion del servicio del catalogo con ese nombre (sin mayusculas ni espacios de mas); 30 si no hay."""
    if not service:
        return SLOT_DURATION_MINUTES
    duration = session.exec(
        select(Product.duration_minutes).where(
            Product.business_id == business_id,
            func.lower(func.trim(Product.name)) == service.strip().lower(),
        )
    ).first()
    return duration or SLOT_DURATION_MINUTES


def _get_hours_for_employee(
    session: Session,
    business_id: str,
    day_of_week: int,
    member_user_id: str | None,
) -> list[WorkingHours]:
    """Get working hour blocks for a specific day.
    Employee-specific hours take priority; falls back to business-wide."""
    if member_user_id:
        # Check if employee has any configuration for this day (enabled or disabled).
        # If there is at least one row for the employee/day, do NOT fallback to
        # business-wide hours: explicit disabled rows must keep the day closed.
        emp_day_rows = session.exec(
            select(WorkingHours).where(
                WorkingHours.business_id == business_id,
                WorkingHours.day_of_week == day_of_week,
                WorkingHours.member_user_id == member_user_id,
            )
        ).all()

        if emp_day_rows:
            return [row for row in emp_day_rows if row.enabled]

        # No employee-specific rows for this day: fallback to business-wide.

    # Fallback to business-wide
    biz_hours = session.exec(
        select(WorkingHours).where(
            WorkingHours.business_id == business_id,
            WorkingHours.day_of_week == day_of_week,
            WorkingHours.enabled == True,  # noqa: E712
            WorkingHours.member_user_id == None,  # noqa: E711
        )
    ).all()
    return list(biz_hours)


def _generate_slots_from_blocks(
    blocks: list[WorkingHours],
    target_date: date,
    now: datetime,
    duration_minutes: int = SLOT_DURATION_MINUTES,
    step_minutes: int = SLOT_DURATION_MINUTES,
) -> list[str]:
    """Start times, every `step_minutes`, where `duration_minutes` fits whole inside a working hour block."""
    slots: list[str] = []
    for wh in blocks:
        start_h, start_m = map(int, wh.start_time.split(":"))
        end_h, end_m = map(int, wh.end_time.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        current = start_minutes
        while current + duration_minutes <= end_minutes:
            h, m = divmod(current, 60)
            time_str = f"{h:02d}:{m:02d}"

            # Skip past slots if target_date is today
            slot_dt = datetime.combine(target_date, datetime.min.time()) + timedelta(
                hours=h, minutes=m
            )
            if slot_dt > now:
                slots.append(time_str)

            current += step_minutes
    return slots


def get_available_slots(
    session: Session,
    business_id: str,
    target_date: date,
    employee_name: str | None = None,
    duration_minutes: int = SLOT_DURATION_MINUTES,
) -> list[dict]:
    """
    Returns start times (every 15 min) where `duration_minutes` fits for a given date and optional employee.
    Considers working hours (multi-block), existing reservations (their real start/end) and pending booking requests.
    """
    day_of_week = target_date.weekday()  # 0=Monday
    now = datetime.utcnow()

    # Resolve which employees to check
    if employee_name:
        employees_to_check = [employee_name]
    else:
        all_emps = get_employees_with_availability(session, business_id)
        emp_names = [e["name"] for e in all_emps]
        employees_to_check = emp_names if emp_names else [None]

    # Resolve member_user_id for each employee name
    emp_member_map: dict[str | None, str | None] = {}
    if employees_to_check != [None]:
        members = session.exec(
            select(BusinessMember).where(
                BusinessMember.business_id == business_id,
                BusinessMember.status == "active",
            )
        ).all()
        for member in members:
            profile = session.exec(
                select(Profile).where(Profile.id == member.member_user_id)
            ).first()
            if profile and profile.display_name:
                emp_member_map[profile.display_name] = member.member_user_id
    else:
        emp_member_map[None] = None

    # Fetch existing reservations for target_date
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    existing_reservations = session.exec(
        select(Reservation).where(
            Reservation.business_id == business_id,
            Reservation.reservation_start_date >= day_start,
            Reservation.reservation_start_date < day_end,
            Reservation.status.in_(["PENDING", "COMPLETED"]),  # type: ignore
        )
    ).all()

    # Fetch pending booking requests for the same day
    pending_requests = session.exec(
        select(BookingRequest).where(
            BookingRequest.business_id == business_id,
            BookingRequest.requested_date >= day_start,
            BookingRequest.requested_date < day_end,
            BookingRequest.status == "REQUESTED",
        )
    ).all()

    occupied: dict[str | None, list[tuple[datetime, datetime]]] = {}
    for r in existing_reservations:
        end = r.reservation_end_date or r.reservation_start_date + timedelta(minutes=r.time_per_reservation)
        occupied.setdefault(r.in_charge, []).append((r.reservation_start_date, end))

    for br in pending_requests:
        length = timedelta(minutes=service_duration(session, business_id, br.service))
        occupied.setdefault(br.employee_name, []).append((br.requested_date, br.requested_date + length))

    length = timedelta(minutes=duration_minutes)

    def is_free(emp: str | None, start: datetime) -> bool:
        return all(not (start < busy_end and start + length > busy_start) for busy_start, busy_end in occupied.get(emp, []))

    # Generate available slots per employee
    slots: list[dict] = []
    for emp in employees_to_check:
        member_uid = emp_member_map.get(emp)
        blocks = _get_hours_for_employee(session, business_id, day_of_week, member_uid)
        if not blocks:
            continue

        time_slots = _generate_slots_from_blocks(blocks, target_date, now, duration_minutes, SLOT_STEP_MINUTES)
        emp_name = emp if emp else None
        for time_str in time_slots:
            start = datetime.combine(target_date, datetime.strptime(time_str, "%H:%M").time())
            if is_free(emp_name, start):
                slots.append(
                    {"time": time_str, "employee_name": emp_name or "Cualquiera"}
                )

    # Sort by time
    slots.sort(key=lambda s: s["time"])
    return slots


def get_employees_with_availability(
    session: Session, business_id: str, location_id: int | None = None,
) -> list[dict]:
    """Returns all active employees (including owner) for a business, optionally filtered by location."""
    employees: set[str] = set()

    # Get all active members (including owner who now has a row in businessmember)
    query = select(BusinessMember).where(
        BusinessMember.business_id == business_id,
        BusinessMember.status == "active",
    )
    if location_id is not None:
        query = query.where(BusinessMember.location_id == location_id)

    members = session.exec(query).all()
    for member in members:
        profile = session.exec(
            select(Profile).where(Profile.id == member.member_user_id)
        ).first()
        if profile and profile.display_name:
            employees.add(profile.display_name)

    return [{"name": e, "available": True} for e in sorted(employees)]
