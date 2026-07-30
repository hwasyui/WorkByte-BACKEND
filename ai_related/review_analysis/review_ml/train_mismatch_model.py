"""
RETIRED - kept for the record, not part of the pipeline. Nothing loads the
artifact this produces, and model_artifacts/mismatch/ has been deleted.

Trains a regressor predicting the star rating implied by review TEXT ALONE.
The pipeline used to threshold |predicted - actual| as its mismatch signal.
That was replaced by train_disagreement_model.py, which classifies the
(text, rating) pair directly.

Why it was retired, both measured on held-out data:

  * As a DETECTOR it is structurally wrong. |f(text) - rating| >= 1.5 cannot
    condition on the rating, because the rating is not available when f runs,
    so it cannot learn "harsh text with 1 star is fine, harsh text with 5
    stars is suspicious." A scathing review rated 1 star produced a 1.7-star
    residual and was flagged.
  * As a TRUST INPUT it is biased. On agreeing pairs, mean severity ran 2.034
    stars at 1 star against 0.502 at 5 stars - 60% of the training corpus is
    5-star, so it reverts to the mean. That handed consistency_score 0.492 to
    freelancers rated honestly low and 0.874 to those rated high.

Final metrics before retirement, after adding the VADER features and
stratifying the split: MAE 0.6447, RMSE 0.8873, r2 0.4017 (from MAE 0.6861 /
r2 0.3492). The features accounted for roughly 73% of that gain and the split
fix the rest - but the bias above is not a metrics problem, which is the point.

Usage (if ever revived): python train_mismatch_model.py
"""
import os
import sys
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared_features import SBERT_ENCODER_NAME, build_feature_matrix_with_sentiment_cached

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "mismatch")


def main():
    print(f"Loading dataset from {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    df = df.dropna(subset=["text_", "label", "rating"])
    df = df[df["label"] == "OR"].reset_index(drop=True)
    print(f"Genuine (OR) subset: {len(df)} rows")

    y = df["rating"].astype(float).values
    texts = df["text_"].astype(str).tolist()

    print(f"Building/loading feature matrix for {len(texts)} rows...")
    X_full = build_feature_matrix_with_sentiment_cached(texts, cache_key="or_subset")

    # Stratify on the star value itself. 60% of this corpus is 5-star, so an
    # unstratified split leaves the rare low ratings unevenly distributed and
    # makes the held-out MAE noisy between runs.
    X_train, X_test, y_train, y_test = train_test_split(
        X_full, y, test_size=0.2, random_state=42, stratify=y,
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    candidates = {
        "linear_regression": LinearRegression(),
        "random_forest": RandomForestRegressor(n_estimators=300, max_depth=None, random_state=42, n_jobs=-1),
        "xgboost": XGBRegressor(n_estimators=300, max_depth=6, learning_rate=0.1, random_state=42, n_jobs=-1),
    }

    results = {}
    best_name, best_model, best_mae = None, None, float("inf")

    for name, model in candidates.items():
        print(f"\nTraining {name}...")
        model.fit(X_train_scaled, y_train)
        preds = np.clip(model.predict(X_test_scaled), 1.0, 5.0)

        metrics = {
            "mae": round(mean_absolute_error(y_test, preds), 4),
            "rmse": round(mean_squared_error(y_test, preds) ** 0.5, 4),
            "r2": round(r2_score(y_test, preds), 4),
        }
        results[name] = metrics
        print(json.dumps(metrics, indent=2))

        if metrics["mae"] < best_mae:
            best_mae = metrics["mae"]
            best_name = name
            best_model = model

    print(f"\nBest model: {best_name} (MAE={best_mae})")

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    joblib.dump(best_model, os.path.join(ARTIFACT_DIR, "model.pkl"))
    joblib.dump(scaler, os.path.join(ARTIFACT_DIR, "scaler.pkl"))
    with open(os.path.join(ARTIFACT_DIR, "sbert_encoder_name.txt"), "w") as f:
        f.write(SBERT_ENCODER_NAME)
    with open(os.path.join(ARTIFACT_DIR, "metrics_comparison.json"), "w") as f:
        json.dump({"best_model": best_name, "results": results}, f, indent=2)

    print(f"\nSaved artifacts to {ARTIFACT_DIR}")


if __name__ == "__main__":
    main()
