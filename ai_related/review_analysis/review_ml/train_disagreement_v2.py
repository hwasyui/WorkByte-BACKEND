"""
Component 3, rebuilt on PRETRAINED features instead of MiniLM embeddings.

The idea: stop competing with pretrained models and start combining them. Instead
of 400 dimensions of frozen all-MiniLM-L6-v2 embedding, feed the classifier a
handful of strong signals - Cardiff's sentiment probabilities, VADER's polarity,
and the rating itself - and let it learn the text/rating relationship over those.

Expected advantages over the 400-dim version:
  * 12 features instead of 400, so far less room to overfit 15,936 training pairs.
  * Inspectable coefficients. "Disagreement rises when Cardiff says negative and
    the rating is 5" is a sentence you can read off a linear model.
  * It learns WHEN TO TRUST the pretrained models rather than duplicating them.
  * MiniLM drops out of this path entirely.

WHAT IS MISSING, and why
------------------------
The design for this component called for behavioural features too - on_time_score,
revision_rate_score, responsiveness_score - so the model could catch a glowing
review of an objectively late, heavily-revised engagement. Those are NOT here,
because the Amazon corpus this trains on has no contract telemetry and never will.
Inventing it synthetically would teach the model a correlation I made up.

That capability is instead implemented as arithmetic in review_consistency.py,
which needs no training data. This model stays a pure text-vs-rating judge.

Usage: python train_disagreement_v2.py
"""
import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, average_precision_score, classification_report,
    confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared_features import extract_sentiment_features, SENTIMENT_FEATURE_NAMES
from train_disagreement_model import build_rating_features, build_balanced_pairs, MIN_CORRUPTION_GAP

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "disagreement_v2")
CARDIFF_CACHE = os.path.join(_HERE, "machine_learning", "_feature_cache", "cardiff_or_subset.npy")

RANDOM_SEED = 42

FEATURE_NAMES = (
    ["cardiff_negative", "cardiff_neutral", "cardiff_positive"]
    + SENTIMENT_FEATURE_NAMES
    + ["rating", "rating_distance_from_mid", "rating_is_high", "rating_is_low"]
)


def cardiff_probabilities(texts) -> np.ndarray:
    """
    (n, 3) array of Cardiff's negative/neutral/positive probabilities.

    Cached to disk: this is ~20k transformer forward passes on CPU, several
    minutes, and it is deterministic.
    """
    if os.path.exists(CARDIFF_CACHE):
        cached = np.load(CARDIFF_CACHE)
        if cached.shape[0] == len(texts):
            print(f"Loaded cached Cardiff scores {cached.shape}")
            return cached

    from transformers import pipeline
    print("Scoring with Cardiff (this takes a while on CPU)...", flush=True)
    clf = pipeline(
        "sentiment-analysis",
        model="cardiffnlp/twitter-roberta-base-sentiment-latest",
        top_k=None, truncation=True, max_length=512,
    )

    out = np.zeros((len(texts), 3), dtype=np.float64)
    t0 = time.time()
    batch = 64
    for start in range(0, len(texts), batch):
        chunk = texts[start:start + batch]
        for i, row in enumerate(clf(chunk, batch_size=batch)):
            scores = {r["label"].lower(): float(r["score"]) for r in row}
            out[start + i] = [scores.get("negative", 0.0),
                              scores.get("neutral", 0.0),
                              scores.get("positive", 0.0)]
        if start and start % (batch * 40) == 0:
            done = start + len(chunk)
            rate = done / max(time.time() - t0, 1e-9)
            print(f"  {done}/{len(texts)}  {rate:.1f}/s  eta {(len(texts)-done)/max(rate,1e-9)/60:.1f} min",
                  flush=True)

    os.makedirs(os.path.dirname(CARDIFF_CACHE), exist_ok=True)
    np.save(CARDIFF_CACHE, out)
    print(f"  done in {(time.time()-t0)/60:.1f} min, cached to {CARDIFF_CACHE}")
    return out


def main():
    rng = np.random.default_rng(RANDOM_SEED)

    df = pd.read_csv(DATA_PATH).dropna(subset=["text_", "label", "rating"])
    df = df[df["label"] == "OR"].reset_index(drop=True)
    texts = df["text_"].astype(str).tolist()
    ratings = df["rating"].astype(float).values
    print(f"Genuine (OR) subset: {len(texts)} rows")

    cardiff = cardiff_probabilities(texts)
    vader = np.stack([extract_sentiment_features(t) for t in texts])
    X_text = np.hstack([cardiff, vader])
    print(f"Text feature matrix: {X_text.shape} (3 cardiff + {vader.shape[1]} vader)")

    idx_train, idx_test = train_test_split(
        np.arange(len(texts)), test_size=0.2, random_state=RANDOM_SEED, stratify=ratings,
    )

    def assemble(idx):
        t_idx, assigned, y = build_balanced_pairs(idx, ratings, rng)
        return np.hstack([X_text[t_idx], build_rating_features(assigned)]), y

    X_train, y_train = assemble(idx_train)
    X_test, y_test = assemble(idx_test)
    print(f"Train pairs: {len(y_train)} | Test pairs: {len(y_test)} | features: {X_train.shape[1]}")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    candidates = {
        "logistic_regression": LogisticRegression(max_iter=2000, class_weight="balanced"),
        "xgboost": XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1,
                                 eval_metric="logloss", random_state=RANDOM_SEED, n_jobs=-1),
    }

    results = {}
    best_name, best_model, best_ap = None, None, -1.0

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
            "average_precision": round(average_precision_score(y_test, proba), 4),
            "confusion_matrix": confusion_matrix(y_test, preds).tolist(),
        }
        results[name] = metrics
        print(json.dumps({k: v for k, v in metrics.items() if k != "confusion_matrix"}, indent=2))
        print(classification_report(y_test, preds, target_names=["agrees", "disagrees"]))

        if metrics["average_precision"] > best_ap:
            best_ap = metrics["average_precision"]
            best_name, best_model = name, model

    print(f"\nBest: {best_name} (average precision={best_ap})")
    print(f"400-dim MiniLM version for comparison: average precision=0.8721")

    if best_name == "logistic_regression":
        print("\nCoefficients (the point of a small feature set):")
        for fname, coef in sorted(zip(FEATURE_NAMES, best_model.coef_[0]),
                                  key=lambda kv: -abs(kv[1])):
            print(f"  {fname:28s} {coef:+.4f}")

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    joblib.dump(best_model, os.path.join(ARTIFACT_DIR, "model.pkl"))
    joblib.dump(scaler, os.path.join(ARTIFACT_DIR, "scaler.pkl"))
    with open(os.path.join(ARTIFACT_DIR, "metrics_comparison.json"), "w") as f:
        json.dump({
            "best_model": best_name,
            "feature_names": FEATURE_NAMES,
            "label_construction": f"balanced per-rating pairing, min gap {MIN_CORRUPTION_GAP}",
            "baseline_400dim_minilm_average_precision": 0.8721,
            "results": results,
        }, f, indent=2)
    print(f"\nSaved to {ARTIFACT_DIR}")


if __name__ == "__main__":
    main()
