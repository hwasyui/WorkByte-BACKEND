import json
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from functions.db_manager import get_db
from functions.logger import logger

AUTO_APPROVE_DAYS: int = 30

_KEYWORDS_PATH = os.path.join(os.path.dirname(__file__), "moderation_keywords.json")

with open(_KEYWORDS_PATH, "r", encoding="utf-8") as _f:
    _kw_data = json.load(_f)

_LABEL_KEYWORDS: Dict[str, List[str]] = _kw_data["content_labels"]
_SCAM_KEYWORDS: List[str] = _kw_data["scam_keywords"]

# Scam thresholds (also documented in moderation_keywords.json _meta)
SCAM_FLAG_THRESHOLD: float = 0.10       # ≥ 1 keyword match → admin review queue
SCAM_AUTO_REMOVE_THRESHOLD: float = 0.85  # ≥ 5 matches + 30 days → auto-remove

# ML label names → DB/keyword naming convention
_ML_LABEL_REMAP = {
    "toxicity": "toxic",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


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
        hits = sum(1 for kw in keywords if kw in normalized)
        score = round(min(hits * 0.35, 1.0), 4)
        scores[label] = score
        if hits > 0:
            detected.append(label)

    return {
        "toxic_score":          scores["toxic"],
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
    primary harmful text scan entry point.

    1. runs inference with the best available trained model (chunked for long
       text, each label uses its own tuned threshold from config.pkl).
    2. on any failure (model files missing, cuda oom, etc.) logs a warning
       and transparently falls back to keyword matching.

    return shape is identical to scan_harmful_text() plus a 'scan_method' key
    ('ml' or 'keyword') so callers can log which path was taken.
    """
    try:
        from ai_related.harmful_text_detection.model_inference import predict

        ml = predict(text, model_type="best")

        # Map ML label names to the DB/keyword naming convention
        normalized_labels = [_ML_LABEL_REMAP.get(lbl, lbl) for lbl in ml["labels"]]

        return {
            "toxic_score":          round(ml["scores"].get("toxicity", 0.0), 4),
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
            "HARMFUL_TEXT",
            f"ML harmful text scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_harmful_text(text)


def insert_harmful_text_queue_entry(
    content_type: str,
    content_id: str,
    user_id: str,
    text: str,
    result: Dict,
) -> Optional[Dict]:
    """
    Insert an already-flagged scan result into harmful_text_queue as an audit/review
    record. Shared by queue_harmful_text_scan() (job posts, freelancer/client profiles,
    which scan-then-queue in one step) and the proposal instant-block path (which has
    already scanned and just needs the record persisted, not a second scan run).
    content_type: 'job_post' | 'freelancer_profile' | 'client_profile' | 'proposal'
    """
    scan_method = result.get("scan_method", "unknown")
    auto_approve_at = datetime.utcnow() + timedelta(days=AUTO_APPROVE_DAYS)
    try:
        row = _row(get_db().execute_query(
            """
            INSERT INTO harmful_text_queue (
                content_type, content_id, user_id,
                toxic_score, obscene_score,
                threat_score, insult_score, identity_hate_score,
                detected_labels, flagged_text, auto_approve_at
            ) VALUES (
                :content_type, :content_id, :user_id,
                :toxic_score, :obscene_score,
                :threat_score, :insult_score, :identity_hate_score,
                CAST(:detected_labels AS JSONB), :flagged_text, :auto_approve_at
            )
            RETURNING *
            """,
            params={
                "content_type":         content_type,
                "content_id":           content_id,
                "user_id":              user_id,
                "toxic_score":          result["toxic_score"],
                "obscene_score":        result["obscene_score"],
                "threat_score":         result["threat_score"],
                "insult_score":         result["insult_score"],
                "identity_hate_score":  result["identity_hate_score"],
                "detected_labels":      json.dumps(result["detected_labels"]),
                "flagged_text":         text[:500],
                "auto_approve_at":      auto_approve_at,
            },
        ))
        logger(
            "ADMIN",
            f"Content flagged via {scan_method} scan: {content_type} {content_id} labels={result['detected_labels']}",
            level="INFO",
        )
        return row
    except Exception as e:
        logger("ADMIN", f"Failed to queue content scan: {e}", level="ERROR")
        return None


def _row(result) -> Optional[Dict]:
    if not result:
        return None
    return dict(result[0])
