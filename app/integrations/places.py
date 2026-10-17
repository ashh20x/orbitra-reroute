"""Google Places API (New) integration for provider qualification.

Falls back to data/providers_seed.csv when GOOGLE_PLACES_API_KEY is absent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Minimum thresholds from config/qualification.yaml
_MIN_RATING = 4.5
_MIN_REVIEW_COUNT = 50

_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.rating",
    "places.userRatingCount",
    "places.websiteUri",
    "places.internationalPhoneNumber",
    "places.googleMapsUri",
])


async def search_providers(
    port: str, service_type: str, max_results: int = 10
) -> list[dict[str, Any]]:
    """Text Search for providers at a port by service type.

    Returns provider dicts normalised to the same schema as providers_seed.csv.
    Falls back to seed CSV if no API key or on any API error.
    """
    if not _API_KEY:
        return _load_seed_csv(port, service_type)

    payload = {
        "textQuery": f"{service_type} {port} port cold chain logistics",
        "maxResultCount": max_results,
        "languageCode": "en",
    }
    headers = {
        "X-Goog-Api-Key": _API_KEY,
        "X-Goog-FieldMask": _FIELD_MASK,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(_TEXT_SEARCH_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Google Places API failed for %s/%s: %s — using seed", port, service_type, exc)
        return _load_seed_csv(port, service_type)

    results = []
    for place in data.get("places") or []:
        rating = place.get("rating") or 0.0
        review_count = place.get("userRatingCount") or 0
        if rating < _MIN_RATING or review_count < _MIN_REVIEW_COUNT:
            continue
        results.append({
            "place_id": place.get("id", ""),
            "name": (place.get("displayName") or {}).get("text", ""),
            "port": port,
            "service_type": service_type,
            "google_rating": rating,
            "google_review_count": review_count,
            "contact_website": place.get("websiteUri", ""),
            "google_maps_uri": place.get("googleMapsUri", ""),
            "pre_registration_requirements": "TBC with provider",
            "est_clearance_time": "TBC with provider",
            "notes": "Sourced via Google Places API",
            "preferred": False,
            "source_url": place.get("googleMapsUri", "NEEDS REVIEW"),
        })

    logger.info("Google Places: found %d qualified providers for %s/%s", len(results), port, service_type)

    if not results:
        logger.info("No Places results met qualification threshold — falling back to seed")
        return _load_seed_csv(port, service_type)

    return results


def _load_seed_csv(port: str, service_type: str) -> list[dict[str, Any]]:
    """Load providers from data/providers_seed.csv as fallback."""
    import csv
    import pathlib

    path = pathlib.Path(__file__).parent.parent.parent / "data" / "providers_seed.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("port", "").lower() == port.lower()]
