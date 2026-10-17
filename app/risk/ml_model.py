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
    raise NotImplementedError(
        "TODO Stage2/Ericka: "
        "1. Call _load_model() "
        "2. Build feature vector from ChokePointFeatures "
        "3. model.predict_proba([vector]) → probability "
        "4. Return BlockagePrediction with model_used='xgboost' or 'lstm'. "
        "If model unavailable, use a simple rules fallback and set model_used='fallback_rules'."
    )
