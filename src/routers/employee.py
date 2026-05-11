from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.auth import AuthContext, get_auth_context, require_owner
from src.routers.auth_context import SuccessResponse
from src.database.database import SessionDep
from src.services.employee_service import (
    complete_employee_onboarding,
    delete_employee,
    invite_employee,
    list_employees_for_business,
    update_employee,
)


class EmployeePublic(BaseModel):
    id: int | None = None
    businessId: str
    memberUserId: str
    displayName: str | None = None
    email: str | None = None
    phone: str | None = None
    role: str
    status: str
    locationId: int | None = None
    createdAt: datetime | None = None


class InviteEmployeeInput(BaseModel):
    displayName: str
    email: str
    phone: str
    role: str = "employee"


class UpdateEmployeeInput(BaseModel):
    role: str | None = None
    status: str | None = None
    locationId: int | None = None


class InviteEmployeeResponse(BaseModel):
    success: bool
    employees: list[EmployeePublic]


class CompleteEmployeeOnboardingInput(BaseModel):
    password: str


router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("/", response_model=list[EmployeePublic])
def get_employees(
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    return list_employees_for_business(session, auth.business_id)


@router.post("/", response_model=InviteEmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee_invite(
    employee: InviteEmployeeInput,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    employees = await invite_employee(
        session=session,
        business_id=auth.business_id,
        invited_by=auth.user_id,
        display_name=employee.displayName,
        email=employee.email,
        phone=employee.phone,
        role=employee.role,
    )
    return {"success": True, "employees": employees}


@router.delete("/{member_user_id}", response_model=SuccessResponse)
async def remove_employee(
    member_user_id: str,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    await delete_employee(
        session=session,
        business_id=auth.business_id,
        member_user_id=member_user_id,
    )
    return {"success": True}


@router.patch("/{member_user_id}", response_model=EmployeePublic)
def patch_employee(
    member_user_id: str,
    payload: UpdateEmployeeInput,
    session: SessionDep,
    auth: AuthContext = Depends(require_owner),
):
    return update_employee(
        session=session,
        business_id=auth.business_id,
        member_user_id=member_user_id,
        role=payload.role,
        status=payload.status,
        location_id=payload.locationId,
    )



@router.post("/onboarding/complete", response_model=SuccessResponse)
async def complete_onboarding(
    payload: CompleteEmployeeOnboardingInput,
    session: SessionDep,
    auth: AuthContext = Depends(get_auth_context),
):
    if auth.account_type != "member":
        raise HTTPException(
            status_code=403,
            detail="Solo los empleados invitados pueden completar este onboarding",
        )

    await complete_employee_onboarding(
        session=session,
        user_id=auth.user_id,
        password=payload.password,
    )
    return {"success": True}