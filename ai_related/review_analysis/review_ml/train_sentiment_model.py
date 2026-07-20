"""
Trains a 3-class sentiment classifier (positive/neutral/negative) for review
text, replacing the LLM's sentiment guess. Trained on the genuine (OR) subset
of the same dataset used for the mismatch model, using the star rating as a
proxy label: >=4 stars -> positive, 3 stars -> neutral, <=2 stars -> negative.
Reuses the same cached feature matrix as train_mismatch_model.py (same OR
subset, same texts) so no re-encoding cost.

Usage: python train_sentiment_model.py
"""
import os
import sys
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared_features import SBERT_ENCODER_NAME, build_feature_matrix_cached

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "sentiment")

LABEL_NAMES = ["negative", "neutral", "positive"]


def rating_to_sentiment(rating: float) -> int:
    if rating >= 4:
        return 2  # positive
    if rating == 3:
        return 1  # neutral
    return 0  # negative


def main():
    print(f"Loading dataset from {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    df = df.dropna(subset=["text_", "label", "rating"])
    df = df[df["label"] == "OR"].reset_index(drop=True)
    print(f"Genuine (OR) subset: {len(df)} rows")

    y = df["rating"].astype(float).apply(rating_to_sentiment).values
    texts = df["text_"].astype(str).tolist()

    print(f"Building/loading feature matrix for {len(texts)} rows...")
    X_full = build_feature_matrix_cached(texts, cache_key="or_subset")

    X_train, X_test, y_train, y_test = train_test_split(
        X_full, y, test_size=0.2, random_state=42, stratify=y,
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    candidates = {
        "logistic_regression": LogisticRegression(max_iter=2000, class_weight="balanced"),
        "random_forest": RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=42, n_jobs=-1),
        "xgboost": XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1, eval_metric="mlogloss", random_state=42, n_jobs=-1),
    }

    results = {}
    best_name, best_model, best_f1 = None, None, -1.0

    for name, model in candidates.items():
        print(f"\nTraining {name}...")
        model.fit(X_train_scaled, y_train)
        preds = model.predict(X_test_scaled)

        metrics = {
            "accuracy": round(accuracy_score(y_test, preds), 4),
            "f1_macro": round(f1_score(y_test, preds, average="macro"), 4),
            "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
        }
        results[name] = metrics
        print(json.dumps(metrics, indent=2))
        print(classification_report(y_test, preds, target_names=LABEL_NAMES))

        if metrics["f1_macro"] > best_f1:
            best_f1 = metrics["f1_macro"]
            best_name = name
            best_model = model

    print(f"\nBest model: {best_name} (F1 macro={best_f1})")

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
