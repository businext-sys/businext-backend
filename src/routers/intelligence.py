from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from src.api.auth import AuthContext, require_subscription
from src.services.ai_service import AIServiceError, generate_weekly_summary

from ..database.database import SessionDep
from ..database.models.business_conf_model import BusinessConfiguration
from ..database.models.finances_model import Finances
from ..database.models.reservation_model import Reservation
from ..database.models.weekly_summary_model import WeeklySummary, WeeklySummaryPublic

router = APIRouter(
    prefix="/intelligence",
    tags=["intelligence"],
    responses={404: {"description": "Not found"}},
)


def _get_week_range(ref: date) -> tuple[date, date]:
    """Return (Monday, Sunday) of the week containing `ref`."""
    monday = ref - timedelta(days=ref.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _prev_week_range(week_start: date) -> tuple[date, date]:
    prev_monday = week_start - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday, prev_sunday


@router.get("/summary", response_model=WeeklySummaryPublic)
def get_summary(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Return the most recent weekly summary for this business."""
    summary = session.exec(
        select(WeeklySummary)
        .where(WeeklySummary.business_id == auth.business_id)
        .order_by(WeeklySummary.week_start.desc())  # type: ignore[union-attr]
    ).first()

    if not summary:
        raise HTTPException(status_code=404, detail="No se encontró ningún resumen semanal.")

    return summary


@router.post("/summary/generate", response_model=WeeklySummaryPublic)
def generate_summary(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Generate the weekly summary for the previous week."""
    today = datetime.now(timezone.utc).date()
    prev_monday, prev_sunday = _get_week_range(today - timedelta(days=7))

    # Check if already exists
    existing = session.exec(
        select(WeeklySummary).where(
            WeeklySummary.business_id == auth.business_id,
            WeeklySummary.week_start == prev_monday,
        )
    ).first()

    if existing:
        next_monday = prev_monday + timedelta(days=7)
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un resumen para esta semana. Podrás generar el siguiente el lunes {next_monday + timedelta(days=7)}.",
        )

    # Get business name
    config = session.exec(
        select(BusinessConfiguration).where(
            BusinessConfiguration.business_id == auth.business_id
        )
    ).first()
    business_name = config.business_name if config else "Negocio"

    # Fetch current week finances & reservations
    def _fetch_data(start: date, end: date) -> tuple[list[dict], list[dict]]:
        start_dt = datetime(start.year, start.month, start.day)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59)

        finances = session.exec(
            select(Finances).where(
                Finances.business_id == auth.business_id,
                Finances.created_at >= start_dt,
                Finances.created_at <= end_dt,
            )
        ).all()

        reservations = session.exec(
            select(Reservation).where(
                Reservation.business_id == auth.business_id,
                Reservation.reservation_start_date >= start_dt,
                Reservation.reservation_start_date <= end_dt,
            )
        ).all()

        fin_dicts = [
            {
                "amount": f.amount,
                "type": f.type,
                "concept": f.concept,
                "creator": f.creator,
                "reservation_id": f.reservation_id,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in finances
        ]
        res_dicts = [
            {
                "customer_name": r.customer_name,
                "service": r.service,
                "status": r.status,
                "in_charge": r.in_charge,
                "reservation_start_date": r.reservation_start_date.isoformat() if r.reservation_start_date else None,
            }
            for r in reservations
        ]
        return fin_dicts, res_dicts

    curr_fin, curr_res = _fetch_data(prev_monday, prev_sunday)
    pp_monday, pp_sunday = _prev_week_range(prev_monday)
    prev_fin, prev_res = _fetch_data(pp_monday, pp_sunday)

    week_label = f"{prev_monday.strftime('%d/%m/%Y')} - {prev_sunday.strftime('%d/%m/%Y')}"

    # Fetch previous summary narrative for richer AI context
    prev_summary = session.exec(
        select(WeeklySummary).where(
            WeeklySummary.business_id == auth.business_id,
            WeeklySummary.week_start == pp_monday,
        )
    ).first()
    prev_narrative = prev_summary.narrative if prev_summary else None

    try:
        ai_result = generate_weekly_summary(
            business_name=business_name,
            week_label=week_label,
            finances_data=curr_fin,
            reservations_data=curr_res,
            prev_finances_data=prev_fin,
            prev_reservations_data=prev_res,
            prev_week_narrative=prev_narrative,
        )
    except AIServiceError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="El servicio de IA no está disponible. Inténtalo de nuevo más tarde.",
        ) from exc

    summary = WeeklySummary(
        business_id=auth.business_id,
        week_start=prev_monday,
        week_end=prev_sunday,
        narrative=ai_result["narrative"],
        kpis=ai_result["kpis"],
        client_narrative=ai_result.get("client_narrative"),
    )

    session.add(summary)
    session.commit()
    session.refresh(summary)

    return summary
