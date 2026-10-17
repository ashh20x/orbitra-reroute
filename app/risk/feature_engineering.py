"""Stage 1 feature engineering — GDELT + PortWatch + Gemma → ChokePointFeatures.

Ericka's workstream. Claude Code scaffolds the interface; Ericka implements.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from app.domain.cdm import ChokePointFeatures, DisruptionSignal, Shipment

logger = logging.getLogger(__name__)


class _NewsRiskScore(BaseModel):
    news_risk_score: float
    reason: str


async def build_features(
    shipment: Shipment,
    signal: DisruptionSignal,
) -> ChokePointFeatures:
    """Fetch GDELT events + PortWatch vessel count + Gemma news risk score.

    Returns ChokePointFeatures ready for the ML blockage classifier.
    """
    from app.integrations.gdelt import fetch_chokepoint_events, get_article_text
    from app.integrations.portwatch import get_vessel_count
    from app.llm import chat_json

    chokepoint = signal.chokepoint_mentioned or "Strait of Hormuz"
    ref_date = datetime.now(timezone.utc)

    # Step 1 — GDELT news events
    goldstein_score = 0.0
    headline_sample = None
    first_url = None
    try:
        events = await fetch_chokepoint_events(
            chokepoint, since=ref_date - timedelta(days=7)
        )
        if events:
            scores = [e["goldstein_score"] for e in events if e.get("goldstein_score") is not None]
            goldstein_score = sum(scores) / len(scores) if scores else 0.0
            headline_sample = events[0].get("title")
            first_url = events[0].get("url")
    except Exception as exc:
        logger.warning("GDELT step failed (non-fatal): %s", exc)

    # Step 2 — PortWatch vessel count
    vessel_count = None
    try:
        vessel_count = await get_vessel_count(shipment.destination_port)
    except Exception as exc:
        logger.warning("PortWatch step failed (non-fatal): %s", exc)

    # Step 3 — Gemma news risk score (0–10)
    news_risk_score = 5.0
    news_risk_reason = "Default — LLM not invoked"
    try:
        article_text = ""
        if first_url:
            article_text = await get_article_text(first_url)

        context = article_text[:2000] if article_text else (
            f"Disruption signal at {chokepoint}. "
            f"Signal type: {signal.signal_type}. "
            f"Severity keywords: {', '.join(signal.severity_keywords or [])}."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a maritime risk analyst. "
                    "Score the disruption risk for a chokepoint on a scale of 0 (no risk) to 10 (severe). "
                    'Return ONLY valid JSON: {"news_risk_score": <number>, "reason": "<one sentence>"}'
                ),
            },
            {
                "role": "user",
                "content": f"Chokepoint: {chokepoint}\n\nContext:\n{context}",
            },
        ]
        result, _ = await chat_json(messages, _NewsRiskScore, max_tokens=256)
        news_risk_score = max(0.0, min(10.0, float(result.news_risk_score)))
        news_risk_reason = result.reason
    except Exception as exc:
        logger.warning("Gemma news risk step failed (non-fatal): %s", exc)

    return ChokePointFeatures(
        date=ref_date,
        chokepoint=chokepoint,
        goldstein_score=goldstein_score,
        news_risk_score=news_risk_score,
        news_risk_reason=news_risk_reason,
        vessel_count=vessel_count,
        headline_sample=headline_sample,
    )
