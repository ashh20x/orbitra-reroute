"""GDELT API integration — news events + Goldstein sentiment scores.

Part of Stage 1 (Ericka's data pipeline).
Caches responses to data/gdelt_cache/ to avoid repeated fetches.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime
from typing import Any

_GDELT_V2_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_CACHE_DIR = pathlib.Path(
    os.environ.get("GDELT_CACHE_DIR", "data/gdelt_cache")
)


async def fetch_chokepoint_events(
    chokepoint: str,
    since: datetime,
    max_records: int = 100,
) -> list[dict[str, Any]]:
    """Fetch news events mentioning a chokepoint from GDELT.

    Returns list of event dicts with fields: url, title, goldstein_score, date.
    Results are cached to _CACHE_DIR.
    """
    raise NotImplementedError(
        "TODO Stage1/Ericka: GET GDELT API with query for chokepoint keyword. "
        "Extract Goldstein scale scores. Cache responses to data/gdelt_cache/. "
        "Handle API rate limits gracefully."
    )


async def get_article_text(url: str) -> str:
    """Fetch article text for LLM news risk scoring."""
    raise NotImplementedError(
        "TODO Stage1/Ericka: fetch article text for Gemma news risk scoring."
    )
