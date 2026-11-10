"""
Recordatorios por email el dia antes de la cita (businext-sys/businext#183).

`POST /internal/reminders/run` lo llama cada hora el cron job de Render y exige
`Authorization: Bearer <REMINDERS_JOB_SECRET>` (401 sin el).
"""

import hmac
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException

from src.database.database import SessionDep
from src.services.reminder_service import send_reminders
from src.services.trend_sources import BUSINESS_TIMEZONE


def require_job_secret(authorization: str | None = Header(default=None)):
    secret = os.getenv("REMINDERS_JOB_SECRET")
    if not secret or not authorization or not hmac.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Firma del job no valida")


internal_router = APIRouter(prefix="/internal/reminders", tags=["reminders"], dependencies=[Depends(require_job_secret)])


@internal_router.post("/run")
def run(session: SessionDep):
    """Envia los recordatorios de las citas de mañana; relanzarlo no reenvia ninguno."""
    return send_reminders(session, datetime.now(BUSINESS_TIMEZONE).replace(tzinfo=None))
