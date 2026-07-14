import json
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typing import Dict, List, Optional

from functions.logger import logger

_KEYWORDS_PATH = os.path.join(os.path.dirname(__file__), "moderation_keywords.json")

with open(_KEYWORDS_PATH, "r", encoding="utf-8") as _f:
    _kw_data = json.load(_f)

_LABEL_KEYWORDS: Dict[str, List[str]] = _kw_data["content_labels"]
_SCAM_KEYWORDS: List[str] = _kw_data["scam_keywords"]

# Scam thresholds (also documented in moderation_keywords.json _meta)
SCAM_FLAG_THRESHOLD: float = 0.10       # ≥ 1 keyword match → admin review queue
SCAM_AUTO_REMOVE_THRESHOLD: float = 0.85  # ≥ 5 matches + 30 days → auto-remove


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


# Keywords that are deliberately truncated stems (match longer word forms too).
# Everything else must match on a whole-word boundary, otherwise "dumb" hits
# "dumbbell" and "cunt" hits "Scunthorpe".
_PREFIX_KEYWORDS = set(_kw_data.get("prefix_keywords", []))


def _keyword_hits(keywords: List[str], normalized_text: str) -> List[str]:
    hits = []
    for kw in keywords:
        pattern = rf"\b{re.escape(kw)}" if kw in _PREFIX_KEYWORDS else rf"\b{re.escape(kw)}\b"
        if re.search(pattern, normalized_text):
            hits.append(kw)
    return hits


def scan_harmful_text(text: str) -> Dict:
    """
    Deterministic keyword scan across 5 harm labels.
    Scoring: score = min(hit_count × 0.35, 1.0)
      - 1 keyword hit: 0.35 (flags for admin review)
      - 2 hits: 0.70
      - 3+ hits: 1.0
    Used directly when called explicitly, or as fallback by
    scan_harmful_text_with_ml_fallback() when the ML model is unavailable.
    """
    normalized = _normalize(text)
    scores: Dict[str, float] = {}
    detected: List[str] = []

    for label, keywords in _LABEL_KEYWORDS.items():
        hits = len(_keyword_hits(keywords, normalized))
        score = round(min(hits * 0.35, 1.0), 4)
        scores[label] = score
        if hits > 0:
            detected.append(label)

    return {
        "toxic_score":          scores["toxicity"],
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
      - 6 keywords: 100% scam score
      - 5 keywords: 83% (near auto-remove threshold)
    Keyword list and threshold rationale: moderation_keywords.json.
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


def scan_harmful_text_with_ml_fallback(text: str) -> Dict:
    """
    Primary harmful text scan entry point.

    1. Attempts inference with the trained BERT model, using its own tuned
       per-label thresholds (toxicity 0.50, obscene 0.38, threat 0.58, insult 0.28,
       identity_hate 0.38 -- from config.pkl, not a flat 0.5 for every label).
       Model metrics on Jigsaw+ETHOS test set: F1=0.85, precision=0.79,
       recall=0.93, hamming_loss=0.068.
    2. On any failure (model files missing, CUDA OOM, etc.) logs a WARNING
       and transparently falls back to keyword matching.

    Return shape is identical to scan_harmful_text() plus a 'scan_method' key
    ('ml' or 'keyword') so callers can log which path was taken.
    """
    try:
        from ai_related.harmful_text_detection.model_inference import predict

        ml = predict(text, model_type="best")

        return {
            "toxic_score":          round(ml["scores"].get("toxicity", 0.0), 4),
            "obscene_score":        round(ml["scores"].get("obscene", 0.0), 4),
            "threat_score":         round(ml["scores"].get("threat", 0.0), 4),
            "insult_score":         round(ml["scores"].get("insult", 0.0), 4),
            "identity_hate_score":  round(ml["scores"].get("identity_hate", 0.0), 4),
            "detected_labels":      ml["labels"],
            "is_flagged":           ml["is_harmful"],
            "scan_method":          "ml",
        }

    except Exception as exc:
        logger(
            "HARMFUL_TEXT",
            f"ML harmful text scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_harmful_text(text)
