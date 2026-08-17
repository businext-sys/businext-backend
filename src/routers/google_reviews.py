import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import col, select

from src.api.auth import AuthContext, require_subscription
from src.services.ai_service import (
    AIServiceError,
    generate_business_summary,
    generate_review_response,
)
from src.services.outscraper_service import (
    OutscraperError,
    OutscraperQueuedError,
    extract_google_id,
    fetch_business_and_reviews,
)

from ..database.database import SessionDep
from ..database.models.google_business_profile_model import (
    GoogleBusinessProfile,
    GoogleBusinessProfilePublic,
)
from ..database.models.google_review_model import (
    GoogleReview,
    GoogleReviewPublic,
)

router = APIRouter(
    prefix="/google-reviews",
    tags=["google-reviews"],
    responses={404: {"description": "Not found"}},
)


# ── Request/Response models ────────────────────────────────────────────

class SubmitUrlRequest(BaseModel):
    source_url: str


class SyncResponse(BaseModel):
    new_reviews_count: int
    total_reviews: int
    last_sync_at: str


class PaginatedReviewsResponse(BaseModel):
    items: list[GoogleReviewPublic]
    total: int
    page: int
    page_size: int
    total_pages: int


class GenerateResponseResult(BaseModel):
    review_id: int
    ai_generated_response: str
    ai_response_generated_at: str


# ── Helpers ─────────────────────────────────────────────────────────────

def _persist_reviews(
    session: SessionDep,
    profile: GoogleBusinessProfile,
    reviews_data: list[dict],
) -> int:
    """Persist fetched reviews, skipping duplicates. Returns count of new reviews."""
    new_count = 0
    for r in reviews_data:
        rid = r.get("review_id")
        if not rid:
            continue
        existing = session.exec(
            select(GoogleReview).where(GoogleReview.review_id == rid)
        ).first()
        if existing:
            continue

        review = GoogleReview(
            business_id=profile.business_id,
            profile_id=profile.id,  # type: ignore[arg-type]
            review_id=rid,
            author_title=r.get("author_title"),
            author_image=r.get("author_image"),
            review_text=r.get("review_text"),
            review_rating=r.get("review_rating", 0),
            review_timestamp=r.get("review_timestamp", 0),
            review_datetime_utc=r.get("review_datetime_utc"),
            review_link=r.get("review_link"),
            owner_answer=r.get("owner_answer"),
            owner_answer_timestamp=r.get("owner_answer_timestamp"),
        )
        session.add(review)
        new_count += 1

    return new_count


# ── Endpoints ───────────────────────────────────────────────────────────

@router.post("/profile", response_model=GoogleBusinessProfilePublic, status_code=201)
def create_profile(
    body: SubmitUrlRequest,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Submit a Google Maps URL to create a business profile."""
    # Check if profile already exists
    existing = session.exec(
        select(GoogleBusinessProfile).where(
            GoogleBusinessProfile.business_id == auth.business_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Este negocio ya tiene un perfil de Google vinculado.")

    # Extract google_id
    google_id = extract_google_id(body.source_url)
    if not google_id:
        raise HTTPException(
            status_code=400,
            detail="No se pudo extraer el identificador de Google Maps de la URL proporcionada. "
                   "Asegúrate de copiar la URL completa desde Google Maps.",
        )

    # Fetch from Outscraper
    try:
        place_data = fetch_business_and_reviews(google_id)
    except OutscraperError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    now = datetime.now(timezone.utc)

    # Build reviews_per_score JSON
    reviews_per_score = place_data.get("reviews_per_score")
    if isinstance(reviews_per_score, dict):
        reviews_per_score = json.dumps(reviews_per_score)
    else:
        # Calculate from reviews_data if API doesn't provide it
        reviews_data_raw = place_data.get("reviews_data", [])
        if reviews_data_raw:
            score_counts = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
            for r in reviews_data_raw:
                rating_val = r.get("review_rating", 0)
                if 1 <= rating_val <= 5:
                    score_counts[str(rating_val)] += 1
            reviews_per_score = json.dumps(score_counts)
        else:
            reviews_per_score = None

    profile = GoogleBusinessProfile(
        business_id=auth.business_id,
        source_url=body.source_url,
        google_id=google_id,
        name=place_data.get("name"),
        address=place_data.get("full_address") or place_data.get("address"),
        category=place_data.get("category") or place_data.get("type"),
        phone=place_data.get("phone"),
        rating=place_data.get("rating"),
        total_reviews=place_data.get("reviews", 0),
        reviews_per_score=reviews_per_score,
        location_link=place_data.get("location_link"),
        last_sync_at=now,
    )

    session.add(profile)
    session.commit()
    session.refresh(profile)

    # Persist reviews
    reviews_data = place_data.get("reviews_data", [])
    if reviews_data:
        _persist_reviews(session, profile, reviews_data)
        # Update last_review_timestamp
        max_ts = max(r.get("review_timestamp", 0) for r in reviews_data)
        profile.last_review_timestamp = max_ts
        session.add(profile)
        session.commit()
        session.refresh(profile)

    return profile


@router.get("/profile", response_model=GoogleBusinessProfilePublic)
def get_profile(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Get the current business's Google profile."""
    profile = session.exec(
        select(GoogleBusinessProfile).where(
            GoogleBusinessProfile.business_id == auth.business_id
        )
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No se encontró un perfil de Google vinculado.")
    return profile


@router.post("/sync", response_model=SyncResponse)
def sync_reviews(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Trigger incremental review sync from Outscraper."""
    profile = session.exec(
        select(GoogleBusinessProfile).where(
            GoogleBusinessProfile.business_id == auth.business_id
        )
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No se encontró un perfil de Google vinculado.")

    # Rate limit: allow sync only once per week
    if profile.last_sync_at:
        days_since_sync = (datetime.now(timezone.utc) - profile.last_sync_at.replace(tzinfo=timezone.utc)).days
        if days_since_sync < 7:
            days_remaining = 7 - days_since_sync
            raise HTTPException(
                status_code=429,
                detail=f"Solo puedes sincronizar las reseñas una vez por semana. Podrás volver a sincronizar en {days_remaining} día{'s' if days_remaining != 1 else ''}.",
            )

    try:
        place_data = fetch_business_and_reviews(
            profile.google_id,
            reviews_limit=100,
            cutoff=profile.last_review_timestamp,
        )
    except OutscraperQueuedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except OutscraperError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    now = datetime.now(timezone.utc)

    # Update profile with latest business info
    profile.rating = place_data.get("rating", profile.rating)
    profile.total_reviews = place_data.get("reviews", profile.total_reviews)
    reviews_per_score = place_data.get("reviews_per_score")
    if isinstance(reviews_per_score, dict):
        profile.reviews_per_score = json.dumps(reviews_per_score)
    profile.last_sync_at = now
    profile.updated_at = now

    # Persist new reviews
    reviews_data = place_data.get("reviews_data", [])
    new_count = 0
    if reviews_data:
        new_count = _persist_reviews(session, profile, reviews_data)
        max_ts = max(r.get("review_timestamp", 0) for r in reviews_data)
        if max_ts > profile.last_review_timestamp:
            profile.last_review_timestamp = max_ts

    # Recalculate reviews_per_score from all stored reviews if still missing
    if not profile.reviews_per_score:
        all_reviews = session.exec(
            select(GoogleReview).where(GoogleReview.profile_id == profile.id)
        ).all()
        if all_reviews:
            score_counts: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
            for r in all_reviews:
                if 1 <= r.review_rating <= 5:
                    score_counts[str(r.review_rating)] += 1
            profile.reviews_per_score = json.dumps(score_counts)

    session.add(profile)
    session.commit()
    session.refresh(profile)

    return SyncResponse(
        new_reviews_count=new_count,
        total_reviews=profile.total_reviews,
        last_sync_at=now.isoformat(),
    )


@router.get("/reviews", response_model=PaginatedReviewsResponse)
def get_reviews(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    rating: int | None = Query(default=None, ge=1, le=5),
    sort: str = Query(default="newest"),
    search: str | None = Query(default=None),
):
    """Get paginated reviews for the business."""
    # Base query
    query = select(GoogleReview).where(GoogleReview.business_id == auth.business_id)

    # Filters
    if rating:
        query = query.where(GoogleReview.review_rating == rating)
    if search:
        query = query.where(col(GoogleReview.review_text).contains(search))

    # Count total
    count_query = select(GoogleReview).where(GoogleReview.business_id == auth.business_id)
    if rating:
        count_query = count_query.where(GoogleReview.review_rating == rating)
    if search:
        count_query = count_query.where(col(GoogleReview.review_text).contains(search))
    all_matching = session.exec(count_query).all()
    total = len(all_matching)

    # Sort
    if sort == "oldest":
        query = query.order_by(GoogleReview.review_timestamp.asc())  # type: ignore[union-attr]
    elif sort == "highest":
        query = query.order_by(GoogleReview.review_rating.desc())  # type: ignore[union-attr]
    elif sort == "lowest":
        query = query.order_by(GoogleReview.review_rating.asc())  # type: ignore[union-attr]
    else:  # newest (default)
        query = query.order_by(GoogleReview.review_timestamp.desc())  # type: ignore[union-attr]

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    items = session.exec(query).all()
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedReviewsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/reviews/{review_db_id}/generate-response", response_model=GenerateResponseResult)
def generate_response_for_review(
    review_db_id: int,
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Generate an AI response for a specific review."""
    review = session.get(GoogleReview, review_db_id)
    if not review or review.business_id != auth.business_id:
        raise HTTPException(status_code=404, detail="Reseña no encontrada.")

    # Get profile for business name
    profile = session.exec(
        select(GoogleBusinessProfile).where(
            GoogleBusinessProfile.business_id == auth.business_id
        )
    ).first()
    business_name = profile.name if profile else "Negocio"

    try:
        response_text = generate_review_response(
            review_text=review.review_text or "",
            review_rating=review.review_rating,
            business_name=business_name,
        )
    except AIServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    now = datetime.now(timezone.utc)
    review.ai_generated_response = response_text
    review.ai_response_generated_at = now
    session.add(review)
    session.commit()
    session.refresh(review)

    return GenerateResponseResult(
        review_id=review.id,  # type: ignore[arg-type]
        ai_generated_response=response_text,
        ai_response_generated_at=now.isoformat(),
    )


@router.post("/summary")
def generate_summary(
    session: SessionDep,
    auth: AuthContext = Depends(require_subscription),
):
    """Generate or refresh the AI business summary."""
    profile = session.exec(
        select(GoogleBusinessProfile).where(
            GoogleBusinessProfile.business_id == auth.business_id
        )
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="No se encontró un perfil de Google vinculado.")

    # Get all reviews
    reviews = session.exec(
        select(GoogleReview)
        .where(GoogleReview.business_id == auth.business_id)
        .order_by(GoogleReview.review_timestamp.desc())  # type: ignore[union-attr]
    ).all()

    if len(reviews) < 10:
        raise HTTPException(
            status_code=400,
            detail="Se necesitan al menos 10 reseñas para generar un análisis completo.",
        )

    # Build review dicts for AI
    review_dicts = [
        {
            "review_text": r.review_text,
            "review_rating": r.review_rating,
        }
        for r in reviews
    ]

    try:
        summary = generate_business_summary(review_dicts, profile.name or "Negocio")
    except AIServiceError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Persist summary
    now = datetime.now(timezone.utc)
    profile.ai_summary = json.dumps(summary, ensure_ascii=False)
    profile.ai_summary_generated_at = now
    profile.updated_at = now
    session.add(profile)
    session.commit()

    return summary


## PATCH /profile endpoint removed — validation logic disabled for now.
## The validation_status column remains in the DB but is unused.
