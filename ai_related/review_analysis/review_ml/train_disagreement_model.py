"""
Trains a binary detector for "does this review's TEXT disagree with the star
rating attached to it?" - the question the pipeline actually asks, supervised
directly instead of inferred from a regression residual.

Why this exists alongside train_mismatch_model.py
-------------------------------------------------
The regressor predicts an implied star rating and the pipeline thresholds
|predicted - actual|. That works, but r^2 ~ 0.35 means the regressor is a
noisy proxy, and "r^2 = 0.35" says nothing about how well the pipeline
actually catches misrated reviews. This model is scored on precision/recall
for the real decision, which is both a better metric and a better-behaved
signal.

Label construction
------------------
There is no ground-truth "this rating is wrong" column, so labels come from
pairing. For a given star value r:
  - (text whose true rating IS r,            r) -> 0, the pair agrees
  - (text whose true rating is >=gap from r, r) -> 1, the pair disagrees

Pairs are built PER RATING LEVEL, with an equal number of agreeing and
disagreeing examples at each level. This matters. The first version of this
script corrupted each review's rating independently, and because 60% of the
corpus is 5-star, corrupting those scattered mostly into 1-3 stars - which
made a low rating statistically predictive of the "disagree" label all by
itself. The model learned the shortcut "low rating => probably disagreement"
instead of judging coherence, and on held-out AGREEING pairs it returned a
mean P(disagree) of 0.60 for 1-star reviews versus 0.07 for 5-star ones.

Matching the per-rating counts across both classes makes the rating carry
zero marginal information about the label, so the only way to separate them
is the text/rating relationship. The cost is dataset size: at each level the
usable count is capped by whichever side is scarcer, since only ~3.9k reviews
sit far enough from 5 stars to serve as its disagreeing partners.

LIMITATION, state this in any writeup: the negatives are still synthetic.
Real misclicks and coerced reviews are subtler than a mismatched pairing, so
held-out numbers here are optimistic relative to production. What the model
genuinely learns is text/rating coherence, which is the right shape for the
task even though the difficulty is understated.

Usage: python train_disagreement_model.py
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
    accuracy_score,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared_features import SBERT_ENCODER_NAME, build_feature_matrix_with_sentiment_cached

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "disagreement")

MIN_CORRUPTION_GAP = 2.0
RANDOM_SEED = 42


def build_rating_features(ratings: np.ndarray) -> np.ndarray:
    """
    The rating must be visible to the model - it is judging a (text, rating)
    PAIR, not the text alone. Distance from the midpoint and the two extreme
    flags are included because disagreement concentrates at 1 and 5 stars.
    """
    ratings = ratings.astype(np.float64)
    return np.column_stack([
        ratings,
        np.abs(ratings - 3.0),
        (ratings >= 4).astype(np.float64),
        (ratings <= 2).astype(np.float64),
    ])


def build_balanced_pairs(idx: np.ndarray, all_ratings: np.ndarray, rng: np.random.Generator):
    """
    Build (text_index, assigned_rating, label) triples restricted to `idx`,
    with the agreeing and disagreeing classes given IDENTICAL per-rating
    counts.

    At each star level r, n is capped by min(#texts truly rated r, #texts far
    enough from r to disagree with it), and both classes draw n texts without
    replacement. Equal counts at every level means P(rating | agree) equals
    P(rating | disagree) exactly, so the rating alone cannot predict the label.

    A text can serve as the disagreeing partner for more than one star level
    (a 1-star review disagrees with both 4 and 5), so the disagreeing class
    holds fewer distinct texts than rows. That is intended: the same text
    under two different ratings forces the model onto the pairing rather than
    onto the text alone.
    """
    ratings_here = all_ratings[idx]
    text_idx, assigned, labels = [], [], []

    for r in (1.0, 2.0, 3.0, 4.0, 5.0):
        same = idx[ratings_here == r]
        far = idx[np.abs(ratings_here - r) >= MIN_CORRUPTION_GAP]
        n = min(len(same), len(far))
        if n == 0:
            continue

        for t in rng.choice(same, size=n, replace=False):
            text_idx.append(t); assigned.append(r); labels.append(0)
        for t in rng.choice(far, size=n, replace=False):
            text_idx.append(t); assigned.append(r); labels.append(1)

    return (
        np.array(text_idx, dtype=int),
        np.array(assigned, dtype=np.float64),
        np.array(labels, dtype=np.float64),
    )


def main():
    rng = np.random.default_rng(RANDOM_SEED)

    print(f"Loading dataset from {DATA_PATH}")
    df = pd.read_csv(DATA_PATH)
    df = df.dropna(subset=["text_", "label", "rating"])
    df = df[df["label"] == "OR"].reset_index(drop=True)
    print(f"Genuine (OR) subset: {len(df)} rows")

    texts = df["text_"].astype(str).tolist()
    true_ratings = df["rating"].astype(float).values

    print(f"Building/loading feature matrix for {len(texts)} rows...")
    X_text = build_feature_matrix_with_sentiment_cached(texts, cache_key="or_subset")

    # Split by REVIEW before pairing, so a review's agreeing pair and its
    # disagreeing pair can never straddle the train/test boundary. Splitting
    # after would leak the text across folds and inflate every metric.
    idx_train, idx_test = train_test_split(
        np.arange(len(texts)), test_size=0.2, random_state=RANDOM_SEED, stratify=true_ratings,
    )

    def assemble(idx):
        t_idx, assigned, y = build_balanced_pairs(idx, true_ratings, rng)
        X = np.hstack([X_text[t_idx], build_rating_features(assigned)])
        return X, y, assigned

    X_train, y_train, _ = assemble(idx_train)
    X_test, y_test, assigned_test = assemble(idx_test)
    print(f"Train pairs: {len(y_train)} ({int(y_train.sum())} disagreeing)")
    print(f"Test pairs : {len(y_test)} ({int(y_test.sum())} disagreeing)")

    # Confirm the shortcut is actually gone: these two rows must match.
    print("\nRating marginals by class (must be identical):")
    for r in (1.0, 2.0, 3.0, 4.0, 5.0):
        a = int(((assigned_test == r) & (y_test == 0)).sum())
        d = int(((assigned_test == r) & (y_test == 1)).sum())
        print(f"  {r:.0f}*  agree={a:5d}  disagree={d:5d}")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    candidates = {
        "logistic_regression": LogisticRegression(max_iter=2000, class_weight="balanced"),
        "random_forest": RandomForestClassifier(
            n_estimators=300, class_weight="balanced", random_state=RANDOM_SEED, n_jobs=-1
        ),
        "xgboost": XGBClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.1,
            eval_metric="logloss", random_state=RANDOM_SEED, n_jobs=-1,
        ),
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
        print(json.dumps(metrics, indent=2))
        print(classification_report(y_test, preds, target_names=["agrees", "disagrees"]))

        if metrics["average_precision"] > best_ap:
            best_ap = metrics["average_precision"]
            best_name = name
            best_model = model

    print(f"\nBest model: {best_name} (average precision={best_ap})")

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    joblib.dump(best_model, os.path.join(ARTIFACT_DIR, "model.pkl"))
    joblib.dump(scaler, os.path.join(ARTIFACT_DIR, "scaler.pkl"))
    with open(os.path.join(ARTIFACT_DIR, "sbert_encoder_name.txt"), "w") as f:
        f.write(SBERT_ENCODER_NAME)
    with open(os.path.join(ARTIFACT_DIR, "metrics_comparison.json"), "w") as f:
        json.dump(
            {
                "best_model": best_name,
                "label_construction": f"synthetic corruption, min gap {MIN_CORRUPTION_GAP} stars",
                "results": results,
            },
            f,
            indent=2,
        )

    print(f"\nSaved artifacts to {ARTIFACT_DIR}")


if __name__ == "__main__":
    main()
