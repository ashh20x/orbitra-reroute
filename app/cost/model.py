"""Deterministic cost forecast — parametric triangular distributions + Monte Carlo.

LLMs are NOT used for the numbers. Gemma only writes the plain-language summary.
All parameters in config/cost_assumptions.yaml. PLACEHOLDER markers shown in UI.
"""

from __future__ import annotations

import pathlib
from typing import Any

import numpy as np
import yaml

from app.domain.cdm import CostForecast, RiskAssessment

_CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "config" / "cost_assumptions.yaml"
_N_DRAWS = 5_000


def _load_assumptions() -> dict[str, Any]:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _triangular(low: float, mode: float, high: float, n: int) -> np.ndarray:
    return np.random.triangular(low, mode, high, n)


async def forecast(assessment: RiskAssessment, container_count: int) -> CostForecast:
    """Run Monte Carlo simulation and return P10/P50/P90 cost estimates.

    Raises if config file is missing. Shows PLACEHOLDER banner when any
    assumption has source='PLACEHOLDER — replace with quote'.
    """
    assumptions = _load_assumptions()
    port = assessment.recommended_port or "Salalah"
    port_params = assumptions.get("ports", {}).get(port, {})

    rng = np.random.default_rng(42)
    n = _N_DRAWS

    # --- Components (all per-container, USD) ---
    # Extra sea days × reefer daily cost
    sea_days = _triangular(
        port_params.get("extra_sea_days", {}).get("low", 3),
        port_params.get("extra_sea_days", {}).get("mode", 6),
        port_params.get("extra_sea_days", {}).get("high", 12),
        n,
    )
    reefer_daily = _triangular(
        assumptions["reefer_daily_usd"]["low"],
        assumptions["reefer_daily_usd"]["mode"],
        assumptions["reefer_daily_usd"]["high"],
        n,
    )

    # Handling at alt port
    handling = _triangular(
        port_params.get("handling_usd", {}).get("low", 300),
        port_params.get("handling_usd", {}).get("mode", 600),
        port_params.get("handling_usd", {}).get("high", 1200),
        n,
    )

    # Inspection + customs
    inspection = _triangular(
        port_params.get("inspection_usd", {}).get("low", 200),
        port_params.get("inspection_usd", {}).get("mode", 500),
        port_params.get("inspection_usd", {}).get("high", 1000),
        n,
    )

    # Insurance uplift (% of cargo value per container)
    cargo_value_per_container = assumptions.get("cargo_value_per_container_usd", {}).get("mode", 50000)
    insurance_uplift_pct = assumptions.get("insurance_uplift_pct", {}).get("mode", 0.5) / 100
    insurance = cargo_value_per_container * insurance_uplift_pct

    # Extension point for W5 dwell-time heuristics (ROADMAP — not implemented)
    dwell_multiplier = float(assumptions.get("dwell_time_multiplier", 1.0))

    total_per_container = (
        (sea_days * reefer_daily + handling + inspection + insurance) * dwell_multiplier
    )

    p10 = float(np.percentile(total_per_container, 10)) * container_count
    p50 = float(np.percentile(total_per_container, 50)) * container_count
    p90 = float(np.percentile(total_per_container, 90)) * container_count
    p50_per_container = float(np.percentile(total_per_container, 50))

    has_placeholders = any(
        "PLACEHOLDER" in str(v.get("source", ""))
        for section in assumptions.values()
        if isinstance(section, dict)
        for v in section.values()
        if isinstance(v, dict)
    )

    return CostForecast(
        shipment_id=assessment.shipment_id,
        assessment_id=assessment.id,
        alternative_port=port,
        p10_usd=round(p10, 2),
        p50_usd=round(p50, 2),
        p90_usd=round(p90, 2),
        per_container_p50_usd=round(p50_per_container, 2),
        container_count=container_count,
        assumptions_have_placeholders=has_placeholders,
    )
