import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
OUTSCRAPER_BASE_URL = "https://api.outscraper.cloud/google-maps-reviews"


def extract_google_id(url: str) -> str | None:
    """Extract google_id from a Google Maps URL.

    Primary: hex pattern 0x...:0x...
    Fallback: feature_id pattern /g/...
    """
    match = re.search(r"!1s(0x[a-f0-9]+:0x[a-f0-9]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"!16s([^?&]+)", url)
    if match:
        return match.group(1)
    return None


def fetch_business_and_reviews(
    google_id: str,
    reviews_limit: int = 100,
    cutoff: int = 0,
) -> dict:
    """Fetch business info and reviews from Outscraper API.

    Returns the first place result dict containing both business info and reviews.
    Raises appropriate exceptions for error status codes.
    """
    params = {
        "query": google_id,
        "limit": 1,
        "reviewsLimit": reviews_limit,
        "sort": "newest",
        "cutoff": cutoff,
        "language": "es",
        "async": "false",
    }
    headers = {"X-API-KEY": OUTSCRAPER_API_KEY}

    try:
        response = httpx.get(OUTSCRAPER_BASE_URL, params=params, headers=headers, timeout=120.0)
    except httpx.TimeoutException as exc:
        raise OutscraperError(
            "El servicio de reseñas tardó demasiado en responder. Intenta de nuevo."
        ) from exc

    if response.status_code == 202:
        raise OutscraperQueuedError("Solicitud en cola. Intenta de nuevo en unos momentos.")
    if response.status_code == 204:
        raise OutscraperNoResultsError("No se encontraron resultados para esta URL.")
    if response.status_code == 401:
        raise OutscraperAuthError("Error de autenticación con el servicio de reseñas.")
    if response.status_code == 422:
        raise OutscraperBadQueryError("Parámetro de búsqueda inválido.")
    if response.status_code != 200:
        raise OutscraperError(f"Error del servicio de reseñas: {response.status_code}")

    data = response.json()

    # Response structure: {"data": [[{place_data_with_reviews}]]}
    if not data or "data" not in data:
        raise OutscraperNoResultsError("Respuesta vacía del servicio de reseñas.")

    places = data["data"]
    if not places or not places[0]:
        raise OutscraperNoResultsError("No se encontraron resultados para esta URL.")

    # Response can be {"data": [{...}]} or {"data": [[{...}]]}
    first = places[0]
    result = first[0] if isinstance(first, list) else first
    return result


# Custom exceptions
class OutscraperError(Exception):
    pass


class OutscraperQueuedError(OutscraperError):
    pass


class OutscraperNoResultsError(OutscraperError):
    pass


class OutscraperAuthError(OutscraperError):
    pass


class OutscraperBadQueryError(OutscraperError):
    pass
