"""Hybrid risk assessment — rules + ML classifier + Evolus LLM agent.

Final level = max(rules_level, ml_level, llm_level).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

from app.domain.cdm import (
    BlockageClassification,
    DisruptionSignal,
    RiskAssessment,
    RiskLevel,
    Shipment,
)

logger = logging.getLogger(__name__)

_LEVEL_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
_EVOLUS_AGENT_ID = os.environ.get("EVOLUS_AGENT_ID", "")


def _max_level(*levels: RiskLevel) -> RiskLevel:
    return max((lv for lv in levels if lv is not None), key=_LEVEL_ORDER.index)


def _prob_to_level(prob: float) -> RiskLevel:
    if prob >= 0.8:
        return RiskLevel.CRITICAL
    if prob >= 0.6:
        return RiskLevel.HIGH
    if prob >= 0.3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _parse_level(raw: str) -> RiskLevel:
    norm = raw.strip().title()
    try:
        return RiskLevel(norm)
    except ValueError:
        if "critical" in raw.lower():
            return RiskLevel.CRITICAL
        if "high" in raw.lower():
            return RiskLevel.HIGH
        if "medium" in raw.lower() or "moderate" in raw.lower():
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


async def assess(shipment: Shipment, signal: DisruptionSignal) -> RiskAssessment:
    """Run full hybrid assessment: rules → ML → Evolus route-risk agent."""
    from app.risk.rules import score as rules_score

    t0 = time.monotonic()
    raw_score, rules_level = rules_score(shipment, signal)

    # ML layer — graceful skip if model not trained yet (Ericka's workstream)
    from app.domain.cdm import BlockagePrediction
    ml_prediction: BlockagePrediction | None = None
    ml_level: RiskLevel | None = None
    try:
        from app.risk.feature_engineering import build_features
        from app.risk.ml_model import predict_blockage
        features = await build_features(shipment, signal)
        ml_prediction = await predict_blockage(features)
        if ml_prediction.classification == BlockageClassification.BLOCKED:
            ml_level = _prob_to_level(ml_prediction.probability)
        logger.info("ML: prob=%.2f level=%s", ml_prediction.probability, ml_level)
    except NotImplementedError:
        logger.debug("ML model not trained — skipping ML layer.")
    except Exception as exc:
        logger.warning("ML layer error (non-fatal): %s", exc)

    # Evolus route-risk agent (AMD Gemma via Token Factory)
    llm_level: RiskLevel | None = None
    llm_justification: str | None = None
    recommended_port: str | None = None
    secondary_ports: list[str] = []
    cold_chain_implications: str | None = None
    model_used: str | None = None

    agent_id = _EVOLUS_AGENT_ID
    if agent_id:
        prompt = json.dumps(
            {
                "chokepoint": signal.chokepoint_mentioned or "Unknown",
                "signal_type": signal.signal_type,
                "eta_slip_days": shipment.eta_slip_days,
                "rules_score": raw_score,
                "rules_level": rules_level.value,
                "ml_blockage_probability": (
                    ml_prediction.probability if ml_prediction else None
                ),
                "carrier_advisory_present": signal.carrier_advisory_present,
                "severity_keywords": signal.severity_keywords,
                "diversion_language": signal.diversion_language,
            },
            ensure_ascii=False,
        )
        try:
            from app.integrations.evolus import agent_chat

            reply, _conv_id = await agent_chat(agent_id, prompt)
            # Strip markdown fences if present
            clean = re.sub(
                r"```(?:json)?\s*(.*?)\s*```", r"\1", reply, flags=re.DOTALL
            ).strip()
            data = json.loads(clean)
            llm_level = _parse_level(data.get("risk_level", ""))
            llm_justification = data.get("justification") or data.get("llm_justification")
            recommended_port = data.get("recommended_port")
            secondary_ports = list(data.get("secondary_ports") or [])
            cold_chain_implications = data.get("cold_chain_implications")
            model_used = "evolus/gemma"
        except Exception as exc:
            logger.warning("Evolus agent assessment failed (non-fatal): %s", exc)

    all_levels = [lv for lv in (rules_level, ml_level, llm_level) if lv is not None]
    final_level = _max_level(*all_levels) if all_levels else rules_level

    logger.info(
        "Assessment complete: rules=%s ml=%s llm=%s final=%s port=%s latency=%dms",
        rules_level,
        ml_level,
        llm_level,
        final_level,
        recommended_port,
        int((time.monotonic() - t0) * 1000),
    )

    return RiskAssessment(
        shipment_id=shipment.id,
        rules_level=rules_level,
        rules_score=raw_score,
        llm_level=llm_level,
        llm_justification=llm_justification,
        ml_prediction=ml_prediction,
        final_level=final_level,
        recommended_port=recommended_port,
        secondary_ports=secondary_ports,
        cold_chain_implications=cold_chain_implications,
        chokepoint_affected=signal.chokepoint_mentioned,
        model_used=model_used,
    )
