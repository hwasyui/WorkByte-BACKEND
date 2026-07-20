import os
import sys
from functools import lru_cache
from typing import Dict

import joblib

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.review_analysis.review_ml.shared_features import build_feature_matrix

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model_artifacts", "sentiment")
_MODEL_PATH = os.path.join(_MODEL_DIR, "model.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.pkl")

_LABEL_NAMES = ["negative", "neutral", "positive"]
# Maps class index (0/1/2) to a -1..1 score for consistency with the
# existing review_ai_analysis.sentiment_score column.
_LABEL_TO_SCORE = {0: -1.0, 1: 0.0, 2: 1.0}


@lru_cache(maxsize=1)
def _load_models():
    model = joblib.load(_MODEL_PATH)
    scaler = joblib.load(_SCALER_PATH)
    return model, scaler


def predict_sentiment(review_text: str) -> Dict:
    """
    Predict positive/neutral/negative sentiment for review text, trained
    on genuine reviews using star rating as a proxy label.

    Returns:
        {
            "sentiment_label": "positive" | "neutral" | "negative",
            "sentiment_score": float (-1..1),
            "model_used": "sbert_<classifier>",
        }
    """
    model, scaler = _load_models()

    features = build_feature_matrix([review_text])
    features_scaled = scaler.transform(features)

    class_idx = int(model.predict(features_scaled)[0])
    proba = model.predict_proba(features_scaled)[0]

    # Confidence-weighted score rather than a flat {-1,0,1} bucket: blends
    # the predicted class's polarity with how much probability mass leaned
    # positive vs. negative, so two "positive" predictions with different
    # confidence don't collapse to the identical score.
    polarity = float(proba[2] - proba[0])  # P(positive) - P(negative)

    return {
        "sentiment_label": _LABEL_NAMES[class_idx],
        "sentiment_score": round(polarity, 4),
        "model_used": f"sbert_{type(model).__name__}",
    }
