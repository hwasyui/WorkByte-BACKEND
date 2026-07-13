import os
import sys
import asyncio
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.reviews.review_functions import ReviewFunctions
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.review_analysis.review_ai_functions import (
    get_targeted_question,
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
    MIN_REVIEWS_FOR_SUMMARY,
    SUMMARY_REGEN_INTERVAL,
)
from ai_related.review_analysis.review_ml.authenticity_detector import predict_authenticity
from ai_related.review_analysis.review_ml.mismatch_detector import predict_mismatch
from ai_related.review_analysis.review_ml.sentiment_detector import predict_sentiment


def infer_project_category(role_title: str, job_title: str, job_description: str) -> str:
    text = " ".join(filter(None, [role_title, job_title, job_description])).lower()
    category_map = [
        ("mobile", "mobile_dev"),
        ("android", "mobile_dev"),
        ("ios", "mobile_dev"),
        ("flutter", "mobile_dev"),
        ("react native", "mobile_dev"),
        ("swift", "mobile_dev"),
        ("kotlin", "mobile_dev"),
        ("backend", "backend_dev"),
        ("api", "backend_dev"),
        ("server", "backend_dev"),
        ("database", "backend_dev"),
        ("web", "web_dev"),
        ("frontend", "web_dev"),
        ("ui/ux", "ui_ux_design"),
        ("ui ux", "ui_ux_design"),
        ("user interface", "ui_ux_design"),
        ("graphic", "graphic_design"),
        ("design", "graphic_design"),
        ("copy", "copywriting"),
        ("writing", "copywriting"),
        ("data", "data_analytics"),
        ("analytics", "data_analytics"),
        ("video", "video_editing"),
        ("motion", "video_editing"),
    ]
    for keyword, category in category_map:
        if keyword in text:
            return category
    return "general"


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
                 c.contract_id, c.freelancer_id, c.client_id,
                 c.end_date, c.actual_completion_date,
                 jp.job_title, jp.job_description,
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

        freelancer_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", str(contract["freelancer_id"]))], limit=1)
        client_rows     = db.fetch_data("client",     conditions=[("client_id",     "=", str(contract["client_id"]))],     limit=1)

        if not freelancer_rows or not client_rows:
            logger("REVIEW_PIPELINE", "Could not resolve user IDs, pipeline aborted", level="ERROR")
            return

        freelancer_user_id = str(freelancer_rows[0]["user_id"])
        client_user_id     = str(client_rows[0]["user_id"])

        # Step 2: Classify category deterministically
        category = infer_project_category(
            role_title=contract.get("role_title", "") or "",
            job_title=contract.get("job_title", "") or "",
            job_description=contract.get("job_description", "") or "",
        )

        # Step 2: Create pending review shell
        review = ReviewFunctions.create_pending_review(
            contract_id=contract_id,
            reviewer_id=client_user_id,
            freelancer_id=freelancer_user_id,
            inferred_category=category,
        )
        review_id = review["id"]

        # Step 3: Fetch + save AI-targeted question
        question = get_targeted_question(category)
        ReviewFunctions.save_ai_question(review_id, question)

        # Step 4: Compute all objective performance scores
        on_time_score                       = compute_on_time_score(contract.get("end_date"), contract.get("actual_completion_date"))
        revision_count, revision_rate_score = compute_revision_scores(contract_id)
        responsiveness_score                = compute_responsiveness_score(contract_id, freelancer_user_id)

        # Step 4: Persist performance scores; communication fields are placeholders until Step 6
        ReviewFunctions.save_performance_scores(
            contract_id=contract_id,
            freelancer_id=freelancer_user_id,
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


async def run_post_review_pipeline(review_id: str) -> None:
    """
    Steps 6–9. Runs in background after client submits their review.
    Runs AI analysis → publishes or flags → recalculates trust score.
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
        responsiveness_score = float(perf.get("responsiveness_score") or 0.8)

        freelancer_name = "Unknown"
        freelancer_rows = db.execute_query(
            "SELECT full_name FROM freelancer WHERE user_id = :uid",
            {"uid": freelancer_id},
        )
        if freelancer_rows:
            freelancer_name = freelancer_rows[0].get("full_name", "Unknown")

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

        # Step 6: Single LLM call for full review analysis
        analysis_result = await analyze_review_full(
            overall_comment=overall_comment,
            client_answer=client_answer,
            avg_star_rating=avg_stars,
            freelancer_name=freelancer_name,
            performance_score_summary=performance_summary,
            message_thread=message_thread,
            communication_star_rating=communication_star_rating,
        )

        # Step 6b: Three trained classical models (review_ml/) as independent
        # signals alongside the LLM - authenticity, sentiment-rating mismatch,
        # and sentiment. Kept as an ensemble rather than a replacement so the
        # pipeline still works if the Groq API is rate-limited or down.
        review_text_for_ml = f"{overall_comment} {client_answer}".strip()
        ml_authenticity = predict_authenticity(review_text_for_ml)
        ml_mismatch = predict_mismatch(review_text_for_ml, avg_stars)
        ml_sentiment = predict_sentiment(review_text_for_ml)

        flag_reasons = list(analysis_result["flag_reasons"])

        ml_authenticity_score = 1.0 - ml_authenticity["fake_probability"]
        authenticity_score = round((analysis_result["authenticity_score"] + ml_authenticity_score) / 2, 3)
        is_flagged_fake = analysis_result["is_flagged_fake"] or ml_authenticity["is_likely_fake"]
        if ml_authenticity["is_likely_fake"] and not analysis_result["is_flagged_fake"]:
            flag_reasons.append(
                f"Statistical model flagged generic/templated language "
                f"(fake_probability={ml_authenticity['fake_probability']})"
            )

        sentiment_mismatch = analysis_result["sentiment_mismatch"] or ml_mismatch["is_mismatched"]
        if ml_mismatch["is_mismatched"] and not analysis_result["sentiment_mismatch"]:
            flag_reasons.append(
                f"Rating-text mismatch detected (text implies ~{ml_mismatch['predicted_rating']}★, "
                f"actual rating {avg_stars}★)"
            )

        # Own trained classifier replaces the LLM's sentiment guess.
        sentiment_score = ml_sentiment["sentiment_score"]
        sentiment_label = ml_sentiment["sentiment_label"]

        is_flagged_coerced = analysis_result["is_flagged_coerced"]

        # is_flagged_fake and is_flagged_coerced are vetoes, not just contributing
        # signals: authenticity_score is a blend of the LLM's own judgment and the ML
        # classifier's, so a review either model specifically caught (e.g. "extremely
        # short", "generic language", "reviewer indicates lack of choice in rating")
        # could still average out above the 0.5 threshold and auto-publish. Since each
        # flag already means at least one model concluded something is wrong, either
        # should hold the review back regardless of what the blended score says.
        overall_pass = (
            authenticity_score >= 0.5
            and not is_flagged_fake
            and not is_flagged_coerced
            and not (
                sentiment_mismatch
                and avg_stars == 5.0
                and sentiment_label == "negative"
            )
        )

        # Step 6: Persist AI analysis
        ReviewFunctions.save_ai_analysis(
            review_id=review_id,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            sentiment_mismatch=sentiment_mismatch,
            mismatch_severity=ml_mismatch["mismatch_severity"],
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
        communication_sentiment_score = blend_communication_score(
            ai_quality_score=analysis_result["communication_quality_score"],
            client_star_normalized=client_star_normalized,
            responsiveness_score=responsiveness_score,
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
                    recipient_user_id=freelancer_id,
                    notif_type="review_published",
                    title="New Review Received ⭐",
                    body=f"You received a new review with an average rating of {avg_stars}★.",
                    data={"contract_id": review["contract_id"], "review_id": review_id},
                )
                await NotificationFunctions.notify(
                    recipient_user_id=review["reviewer_id"],
                    notif_type="review_publish_confirmed",
                    title="Your Review Was Published",
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
            suppress = authenticity_score < 0.3
            ReviewFunctions.flag_review(review_id, suppress=suppress)
            logger("REVIEW_PIPELINE", f"Review {review_id} not published (pass={overall_pass}, suppressed={suppress})", level="WARNING")
            try:
                if suppress:
                    await NotificationFunctions.notify(
                        recipient_user_id=review["reviewer_id"],
                        notif_type="review_suppressed",
                        title="Your Review Couldn't Be Published",
                        body=f"Your review for {freelancer_name} didn't pass our automated review checks and will not be published.",
                        data={"contract_id": review["contract_id"], "review_id": review_id},
                    )
                else:
                    await NotificationFunctions.notify(
                        recipient_user_id=review["reviewer_id"],
                        notif_type="review_flagged",
                        title="Your Review Is Under Review",
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
        coerced_ratio=aggregate_perf["coerced_ratio"],
    )

    category_rank_pct = None
    if category:
        rank_rows = db.execute_query(
            """SELECT ROUND(
                 100.0 * SUM(CASE WHEN overall_score < :score THEN 1 ELSE 0 END) / COUNT(*),
               2) as rank_pct
               FROM freelancer_trust_scores WHERE category = :cat""",
            {"score": overall_score, "cat": category},
        )
        if rank_rows and rank_rows[0]["rank_pct"] is not None:
            category_rank_pct = float(rank_rows[0]["rank_pct"])

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
            "SELECT full_name FROM freelancer WHERE user_id = :uid", {"uid": freelancer_id}
        )
        if name_rows:
            freelancer_name = name_rows[0].get("full_name", "Unknown")
        ai_review_summary = await generate_freelancer_review_summary(freelancer_id, freelancer_name)

    ReviewFunctions.upsert_trust_score(
        freelancer_id=freelancer_id,
        overall_score=overall_score,
        weighted_review_avg=weighted_avg,
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