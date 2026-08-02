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

SCAM_FLAG_THRESHOLD: float = 0.10
SCAM_AUTO_REMOVE_THRESHOLD: float = 0.85

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())

_PREFIX_KEYWORDS = set(_kw_data.get("prefix_keywords", []))

def _keyword_hits(keywords: List[str], normalized_text: str) -> List[str]:
    hits = []
    for kw in keywords:
        pattern = rf"\b{re.escape(kw)}" if kw in _PREFIX_KEYWORDS else rf"\b{re.escape(kw)}\b"
        if re.search(pattern, normalized_text):
            hits.append(kw)
    return hits

def scan_harmful_text(text: str) -> Dict:
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
    normalized = _normalize(text)
    matched = [kw for kw in _SCAM_KEYWORDS if kw in normalized]
    score = round(min(len(matched) / 6.0, 1.0), 4)
    return {
        "scam_score":        score,
        "detected_keywords": matched,
        "is_flagged":        score >= SCAM_AUTO_REMOVE_THRESHOLD,
        "needs_review":      score >= SCAM_FLAG_THRESHOLD,
        "scan_method":       "keyword",
    }

def scan_for_scam_with_ml_fallback(title: str, description: str) -> Dict:
    combined = f"{title} {description}"
    try:
        from ai_related.job_scam_detection.scam_detector import predict_scam

        ml = predict_scam(title, description)

        normalized = _normalize(combined)
        matched_keywords = [kw for kw in _SCAM_KEYWORDS if kw in normalized]

        return {
            "scam_score":        ml["scam_probability"],
            "detected_keywords": matched_keywords,
            "is_flagged":        ml["is_scam"],
            "needs_review":      ml["needs_review"],
            "scan_method":       "sbert_rf_calibrated",
        }
    except Exception as exc:
        logger(
            "MODERATION",
            f"ML scam scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_for_scam(combined)

def scan_harmful_text_with_ml_fallback(text: str) -> Dict:
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

_SCORE_KEYS = ("toxic_score", "obscene_score", "threat_score",
               "insult_score", "identity_hate_score")

def scan_harmful_text_fields(*fields: str) -> Dict:
    parts = [f for f in fields if f and f.strip()]
    if not parts:
        return scan_harmful_text_with_ml_fallback("")
    if len(parts) == 1:
        return scan_harmful_text_with_ml_fallback(parts[0])

    results = [scan_harmful_text_with_ml_fallback(p) for p in parts]
    merged = {k: max(r[k] for r in results) for k in _SCORE_KEYS}
    merged["detected_labels"] = sorted({l for r in results for l in r["detected_labels"]})
    merged["is_flagged"]      = any(r["is_flagged"] for r in results)
    merged["scan_method"]     = ("keyword" if any(r["scan_method"] == "keyword" for r in results)
                                 else results[0]["scan_method"])
    return merged
