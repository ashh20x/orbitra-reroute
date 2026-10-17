"""Rules-based risk scoring layer.

Signals → integer score → RiskLevel. Fast, deterministic, no LLM.
Final risk = max(rules_level, llm_level) — see app/risk/assess.py.
"""

from __future__ import annotations

from app.domain.cdm import DisruptionSignal, RiskLevel, Shipment

# Keyword sets
_DIVERSION_KEYWORDS = {"diversion", "diverted", "reroute", "rerouted", "avoid", "bypass"}
_CHOKEPOINT_KEYWORDS = {
    "hormuz", "strait of hormuz",
    "bab-el-mandeb", "bab el mandeb",
    "red sea",
    "suez",
}
_ALT_PORT_KEYWORDS = {"salalah", "jeddah", "fujairah", "khor fakkan"}
_SECURITY_KEYWORDS = {"security advisory", "threat", "attack", "mine", "naval", "military"}

# Score thresholds → RiskLevel
_THRESHOLDS = [
    (10, RiskLevel.CRITICAL),
    (6, RiskLevel.HIGH),
    (3, RiskLevel.MEDIUM),
    (0, RiskLevel.LOW),
]


def score(shipment: Shipment, signal: DisruptionSignal) -> tuple[int, RiskLevel]:
    """Return (raw_score, risk_level) from deterministic rules."""
    s = 0

    # Explicit diversion language
    body_lower = (shipment.raw_email_body or "").lower()
    if any(k in body_lower for k in _DIVERSION_KEYWORDS):
        s += 4

    # Chokepoint named
    if signal.chokepoint_mentioned:
        s += 3
    elif any(k in body_lower for k in _CHOKEPOINT_KEYWORDS):
        s += 3

    # Carrier advisory present
    if signal.carrier_advisory_present:
        s += 2

    # ETA slip vs cold-chain tolerance (poultry: typically 2-day tolerance)
    if shipment.eta_slip_days is not None:
        if shipment.eta_slip_days >= 6:
            s += 3
        elif shipment.eta_slip_days >= 3:
            s += 2
        elif shipment.eta_slip_days >= 1:
            s += 1

    # Security language
    if any(k in body_lower for k in _SECURITY_KEYWORDS):
        s += 2

    # Severity keywords from extractor
    s += min(len(signal.severity_keywords), 2)

    level = RiskLevel.LOW
    for threshold, lvl in _THRESHOLDS:
        if s >= threshold:
            level = lvl
            break

    return s, level
