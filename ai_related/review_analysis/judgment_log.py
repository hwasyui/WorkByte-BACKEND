"""
Append-only log of model judgments and admin decisions, for building in-domain
training data.

The problem this exists to solve
-------------------------------
Every model in review_ml/ is trained on Amazon product reviews, because that is
the only labelled review corpus this project has. That mismatch is behind almost
every weakness measured so far: sentiment labels derived from star ratings rather
than sentiment, an authenticity model that learned "short = generated" from a
corpus where that is true, and a threshold raised to 0.75 because the model kept
flagging genuine short praise.

The fix is in-domain data, and this platform generates it continuously - it just
throws it away. Two sources:

  1. LLM JUDGMENTS. analyze_review_full() already runs Groq over every review and
     returns sentiment, authenticity and flag reasons for REAL freelance reviews.
     The pipeline uses some of it and discards the rest. Logged here, a few
     thousand reviews become a distillation set: LLM as teacher, cheap classifier
     as student, trained on the actual deployment distribution.

  2. ADMIN OVERRIDES. When an admin override-publishes a held review, that is a
     human saying the pipeline was wrong, on exactly the decision the pipeline
     exists to make. These are the only true labels available for the publish
     decision itself, and there is no substitute for them.

Written as JSONL to logs/ rather than a database table so it needs no migration in
the DATABASE repo. logs/ is gitignored, so nothing here reaches version control.

Storage note: this file contains review text, which is already stored in the
database and already readable by any authenticated user via public_review(). No
new exposure is created, but it is a plaintext file on disk and should be treated
as production data.

Every function here swallows its own exceptions. Losing a training record is
acceptable; failing a review submission because logging broke is not.
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from functions.logger import logger

_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs"
)
JUDGMENT_LOG_PATH = os.path.join(_LOG_DIR, "review_judgments.jsonl")


def _append(record: Dict[str, Any]) -> None:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        record["logged_at"] = datetime.now(timezone.utc).isoformat()
        with open(JUDGMENT_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger("JUDGMENT_LOG", f"Could not append judgment record: {str(e)[:200]}",
               level="WARNING")


def log_pipeline_judgment(
    *,
    review_id: str,
    review_kind: str,
    review_text: str,
    answer_text: str,
    ratings: Optional[list],
    avg_stars: float,
    llm_analysis: Dict[str, Any],
    ml_authenticity: Dict[str, Any],
    ml_sentiment: Dict[str, Any],
    ml_mismatch: Dict[str, Any],
    outcome: Dict[str, Any],
) -> None:
    """
    One record per analysed review: the inputs, what each model said, and what the
    pipeline decided.

    Both the LLM's and the ML models' outputs are kept even where the pipeline only
    uses one. Their disagreements are the interesting part - a case where the LLM
    and the classifier split is exactly the kind of example worth hand-labelling.
    """
    _append({
        "event": "pipeline_judgment",
        "review_id": str(review_id),
        "review_kind": review_kind,
        "input": {
            "text": review_text,
            "answer": answer_text,
            "ratings": ratings,
            "avg_stars": avg_stars,
        },
        "llm": {
            "sentiment_label": llm_analysis.get("sentiment_label"),
            "sentiment_score": llm_analysis.get("sentiment_score"),
            "authenticity_score": llm_analysis.get("authenticity_score"),
            "is_flagged_fake": llm_analysis.get("is_flagged_fake"),
            "is_flagged_coerced": llm_analysis.get("is_flagged_coerced"),
            "sentiment_mismatch": llm_analysis.get("sentiment_mismatch"),
            "answer_groundedness": llm_analysis.get("answer_groundedness"),
            "communication_quality_score": llm_analysis.get("communication_quality_score"),
            "flag_reasons": llm_analysis.get("flag_reasons"),
            "analysis_unavailable": llm_analysis.get("analysis_unavailable"),
        },
        "ml": {
            "authenticity": ml_authenticity,
            "sentiment": ml_sentiment,
            "mismatch": ml_mismatch,
        },
        "outcome": outcome,
    })


def log_admin_override(
    *,
    review_id: str,
    review_kind: str,
    admin_user_id: str,
    action: str,
    prior_status: Optional[str] = None,
    prior_analysis: Optional[Dict[str, Any]] = None,
) -> None:
    """
    A human correcting the pipeline. `prior_analysis` should carry the stored
    analysis row as it was BEFORE the override, since that is what the label
    attaches to - without it the record says a decision was reversed but not what
    was reversed.
    """
    _append({
        "event": "admin_override",
        "review_id": str(review_id),
        "review_kind": review_kind,
        "admin_user_id": str(admin_user_id),
        "action": action,
        "prior_status": prior_status,
        "prior_analysis": prior_analysis,
    })
