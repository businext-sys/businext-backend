import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, case, func
from sqlmodel import select

from ..database.database import SessionDep
from ..database.models.business_conf_model import BusinessConfiguration
from ..database.models.finances_model import (
    Finances,
    FinancesBase,
    FinancesPublic,
    FinancesUpdate,
)
from ..database.models.product_model import Product
from ..database.models.profile_model import Profile
from src.api.auth import AuthContext, require_manager_or_owner, require_subscription


router = APIRouter(
    prefix="/finances",
    tags=["finances"],
    responses={404: {"description": "Not found"}},
)


def _non_owner_creator_name(session: SessionDep, auth: AuthContext) -> str | None:
    """Return normalized creator name for non-owner users."""
    if auth.role == "owner":
        return None
    profile = session.get(Profile, auth.user_id)
    if profile and profile.display_name:
        return _normalize_text(profile.display_name)
    return None


def _normalize_text(value: str | None) -> str:
    return value.strip().lower() if value else ""


def _is_owner_creator(session: SessionDep, business_id: str, creator: str) -> bool:
    owner_profile = session.get(Profile, business_id)
    if not owner_profile:
        return False

    creator_normalized = _normalize_text(creator)
    owner_names = {
        _normalize_text(owner_profile.display_name),
        _normalize_text(owner_profile.email),
    }
    owner_names.discard("")

    return creator_normalized in owner_names


def _normalize_commission_rate(rate: float | None) -> float:
    if rate is None:
        return 0.0
    return max(0.0, min(100.0, float(rate)))


def _resolve_sale_kind(session: SessionDep, business_id: str, finance: FinancesBase) -> str | None:
    if finance.reservation_id is not None:
        return "service"

    if finance.product_id is not None:
        product_by_id = session.exec(
            select(Product).where(
                Product.id == finance.product_id,
                Product.business_id == business_id,
            )
        ).first()
        if not product_by_id or not product_by_id.type:
            return None
        product_type_by_id = product_by_id.type.strip().lower()
        if product_type_by_id in ("producto", "product"):
            return "product"
        if product_type_by_id in ("servicio", "service"):
            return "service"
        return None

    product = session.exec(
        select(Product).where(
            Product.business_id == business_id,
            Product.name == finance.concept,
        )
    ).first()
    if not product or not product.type:
        return None

    product_type = product.type.strip().lower()
    if product_type in ("producto", "product"):
        return "product"
    if product_type in ("servicio", "service"):
        return "service"
    return None


def _calculate_commission(
    session: SessionDep,
    business_id: str,
    finance: FinancesBase,
) -> tuple[float, float]:
    if finance.type != "INCOME" or finance.amount <= 0:
        return 0.0, 0.0

    if _is_owner_creator(session, business_id, finance.creator):
        return 0.0, 0.0

    sale_kind = _resolve_sale_kind(session, business_id, finance)
    if not sale_kind:
        return 0.0, 0.0

    configuration = session.exec(
        select(BusinessConfiguration).where(
            BusinessConfiguration.business_id == business_id
        )
    ).first()

    product_rate = _normalize_commission_rate(
        configuration.commission_product if configuration else None
    )
    service_rate = _normalize_commission_rate(
        configuration.commission_service if configuration else None
    )

    commission_rate = product_rate if sale_kind == "product" else service_rate
    if commission_rate <= 0:
        return 0.0, 0.0

    commission_amount = round(finance.amount * commission_rate / 100, 2)
    return round(commission_rate, 2), commission_amount


def _apply_employee_filter(
    query, session: SessionDep, auth: AuthContext, model
):
    """Add creator filter for non-owner users (employee + manager)."""
    if auth.role == "owner":
        return query

    creator = _non_owner_creator_name(session, auth)
    if not creator:
        # Defensive: non-owner without profile aliases must not see all records.
        return query.where(False)

    query = query.where(func.lower(model.creator) == creator)
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
    # Non-owners can only see their own records
    if auth.role != "owner":
        creator = _non_owner_creator_name(session, auth)
        if not creator or _normalize_text(finances.creator) != creator:
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
    commission_rate, commission_amount = _calculate_commission(
        session=session,
        business_id=auth.business_id,
        finance=finances,
    )
    finances_data["commission_rate"] = commission_rate
    finances_data["commission_amount"] = commission_amount
    finances_obj = Finances(**finances_data)
    session.add(finances_obj)
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
