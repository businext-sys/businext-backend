import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (
    auth_context,
    booking_request,
    configuration,
    employee,
    finances,
    google_reviews,
    intelligence,
    location,
    product,
    public_booking,
    push_token,
    reservation,
    working_hours,
)
from .services.expiration_service import expire_old_requests


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background task for expiring old booking requests
    task = asyncio.create_task(expire_old_requests())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

origins = [
    "http://localhost:3000",
    "https://businext.greenfourtech.com",
    # Staging frontend (Vercel preview, stable branch alias)
    "https://businext-frontend-git-staging-daniflorezms-projects.vercel.app",
]

# Vercel genera un subdominio distinto por cada deploy de preview
# (businext-frontend-<hash>-daniflorezms-projects.vercel.app). El alias de
# branch de arriba es estable, pero este regex cubre tambien los deploys
# individuales del proyecto en staging para que el qa-runner y los previews
# efimeros no fallen por CORS.
allow_origin_regex = (
    r"https://businext-frontend-[a-z0-9-]+-daniflorezms-projects\.vercel\.app"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth_context.router)
app.include_router(employee.router)
app.include_router(reservation.router)
app.include_router(configuration.router)
app.include_router(product.router)
app.include_router(finances.router)
app.include_router(google_reviews.router)
app.include_router(working_hours.router)
app.include_router(intelligence.router)
app.include_router(public_booking.router)
app.include_router(booking_request.router)
app.include_router(location.router)
app.include_router(push_token.router)
