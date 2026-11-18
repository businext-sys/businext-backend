"""Clientes conocidos del negocio, para autocompletar el formulario de reservas."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.auth import AuthContext, require_subscription
from src.database.database import SessionDep
from src.services import customer_service
from src.services.trend_sources import BUSINESS_TIMEZONE

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerPublic(BaseModel):
    name: str
    phone: str | None
    email: str | None
    last_visit: datetime | None


class CustomerReservation(BaseModel):
    id: int
    start: datetime
    service: str
    in_charge: str | None
    status: str


class CustomerDetail(CustomerPublic):
    visits: int
    no_shows: int
    total_spent: float
    next_reservation: CustomerReservation | None
    reservations: list[CustomerReservation]


@router.get("/", response_model=list[CustomerPublic])
def find_customers(
    session: SessionDep,
    q: str = "",
    limit: int = Query(default=10, ge=1, le=50),
    auth: AuthContext = Depends(require_subscription),
):
    """Clientes de las reservas del negocio cuyo nombre, email o teléfono contiene `q`; el más reciente primero."""
    now = datetime.now(BUSINESS_TIMEZONE).replace(tzinfo=None)
    return customer_service.find_customers(session, auth.business_id, now, q, limit)


@router.get("/detail", response_model=CustomerDetail)
def get_customer_detail(name: str, session: SessionDep, auth: AuthContext = Depends(require_subscription)):
    """Ficha del cliente con ese nombre (sin mayúsculas ni espacios de más): visitas, «no vino», gasto y reservas."""
    now = datetime.now(BUSINESS_TIMEZONE).replace(tzinfo=None)
    detail = customer_service.customer_detail(session, auth.business_id, name, now)
    if detail is None:
        raise HTTPException(status_code=404, detail="No hay ningún cliente con ese nombre")
    return detail
