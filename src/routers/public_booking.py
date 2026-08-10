"""Public booking endpoints — no authentication required."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from src.database.database import SessionDep
from src.database.models.booking_request_model import (
    BookingRequest,
    BookingRequestCreate,
)
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.location_model import Location
from src.database.models.member_model import BusinessMember
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.push_token_model import PushToken
from src.services.availability_service import (
    get_available_slots,
    get_employees_with_availability,
)
from src.services.email_service import (
    email_request_received_client,
    email_request_received_employee,
    send_email,
)
from src.services.push_notification_service import send_push_notifications

router = APIRouter(prefix="/public/book", tags=["public-booking"])


def _get_business_or_404(session, business_id: str) -> BusinessConfiguration:
    biz = session.exec(
        select(BusinessConfiguration).where(
            BusinessConfiguration.business_id == business_id
        )
    ).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    return biz


@router.get("/{business_id}/locations")
def get_locations(business_id: str, session: SessionDep):
    """Returns active locations for a business."""
    biz = _get_business_or_404(session, business_id)
    locations = session.exec(
        select(Location).where(
            Location.business_id == business_id,
            Location.is_active == True,  # noqa: E712
        ).order_by(Location.created_at.asc())
    ).all()
    return {
        "business_name": biz.business_name,
        "locations": [
            {
                "id": loc.id,
                "name": loc.name,
                "address": loc.address,
                "phone": loc.phone,
                "maps_link": loc.maps_link,
            }
            for loc in locations
        ],
    }


def _get_employee_email(
    session, business_id: str, employee_name: str | None
) -> str | None:
    """Look up email for the assigned employee, or fallback to business owner."""
    if employee_name:
        # Find member by display_name match via profile
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
            if profile and profile.display_name and (
                profile.display_name.lower() == employee_name.lower()
            ):
                return profile.email

    # Fallback: get the owner's email (business_id IS the owner's user_id)
    owner_profile = session.exec(
        select(Profile).where(Profile.id == business_id)
    ).first()
    return owner_profile.email if owner_profile else None


@router.get("/{business_id}/services")
def get_services(
    business_id: str,
    session: SessionDep,
    location_id: int | None = None,
):
    """Returns available services and employees for a business, optionally filtered by location."""
    biz = _get_business_or_404(session, business_id)

    products = session.exec(
        select(Product).where(Product.business_id == business_id)
    ).all()

    employees = get_employees_with_availability(session, business_id, location_id=location_id)

    services = [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "type": p.type,
            "image_url": p.image_url,
        }
        for p in products
        if p.type == "servicio"
    ]

    # Include location info if location_id is provided
    location_info = None
    if location_id:
        loc = session.get(Location, location_id)
        if loc and loc.business_id == business_id:
            location_info = {
                "id": loc.id,
                "name": loc.name,
                "address": loc.address,
                "phone": loc.phone,
                "maps_link": loc.maps_link,
            }

    return {
        "business_name": biz.business_name,
        "services": services,
        "employees": employees,
        "location": location_info,
    }


@router.get("/{business_id}/availability")
def get_availability(
    business_id: str,
    session: SessionDep,
    date_param: str = Query(..., alias="date"),
    employee_name: str | None = None,
):
    """Returns available time slots for a given date and optional employee."""
    _get_business_or_404(session, business_id)

    try:
        target_date = date.fromisoformat(date_param)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD")

    slots = get_available_slots(session, business_id, target_date, employee_name)

    return {"date": date_param, "slots": slots}


@router.post("/{business_id}/request", status_code=201)
def create_booking_request(
    business_id: str,
    body: BookingRequestCreate,
    session: SessionDep,
):
    """Creates a new booking request."""
    biz = _get_business_or_404(session, business_id)

    # Validate requested_date is in the future
    req_date = body.requested_date
    if req_date.tzinfo is None:
        req_date = req_date.replace(tzinfo=timezone.utc)
    if req_date <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="La fecha solicitada debe ser futura")

    # Check for existing pending request from same email
    existing = session.exec(
        select(BookingRequest).where(
            BookingRequest.business_id == business_id,
            BookingRequest.client_email == body.client_email,
            BookingRequest.status == "REQUESTED",
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ya tienes una solicitud pendiente. Espera a que sea respondida antes de crear otra."
        )

    # Create the booking request
    booking = BookingRequest(
        business_id=business_id,
        client_name=body.client_name,
        client_email=body.client_email,
        client_phone=body.client_phone,
        employee_name=body.employee_name,
        service=body.service,
        requested_date=body.requested_date,
        location_id=body.location_id,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)

    # Notificacion push al owner del negocio (issue #031). `business_id`
    # coincide con el user_id del owner (ver AuthContext.get_auth_context),
    # por lo que sus tokens son los registrados con ese mismo user_id.
    owner_tokens = session.exec(
        select(PushToken.token).where(PushToken.user_id == business_id)
    ).all()
    if owner_tokens:
        send_push_notifications(
            tokens=list(owner_tokens),
            title="Nueva solicitud de reserva",
            body=f"{booking.client_name} solicito {booking.service}",
            data={"type": "booking_request", "bookingRequestId": booking.id},
        )

    # Send confirmation email to client
    date_str = booking.requested_date.strftime("%d/%m/%Y %H:%M")

    # Get location info for emails
    location_name = None
    location_address = None
    if booking.location_id:
        loc = session.get(Location, booking.location_id)
        if loc:
            location_name = loc.name
            location_address = loc.address

    subject, html = email_request_received_client(
        booking.client_name, booking.service, date_str, biz.business_name, booking.employee_name,
        business_phone=biz.business_phone, business_email=biz.business_email,
        location_name=location_name, location_address=location_address,
    )
    send_email(booking.client_email, subject, html)

    # Send notification to employee/owner
    employee_email = _get_employee_email(session, business_id, booking.employee_name)
    if employee_email:
        subj, body = email_request_received_employee(
            booking.client_name,
            booking.client_phone,
            booking.service,
            date_str,
            biz.business_name,
        )
        send_email(employee_email, subj, body)

    return {
        "id": booking.id,
        "status": booking.status,
        "message": "Solicitud enviada correctamente. Recibirás un email de confirmación.",
    }
