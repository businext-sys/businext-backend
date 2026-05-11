"""Background task to expire booking requests after 48 hours."""

import asyncio
from datetime import datetime, timezone
from sqlmodel import Session, select
from src.database.database import get_engine
from src.database.models.booking_request_model import BookingRequest
from src.database.models.business_conf_model import BusinessConfiguration
from src.services.email_service import send_email, email_request_expired


async def expire_old_requests():
    """Check and expire booking requests that have passed their expires_at."""
    while True:
        try:
            with Session(get_engine()) as session:
                now = datetime.now(timezone.utc)
                expired = session.exec(
                    select(BookingRequest).where(
                        BookingRequest.status == "REQUESTED",
                        BookingRequest.expires_at <= now,
                    )
                ).all()

                for booking in expired:
                    booking.status = "EXPIRED"
                    session.add(booking)

                    # Send expiration email to client
                    biz = session.exec(
                        select(BusinessConfiguration).where(
                            BusinessConfiguration.business_id == booking.business_id
                        )
                    ).first()
                    biz_name = biz.business_name if biz else "Negocio"
                    date_str = booking.requested_date.strftime("%d/%m/%Y %H:%M")
                    subject, html = email_request_expired(
                        booking.client_name, booking.service, date_str, biz_name,
                        business_phone=biz.business_phone if biz else None,
                        business_email=biz.business_email if biz else None,
                    )
                    send_email(booking.client_email, subject, html)

                if expired:
                    session.commit()
                    print(f"[expiration_service] Expired {len(expired)} booking requests")

        except Exception as e:
            print(f"[expiration_service] Error: {e}")

        # Run every hour
        await asyncio.sleep(3600)
