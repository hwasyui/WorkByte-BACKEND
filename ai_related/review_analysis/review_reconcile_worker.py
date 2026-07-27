import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from ai_related.review_analysis.review_ai_functions import ANALYSIS_UNAVAILABLE_REASON

SWEEP_INTERVAL_SECONDS = 900   # 15 min - the pipeline itself takes seconds, so this is
                               # about surviving restarts, not about latency
STRANDED_AFTER_MINUTES = 10    # grace period: long enough that a pipeline still running
                               # is never re-queued underneath itself
RETRY_WINDOW_DAYS = 7          # past this, an unresolved review needs a human rather
                               # than an indefinite retry loop


def _find_stranded_reviews() -> tuple[list[str], list[str]]:
    """Reviews that were submitted but never got a verdict. Two distinct cases:

    1. Still 'pending' with ratings but no ai_analysis row - the pipeline died
       between the submit committing and the analysis running (a restart, a crash).
       Ratings present is what separates this from "the reviewer hasn't submitted
       yet", so no submission deadline is needed to make it safe.

    2. 'flagged' carrying ANALYSIS_UNAVAILABLE_REASON - the analysis ran but the
       LLM was unreachable, so it failed closed. That is a transient infrastructure
       problem, not a judgement about the review, and without this the review would
       sit in the admin queue until a human noticed a Groq outage.

    Reviews flagged for real reasons are deliberately NOT retried - a verdict was
    reached and re-running would just produce it again.
    """
    db = get_db()

    freelancer_side = db.execute_query(
        """
        SELECT r.id
        FROM reviews r
        JOIN review_ratings rr          ON rr.review_id = r.id
        LEFT JOIN review_ai_analysis ra ON ra.review_id = r.id
        WHERE r.created_at < NOW() - (:mins * INTERVAL '1 minute')
          AND r.created_at > NOW() - (:days * INTERVAL '1 day')
          AND (
                (r.status = 'pending' AND ra.id IS NULL)
             OR (r.status = 'flagged' AND ra.flag_reasons @> CAST(:marker AS jsonb))
          )
        GROUP BY r.id
        """,
        {"mins": STRANDED_AFTER_MINUTES, "days": RETRY_WINDOW_DAYS,
         "marker": json.dumps([ANALYSIS_UNAVAILABLE_REASON])},
    ) or []

    client_side = db.execute_query(
        """
        SELECT cr.id
        FROM client_reviews cr
        JOIN client_review_ratings crr          ON crr.client_review_id = cr.id
        LEFT JOIN client_review_ai_analysis cra ON cra.client_review_id = cr.id
        WHERE cr.created_at < NOW() - (:mins * INTERVAL '1 minute')
          AND cr.created_at > NOW() - (:days * INTERVAL '1 day')
          AND (
                (cr.status = 'pending' AND cra.id IS NULL)
             OR (cr.status = 'flagged' AND cra.flag_reasons @> CAST(:marker AS jsonb))
          )
        GROUP BY cr.id
        """,
        {"mins": STRANDED_AFTER_MINUTES, "days": RETRY_WINDOW_DAYS,
         "marker": json.dumps([ANALYSIS_UNAVAILABLE_REASON])},
    ) or []

    return [str(r["id"]) for r in freelancer_side], [str(r["id"]) for r in client_side]


async def run_review_reconcile_sweep() -> None:
    """Re-queue review pipelines that were interrupted before they finished.

    Submission and analysis are separate steps: the route commits the submission
    and hands the pipeline to FastAPI BackgroundTasks, which is in-process. A
    restart, crash, or unhandled error between those two points leaves the review
    'pending' forever - ratings saved, never analysed, never published, and
    invisible to everyone including the freelancer it belongs to.
    """
    # Imported here rather than at module scope: the pipelines import the ML
    # models, and the worker is constructed during app startup.
    from ai_related.review_analysis.review_pipeline import run_post_review_pipeline
    from ai_related.review_analysis.client_review_pipeline import (
        run_client_review_post_submission_pipeline,
    )

    review_ids, client_review_ids = _find_stranded_reviews()
    if not review_ids and not client_review_ids:
        return

    logger(
        "REVIEW_RECONCILE",
        f"Found {len(review_ids)} stranded reviews and {len(client_review_ids)} stranded client reviews",
        level="WARNING",
    )

    for review_id in review_ids:
        try:
            await run_post_review_pipeline(review_id, is_retry=True)
            logger("REVIEW_RECONCILE", f"Re-queued review {review_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_RECONCILE", f"Re-queue failed for review {review_id}: {e}", level="ERROR")

    for client_review_id in client_review_ids:
        try:
            await run_client_review_post_submission_pipeline(client_review_id, is_retry=True)
            logger("REVIEW_RECONCILE", f"Re-queued client review {client_review_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_RECONCILE", f"Re-queue failed for client review {client_review_id}: {e}", level="ERROR")


async def review_reconcile_loop() -> None:
    """Infinite loop running the stranded-review sweep every SWEEP_INTERVAL_SECONDS.
    Launched via asyncio.create_task() in main.py lifespan startup."""
    logger("REVIEW_RECONCILE", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await run_review_reconcile_sweep()
        except Exception as e:
            logger("REVIEW_RECONCILE", f"Sweep loop unhandled error: {e}", level="ERROR")
