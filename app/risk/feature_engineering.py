"""Stage 1 feature engineering — GDELT + PortWatch + Gemma → ChokePointFeatures.

Ericka's workstream. Claude Code scaffolds the interface; Ericka implements.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.cdm import ChokePointFeatures


async def build_features(
    chokepoint: str,
    vessel_lat: float | None = None,
    vessel_lon: float | None = None,
    reference_date: datetime | None = None,
) -> ChokePointFeatures:
    """Fetch GDELT events + PortWatch vessel count + Gemma news risk score.

    Returns ChokePointFeatures ready for the ML blockage classifier.
    """
    raise NotImplementedError(
        "TODO Stage1/Ericka: "
        "1. Fetch GDELT events for chokepoint → goldstein_score "
        "2. Get PortWatch vessel count for destination port "
        "3. Call Gemma (via app/llm.py) for 0-10 news_risk_score + reason "
        "4. Compute vessel_distance_km if lat/lon available "
        "Return ChokePointFeatures."
    )
