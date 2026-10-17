"""Stage 2 — ML blockage classifier.

Default path: XGBoost (trains in <30s on CPU, artifact: models/blockage_classifier.pkl).
Stretch path: PyTorch LSTM on AMD ROCm GPU (artifact: models/lstm_blockage.pt).

ML model choice (XGBoost vs LSTM) is deferred — see CLAUDE.md.
Both paths share the same BlockagePrediction interface so the app needs no changes.

Ericka's workstream. Claude Code scaffolds the interface; Ericka implements.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any

from app.domain.cdm import BlockageClassification, BlockagePrediction, ChokePointFeatures

logger = logging.getLogger(__name__)

_MODELS_DIR = pathlib.Path(__file__).parent.parent.parent / "models"
_XGBOOST_PATH = _MODELS_DIR / "blockage_classifier.pkl"
_LSTM_PATH = _MODELS_DIR / "lstm_blockage.pt"

_model = None  # loaded lazily on first call


def _load_model() -> Any:
    global _model
    if _model is not None:
        return _model
    if _XGBOOST_PATH.exists():
        import joblib
        _model = joblib.load(_XGBOOST_PATH)
        logger.info("Loaded XGBoost blockage classifier from %s", _XGBOOST_PATH)
        return _model
    logger.warning(
        "No trained model found at %s. "
        "Run `make train` to generate it. Using fallback rules.",
        _XGBOOST_PATH,
    )
    return None


async def predict_blockage(features: ChokePointFeatures) -> BlockagePrediction:
    """Run the ML classifier. Falls back to rules if model not loaded."""
    model = _load_model()

    vessel_count = features.vessel_count if features.vessel_count is not None else 100
    goldstein_score = features.goldstein_score if features.goldstein_score is not None else 0.0
    news_risk_score = features.news_risk_score if features.news_risk_score is not None else 5.0
    vector = [[vessel_count, goldstein_score, news_risk_score]]

    if model is not None:
        prob = float(model.predict_proba(vector)[0][1])
        model_used = "xgboost"
    else:
        prob = 0.0
        if news_risk_score >= 7:
            prob += 0.3
        if goldstein_score <= -3:
            prob += 0.25
        if features.vessel_count is not None and features.vessel_count < 80:
            prob += 0.2
        prob = min(prob, 1.0)
        model_used = "fallback_rules"

    classification = (
        BlockageClassification.BLOCKED if prob >= 0.5 else BlockageClassification.NOT_BLOCKED
    )
    confidence = min(abs(prob - 0.5) * 2, 1.0)

    logger.info(
        "predict_blockage: chokepoint=%s prob=%.3f model=%s",
        features.chokepoint, prob, model_used,
    )

    return BlockagePrediction(
        chokepoint=features.chokepoint,
        probability=prob,
        classification=classification,
        model_used=model_used,
        confidence=confidence,
        features_used=features,
    )
