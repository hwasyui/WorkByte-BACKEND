import asyncio
import json
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typing import Dict, List, Optional

# Ceiling on how long a caller waits for the ML model before falling back to keyword
# scanning. Without this, a caller just awaits scan_harmful_text_with_ml_fallback()
# forever if the model's executor queue backs up under load - the wait would be
# invisible (no error, no timeout) until it was very large, the same failure shape as
# the deadlock this concurrency work started from, just hidden one layer deeper now
# that callers no longer see the executor directly.
#
# Two values, not one: background scans (education/work_experience/portfolio/job_post/
# proposal - fired via asyncio.create_task, moderation_status starts 'scanning' and the
# caller's HTTP response has already returned) can afford to wait the full ceiling since
# no one is looking at a spinner. Scans that block the caller's HTTP response (DM message
# send, freelancer/client bio save) should give up sooner - measured worst-case latency
# under 60-way concurrent load was 9.36s (HARMFUL_TEXT.md section 22), so 15s clears that
# with room to spare without making someone wait as long as the background ceiling for no
# reason.
_ML_SCAN_TIMEOUT_SECONDS = float(os.getenv("HARMFUL_TEXT_SCAN_TIMEOUT_SECONDS", "30"))
ML_SCAN_TIMEOUT_BLOCKING_SECONDS = float(os.getenv("HARMFUL_TEXT_SCAN_TIMEOUT_BLOCKING_SECONDS", "15"))

from functions.db_manager import get_db
from functions.logger import logger

_KEYWORDS_PATH = os.path.join(os.path.dirname(__file__), "moderation_keywords.json")

with open(_KEYWORDS_PATH, "r", encoding="utf-8") as _f:
    _kw_data = json.load(_f)

_LABEL_KEYWORDS: Dict[str, List[str]] = _kw_data["content_labels"]
_SCAM_KEYWORDS: List[str] = _kw_data["scam_keywords"]
_PREFIX_KEYWORDS: set = set(_kw_data.get("prefix_keywords", []))

# Scam thresholds (also documented in moderation_keywords.json _meta)
SCAM_FLAG_THRESHOLD: float = 0.10       # ≥ 1 keyword match → admin review queue
SCAM_AUTO_REMOVE_THRESHOLD: float = 0.85  # ≥ 5 matches + 30 days → auto-remove

# Below this word count, an SBERT embedding of title+description carries too little
# signal for the ML model to score reliably (e.g. "desc" false-flagging as scam) —
# use the deterministic keyword scan instead.
SCAM_MIN_TEXT_WORDS: int = 15

# ML label names → DB/keyword naming convention
_ML_LABEL_REMAP = {
    "toxicity": "toxic",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _keyword_hits(keywords: List[str], normalized_text: str) -> List[str]:
    """
    Word-boundary keyword matching - plain substring matching (`kw in text`)
    false-flags text where the keyword is just a fragment of an unrelated word
    (e.g. a keyword "ass" would match inside "class" or "assignment"). Requiring
    \\b on both sides fixes that for ordinary keywords; the handful of
    intentionally-truncated stems in _PREFIX_KEYWORDS only get a left boundary
    so they keep matching their longer word forms.
    """
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
    matched = _keyword_hits(_SCAM_KEYWORDS, normalized)
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

    if len(combined.split()) < SCAM_MIN_TEXT_WORDS:
        result = scan_for_scam(combined)
        result["scan_method"] = "keyword_short_text"
        return result

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


async def scan_harmful_text_with_ml_fallback(text: str, timeout: Optional[float] = None) -> Dict:
    """
    primary harmful text scan entry point.

    1. runs inference with the best available trained model (chunked for long
       text, each label uses its own tuned threshold from config.pkl).
    2. on any failure (model files missing, cuda oom, timeout, etc.) logs a warning
       and transparently falls back to keyword matching.

    return shape is identical to scan_harmful_text() plus a 'scan_method' key
    ('ml' or 'keyword') so callers can log which path was taken. Callers just
    `await` this directly - no asyncio.to_thread() needed, the ML call is natively
    async. `timeout` defaults to _ML_SCAN_TIMEOUT_SECONDS (background scans); callers
    that block the user's HTTP response should pass ML_SCAN_TIMEOUT_BLOCKING_SECONDS
    instead so a slow model degrades to keyword-only well before the request feels
    frozen, not after the same long wait a background scan can afford.
    """
    effective_timeout = timeout if timeout is not None else _ML_SCAN_TIMEOUT_SECONDS
    try:
        from ai_related.harmful_text_detection.model_inference import predict

        ml = await asyncio.wait_for(predict(text, model_type="best"), timeout=effective_timeout)

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

    except asyncio.TimeoutError:
        logger(
            "HARMFUL_TEXT",
            f"ML harmful text scan timed out after {effective_timeout}s; falling back to keyword scan",
            level="WARNING",
        )
        return scan_harmful_text(text)
    except Exception as exc:
        logger(
            "HARMFUL_TEXT",
            f"ML harmful text scan failed ({type(exc).__name__}: {exc}); falling back to keyword scan",
            level="WARNING",
        )
        return scan_harmful_text(text)


_NO_MATCH_RESULT: Dict = {
    "toxic_score": 0.0, "obscene_score": 0.0, "threat_score": 0.0,
    "insult_score": 0.0, "identity_hate_score": 0.0,
    "detected_labels": [], "is_flagged": False, "scan_method": "none",
}


async def scan_short_and_long_text(short_text: str, long_text: str) -> Dict:
    """
    Routes context-free short fields (institution_name, degree, job_title,
    company_name, project_title, ...) and a long-context field (description/bio)
    through the right scanner each, per HARMFUL_TEXT.md section 17: a 1-4 word field
    gives the ML model nothing to condition on, so it matches vocabulary rather than
    meaning ('Kill Chain Analysis' scores threat=0.995, identical confidence to
    'kill yourself'). Short fields are keyword-only; the long field goes through the
    ML model with keyword fallback, since it has real sentence context.

    Short fields are checked first - if flagged, that result is returned without ever
    calling the ML model. Return shape matches scan_harmful_text_with_ml_fallback().
    """
    if short_text and short_text.strip():
        short_result = scan_harmful_text(short_text)
        if short_result["is_flagged"]:
            return short_result

    if not long_text or not long_text.strip():
        return _NO_MATCH_RESULT
    return await scan_harmful_text_with_ml_fallback(long_text)


def insert_harmful_text_queue_entry(
    content_type: str,
    content_id: str,
    user_id: str,
    text: str,
    result: Dict,
) -> Optional[Dict]:
    """
    Insert an already-flagged scan result into harmful_text_queue as a plain audit
    record - nothing reads this row to decide an action; it exists purely so an admin
    can browse what's been flagged over time (optionally marking one reviewed via
    mark_moderation_item_reviewed(), which is bookkeeping only, not a decision with a
    side effect). Shared by queue_harmful_text_scan() (manual admin scan utility) and
    every content type's own instant-block scan (job_post, portfolio, education,
    work_experience, proposal), which have already scanned and just need the record
    persisted, not a second scan run.
    """
    scan_method = result.get("scan_method", "unknown")
    try:
        row = _row(get_db().execute_query(
            """
            INSERT INTO harmful_text_queue (
                content_type, content_id, user_id,
                toxic_score, obscene_score,
                threat_score, insult_score, identity_hate_score,
                detected_labels, flagged_text
            ) VALUES (
                :content_type, :content_id, :user_id,
                :toxic_score, :obscene_score,
                :threat_score, :insult_score, :identity_hate_score,
                CAST(:detected_labels AS JSONB), :flagged_text
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
