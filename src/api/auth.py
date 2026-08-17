import os
from dataclasses import dataclass, field

import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlmodel import select

from src.database.database import SessionDep
from src.database.models.member_model import BusinessMember
from src.database.models.subscription_model import Subscription

load_dotenv()

SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------
# Role hierarchy: owner > manager > employee
# Resources: configuration, products, finances, reservations
#
# | Resource             | owner | manager | employee |
# |----------------------|-------|---------|----------|
# | configuration read   |  ✅   |   ✅    |    ✅    |
# | configuration write  |  ✅   |   ❌    |    ❌    |
# | products read        |  ✅   |   ✅    |    ✅    |
# | products write       |  ✅   |   ✅    |    ❌    |
# | finances read        |  ✅   |   ✅    |    ✅    |
# | finances write       |  ✅   |   ✅    |    ❌    |
# | reservations read    |  ✅   |   ✅    |    ✅    |
# | reservations write   |  ✅   |   ✅    |    ✅    |
# | reviews              |  ✅   |   ✅    |    ❌    |
#
# Subscription requirement: only owners need an active subscription to access
# protected resources. Members can continue operating normally.
# ---------------------------------------------------------------------------


@dataclass
class AccessCapabilities:
    can_access_app: bool = False
    can_manage_configuration: bool = False
    can_manage_team: bool = False
    can_manage_products: bool = False
    can_manage_finances: bool = False
    can_manage_reservations: bool = False
    can_manage_reviews: bool = False


@dataclass
class AuthContext:
    user_id: str
    business_id: str
    role: str           # "owner" | "manager" | "employee"
    account_type: str   # "owner" | "member"
    member_status: str | None = None
    subscription_active: bool = False
    capabilities: AccessCapabilities = field(default_factory=AccessCapabilities)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_user_id_from_token(authorization: str) -> str:
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )
    token = parts[1]
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing subject")
        return user_id
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _has_active_subscription(session: SessionDep, business_id: str) -> bool:
    """Check if the business owner has an active subscription."""
    sub = session.exec(
        select(Subscription).where(
            Subscription.user_id == business_id,
            Subscription.status == "active",
        )
    ).first()
    return sub is not None


def _build_capabilities(role: str, subscription_active: bool) -> AccessCapabilities:
    """Derive UI-facing capability flags from role and subscription state."""
    if not subscription_active and role == "owner":
        return AccessCapabilities(can_access_app=False)

    if role == "owner":
        return AccessCapabilities(
            can_access_app=True,
            can_manage_configuration=True,
            can_manage_team=True,
            can_manage_products=True,
            can_manage_finances=True,
            can_manage_reservations=True,
            can_manage_reviews=True,
        )
    if role == "manager":
        return AccessCapabilities(
            can_access_app=True,
            can_manage_configuration=True,
            can_manage_team=False,
            can_manage_products=True,
            can_manage_finances=True,
            can_manage_reservations=True,
            can_manage_reviews=True,
        )
    # employee
    return AccessCapabilities(
        can_access_app=True,
        can_manage_configuration=False,
        can_manage_team=False,
        can_manage_products=False,
        can_manage_finances=True,
        can_manage_reservations=True,
        can_manage_reviews=False,
    )


# ---------------------------------------------------------------------------
# Core dependency
# ---------------------------------------------------------------------------

def get_auth_context(
    session: SessionDep, authorization: str = Header(...)
) -> AuthContext:
    """Resolve the full auth context: identity, role, subscription, capabilities."""
    user_id = _get_user_id_from_token(authorization)

    membership = session.exec(
        select(BusinessMember).where(BusinessMember.member_user_id == user_id)
    ).first()

    if membership:
        # If the member row has role='owner', treat as owner account
        if membership.role == "owner":
            subscription_active = _has_active_subscription(session, membership.business_id)
            caps = _build_capabilities("owner", subscription_active)
            return AuthContext(
                user_id=user_id,
                business_id=membership.business_id,
                role="owner",
                account_type="owner",
                member_status="active",
                subscription_active=subscription_active,
                capabilities=caps,
            )

        # Member: business_id points to the owner's id
        subscription_active = _has_active_subscription(session, membership.business_id)
        role = membership.role if membership.role in ("manager", "employee") else "employee"
        caps = _build_capabilities(role, subscription_active)
        return AuthContext(
            user_id=user_id,
            business_id=membership.business_id,
            role=role,
            account_type="member",
            member_status=membership.status,
            subscription_active=subscription_active,
            capabilities=caps,
        )

    # Owner: business_id == user_id
    subscription_active = _has_active_subscription(session, user_id)
    caps = _build_capabilities("owner", subscription_active)
    return AuthContext(
        user_id=user_id,
        business_id=user_id,
        role="owner",
        account_type="owner",
        member_status="active",
        subscription_active=subscription_active,
        capabilities=caps,
    )


# ---------------------------------------------------------------------------
# Reusable authorization dependencies
# ---------------------------------------------------------------------------

def require_active_member(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Allow only users who have completed onboarding."""
    if auth.account_type == "member" and auth.member_status != "active":
        raise HTTPException(
            status_code=403,
            detail="Employee onboarding must be completed before accessing business data",
        )
    return auth


def require_subscription(auth: AuthContext = Depends(require_active_member)) -> AuthContext:
    """Allow all members; require active subscription only for owners."""
    if auth.role == "owner" and not auth.subscription_active:
        raise HTTPException(
            status_code=403,
            detail="An active subscription is required to access this resource",
        )
    return auth


def require_owner(auth: AuthContext = Depends(require_subscription)) -> AuthContext:
    """Allow only owners (with active subscription)."""
    if auth.role != "owner":
        raise HTTPException(
            status_code=403, detail="Only owners can perform this action"
        )
    return auth


def require_manager_or_owner(auth: AuthContext = Depends(require_subscription)) -> AuthContext:
    """Allow managers and owners (with active subscription)."""
    if auth.role not in ("owner", "manager"):
        raise HTTPException(
            status_code=403, detail="Only managers and owners can perform this action"
        )
    return auth

