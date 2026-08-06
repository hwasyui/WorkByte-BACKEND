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
from typing import Any, Dict, Iterable, List, Optional

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
    ml_sentiment: Dict[str, Any],
    ml_mismatch: Dict[str, Any],
    outcome: Dict[str, Any],
    specificity: Optional[Dict[str, Any]] = None,
) -> None:
    """
    One record per analysed review: the inputs, what each model said, and what the
    pipeline decided.

    Both the LLM's and the ML models' outputs are kept even where the pipeline only
    uses one. Their disagreements are the interesting part - a case where the LLM
    and the classifier split is exactly the kind of example worth hand-labelling.

    The authenticity classifier used to be logged here as well. It is no longer run
    at all (see review_decision.blend_authenticity), so records written from this
    point carry no "ml.authenticity" key. Readers must treat it as optional: older
    lines in the same file still have it. Nothing is rewritten - the log is
    append-only and the historical values are still true records of what that model
    said at the time.
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
            "sentiment": ml_sentiment,
            "mismatch": ml_mismatch,
        },
        # Component 5. Not a model, but it is a signal the pipeline acted on, and a
        # record of a held review that does not say which check held it is not
        # usable as training data.
        "specificity": specificity,
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
    reason: Optional[str] = None,
) -> None:
    """
    A human ruling on the pipeline's decision. `prior_analysis` should carry the
    stored analysis row as it was BEFORE the action, since that is what the label
    attaches to - without it the record says a decision was ruled on but not what
    was ruled on.

    `action` is "override_publish" when the human reversed the hold and
    "uphold" when they confirmed it. BOTH are labels. An agreement is as
    informative for training as a reversal - it says the pipeline was right on a
    case it was unsure enough about to hold - and logging only reversals would
    build a set of nothing but pipeline errors.

    `reason` is the admin's free-text justification. It is the only place the
    human's actual reasoning is recorded; the status change alone says what was
    decided but never why.
    """
    _append({
        "event": "admin_override",
        "review_id": str(review_id),
        "review_kind": review_kind,
        "admin_user_id": str(admin_user_id),
        "action": action,
        "prior_status": prior_status,
        "prior_analysis": prior_analysis,
        "reason": reason,
    })


def read_admin_rulings(review_id: str) -> List[Dict[str, Any]]:
    """Every admin ruling recorded for a review, oldest first.

    log_admin_override was write-only until this existed: read_latest_judgment
    filters to `pipeline_judgment`, so nothing read the `admin_override` records
    back. The API makes the admin's reason mandatory, and it was going to a file
    no code opened - a second admin opening the same review saw no sign that
    anyone had ruled, or why. That is the same gap red_flag_alerts closed with
    resolved_by/resolution_note.

    A list rather than the latest one: an upheld review can still be
    override-published afterwards, and the sequence is the point - "suppressed,
    then released on appeal" is a different history from "released", and only the
    full list distinguishes them.

    `prior_analysis` is deliberately dropped from the returned records. It exists
    for training and is large; nothing in an admin view renders it.

    Returns [] when the log is missing, unreadable, or has no ruling for this
    review - all normal (logs/ is gitignored, and reviews ruled on before this
    logging existed have no entry). Callers must treat rulings as optional.
    """
    if not os.path.exists(JUDGMENT_LOG_PATH):
        return []
    target = str(review_id)
    rulings: List[Dict[str, Any]] = []
    try:
        with open(JUDGMENT_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or target not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") != "admin_override" or record.get("review_id") != target:
                    continue
                rulings.append({
                    "action": record.get("action"),
                    "admin_user_id": record.get("admin_user_id"),
                    # Records written before the reason dialog shipped carry a
                    # null reason. Kept as "" rather than dropped: a ruling with
                    # no recorded justification is still a ruling, and the second
                    # admin needs to see that it happened.
                    "reason": record.get("reason") or "",
                    "prior_status": record.get("prior_status"),
                    "logged_at": record.get("logged_at"),
                })
    except Exception as e:
        logger("JUDGMENT_LOG", f"Could not read admin rulings: {str(e)[:200]}", level="WARNING")
        return []
    return rulings


def count_admin_rulings(review_ids: Iterable[str]) -> Dict[str, int]:
    """How many admin rulings each of these reviews has on file, as
    {review_id: count}. Ids absent from the result have none.

    Takes a whole page of ids and reads the file once, rather than exposing the
    per-review reader to the queue endpoints. A read_admin_rulings() call inside
    the per-item loop would re-scan an append-only file that grows without bound,
    once per row, on every queue page load.

    Only the count, not the rulings: the queue card renders a "ruled on" marker
    and nothing else, and shipping every reason for every row would grow the
    triage payload for text no queue view displays. The full list is on the
    detail endpoint, which is where an admin reads them.

    Same failure policy as the rest of this module - a missing, unreadable or
    corrupt log yields an empty result, never an exception. Nothing gates on
    these counts; they only decide which buttons a card shows.
    """
    targets = {str(rid) for rid in review_ids if rid}
    if not targets or not os.path.exists(JUDGMENT_LOG_PATH):
        return {}
    counts: Dict[str, int] = {}
    try:
        with open(JUDGMENT_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                # Cheap reject before parsing: the overwhelming majority of lines
                # are pipeline_judgment records carrying full review text.
                if not line or "admin_override" not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") != "admin_override":
                    continue
                review_id = record.get("review_id")
                if review_id in targets:
                    counts[review_id] = counts.get(review_id, 0) + 1
    except Exception as e:
        logger("JUDGMENT_LOG", f"Could not count admin rulings: {str(e)[:200]}", level="WARNING")
        return {}
    return counts


def read_latest_judgment(review_id: str) -> Optional[Dict[str, Any]]:
    """
    The most recent pipeline_judgment record for a review, or None.

    This is the ONLY source of the per-component breakdown. review_ai_analysis
    stores the blended authenticity_score but not its three inputs, nor the raw
    vs length-calibrated P(fake), nor which model objected - so an admin looking
    at a held review cannot otherwise tell whether the LLM or the classifier
    raised the objection they are being asked to adjudicate.

    Reads the whole file and keeps the last match rather than stopping at the
    first: the log is append-only and the reconcile sweep re-runs interrupted
    analyses, so a review can hold several records and the last one is the one
    that produced the stored verdict.

    Returns None when the log is missing, unreadable, or has no record for this
    review - all of which are normal (logs/ is gitignored and reviews analysed
    before judgment logging existed have no entry). Callers must treat the
    breakdown as optional rather than failing the admin view without it.
    """
    if not os.path.exists(JUDGMENT_LOG_PATH):
        return None
    target = str(review_id)
    latest = None
    try:
        with open(JUDGMENT_LOG_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or target not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event") == "pipeline_judgment" and record.get("review_id") == target:
                    latest = record
    except Exception as e:
        logger("JUDGMENT_LOG", f"Could not read judgment log: {str(e)[:200]}", level="WARNING")
        return None
    return latest
