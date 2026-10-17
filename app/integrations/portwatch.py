"""IMF PortWatch via HDX integration — vessel counts and blockage labels.

Part of Stage 1 (Ericka's data pipeline). Training labels come from
data/portwatch_targets.csv (pre-downloaded). This module handles live
vessel count queries for feature engineering at inference time.
"""

from __future__ import annotations

import csv
import pathlib
from datetime import datetime
from typing import Any

_DATA_DIR = pathlib.Path(__file__).parent.parent.parent / "data"


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
    """
    raise NotImplementedError(
        "TODO Stage1/Ericka: query IMF PortWatch API or HDX dataset "
        "for vessel count at the given port/date. "
        "Focused on Middle East region ports."
    )
