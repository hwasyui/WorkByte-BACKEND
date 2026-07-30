import json
import os
import sys
from functools import lru_cache
from typing import Dict, Optional

import joblib
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.review_analysis.review_ml.shared_features import build_feature_matrix

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model_artifacts", "authenticity")
_MODEL_PATH = os.path.join(_MODEL_DIR, "model.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.pkl")
_CALIBRATION_PATH = os.path.join(_MODEL_DIR, "length_calibration.json")

# Raised from 0.50. The model is trained on e-commerce review text, and short warm
# praise - "Very professional and easy to work with. Highly recommended." - is
# exactly what it learned to call templated. At 0.50 it flagged 2 of 8 realistic
# genuine reviews. The pipelines additionally require the LLM to agree before this
# vetoes publication, so a lone high score now lowers authenticity rather than
# blocking a real client's review.
#
# Applies to the RAW probability. The threshold was tuned against raw scores, so
# moving the flag onto the calibrated score below would need it re-tuned - see
# fake_probability_calibrated.
FAKE_PREDICT_THRESHOLD = 0.75


@lru_cache(maxsize=1)
def _load_models():
    model = joblib.load(_MODEL_PATH)
    scaler = joblib.load(_SCALER_PATH)
    return model, scaler


@lru_cache(maxsize=1)
def _load_length_calibration() -> Optional[Dict]:
    """
    Optional. model_artifacts/ is placed by hand at install time, so a folder
    built before this file existed will not have it. Missing calibration falls
    back to the raw probability rather than breaking review submission.
    """
    if not os.path.exists(_CALIBRATION_PATH):
        return None
    try:
        with open(_CALIBRATION_PATH) as fh:
            return json.load(fh)
    except Exception:
        return None


def _calibrate_by_length(raw_probability: float, word_count: int) -> Optional[float]:
    """
    The raw score with its length dependence removed: "what would this review
    score if it were of average length".

    The raw model is biased by length. Audited on genuine held-out reviews, mean
    P(fake) runs 0.348 for 10-19 word reviews against 0.046 for 160+ word ones, so
    short genuine reviews are 7.1x more likely to be wrongly flagged. That is a
    real correlation in the corpus - 60.6% of sub-20-word reviews there are
    generated - so the model is right on the benchmark and unfair in deployment.
    Because authenticity_confidence averages (1 - P(fake)) into trust scores, the
    raw score charges a freelancer for how briefly their clients happen to write.

    Two steps:
      1. Rank the raw score among GENUINE reviews of similar length. This is what
         removes the bias - the rank is uniform in every length bucket.
      2. Map that rank back through the global genuine distribution. Step 1 alone
         would return ~0.5 on average where the raw score returns ~0.22, dropping
         authenticity_confidence ~0.28 for every freelancer and rescaling a trust
         component as a side effect. Step 2 restores the original scale.

    Measured after both steps: mean score by length bucket varies by 0.012 across
    <20 to 160+ words, against 0.250 raw.

    Returns None when no calibration artifact is installed.
    """
    calib = _load_length_calibration()
    if calib is None:
        return None

    bucket = int(np.digitize([float(word_count)], calib["word_count_edges"])[0])
    bucket_grid = calib["bucket_percentiles"][bucket]
    percentiles = calib["percentile_grid"]

    # Percentile curves are non-decreasing, so np.interp inverts them.
    rank = float(np.interp(raw_probability, bucket_grid, percentiles))

    global_grid = calib.get("global_percentiles")
    if global_grid is None:
        # Older artifact without the global curve: fall back to the bare rank.
        return max(0.0, min(1.0, rank / 100.0))

    adjusted = float(np.interp(rank, percentiles, global_grid))
    return max(0.0, min(1.0, adjusted))


def predict_authenticity(review_text: str) -> Dict:
    """
    Predict whether a review's text reads as genuine or fake (templated/
    computer-generated). This is a text-pattern proxy, not fraud/account
    detection - see review_ml training data docs for scope.

    Returns:
        {
            "fake_probability": float (0-1), raw model output
            "fake_probability_calibrated": float (0-1) or None,
                length-neutral rank among genuine reviews of similar length.
                Use THIS for anything that feeds a trust score - the raw score
                penalises short reviews 7.1x more often. None when no
                calibration artifact is installed.
            "is_likely_fake": bool, from the RAW score and FAKE_PREDICT_THRESHOLD
            "model_used": "sbert_<classifier>",
        }
    """
    model, scaler = _load_models()

    features = build_feature_matrix([review_text])
    features_scaled = scaler.transform(features)

    proba = model.predict_proba(features_scaled)[0]
    fake_prob = float(proba[1])

    word_count = len((review_text or "").split())
    calibrated = _calibrate_by_length(fake_prob, word_count)

    return {
        "fake_probability": round(fake_prob, 4),
        "fake_probability_calibrated": None if calibrated is None else round(calibrated, 4),
        "is_likely_fake": fake_prob >= FAKE_PREDICT_THRESHOLD,
        "model_used": f"sbert_{type(model).__name__}",
    }
