"""IMF PortWatch via HDX integration — vessel counts and blockage labels.

Part of Stage 1 (Ericka's data pipeline). Training labels come from
data/portwatch_targets.csv (pre-downloaded). This module handles live
vessel count queries for feature engineering at inference time.
"""

from __future__ import annotations

import csv
import logging
import os
import pathlib
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DATA_DIR = pathlib.Path(__file__).parent.parent.parent / "data"

# Heuristic defaults (vessels/day) derived from seed training data medians
_VESSEL_DEFAULTS: dict[str, int] = {
    "Strait of Hormuz": 160,
    "Hormuz": 160,
    "Bab-el-Mandeb": 90,
    "Red Sea": 90,
    "Jebel Ali": 160,
    "Jeddah": 90,
    "Salalah": 90,
}

_PORTWATCH_API_KEY = os.environ.get("PORTWATCH_API_KEY", "")
_PORTWATCH_BASE = "https://portwatch.imf.org/api/v1"


def load_training_targets() -> list[dict[str, Any]]:
    """Load pre-downloaded PortWatch blockage labels from CSV."""
    path = _DATA_DIR / "portwatch_targets.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def get_vessel_count(port: str, date: datetime | None = None) -> int | None:
    """Fetch current vessel count for a port (port congestion feature).

    Returns None if unavailable (handled gracefully in feature engineering).
    Falls back to seed CSV data when PORTWATCH_API_KEY is absent.
    """
    if _PORTWATCH_API_KEY:
        date_str = (date or datetime.utcnow()).strftime("%Y-%m-%d")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_PORTWATCH_BASE}/portcalls",
                    params={"port_name": port, "date": date_str},
                    headers={"Authorization": f"Bearer {_PORTWATCH_API_KEY}"},
                )
                resp.raise_for_status()
                data = resp.json()
                count = data.get("vessel_count") or data.get("count")
                if count is not None:
                    return int(count)
        except Exception as exc:
            logger.warning("PortWatch API failed for '%s': %s — falling back to seed", port, exc)

    # Seed CSV lookup: find rows where chokepoint contains port name (case-insensitive)
    port_lower = port.lower()
    rows = load_training_targets()
    matching = [
        r for r in rows
        if port_lower in r.get("chokepoint", "").lower()
        or r.get("chokepoint", "").lower() in port_lower
    ]
    if matching:
        # Return vessel_count from the most recent matching row
        matching.sort(key=lambda r: r.get("date", ""), reverse=True)
        try:
            return int(matching[0]["vessel_count"])
        except (KeyError, ValueError):
            pass

    # Last resort: heuristic defaults by well-known corridor names
    for key, default in _VESSEL_DEFAULTS.items():
        if key.lower() in port_lower or port_lower in key.lower():
            return default

    return None
