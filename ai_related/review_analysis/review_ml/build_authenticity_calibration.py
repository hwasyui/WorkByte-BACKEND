"""
Builds the length calibration for the authenticity model.

Why this exists
---------------
The raw model is biased by review length. Audited on GENUINE held-out reviews,
where every flag is by definition a false positive:

    10-19 words   mean P(fake) 0.348   FP rate 11.82%
    20-39 words                0.298             6.69%
    40-79 words                0.188             3.76%
    80-159 words               0.121             2.49%
    160+ words                 0.046             0.55%

Short genuine reviews are 7.1x more likely to be wrongly flagged. The model is
not malfunctioning - it learned a real correlation in the corpus, where 60.6% of
sub-20-word reviews are generated against 45.1% of 80+ word ones. It is correct
on the benchmark and unfair in deployment, which is why ROC-AUC 0.9322 never
revealed it.

The harm is concrete: authenticity_confidence averages (1 - P(fake)) into
freelancer_trust_scores, so a freelancer whose clients write briefly scores
~0.652 against ~0.954 for one whose clients write essays. That is 3 points of a
100-point trust score decided by client verbosity - which the freelancer does not
control. It also lands unevenly on people who write short, simple English,
including non-native speakers.

Method
------
Per-length-bucket percentile calibration. For a review of length L with raw score
p, the calibrated score is the fraction of GENUINE reviews of similar length that
score at or below p. By construction that is uniform on genuine reviews in every
bucket, so the expected score no longer depends on length.

Buckets are word-count quantiles, so each holds a comparable amount of evidence
rather than being sparse at the extremes (the fixed <10-word bucket in the
original audit held only 29 reviews).

Fitted on OUT-OF-FOLD predictions over the train split, so the held-out set stays
clean for verification.

Out-of-fold matters. Scoring the training reviews with the shipped model gives
mean P(fake) of 0.141 for the shortest bucket, against 0.348 on held-out data -
the model fits reviews it was trained on, so those probabilities are optimistic
and a calibration built from them would under-correct badly on unseen reviews.
cross_val_predict refits the same configuration k times and scores each fold with
a model that never saw it, which is the honest distribution to calibrate against.

Usage: python build_authenticity_calibration.py
"""
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import cross_val_predict, train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from xgboost import XGBClassifier

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from shared_features import build_feature_matrix_cached

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_HERE, "machine_learning", "fake_reviews_dataset.csv")
ARTIFACT_DIR = os.path.join(_HERE, "model_artifacts", "authenticity")
OUT_PATH = os.path.join(ARTIFACT_DIR, "length_calibration.json")

N_BUCKETS = 6
PERCENTILE_GRID = np.arange(0, 101)  # 101 points per bucket, ~600 floats total


def main():
    print(f"Loading dataset from {DATA_PATH}")
    df = pd.read_csv(DATA_PATH).dropna(subset=["text_", "label"])
    texts = df["text_"].astype(str).tolist()
    y = (df["label"] == "CG").astype(int).values

    X = build_feature_matrix_cached(texts, cache_key="full_dataset")
    idx_train, _ = train_test_split(
        np.arange(len(texts)), test_size=0.2, random_state=42, stratify=y
    )

    # Same configuration as train_authenticity_model.py's winning candidate, so the
    # out-of-fold probabilities describe the shipped model's behaviour on data it
    # has not seen. Scaler is inside the pipeline so each fold fits its own.
    print("Computing out-of-fold predictions (5 folds)...")
    pipe = make_pipeline(
        StandardScaler(),
        XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.1,
                      eval_metric="logloss", random_state=42, n_jobs=-1),
    )
    oof = cross_val_predict(
        pipe, X[idx_train], y[idx_train], cv=5, method="predict_proba", n_jobs=1
    )[:, 1]

    # Genuine reviews only. Calibrating against the genuine distribution is the
    # point: "unusual for an HONEST review of this length".
    genuine_mask = y[idx_train] == 0
    genuine_train = idx_train[genuine_mask]
    proba = oof[genuine_mask]
    print(f"Fitting calibration on {len(genuine_train)} genuine training reviews")

    word_counts = np.array([len(texts[i].split()) for i in genuine_train], dtype=float)

    # Quantile edges over the genuine training lengths. Interior edges only; the
    # outer bins are unbounded so any review length can be placed at inference.
    quantiles = np.linspace(0, 100, N_BUCKETS + 1)[1:-1]
    edges = np.unique(np.percentile(word_counts, quantiles)).tolist()
    print(f"Bucket edges (words): {edges}")

    buckets = []
    bucket_idx = np.digitize(word_counts, edges)
    for b in range(len(edges) + 1):
        m = bucket_idx == b
        lo = "0" if b == 0 else f"{edges[b-1]:.0f}"
        hi = "inf" if b == len(edges) else f"{edges[b]:.0f}"
        grid = np.percentile(proba[m], PERCENTILE_GRID).tolist()
        buckets.append(grid)
        print(f"  bucket {b} [{lo}-{hi}) n={m.sum():5d} "
              f"mean P(fake)={proba[m].mean():.4f} median={np.median(proba[m]):.4f}")

    # The within-bucket rank alone is uniform on genuine reviews, so it would average
    # 0.5 where the raw score averages ~0.22 - dropping authenticity_confidence by
    # ~0.28 for EVERY freelancer and quietly rescaling a trust component. Mapping the
    # rank back through the global genuine distribution keeps the original scale while
    # still removing the length dependence: "what would this score be if the review
    # were of average length".
    global_grid = np.percentile(proba, PERCENTILE_GRID).tolist()
    print(f"\nGlobal genuine distribution: mean={proba.mean():.4f} median={np.median(proba):.4f}")

    payload = {
        "method": "per-length-bucket rank, remapped through the global genuine distribution",
        "n_buckets": len(buckets),
        "word_count_edges": edges,
        "percentile_grid": PERCENTILE_GRID.tolist(),
        "bucket_percentiles": buckets,
        "global_percentiles": global_grid,
        "fitted_on": "train split, genuine (OR) reviews only, out-of-fold predictions",
        "n_fitted": int(len(genuine_train)),
    }

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
