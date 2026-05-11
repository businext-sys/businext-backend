from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from src.api.auth import AuthContext, require_owner
from src.database.database import SessionDep
from src.database.models.location_model import (
    Location,
    LocationCreate,
    LocationPublic,
    LocationUpdate,
)

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/", response_model=list[LocationPublic])
def list_locations(
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    """List all locations for the business."""
    locations = session.exec(
        select(Location)
        .where(Location.business_id == auth.business_id)
        .order_by(Location.created_at.asc())
    ).all()
    return locations


@router.post("/", response_model=LocationPublic, status_code=201)
def create_location(
    body: LocationCreate,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    """Create a new location for the business."""
    location = Location(
        business_id=auth.business_id,
        name=body.name,
        address=body.address,
        phone=body.phone,
        maps_link=body.maps_link,
    )
    session.add(location)
    session.commit()
    session.refresh(location)
    return location


@router.patch("/{location_id}", response_model=LocationPublic)
def update_location(
    location_id: int,
    body: LocationUpdate,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    """Update an existing location."""
    location = session.get(Location, location_id)
    if not location or location.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Local no encontrado")

    update_data = body.model_dump(exclude_unset=True)
    location.sqlmodel_update(update_data)
    location.updated_at = datetime.utcnow()
    session.add(location)
    session.commit()
    session.refresh(location)
    return location


@router.delete("/{location_id}")
def delete_location(
    location_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    """Delete a location. Prevents deleting the last active location."""
    location = session.get(Location, location_id)
    if not location or location.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Local no encontrado")

    # Prevent deleting the last location
    active_count = len(
        session.exec(
            select(Location).where(
                Location.business_id == auth.business_id,
                Location.is_active == True,  # noqa: E712
            )
        ).all()
    )
    if active_count <= 1 and location.is_active:
        raise HTTPException(
            status_code=400,
            detail="No puedes eliminar el último local activo del negocio.",
        )

    session.delete(location)
    session.commit()
    return {"success": True}
