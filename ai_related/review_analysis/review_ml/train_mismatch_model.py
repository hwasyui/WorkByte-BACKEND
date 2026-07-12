"""
Trains a regressor that predicts the star rating implied by review TEXT
ALONE, trained only on genuine (label == 'OR') reviews so it learns what
rating authentic sentiment implies (not fake reviews' often-exaggerated
rating/text pairing). At inference, the gap between this predicted rating
and the rating actually given is the sentiment-rating mismatch signal.

Usage: python train_mismatch_model.py
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
from shared_features import SBERT_ENCODER_NAME, build_feature_matrix_cached

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
    X_full = build_feature_matrix_cached(texts, cache_key="or_subset")

    X_train, X_test, y_train, y_test = train_test_split(
        X_full, y, test_size=0.2, random_state=42,
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
