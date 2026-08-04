import os
import sys
import asyncio
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from functions.profile_ids import user_id_for_client, user_id_for_freelancer
from routes.reviews.review_functions import ReviewFunctions
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.review_analysis.client_review_ai_functions import (
    calculate_freelancer_review_fairness,
)
from ai_related.review_analysis.judgment_log import log_pipeline_judgment
from ai_related.review_analysis.review_decision import (
    blend_authenticity,
    compute_overall_pass,
    resolve_flags,
    split_ml_inputs,
)
from ai_related.review_analysis.review_ai_functions import (
    generate_targeted_question,
    compute_on_time_score,
    compute_revision_scores,
    compute_responsiveness_score,
    analyze_review_full,
    blend_communication_score,
    calculate_trust_score,
    calculate_weighted_review_avg,
    calculate_aggregate_performance,
    calculate_ai_trust_components,
    generate_freelancer_review_summary,
    shrink_toward_prior,
    MIN_REVIEWS_FOR_SUMMARY,
    SUMMARY_REGEN_INTERVAL,
)
from ai_related.review_analysis.review_ml.authenticity_detector import predict_authenticity
from ai_related.review_analysis.review_ml.mismatch_detector import predict_mismatch
from ai_related.review_analysis.review_ml.sentiment_detector import predict_sentiment


def _fetch_submission_notes(contract_id: str) -> List[str]:
    """Notes the freelancer attached to their submissions - the closest thing the
    schema has to a description of what was actually delivered, which is what makes
    a generated review question specific to this project rather than generic."""
    try:
        rows = get_db().execute_query(
            """SELECT note, revision_note
               FROM contract_submission
               WHERE contract_id = :cid
               ORDER BY submitted_at DESC
               LIMIT 5""",
            {"cid": contract_id},
        )
        notes: List[str] = []
        for r in rows or []:
            for value in (r.get("note"), r.get("revision_note")):
                if value and value.strip():
                    notes.append(value.strip())
        return notes
    except Exception as e:
        logger("REVIEW_PIPELINE", f"Could not load submission notes for {contract_id}: {e}", level="WARNING")
        return []


async def run_post_completion_pipeline(contract_id: str) -> None:
    """
    Steps 2–4. Runs in background when contract is marked complete.
    Creates the pending review shell and computes all performance pre-scores.
    The client has NOT reviewed yet at this point.
    """
    try:
        logger("REVIEW_PIPELINE", f"Starting post-completion pipeline for contract {contract_id}", level="INFO")

        existing_review = ReviewFunctions.get_review_by_contract_id(contract_id)
        if existing_review:
            logger(
                "REVIEW_PIPELINE",
                f"Contract {contract_id} already has a review ({existing_review['id']}), skipping duplicate pipeline run",
                level="WARNING",
            )
            return

        db = get_db()

        rows = db.execute_query(
            """SELECT
                 c.contract_id, c.freelancer_id, c.client_id, c.contract_title,
                 c.end_date, c.original_end_date, c.actual_completion_date,
                 jp.job_title, jp.job_description, jp.project_category,
                 jr.role_title
               FROM contract c
               JOIN job_post jp ON jp.job_post_id = c.job_post_id
               JOIN job_role jr ON jr.job_role_id = c.job_role_id
               WHERE c.contract_id = :cid""",
            {"cid": contract_id},
        )
        if not rows:
            logger("REVIEW_PIPELINE", f"Contract {contract_id} not found, pipeline aborted", level="ERROR")
            return

        contract = rows[0]

        # contract.freelancer_id/client_id are already the profile ids the review
        # tables key on, so no resolution step is needed here. Only the DM-based
        # responsiveness scoring below needs the underlying users.user_id.
        freelancer_id = str(contract["freelancer_id"])
        client_id     = str(contract["client_id"])

        freelancer_user_id = user_id_for_freelancer(freelancer_id)
        if not freelancer_user_id:
            logger("REVIEW_PIPELINE", f"Freelancer {freelancer_id} has no user account, pipeline aborted", level="ERROR")
            return

        # Step 2: Category comes from job_post.project_category, which
        # JobPostFunctions.infer_project_category already computed and persisted at
        # post creation (and re-computes on update). The review pipeline used to
        # re-derive it with a second, order-dependent classifier over a taxonomy that
        # didn't even match ("copywriting" vs "copy_writing", no "marketing"), so
        # freelancer_trust_scores.category peer groups didn't correspond to the
        # categories jobs are browsed by. One source of truth.
        category = contract.get("project_category") or "general"

        # Step 2: Create pending review shell
        review = ReviewFunctions.create_pending_review(
            contract_id=contract_id,
            reviewer_id=client_id,
            freelancer_id=freelancer_id,
            inferred_category=category,
        )
        review_id = review["id"]

        # Step 3: Generate + save the project-specific question. Falls back to the
        # ai_review_prompts table and then a hardcoded string, so a Groq outage
        # degrades the question rather than breaking contract completion.
        question = await generate_targeted_question(
            job_title=contract.get("job_title") or "",
            role_title=contract.get("role_title") or "",
            job_description=contract.get("job_description") or "",
            contract_title=contract.get("contract_title") or "",
            role_skills=ReviewFunctions.get_suggested_skill_tags(contract_id),
            category=category,
            submission_notes=_fetch_submission_notes(contract_id),
        )
        ReviewFunctions.save_ai_question(review_id, question)

        # Step 4: Compute all objective performance scores. on_time is measured
        # against original_end_date so a deadline extension granted during dispute
        # arbitration cannot retroactively turn a late delivery into an on-time one.
        on_time_score                       = compute_on_time_score(
            contract.get("original_end_date") or contract.get("end_date"),
            contract.get("actual_completion_date"),
        )
        revision_count, revision_rate_score = compute_revision_scores(contract_id)
        responsiveness_score                = compute_responsiveness_score(contract_id, freelancer_user_id)  # dm sender_id is a user_id

        # Step 4: Persist performance scores; communication fields are placeholders until Step 6
        ReviewFunctions.save_performance_scores(
            contract_id=contract_id,
            freelancer_id=freelancer_id,
            on_time_score=on_time_score,
            revision_count=revision_count,
            revision_rate_score=revision_rate_score,
            responsiveness_score=responsiveness_score,
            communication_sentiment_score=None,   # filled in Step 6
            conflict_score=None,                  # filled in Step 6
            communication_summary=None,           # filled in Step 6
        )

        logger("REVIEW_PIPELINE", f"Post-completion pipeline done | contract={contract_id} | category={category}", level="INFO")

    except Exception as e:
        logger("REVIEW_PIPELINE", f"Pipeline failed for contract {contract_id}: {str(e)}", level="ERROR")


async def run_post_review_pipeline(review_id: str, is_retry: bool = False) -> None:
    """
    Steps 6–9. Runs in background after client submits their review.
    Runs AI analysis → publishes or flags → recalculates trust score.

    is_retry is set by the reconcile sweep when re-running a review whose analysis
    was interrupted or unavailable. It suppresses the hold-back notifications only:
    the reviewer has already been told their review is under review, and repeating
    that every sweep cycle for the duration of an outage would be spam. A retry that
    succeeds still sends the publish notifications, because that is news.
    """
    try:
        logger("REVIEW_PIPELINE", f"Starting post-review pipeline for review {review_id}", level="INFO")
        db = get_db()

        review = ReviewFunctions.get_review_detail(review_id)
        if not review:
            logger("REVIEW_PIPELINE", f"Review {review_id} not found, pipeline aborted", level="ERROR")
            return

        freelancer_id   = review["freelancer_id"]
        written         = review.get("written_content") or {}
        overall_comment = written.get("overall_comment", "")
        client_answer   = written.get("client_answer", "")
        ratings         = review.get("ratings", [])

        if not ratings:
            logger("REVIEW_PIPELINE", f"No ratings found for review {review_id}, pipeline aborted", level="WARNING")
            return

        avg_stars = round(sum(float(r["score"]) for r in ratings) / len(ratings), 2)

        # Extract client's explicit communication star rating (1–5) from review_ratings
        communication_star_rating = next(
            (float(r["score"]) for r in ratings if r.get("category") == "communication"),
            None,
        )

        # Fetch pre-computed performance scores for bias cross-reference + responsiveness blend
        perf_rows = db.fetch_data(
            "freelancer_performance_scores",
            conditions=[("contract_id", "=", review["contract_id"])],
            limit=1,
        )
        perf = dict(perf_rows[0]) if perf_rows else {}
        performance_summary = {
            "on_time":        perf.get("on_time_score"),
            "revision_rate":  perf.get("revision_rate_score"),
            "responsiveness": perf.get("responsiveness_score"),
        }
        # Left as None when there is no message history - blend_communication_score
        # renormalizes rather than crediting an unmeasured contract with 0.8.
        _resp = perf.get("responsiveness_score")
        responsiveness_score = float(_resp) if _resp is not None else None

        freelancer_name = "Unknown"
        freelancer_rows = db.execute_query(
            "SELECT full_name FROM freelancer WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )
        if freelancer_rows:
            freelancer_name = freelancer_rows[0].get("full_name", "Unknown")

        # Notifications address users, not profiles - resolve both parties once.
        freelancer_user_id = user_id_for_freelancer(freelancer_id)
        reviewer_user_id   = user_id_for_client(review["reviewer_id"])

        dm_thread = DMFunctions.get_thread_by_contract_id(review["contract_id"])
        if dm_thread:
            messages, _, _ = DMFunctions.get_messages(dm_thread["thread_id"], limit=1000)
            message_thread = (
                "\n".join([f"[{m['sender_id']}]: {m['message_text']}" for m in messages])
                if messages
                else ""
            )
        else:
            message_thread = ""

        # Step 6: Single LLM call for full review analysis. ai_question is passed so
        # the model can judge answer_groundedness - without it, it would be rating
        # an answer against a question it never saw.
        analysis_result = await analyze_review_full(
            overall_comment=overall_comment,
            client_answer=client_answer,
            avg_star_rating=avg_stars,
            freelancer_name=freelancer_name,
            performance_score_summary=performance_summary,
            message_thread=message_thread,
            communication_star_rating=communication_star_rating,
            ai_question=written.get("ai_question") or "",
        )

        # Step 6b: Trained models (review_ml/) as independent signals alongside the
        # LLM - authenticity, text-rating disagreement, and sentiment. Kept as an
        # ensemble rather than a replacement so the pipeline still works if the Groq
        # API is rate-limited or down.
        #
        # Everything from here to overall_pass lives in review_decision.py, shared
        # with client_review_pipeline.py. Those two used to hold identical copies of
        # this logic, and keeping them in step cost real work every time it changed.
        # The I/O around it stays per-pipeline, since the tables, field names and
        # trust formulas genuinely differ.
        review_text_for_ml, review_text_full = split_ml_inputs(overall_comment, client_answer)

        ml_authenticity = predict_authenticity(review_text_for_ml)
        ml_sentiment = predict_sentiment(review_text_for_ml)
        ml_mismatch = predict_mismatch(review_text_full, avg_stars)

        authenticity_score = blend_authenticity(
            llm_authenticity_score=analysis_result["authenticity_score"],
            ml_authenticity=ml_authenticity,
            answer_groundedness=analysis_result.get("answer_groundedness"),
            answer_text=client_answer,
        )

        is_flagged_fake, sentiment_mismatch, flag_reasons = resolve_flags(
            llm_analysis=analysis_result,
            ml_authenticity=ml_authenticity,
            ml_mismatch=ml_mismatch,
            avg_stars=avg_stars,
            base_flag_reasons=analysis_result["flag_reasons"],
        )

        # Component 1 (Cardiff) replaces the LLM's sentiment guess.
        sentiment_score = ml_sentiment["sentiment_score"]
        sentiment_label = ml_sentiment["sentiment_label"]

        is_flagged_coerced = analysis_result["is_flagged_coerced"]

        overall_pass = compute_overall_pass(
            llm_analysis=analysis_result,
            authenticity_score=authenticity_score,
            is_flagged_fake=is_flagged_fake,
            is_flagged_coerced=is_flagged_coerced,
            sentiment_mismatch=sentiment_mismatch,
            avg_stars=avg_stars,
            sentiment_label=sentiment_label,
        )

        # Capture this judgment for future in-domain training data. Every model here
        # is trained on Amazon product reviews; this is real freelance-review text
        # with an LLM judgment attached, which is the raw material for replacing that
        # corpus. See judgment_log.py. Non-fatal by construction.
        log_pipeline_judgment(
            review_id=review_id,
            review_kind="freelancer_review",
            review_text=review_text_for_ml,
            answer_text=(client_answer or "").strip(),
            ratings=[{"category": r.get("category"), "score": r.get("score")} for r in ratings],
            avg_stars=avg_stars,
            llm_analysis=analysis_result,
            ml_authenticity=ml_authenticity,
            ml_sentiment=ml_sentiment,
            ml_mismatch=ml_mismatch,
            outcome={
                "overall_pass": overall_pass,
                "authenticity_score": authenticity_score,
                "is_flagged_fake": is_flagged_fake,
                "is_flagged_coerced": is_flagged_coerced,
                "sentiment_mismatch": sentiment_mismatch,
                "flag_reasons": flag_reasons,
            },
        )

        # Step 6: Persist AI analysis
        ReviewFunctions.save_ai_analysis(
            review_id=review_id,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            sentiment_mismatch=sentiment_mismatch,
            # P(text and rating disagree), 0-1, from the disagreement classifier.
            # Replaced the old mismatch_severity column, which held a 0-4 star gap
            # from a regressor that was measurably biased by rating level - see the
            # migration note in the DATABASE repo's alter_table.sql.
            disagreement_probability=ml_mismatch["disagreement_probability"],
            authenticity_score=authenticity_score,
            is_flagged_fake=is_flagged_fake,
            is_flagged_coerced=is_flagged_coerced,
            flag_reasons=flag_reasons,
            overall_pass=overall_pass,
        )

        # Step 6.5: Update communication fields in performance scores.
        # Blended here (not inside analyze_review_full) so it uses the ML sentiment
        # score above as its "sentiment" input, instead of asking the LLM for a second,
        # separately-computed sentiment guess just for this blend.
        client_star_normalized = (
            max(0.0, min(1.0, (communication_star_rating - 1) / 4.0))
            if communication_star_rating is not None
            else None
        )
        # responsiveness_score is deliberately NOT passed: it is already its own
        # 10% component of calculate_trust_score, and including it here counted it
        # twice. See blend_communication_score.
        communication_sentiment_score = blend_communication_score(
            ai_quality_score=analysis_result["communication_quality_score"],
            client_star_normalized=client_star_normalized,
            sentiment_score=sentiment_score,
        )
        conflict_score = 1.0 if is_flagged_coerced else 0.0
        communication_summary = analysis_result.get("communication_summary", "")

        ReviewFunctions.update_performance_scores(
            contract_id=review["contract_id"],
            communication_sentiment_score=communication_sentiment_score,
            conflict_score=conflict_score,
            communication_summary=communication_summary,
        )

        # Step 7: Publish or flag
        if overall_pass:
            ReviewFunctions.publish_review(review_id)
            try:
                await NotificationFunctions.notify(
                    recipient_user_id=freelancer_user_id,
                    notif_type="review_published",
                    title="New review received",
                    body=f"You received a new review with an average rating of {avg_stars} stars.",
                    data={"contract_id": review["contract_id"], "review_id": review_id},
                )
                await NotificationFunctions.notify(
                    recipient_user_id=reviewer_user_id,
                    notif_type="review_publish_confirmed",
                    title="Your review was published",
                    body=f"Your review for {freelancer_name} is now live.",
                    data={"contract_id": review["contract_id"], "review_id": review_id},
                )
            except Exception as notif_err:
                logger("REVIEW_PIPELINE", f"Publish notification failed (non-fatal): {notif_err}", level="WARNING")
        else:
            # suppressed = high-confidence bad review (authenticity very low, multiple
            # signals agree). flagged = didn't pass, but for a softer reason (is_flagged_fake
            # or is_flagged_coerced alone, bias, or the 5-star/negative-sentiment mismatch
            # rule) - still held for admin review, not written off as almost-certainly
            # fake/abusive.
            #
            # An unavailable analysis is never suppressed: we learned nothing about this
            # review, which is not the same as having judged it bad. It goes to the admin
            # queue with its reason, and the reviewer is told it is pending, not rejected.
            suppress = (
                not analysis_result.get("analysis_unavailable")
                and authenticity_score < 0.3
            )
            ReviewFunctions.flag_review(review_id, suppress=suppress)
            logger("REVIEW_PIPELINE", f"Review {review_id} not published (pass={overall_pass}, suppressed={suppress})", level="WARNING")
            try:
                if is_retry:
                    logger("REVIEW_PIPELINE", f"Retry still not passing for {review_id}, hold-back notification suppressed", level="INFO")
                elif suppress:
                    await NotificationFunctions.notify(
                        recipient_user_id=reviewer_user_id,
                        notif_type="review_suppressed",
                        title="Your review was not published",
                        body=f"Your review for {freelancer_name} didn't pass our automated review checks and will not be published.",
                        data={"contract_id": review["contract_id"], "review_id": review_id},
                    )
                else:
                    await NotificationFunctions.notify(
                        recipient_user_id=reviewer_user_id,
                        notif_type="review_flagged",
                        title="Your review is under review",
                        body=f"Your review for {freelancer_name} is being held for manual review before publishing. We'll notify you once it's resolved.",
                        data={"contract_id": review["contract_id"], "review_id": review_id},
                    )
            except Exception as notif_err:
                logger("REVIEW_PIPELINE", f"Hold-back notification failed (non-fatal): {notif_err}", level="WARNING")
            return  # Do not recalculate trust score for unpublished reviews

        # Step 8-9: Recalculate trust score + red flag check
        overall_score = await recalculate_and_persist_trust_score(freelancer_id, review.get("inferred_category"))

        logger("REVIEW_PIPELINE", f"Post-review pipeline done | review={review_id} | trust_score={overall_score}", level="INFO")

    except Exception as e:
        logger("REVIEW_PIPELINE", f"Post-review pipeline failed for review {review_id}: {str(e)}", level="ERROR")


async def recalculate_and_persist_trust_score(freelancer_id: str, category: Optional[str]) -> float:
    """
    Recomputes and upserts a freelancer's trust score from every sub-score
    aggregated across their FULL contract/review history (see
    calculate_aggregate_performance/calculate_ai_trust_components), then
    checks for a red-flag-worthy score drop. Shared by run_post_review_pipeline
    (after a new review publishes) and the admin override-publish endpoint
    (after a held-back review is manually approved) so both paths compute
    the trust score the same way instead of duplicating this logic.

    Async because the AI review summary (below) needs an LLM call - it only
    actually fires every SUMMARY_REGEN_INTERVAL reviews, not on every publish.
    """
    db = get_db()

    weighted_avg, total_reviews = calculate_weighted_review_avg(freelancer_id)
    aggregate_perf = calculate_aggregate_performance(freelancer_id)
    ai_trust = calculate_ai_trust_components(freelancer_id)

    overall_score = calculate_trust_score(
        weighted_review_avg=weighted_avg,
        on_time_score=aggregate_perf["on_time_score"],
        revision_rate_score=aggregate_perf["revision_rate_score"],
        responsiveness_score=aggregate_perf["responsiveness_score"],
        communication_sentiment=aggregate_perf["communication_sentiment_score"],
        authenticity_confidence=ai_trust["authenticity_confidence"],
        consistency_score=ai_trust["consistency_score"],
        # None when nothing was comparable (no telemetry, or no mappable rating
        # category yet - `timeliness` is still optional on the review form). Dropped
        # with its weight redistributed rather than credited.
        record_consistency=ai_trust.get("record_consistency"),
        coerced_ratio=aggregate_perf["coerced_ratio"],
        # Conduct as a REVIEWER, not reputation as a freelancer: the share of client
        # reviews this freelancer wrote that are materially harsher than that client's
        # record. Symmetric with unfair_review_ratio on the client side.
        unfair_review_ratio=calculate_freelancer_review_fairness(freelancer_id),
        total_reviews=total_reviews,
    )

    display_star_rows = db.execute_query(
        """
        SELECT AVG(rr.score) as avg_score
        FROM review_ratings rr
        JOIN reviews r ON r.id = rr.review_id
        WHERE r.freelancer_id = :fid AND r.status = 'published'
        """,
        {"fid": freelancer_id},
    )
    display_star_avg = (
        round(float(display_star_rows[0]["avg_score"]), 2)
        if display_star_rows and display_star_rows[0]["avg_score"] is not None
        else None
    )

    # Regenerate the profile-level AI summary only every SUMMARY_REGEN_INTERVAL
    # reviews (3, 8, 13...), not on every single publish - it barely changes
    # between consecutive reviews and each regen costs an LLM call.
    ai_review_summary = None
    if (
        total_reviews >= MIN_REVIEWS_FOR_SUMMARY
        and (total_reviews - MIN_REVIEWS_FOR_SUMMARY) % SUMMARY_REGEN_INTERVAL == 0
    ):
        freelancer_name = "Unknown"
        name_rows = db.execute_query(
            "SELECT full_name FROM freelancer WHERE freelancer_id = :fid", {"fid": freelancer_id}
        )
        if name_rows:
            freelancer_name = name_rows[0].get("full_name", "Unknown")
        ai_review_summary = await generate_freelancer_review_summary(freelancer_id, freelancer_name)

    # Percentile against category peers, excluding this freelancer's own row.
    # Previously this ran before the upsert, so on every recalculation after the
    # first the freelancer was compared against their own stale score - and it was
    # NULL for whoever was first in a category. Excluding self by id makes it
    # order-independent and correct on the first review too.
    category_rank_pct = None
    if category:
        rank_rows = db.execute_query(
            """SELECT ROUND(
                 100.0 * SUM(CASE WHEN overall_score < :score THEN 1 ELSE 0 END)
                 / NULLIF(COUNT(*), 0),
               2) AS rank_pct
               FROM freelancer_trust_scores
               WHERE category = :cat AND freelancer_id <> :fid""",
            {"score": overall_score, "cat": category, "fid": freelancer_id},
        )
        if rank_rows and rank_rows[0]["rank_pct"] is not None:
            category_rank_pct = float(rank_rows[0]["rank_pct"])

    ReviewFunctions.upsert_trust_score(
        freelancer_id=freelancer_id,
        overall_score=overall_score,
        weighted_review_avg=weighted_avg,
        effective_review_avg=round(shrink_toward_prior(weighted_avg, total_reviews), 3),
        display_star_avg=display_star_avg,
        revision_rate_score=aggregate_perf["revision_rate_score"],
        responsiveness_score=aggregate_perf["responsiveness_score"],
        communication_sentiment=aggregate_perf["communication_sentiment_score"],
        total_reviews=total_reviews,
        category=category,
        category_rank_pct=category_rank_pct,
        on_time_score=aggregate_perf["on_time_score"],
        authenticity_confidence=ai_trust["authenticity_confidence"],
        consistency_score=ai_trust["consistency_score"],
        ai_review_summary=ai_review_summary,
    )

    ReviewFunctions.check_and_create_red_flag(freelancer_id, overall_score)

    return overall_score