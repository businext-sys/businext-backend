from datetime import datetime, date, timedelta
from sqlmodel import Session, select, or_
from src.database.models.working_hours_model import WorkingHours
from src.database.models.reservation_model import Reservation
from src.database.models.booking_request_model import BookingRequest
from src.database.models.member_model import BusinessMember
from src.database.models.profile_model import Profile

SLOT_DURATION_MINUTES = 30


def get_available_slots(
    session: Session,
    business_id: str,
    target_date: date,
    employee_name: str | None = None,
) -> list[dict]:
    """
    Returns available 30-min slots for a given date and optional employee.
    Considers working hours, existing reservations, and pending booking requests.
    """
    day_of_week = target_date.weekday()  # 0=Monday

    # Fetch working hours for this day
    wh_query = select(WorkingHours).where(
        WorkingHours.business_id == business_id,
        WorkingHours.day_of_week == day_of_week,
        WorkingHours.enabled == True,  # noqa: E712
    )
    if employee_name:
        # Get employee-specific hours OR business-wide (null) hours
        wh_query = wh_query.where(
            or_(
                WorkingHours.employee_name == employee_name,
                WorkingHours.employee_name == None,  # noqa: E711
            )
        )

    working_hours = session.exec(wh_query).all()

    if not working_hours:
        return []

    # Build employee → hours mapping
    # Employee-specific hours take priority over business-wide
    employee_hours: dict[str | None, WorkingHours] = {}
    for wh in working_hours:
        key = wh.employee_name
        if key not in employee_hours or key is not None:
            employee_hours[key] = wh

    # Get all employees to check (from working hours that have employee_name set)
    employees_to_check: list[str | None] = []
    if employee_name:
        employees_to_check = [employee_name]
    else:
        # If only business-wide hours exist, generate slots per actual employee
        named_employees = [k for k in employee_hours.keys() if k is not None]
        if named_employees:
            employees_to_check = named_employees
        else:
            # Use all active employees with business-wide hours
            all_emps = get_employees_with_availability(session, business_id)
            emp_names = [e["name"] for e in all_emps]
            if emp_names:
                employees_to_check = emp_names
            else:
                employees_to_check = [None]

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

    # Build set of occupied slots: (employee_name, HH:MM)
    occupied: set[tuple[str | None, str]] = set()
    for r in existing_reservations:
        time_key = r.reservation_start_date.strftime("%H:%M")
        occupied.add((r.in_charge, time_key))

    for br in pending_requests:
        time_key = br.requested_date.strftime("%H:%M")
        occupied.add((br.employee_name, time_key))

    # Generate available slots
    now = datetime.utcnow()
    slots: list[dict] = []

    for emp in employees_to_check:
        # Resolve hours: employee-specific > business-wide
        wh = employee_hours.get(emp) or employee_hours.get(None)
        if not wh:
            continue

        start_h, start_m = map(int, wh.start_time.split(":"))
        end_h, end_m = map(int, wh.end_time.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        current = start_minutes
        while current + SLOT_DURATION_MINUTES <= end_minutes:
            h, m = divmod(current, 60)
            time_str = f"{h:02d}:{m:02d}"

            # Skip past slots if target_date is today
            slot_dt = datetime.combine(target_date, datetime.min.time()) + timedelta(
                hours=h, minutes=m
            )
            if slot_dt <= now:
                current += SLOT_DURATION_MINUTES
                continue

            # Check if occupied
            emp_name = emp if emp else None
            if (emp_name, time_str) not in occupied:
                slots.append(
                    {"time": time_str, "employee_name": emp_name or "Cualquiera"}
                )

            current += SLOT_DURATION_MINUTES

    # Sort by time
    slots.sort(key=lambda s: s["time"])
    return slots


def get_employees_with_availability(
    session: Session, business_id: str
) -> list[dict]:
    """Returns all active employees (members + owner) for a business."""
    employees: set[str] = set()

    # Get owner display name
    owner_profile = session.exec(
        select(Profile).where(Profile.id == business_id)
    ).first()
    if owner_profile and owner_profile.display_name:
        employees.add(owner_profile.display_name)

    # Get active members
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
            employees.add(profile.display_name)

    return [{"name": e, "available": True} for e in sorted(employees)]
