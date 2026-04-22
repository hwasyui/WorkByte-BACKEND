import os
import sys
import asyncio
import uuid
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.reviews.review_functions import ReviewFunctions
from ai_related.review_analysis.review_ai_functions import (
    classify_project_category,
    get_targeted_question,
    compute_on_time_score,
    compute_revision_scores,
    compute_responsiveness_score,
    analyze_message_thread,
    analyze_submitted_files,
    analyze_sentiment,
    check_authenticity,
    check_bias,
    calculate_trust_score,
    calculate_weighted_review_avg,
)


async def run_post_completion_pipeline(contract_id: str) -> None:
    """
    Steps 2–4. Runs in background when contract is marked complete.
    Creates the pending review shell and computes all performance pre-scores.
    The client has NOT reviewed yet at this point.
    """
    try:
        logger("REVIEW_PIPELINE", f"Starting post-completion pipeline for contract {contract_id}", level="INFO")
        db = get_db()

        # Fetch contract + job details in one query
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
            logger("REVIEW_PIPELINE", f"Contract {contract_id} not found — pipeline aborted", level="ERROR")
            return

        contract = rows[0]

        # Resolve user_ids from profile tables
        freelancer_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", str(contract["freelancer_id"]))], limit=1)
        client_rows     = db.fetch_data("client",     conditions=[("client_id",     "=", str(contract["client_id"]))],     limit=1)

        if not freelancer_rows or not client_rows:
            logger("REVIEW_PIPELINE", "Could not resolve user IDs — pipeline aborted", level="ERROR")
            return

        freelancer_user_id = str(freelancer_rows[0]["user_id"])
        client_user_id     = str(client_rows[0]["user_id"])

        # Step 2 — Classify category
        category = await classify_project_category(
            job_title=contract["job_title"],
            role_title=contract["role_title"] or "",
            job_description=contract["job_description"],
        )

        # Step 2 — Create pending review record
        review = ReviewFunctions.create_pending_review(
            contract_id=contract_id,
            reviewer_id=client_user_id,
            freelancer_id=freelancer_user_id,
            inferred_category=category,
        )
        review_id = review["id"]

        # Step 3 — Fetch + save AI-targeted question
        question = get_targeted_question(category)
        ReviewFunctions.save_ai_question(review_id, question)

        # Step 4 — Compute all performance scores
        on_time_score                        = compute_on_time_score(contract.get("end_date"), contract.get("actual_completion_date"))
        revision_count, revision_rate_score  = compute_revision_scores(contract_id)
        responsiveness_score                 = compute_responsiveness_score(contract_id, freelancer_user_id)

        # Message analysis + file analysis run in parallel
        message_analysis, file_analysis = await asyncio.gather(
            analyze_message_thread(contract_id),
            analyze_submitted_files(contract_id),
        )

        # Step 4 — Persist all performance scores
        ReviewFunctions.save_performance_scores(
            contract_id=contract_id,
            freelancer_id=freelancer_user_id,
            on_time_score=on_time_score,
            revision_count=revision_count,
            revision_rate_score=revision_rate_score,
            responsiveness_score=responsiveness_score,
            communication_sentiment_score=message_analysis["communication_sentiment_score"],
            conflict_score=message_analysis["conflict_score"],
            communication_summary=message_analysis["communication_summary"],
            work_quality_score=file_analysis["work_quality_score"],
            work_quality_notes=file_analysis["work_quality_notes"],
        )

        logger("REVIEW_PIPELINE", f"Post-completion pipeline done | contract={contract_id} | category={category}", level="INFO")

    except Exception as e:
        logger("REVIEW_PIPELINE", f"Pipeline failed for contract {contract_id}: {str(e)}", level="ERROR")


async def run_post_review_pipeline(review_id: str) -> None:
    """
    Steps 6–9. Runs in background after client submits their review.
    Runs 3 AI checks in parallel → publishes or flags → recalculates trust score.
    """
    try:
        logger("REVIEW_PIPELINE", f"Starting post-review pipeline for review {review_id}", level="INFO")
        db = get_db()

        review  = ReviewFunctions.get_review_detail(review_id)
        if not review:
            logger("REVIEW_PIPELINE", f"Review {review_id} not found — pipeline aborted", level="ERROR")
            return

        freelancer_id = review["freelancer_id"]
        written       = review.get("written_content") or {}
        overall_comment = written.get("overall_comment", "")
        client_answer   = written.get("client_answer", "")
        ratings         = review.get("ratings", [])

        if not ratings:
            logger("REVIEW_PIPELINE", f"No ratings found for review {review_id} — pipeline aborted", level="WARNING")
            return

        avg_stars = round(sum(float(r["score"]) for r in ratings) / len(ratings), 2)

        # Fetch performance scores for bias cross-reference
        perf_rows = db.fetch_data(
            "freelancer_performance_scores",
            conditions=[("contract_id", "=", review["contract_id"])],
            limit=1,
        )
        perf = dict(perf_rows[0]) if perf_rows else {}
        performance_summary = {
            "on_time":       perf.get("on_time_score"),
            "revision_rate": perf.get("revision_rate_score"),
            "responsiveness":perf.get("responsiveness_score"),
            "work_quality":  perf.get("work_quality_score"),
        }

        # Fetch freelancer display name for bias detection
        freelancer_name = "Unknown"
        user_rows = db.execute_query(
            """SELECT u.full_name FROM users u
               WHERE u.user_id = :uid""",
            {"uid": freelancer_id},
        )
        if user_rows:
            freelancer_name = user_rows[0].get("full_name", "Unknown")

        # Step 6 — Run 3 AI checks in parallel
        sentiment_result, authenticity_result, bias_result = await asyncio.gather(
            analyze_sentiment(overall_comment, client_answer, avg_stars),
            check_authenticity(overall_comment),
            check_bias(overall_comment, avg_stars, freelancer_name, performance_summary),
        )

        # Determine overall pass / fail
        overall_pass = (
            authenticity_result["authenticity_score"] >= 0.5
            and bias_result["bias_score"] <= 0.6
            and not (
                sentiment_result["sentiment_mismatch"]
                and avg_stars == 5.0
                and sentiment_result["sentiment_label"] == "negative"
            )
        )

        # Step 6 — Save AI analysis results
        ReviewFunctions.save_ai_analysis(
            review_id=review_id,
            sentiment_score=sentiment_result["sentiment_score"],
            sentiment_label=sentiment_result["sentiment_label"],
            sentiment_mismatch=sentiment_result["sentiment_mismatch"],
            authenticity_score=authenticity_result["authenticity_score"],
            is_flagged_fake=authenticity_result["is_flagged_fake"],
            is_flagged_coerced=authenticity_result["is_flagged_coerced"],
            flag_reasons=authenticity_result["flag_reasons"],
            bias_score=bias_result["bias_score"],
            bias_flags=bias_result["bias_flags"],
            overall_pass=overall_pass,
        )

        # Log bias if significant
        if bias_result["bias_score"] > 0.3:
            db.insert_data(
                table_name="bias_detection_log",
                data={
                    "id": str(uuid.uuid4()),
                    "freelancer_id": freelancer_id,
                    "review_id": review_id,
                    "detected_factors": bias_result["bias_flags"],
                    "score_before_adjustment": avg_stars,
                    "adjustment_applied": False,
                },
            )

        # Step 7 — Publish or flag
        if overall_pass:
            ReviewFunctions.publish_review(review_id)
        else:
            suppress = (
                authenticity_result["is_flagged_fake"]
                or authenticity_result["authenticity_score"] < 0.3
            )
            ReviewFunctions.flag_review(review_id, suppress=suppress)
            logger("REVIEW_PIPELINE", f"Review {review_id} not published (pass={overall_pass})", level="WARNING")
            return  # Do not update trust score for unpublished reviews

        # Step 8 — Recalculate trust score
        weighted_avg, total_reviews = calculate_weighted_review_avg(freelancer_id)
        overall_score = calculate_trust_score(
            weighted_review_avg=weighted_avg,
            work_quality_score=perf.get("work_quality_score"),
            revision_rate_score=float(perf.get("revision_rate_score") or 0.5),
            responsiveness_score=float(perf.get("responsiveness_score") or 0.8),
            communication_sentiment=perf.get("communication_sentiment_score"),
            conflict_score=perf.get("conflict_score"),
        )

        # Compute category rank percentile
        category_rank_pct = None
        category = review.get("inferred_category")
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

        ReviewFunctions.upsert_trust_score(
            freelancer_id=freelancer_id,
            overall_score=overall_score,
            weighted_review_avg=weighted_avg,
            work_quality_score=perf.get("work_quality_score"),
            revision_rate_score=float(perf.get("revision_rate_score") or 0.5),
            responsiveness_score=float(perf.get("responsiveness_score") or 0.8),
            communication_sentiment=perf.get("communication_sentiment_score"),
            total_reviews=total_reviews,
            category=category,
            category_rank_pct=category_rank_pct,
        )

        # Step 9 — Red flag check
        ReviewFunctions.check_and_create_red_flag(freelancer_id, overall_score)

        logger("REVIEW_PIPELINE", f"Post-review pipeline done | review={review_id} | trust_score={overall_score}", level="INFO")

    except Exception as e:
        logger("REVIEW_PIPELINE", f"Post-review pipeline failed for review {review_id}: {str(e)}", level="ERROR")