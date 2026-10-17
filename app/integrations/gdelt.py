"""GDELT API integration — news events + Goldstein sentiment scores.

Part of Stage 1 (Ericka's data pipeline).
Caches responses to data/gdelt_cache/ to avoid repeated fetches.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GDELT_V2_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_CACHE_DIR = pathlib.Path(
    os.environ.get("GDELT_CACHE_DIR", "data/gdelt_cache")
)
_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours


async def fetch_chokepoint_events(
    chokepoint: str,
    since: datetime,
    max_records: int = 100,
) -> list[dict[str, Any]]:
    """Fetch news events mentioning a chokepoint from GDELT.

    Returns list of event dicts with fields: url, title, goldstein_score, date.
    Results are cached to _CACHE_DIR.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    days_back = max(1, (datetime.utcnow() - since.replace(tzinfo=None)).days)
    cache_key = hashlib.md5(
        f"{chokepoint}-{since.date()}-{max_records}".encode()
    ).hexdigest()
    cache_path = _CACHE_DIR / f"{cache_key}.json"

    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < _CACHE_TTL_SECONDS:
        try:
            return json.loads(cache_path.read_text())
        except Exception:
            pass

    params = {
        "query": chokepoint,
        "mode": "ArtList",
        "maxrecords": str(max_records),
        "timespan": f"{days_back}d",
        "format": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(_GDELT_V2_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("GDELT fetch failed for '%s': %s", chokepoint, exc)
        return []

    articles = data.get("articles") or []
    events = [
        {
            "url": a.get("url", ""),
            "title": a.get("title", ""),
            # GDELT DOC API does not return Goldstein scores; 0.0 matches training data
            "goldstein_score": 0.0,
            "date": a.get("seendate", ""),
        }
        for a in articles
    ]

    try:
        cache_path.write_text(json.dumps(events))
    except Exception:
        pass

    logger.info("GDELT: fetched %d events for '%s'", len(events), chokepoint)
    return events


async def get_article_text(url: str) -> str:
    """Fetch and strip HTML from an article URL for LLM news risk scoring."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; OrbitraReroute/1.0; +https://orbitra.ai)"
            )
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.debug("get_article_text failed for %s: %s", url, exc)
        return ""

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]
