import json
import os
import sys
from functools import lru_cache
from typing import Dict

import joblib

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "model3_sbert_rf_complete")

EXPIRE_CLOSE_THRESHOLD = 0.4
AUTO_CLOSE_THRESHOLD = 0.6

_NLTK_PACKAGES = {
    "corpora/stopwords":    "stopwords",
    "corpora/wordnet":      "wordnet",
    "corpora/omw-1.4":      "omw-1.4",
    "tokenizers/punkt_tab": "punkt_tab",
}


def _ensure_nltk() -> None:
    import nltk

    for path, package in _NLTK_PACKAGES.items():
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(package, quiet=True)


@lru_cache(maxsize=1)
def get_thresholds() -> Dict[str, float]:
    with open(os.path.join(_MODEL_DIR, "thresholds.json")) as fh:
        raw = json.load(fh)
    return {
        "review":       float(raw["SOFT_FLAG"]),
        "expire_close": EXPIRE_CLOSE_THRESHOLD,
        "auto_close":   AUTO_CLOSE_THRESHOLD,
    }


@lru_cache(maxsize=1)
def _load_models():
    _ensure_nltk()
    if _MODEL_DIR not in sys.path:
        sys.path.insert(0, _MODEL_DIR)

    import scam_features
    from sentence_transformers import SentenceTransformer

    with open(os.path.join(_MODEL_DIR, "sbert_encoder_name.txt")) as fh:
        encoder = SentenceTransformer(fh.read().strip())

    model = joblib.load(os.path.join(_MODEL_DIR, "sbert_rf_model.pkl"))
    scaler = joblib.load(os.path.join(_MODEL_DIR, "scaler.pkl"))
    calibrator = joblib.load(os.path.join(_MODEL_DIR, "calibrator.pkl"))
    return scam_features, encoder, model, scaler, calibrator


def predict_scam(title: str, description: str) -> Dict:
    scam_features, encoder, model, scaler, calibrator = _load_models()
    thresholds = get_thresholds()

    features = scam_features.build_row(title, description, encoder).reshape(1, -1)
    raw = float(model.predict_proba(scaler.transform(features))[0][1])
    score = float(calibrator.predict([raw])[0])

    return {
        "scam_probability": round(score, 4),
        "raw_probability":  round(raw, 4),
        "is_scam":          score >= thresholds["auto_close"],
        "needs_review":     score >= thresholds["review"],
        "model_used":       "sbert_rf_calibrated",
    }
