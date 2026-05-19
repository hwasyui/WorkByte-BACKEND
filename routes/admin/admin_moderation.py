"""
Toxicity detection and scam detection utilities.

scan_toxicity_with_ml_fallback() is the primary entry point for toxicity scans.
It tries the trained DistilBERT ML model first (F1=0.7298 on Jigsaw+ETHOS test set),
then falls back to deterministic keyword matching if the model is unavailable.

Keyword lists, threshold values, and their rationale are documented in
moderation_keywords.json (same directory) so they can be audited and updated
without touching Python code.
"""
import json
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typing import Dict, List, Optional

from functions.logger import logger

# ---------------------------------------------------------------------------
# Load keyword data
# ---------------------------------------------------------------------------

_KEYWORDS_PATH = os.path.join(os.path.dirname(__file__), "moderation_keywords.json")

with open(_KEYWORDS_PATH, "r", encoding="utf-8") as _f:
    _kw_data = json.load(_f)

_LABEL_KEYWORDS: Dict[str, List[str]] = _kw_data["content_labels"]
_SCAM_KEYWORDS: List[str] = _kw_data["scam_keywords"]

# Scam thresholds (also documented in moderation_keywords.json _meta)
SCAM_FLAG_THRESHOLD: float = 0.10       # ≥ 1 keyword match → admin review queue
SCAM_AUTO_REMOVE_THRESHOLD: float = 0.85  # ≥ 5 matches + 30 days → auto-remove

# ML label names → DB/keyword naming convention (only the two that differ)
_ML_LABEL_REMAP = {
    "toxicity": "toxic",
    "severe_toxicity": "severe_toxic",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


# ---------------------------------------------------------------------------
# Keyword-based scan (fallback / standalone)
# ---------------------------------------------------------------------------

def scan_toxicity(text: str) -> Dict:
    """
    Deterministic keyword scan across 6 harm labels.
    Scoring: score = min(hit_count × 0.35, 1.0)
      — 1 keyword hit → 0.35 (flags for admin review)
      — 2 hits → 0.70
      — 3+ hits → 1.0
    Used directly when called explicitly, or as fallback by
    scan_toxicity_with_ml_fallback() when the ML model is unavailable.
    """
    normalized = _normalize(text)
    scores: Dict[str, float] = {}
    detected: List[str] = []

    for label, keywords in _LABEL_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in normalized)
        score = round(min(hits * 0.35, 1.0), 4)
        scores[label] = score
        if hits > 0:
            detected.append(label)

    return {
        "toxic_score":          scores["toxic"],
        "severe_toxic_score":   scores["severe_toxic"],
        "obscene_score":        scores["obscene"],
        "threat_score":         scores["threat"],
        "insult_score":         scores["insult"],
        "identity_hate_score":  scores["identity_hate"],
        "detected_labels":      detected,
        "is_flagged":           len(detected) > 0,
        "scan_method":          "keyword",
    }


def scan_for_scam(text: str) -> Dict:
    """
    Keyword-based scam indicator scan for job posts.
    Score = min(matched_count / 6.0, 1.0)
      — 6 keywords ≈ 100% scam score
      — 5 keywords ≈ 83% (near auto-remove threshold)
    Keyword list and threshold rationale: moderation_keywords.json
    """
    normalized = _normalize(text)
    matched = [kw for kw in _SCAM_KEYWORDS if kw in normalized]
    score = round(min(len(matched) / 6.0, 1.0), 4)
    return {
        "scam_score":        score,
        "detected_keywords": matched,
        "is_flagged":        score >= SCAM_FLAG_THRESHOLD,
        "scan_method":       "keyword",
    }


def scan_for_scam_with_ml_fallback(title: str, description: str) -> Dict:
    """
    ML-first scam scan using SBERT + Random Forest (AUC-ROC 0.978).
    Falls back transparently to keyword scan if the model is unavailable.

    Returns the same shape as scan_for_scam() plus a 'scan_method' key
    ('sbert_rf' or 'keyword') so callers can log which path ran.
    """
    combined = f"{title} {description}"
    try:
        from ai_related.job_scam_detection.scam_detector import predict_scam

        ml = predict_scam(title, description)

        # Also collect keyword matches for admin review context (informational only).
        normalized = _normalize(combined)
        matched_keywords = [kw for kw in _SCAM_KEYWORDS if kw in normalized]

        return {
            "scam_score":        ml["scam_probability"],
            "detected_keywords": matched_keywords,
            "is_flagged":        ml["is_scam"],
            "scan_method":       "sbert_rf",
        }
    except Exception as exc:
        logger(
            "MODERATION",
            f"ML scam scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_for_scam(combined)


# ---------------------------------------------------------------------------
# ML-first scan (primary entry point)
# ---------------------------------------------------------------------------

def scan_toxicity_with_ml_fallback(text: str) -> Dict:
    """
    Primary toxicity scan entry point.

    1. Attempts inference with the trained RoBERTa model (threshold=0.5).
       Model metrics on Jigsaw+ETHOS test set: F1=0.71, precision=0.70,
       recall=0.73, hamming_loss=0.062.
    2. On any failure (model files missing, CUDA OOM, etc.) logs a WARNING
       and transparently falls back to keyword matching.

    Return shape is identical to scan_toxicity() plus a 'scan_method' key
    ('ml' or 'keyword') so callers can log which path was taken.
    """
    try:
        from ai_related.toxicity_detection.model_inference import predict

        ml = predict(text, model_type="best", threshold=0.5)

        # Map ML label names to the DB/keyword naming convention
        normalized_labels = [_ML_LABEL_REMAP.get(lbl, lbl) for lbl in ml["labels"]]

        return {
            "toxic_score":          round(ml["scores"].get("toxicity", 0.0), 4),
            "severe_toxic_score":   round(ml["scores"].get("severe_toxicity", 0.0), 4),
            "obscene_score":        round(ml["scores"].get("obscene", 0.0), 4),
            "threat_score":         round(ml["scores"].get("threat", 0.0), 4),
            "insult_score":         round(ml["scores"].get("insult", 0.0), 4),
            "identity_hate_score":  round(ml["scores"].get("identity_hate", 0.0), 4),
            "detected_labels":      normalized_labels,
            "is_flagged":           ml["is_harmful"],
            "scan_method":          "ml",
        }

    except Exception as exc:
        logger(
            "TOXICITY",
            f"ML toxicity scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_toxicity(text)
