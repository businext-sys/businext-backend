from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import select

from src.api.auth import AuthContext, get_auth_context
from src.database.database import SessionDep
from src.database.models.profile_model import Profile
from src.services.auth_service import signup_owner

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupInput(BaseModel):
    email: str
    password: str
    fullName: str


class SuccessResponse(BaseModel):
    success: bool


@router.post("/signup", response_model=SuccessResponse, status_code=status.HTTP_201_CREATED)
async def signup_owner_endpoint(
    payload: SignupInput,
    session: SessionDep,
):
    await signup_owner(
        session=session,
        email=payload.email,
        password=payload.password,
        full_name=payload.fullName,
    )
    return {"success": True}


@router.get("/me")
def get_access_context(
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    """
    Returns the full access context for the authenticated user.

    The frontend should consume this endpoint to determine what the user can
    do, instead of querying role/subscription/status from Supabase directly.

    HTTP status codes carry the authorization signal:
    - 200: authenticated and context resolved
    - 401: missing/invalid/expired token
    - 403: valid token but access denied (e.g., incomplete onboarding)
    """
    profile = session.exec(select(Profile).where(Profile.id == auth.user_id)).first()

    return {
        "userId": auth.user_id,
        "businessId": auth.business_id,
        "role": auth.role,
        "accountType": auth.account_type,
        "memberStatus": auth.member_status,
        "subscriptionActive": auth.subscription_active,
        "profile": {
            "displayName": profile.display_name if profile else None,
            "email": profile.email if profile else None,
            "phone": profile.phone if profile else None,
        },
        "capabilities": {
            "canAccessApp": auth.capabilities.can_access_app,
            "canManageConfiguration": auth.capabilities.can_manage_configuration,
            "canManageTeam": auth.capabilities.can_manage_team,
            "canManageProducts": auth.capabilities.can_manage_products,
            "canManageFinances": auth.capabilities.can_manage_finances,
            "canManageReservations": auth.capabilities.can_manage_reservations,
            "canManageReviews": auth.capabilities.can_manage_reviews,
        },
    }
