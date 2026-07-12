"""
Trains a classical authenticity (genuine vs. fake) classifier for reviews.

Dataset: Kaggle "Fake Reviews Dataset" (Salminen et al.) - text_/rating/label
columns, label OR=genuine, CG=computer-generated. Place the CSV at
machine_learning/fake_reviews_dataset.csv before running.

Usage: python train_authenticity_model.py
"""
import os
import sys
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix,
    f1_score, precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared_features import SBERT_ENCODER_NAME, build_feature_matrix_cached

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "authenticity")


def main():
    print(f"Loading dataset from {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    df = df.dropna(subset=["text_", "label"])
    y = (df["label"] == "CG").astype(int).values  # 1 = fake, 0 = genuine
    texts = df["text_"].astype(str).tolist()

    print(f"Building/loading feature matrix for {len(texts)} rows...")
    X = build_feature_matrix_cached(texts, cache_key="full_dataset")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y,
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    candidates = {
        "logistic_regression": LogisticRegression(max_iter=2000, class_weight="balanced"),
        "random_forest": RandomForestClassifier(n_estimators=300, max_depth=None, class_weight="balanced", random_state=42, n_jobs=-1),
        "xgboost": XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1, eval_metric="logloss", random_state=42, n_jobs=-1),
    }

    results = {}
    best_name, best_model, best_f1 = None, None, -1.0

    for name, model in candidates.items():
        print(f"\nTraining {name}...")
        model.fit(X_train_scaled, y_train)
        preds = model.predict(X_test_scaled)
        proba = model.predict_proba(X_test_scaled)[:, 1]

        metrics = {
            "accuracy": round(accuracy_score(y_test, preds), 4),
            "precision": round(precision_score(y_test, preds), 4),
            "recall": round(recall_score(y_test, preds), 4),
            "f1": round(f1_score(y_test, preds), 4),
            "roc_auc": round(roc_auc_score(y_test, proba), 4),
            "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
        }
        results[name] = metrics
        print(json.dumps(metrics, indent=2))
        print(classification_report(y_test, preds, target_names=["genuine", "fake"]))

        if metrics["f1"] > best_f1:
            best_f1 = metrics["f1"]
            best_name = name
            best_model = model

    print(f"\nBest model: {best_name} (F1={best_f1})")

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
