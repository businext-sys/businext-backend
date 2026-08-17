from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from src.api.auth import AuthContext, get_auth_context
from src.database.database import SessionDep
from src.database.models.profile_model import Profile

router = APIRouter(prefix="/tour", tags=["tour"])

VALID_TOUR_KEYS = {"owner_v1", "employee_v1"}


class TourProgressInput(BaseModel):
    tourKey: str
    step: int = Field(ge=0)
    done: bool = False


class TourStateResponse(BaseModel):
    tourState: dict[str, Any]


def _load_profile(session, user_id: str) -> Profile:
    profile = session.exec(select(Profile).where(Profile.id == user_id)).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return profile


@router.get("", response_model=TourStateResponse)
def get_tour_state(
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    profile = _load_profile(session, auth.user_id)
    return {"tourState": profile.tour_state or {}}


@router.put("", response_model=TourStateResponse)
def save_tour_progress(
    body: TourProgressInput,
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    if body.tourKey not in VALID_TOUR_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Clave de tour invalida. Opciones: {sorted(VALID_TOUR_KEYS)}",
        )

    profile = _load_profile(session, auth.user_id)
    state = dict(profile.tour_state or {})
    previous = state.get(body.tourKey) or {}

    entry: dict[str, Any] = {
        "step": max(int(previous.get("step", 0)), body.step),
        "done": bool(previous.get("done")) or body.done,
    }
    if entry["done"]:
        entry["at"] = previous.get("at") or datetime.now(timezone.utc).isoformat()

    state[body.tourKey] = entry
    profile.tour_state = state
    flag_modified(profile, "tour_state")
    session.add(profile)
    session.commit()
    session.refresh(profile)

    return {"tourState": profile.tour_state or {}}
