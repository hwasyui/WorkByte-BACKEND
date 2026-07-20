import os
import sys
from functools import lru_cache
from typing import Dict

import joblib

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.review_analysis.review_ml.shared_features import build_feature_matrix

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model_artifacts", "authenticity")
_MODEL_PATH = os.path.join(_MODEL_DIR, "model.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.pkl")

FAKE_PREDICT_THRESHOLD = 0.5


@lru_cache(maxsize=1)
def _load_models():
    model = joblib.load(_MODEL_PATH)
    scaler = joblib.load(_SCALER_PATH)
    return model, scaler


def predict_authenticity(review_text: str) -> Dict:
    """
    Predict whether a review's text reads as genuine or fake (templated/
    computer-generated). This is a text-pattern proxy, not fraud/account
    detection - see review_ml training data docs for scope.

    Returns:
        {
            "fake_probability": float (0-1),
            "is_likely_fake": bool,
            "model_used": "sbert_<classifier>",
        }
    """
    model, scaler = _load_models()

    features = build_feature_matrix([review_text])
    features_scaled = scaler.transform(features)

    proba = model.predict_proba(features_scaled)[0]
    fake_prob = float(proba[1])

    return {
        "fake_probability": round(fake_prob, 4),
        "is_likely_fake": fake_prob >= FAKE_PREDICT_THRESHOLD,
        "model_used": f"sbert_{type(model).__name__}",
    }
