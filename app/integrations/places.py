"""Google Places API (New) integration for provider qualification.

Falls back to data/providers_seed.csv when GOOGLE_PLACES_API_KEY is absent.
"""

from __future__ import annotations

import os
from typing import Any

_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


async def search_providers(
    port: str, service_type: str, max_results: int = 10
) -> list[dict[str, Any]]:
    """Text Search for providers at a port by service type.

    Returns raw Places API response dicts. Qualification happens in app/risk/assess.py.
    Falls back to seed CSV if no API key.
    """
    if not _API_KEY:
        return _load_seed_csv(port, service_type)
    raise NotImplementedError(
        "TODO P2: POST to Places Text Search API. "
        "Fields: displayName, rating, userRatingCount, websiteUri, "
        "internationalPhoneNumber, googleMapsUri, reviews. "
        "Respect Google Maps Platform terms: fetch at generation time, "
        "show attribution, persist only place_id."
    )


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
