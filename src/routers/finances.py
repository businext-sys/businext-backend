import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import and_, func, case
from sqlmodel import select
from ..database.database import SessionDep
from ..database.models.finances_model import (
    Finances,
    FinancesPublic,
    FinancesBase,
    FinancesUpdate,
)
from ..database.models.profile_model import Profile
from ..database.models.product_model import Product
from src.api.auth import AuthContext, require_manager_or_owner, require_subscription


router = APIRouter(
    prefix="/finances",
    tags=["finances"],
    responses={404: {"description": "Not found"}},
)


def _employee_creator_name(session: SessionDep, auth: AuthContext) -> str | None:
    """Return the employee's display name to filter finances by creator."""
    if auth.role != "employee":
        return None
    profile = session.get(Profile, auth.user_id)
    if profile and profile.display_name:
        return profile.display_name
    return None


def _apply_employee_filter(
    query, session: SessionDep, auth: AuthContext, model
):
    """Add creator filter when the user is an employee."""
    creator = _employee_creator_name(session, auth)
    if creator:
        query = query.where(model.creator == creator)
    return query


@router.get("/", response_model=list[FinancesPublic])
def get_finances(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    query = select(Finances).where(Finances.business_id == auth.business_id)
    query = _apply_employee_filter(query, session, auth, Finances)
    finances = session.exec(query).all()
    if not finances:
        raise HTTPException(status_code=404, detail="No finances found")
    return finances


@router.get("/{finances_id}", response_model=FinancesPublic)
def get_finances_by_id(
    finances_id: int, session: SessionDep, auth: AuthContext = Depends(require_subscription)
):
    finances = session.get(Finances, finances_id)
    if not finances or finances.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Finances not found")
    # Employees can only see their own records
    if auth.role == "employee":
        creator = _employee_creator_name(session, auth)
        if not creator or finances.creator != creator:
            raise HTTPException(status_code=404, detail="Finances not found")
    return finances


@router.get("/annual_finances/{year}")
def get_annual_finances(
    session: SessionDep, year: int, auth: AuthContext = Depends(require_subscription)
):
    start = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)

    query = select(
        func.extract("month", Finances.created_at).label("month"),
        func.sum(
            case((Finances.type == "INCOME", Finances.amount), else_=0)
        ).label("incomes"),
        func.sum(
            case((Finances.type == "EXPENSE", Finances.amount), else_=0)
        ).label("expenses"),
    ).where(
        and_(
            Finances.created_at >= start,
            Finances.created_at <= end,
            Finances.business_id == auth.business_id,
        )
    )
    query = _apply_employee_filter(query, session, auth, Finances)
    query = query.group_by(func.extract("month", Finances.created_at))

    rows = session.exec(query).all()

    totals = {int(row.month): (row.incomes or 0, row.expenses or 0) for row in rows}

    return [
        {
            "month": month,
            "balance": totals.get(month, (0, 0))[0] - totals.get(month, (0, 0))[1],
        }
        for month in range(1, 13)
    ]


@router.post("/", response_model=FinancesPublic)
def create_finances(
    finances: FinancesBase,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    if auth.role == "employee":
        profile = session.get(Profile, auth.user_id)
        emp_name = profile.display_name if profile else None
        if not emp_name or finances.creator != emp_name:
            raise HTTPException(
                status_code=403,
                detail="Los empleados solo pueden crear registros a nombre propio",
            )
        if finances.type != "INCOME":
            raise HTTPException(
                status_code=403,
                detail="Los empleados solo pueden crear registros de ingreso",
            )
    finances_data = finances.model_dump()
    finances_data["business_id"] = auth.business_id

    # Look up commission percentage if linked to a product
    commission_amount = 0.0
    commission_pct = 0.0
    if finances.product_id:
        product = session.get(Product, finances.product_id)
        if product and product.commission_percentage and product.business_id == auth.business_id:
            commission_pct = product.commission_percentage
            commission_amount = round(finances.amount * commission_pct / 100, 2)

    finances_obj = Finances(**finances_data)
    session.add(finances_obj)

    # Auto-create commission expense record when applicable
    if commission_amount > 0 and finances.type == "INCOME":
        commission_record = Finances(
            concept=f"Comisión ({commission_pct:.0f}%) por {finances.concept}",
            amount=commission_amount,
            type="EXPENSE",
            creator=finances.creator,
            business_id=auth.business_id,
            product_id=finances.product_id,
        )
        session.add(commission_record)

    session.commit()
    session.refresh(finances_obj)
    return finances_obj


@router.patch("/{finances_id}", response_model=FinancesPublic)
def update_finances(
    finances_id: int,
    finances: FinancesUpdate,
    session: SessionDep,
    auth: AuthContext = Depends(require_manager_or_owner),
):
    finances_db = session.get(Finances, finances_id)
    if not finances_db or finances_db.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Finances not found")
    finances_data = finances.model_dump(exclude_unset=True)
    finances_db.sqlmodel_update(finances_data)
    session.add(finances_db)
    session.commit()
    session.refresh(finances_db)
    return finances_db


@router.delete("/{finances_id}", response_model=dict)
def delete_finances(
    finances_id: int, session: SessionDep, auth: AuthContext = Depends(require_manager_or_owner)
):
    finances_db = session.get(Finances, finances_id)
    if not finances_db or finances_db.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Finances not found")
    if finances_db.reservation_id is not None:
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar un registro vinculado a una reserva. Revertir la reserva primero.",
        )
    session.delete(finances_db)
    session.commit()
    return {"Finances record deleted": True}
