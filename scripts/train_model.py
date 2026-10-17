#!/usr/bin/env python3
"""Offline training script — blockage classifier.

Trains on GDELT features + PortWatch labels from data/portwatch_targets.csv.
Saves artifact to models/blockage_classifier.pkl (XGBoost) or
models/lstm_blockage.pt (PyTorch/ROCm — requires --model lstm and a ROCm GPU).

Usage:
  uv run python scripts/train_model.py            # XGBoost (default)
  uv run python scripts/train_model.py --model lstm  # PyTorch LSTM (ROCm)

Run this during P0 so the artifact is ready for P1 inference.
"""

import argparse
import pathlib
import sys

import numpy as np

MODELS_DIR = pathlib.Path(__file__).parent.parent / "models"
DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def train_xgboost() -> None:
    import csv
    import joblib
    import xgboost as xgb
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report

    path = DATA_DIR / "portwatch_targets.csv"
    if not path.exists():
        print(f"ERROR: {path} not found. Cannot train.")
        sys.exit(1)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Feature engineering on seed data
    # In production: pull GDELT scores + vessel counts via integrations
    X, y = [], []
    for row in rows:
        vessel_count = float(row.get("vessel_count", 100) or 100)
        # Placeholder: goldstein_score and news_risk_score not yet in seed data
        # TODO Stage1/Ericka: replace with real GDELT features
        goldstein = 0.0
        news_risk = 5.0
        X.append([vessel_count, goldstein, news_risk])
        y.append(int(row.get("target", 0) or 0))

    X = np.array(X)
    y = np.array(y)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train)

    print("\nClassification report (test set):")
    print(classification_report(y_test, model.predict(X_test)))

    MODELS_DIR.mkdir(exist_ok=True)
    out = MODELS_DIR / "blockage_classifier.pkl"
    joblib.dump(model, out)
    print(f"\nSaved XGBoost model → {out}")


def train_lstm() -> None:
    """PyTorch LSTM on AMD ROCm GPU.

    TODO Stage2/Ericka: implement LSTM architecture.
    Requires: pip install torch --index-url https://download.pytorch.org/whl/rocm6.1
    """
    raise NotImplementedError(
        "TODO Stage2/Ericka: implement PyTorch LSTM training. "
        "Install ROCm PyTorch build. Use torch.device('cuda') for AMD GPU. "
        "Save to models/lstm_blockage.pt."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train blockage classifier")
    parser.add_argument(
        "--model", choices=["xgboost", "lstm"], default="xgboost",
        help="Model type to train (default: xgboost)"
    )
    args = parser.parse_args()

    print(f"Training {args.model} blockage classifier...")
    if args.model == "xgboost":
        train_xgboost()
    else:
        train_lstm()


if __name__ == "__main__":
    main()
