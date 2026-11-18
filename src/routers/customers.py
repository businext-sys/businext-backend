"""Clientes conocidos del negocio, para autocompletar el formulario de reservas."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.auth import AuthContext, require_subscription
from src.database.database import SessionDep
from src.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerPublic(BaseModel):
    name: str
    phone: str | None
    email: str | None
    last_visit: datetime


@router.get("/", response_model=list[CustomerPublic])
def find_customers(
    session: SessionDep,
    q: str = "",
    limit: int = Query(default=10, ge=1, le=50),
    auth: AuthContext = Depends(require_subscription),
):
    """Clientes de las reservas del negocio cuyo nombre, email o teléfono contiene `q`; última visita primero."""
    return customer_service.find_customers(session, auth.business_id, q, limit)
