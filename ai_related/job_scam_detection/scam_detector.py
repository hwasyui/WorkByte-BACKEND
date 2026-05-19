import os
import re
from functools import lru_cache
from typing import Dict, List

import joblib
import numpy as np

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model1_sbert_rf_complete")
_ENCODER_NAME_PATH = os.path.join(_MODEL_DIR, "sbert_encoder_name.txt")
_MODEL_PATH = os.path.join(_MODEL_DIR, "sbert_rf_model.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "scaler.pkl")

SCAM_PREDICT_THRESHOLD = 0.4

_URGENT_KEYWORDS: List[str] = [
    "urgent", "immediately", "asap", "hurry", "right now",
    "today only", "limited time", "act now",
]
_HIGH_PAY_KEYWORDS: List[str] = [
    "earn", "income", "salary", "$", "pay", "money", "bonus",
    "lucrative", "rich", "profit", "high paying", "get paid",
]
_LOW_SKILL_KEYWORDS: List[str] = [
    "no experience", "entry level", "no degree", "beginner",
    "fresher", "easy work", "simple task", "no skills required",
    "no qualification", "anyone can",
]
_SUSPICIOUS_KEYWORDS: List[str] = [
    "wire transfer", "western union", "moneygram", "upfront",
    "advance fee", "investment required", "deposit required",
    "bitcoin", "cryptocurrency", "gift card", "cash advance",
    "processing fee", "registration fee",
]
_TELECOMMUTE_KEYWORDS: List[str] = [
    "remote", "work from home", "telecommute", "wfh",
    "home based", "online work", "work anywhere",
]


@lru_cache(maxsize=1)
def _load_models():
    """Load encoder, RF model, and scaler once and cache in memory."""
    with open(_ENCODER_NAME_PATH) as fh:
        encoder_name = fh.read().strip()

    from sentence_transformers import SentenceTransformer
    encoder = SentenceTransformer(encoder_name)
    model = joblib.load(_MODEL_PATH)
    scaler = joblib.load(_SCALER_PATH)
    return encoder, model, scaler


def _count_keywords(text_lower: str, keywords: List[str]) -> int:
    return sum(1 for kw in keywords if kw in text_lower)


def _extract_engineered_features(text: str) -> np.ndarray:
    text_lower = text.lower()
    words = text.split()
    features = np.array([
        float(len(text)),
        float(len(words)),
        0.0,  # missing_count
        float(_count_keywords(text_lower, _URGENT_KEYWORDS)),
        float(_count_keywords(text_lower, _HIGH_PAY_KEYWORDS)),
        float(_count_keywords(text_lower, _LOW_SKILL_KEYWORDS)),
        float(_count_keywords(text_lower, _SUSPICIOUS_KEYWORDS)),
        1.0 if any(kw in text_lower for kw in _TELECOMMUTE_KEYWORDS) else 0.0,
        0.0,  # has_company_logo
        1.0 if "?" in text else 0.0,
    ], dtype=np.float64)
    return features


def predict_scam(title: str, description: str) -> Dict:
    """
    Predict whether a job post is a scam.

    Args:
        title: Job post title.
        description: Job post description.

    Returns:
        {
            "scam_probability": float (0-1),
            "is_scam": bool,
            "model_used": "sbert_rf",
        }

    Raises:
        RuntimeError: if model loading or inference fails.
    """
    encoder, model, scaler = _load_models()

    text = f"{title} {description}".strip()

    # SBERT embedding: (384,)
    embedding = encoder.encode([text], show_progress_bar=False)[0]

    # Engineered features: (10,)
    engineered = _extract_engineered_features(text)

    # Combined feature vector: (1, 394)
    features = np.concatenate([embedding, engineered]).reshape(1, -1)

    features_scaled = scaler.transform(features)

    proba = model.predict_proba(features_scaled)[0]
    scam_prob = float(proba[1])  # probability of class 1 (scam)

    return {
        "scam_probability": round(scam_prob, 4),
        "is_scam": scam_prob >= SCAM_PREDICT_THRESHOLD,
        "model_used": "sbert_rf",
    }
