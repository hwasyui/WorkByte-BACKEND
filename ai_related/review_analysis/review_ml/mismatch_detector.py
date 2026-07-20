import os
import sys
from functools import lru_cache
from typing import Dict

import joblib

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.review_analysis.review_ml.shared_features import build_feature_matrix

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model_artifacts", "mismatch")
_MODEL_PATH = os.path.join(_MODEL_DIR, "model.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.pkl")

# Gap (in stars) between predicted and actual rating past which we call it
# a mismatch. On a 1-5 scale, 1.5 stars is a clear directional disagreement,
# not just noise from the regressor.
MISMATCH_SEVERITY_THRESHOLD = 1.5


@lru_cache(maxsize=1)
def _load_models():
    model = joblib.load(_MODEL_PATH)
    scaler = joblib.load(_SCALER_PATH)
    return model, scaler


def predict_mismatch(review_text: str, actual_rating: float) -> Dict:
    """
    Predict the star rating implied by review TEXT ALONE (trained only on
    genuine reviews) and compare it to the rating actually given. A large
    gap in either direction is the classic signature of an accidental
    misclick or a coerced/pressured review.

    Returns:
        {
            "predicted_rating": float (1-5),
            "mismatch_severity": float (0+, |predicted - actual|),
            "is_mismatched": bool,
            "model_used": "sbert_<regressor>",
        }
    """
    model, scaler = _load_models()

    features = build_feature_matrix([review_text])
    features_scaled = scaler.transform(features)

    predicted_rating = float(model.predict(features_scaled)[0])
    predicted_rating = max(1.0, min(5.0, predicted_rating))

    severity = abs(predicted_rating - actual_rating)

    return {
        "predicted_rating": round(predicted_rating, 3),
        "mismatch_severity": round(severity, 3),
        "is_mismatched": severity >= MISMATCH_SEVERITY_THRESHOLD,
        "model_used": f"sbert_{type(model).__name__}",
    }
