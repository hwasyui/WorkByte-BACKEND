import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.client_reviews.client_review_functions import ClientReviewFunctions
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.review_analysis.client_review_ai_functions import (
    get_client_targeted_question,
    compute_client_responsiveness_score,
    compute_client_dispute_rate_score,
    calculate_weighted_client_review_avg,
    calculate_client_ai_trust_components,
    calculate_client_trust_score,
    analyze_client_review_full,
)
from ai_related.review_analysis.review_ml.authenticity_detector import predict_authenticity
from ai_related.review_analysis.review_ml.mismatch_detector import predict_mismatch
from ai_related.review_analysis.review_ml.sentiment_detector import predict_sentiment


async def run_client_review_post_completion_pipeline(contract_id: str) -> None:
    """
    Freelancer-reviews-client counterpart to run_post_completion_pipeline.
    Runs in background when a contract is marked complete - creates the
    pending client-review shell with its AI question. The freelancer has
    not reviewed the client yet at this point.
    """
    try:
        logger("CLIENT_REVIEW_PIPELINE", f"Starting client-review post-completion pipeline for contract {contract_id}", level="INFO")

        existing = ClientReviewFunctions.get_client_review_by_contract_id(contract_id)
        if existing:
            logger(
                "CLIENT_REVIEW_PIPELINE",
                f"Contract {contract_id} already has a client review ({existing['id']}), skipping duplicate pipeline run",
                level="WARNING",
            )
            return

        db = get_db()
        rows = db.execute_query(
            "SELECT contract_id, freelancer_id, client_id FROM contract WHERE contract_id = :cid",
            {"cid": contract_id},
        )
        if not rows:
            logger("CLIENT_REVIEW_PIPELINE", f"Contract {contract_id} not found, pipeline aborted", level="ERROR")
            return
        contract = rows[0]

        freelancer_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", str(contract["freelancer_id"]))], limit=1)
        client_rows = db.fetch_data("client", conditions=[("client_id", "=", str(contract["client_id"]))], limit=1)
        if not freelancer_rows or not client_rows:
            logger("CLIENT_REVIEW_PIPELINE", "Could not resolve user IDs, pipeline aborted", level="ERROR")
            return

        freelancer_user_id = str(freelancer_rows[0]["user_id"])
        client_user_id = str(client_rows[0]["user_id"])

        review = ClientReviewFunctions.create_pending_client_review(
            contract_id=contract_id,
            reviewer_id=freelancer_user_id,
            client_id=client_user_id,
        )
        review_id = review["id"]

        question = get_client_targeted_question()
        ClientReviewFunctions.save_ai_question(review_id, question)

        logger("CLIENT_REVIEW_PIPELINE", f"Client-review post-completion pipeline done | contract={contract_id}", level="INFO")

    except Exception as e:
        logger("CLIENT_REVIEW_PIPELINE", f"Pipeline failed for contract {contract_id}: {str(e)}", level="ERROR")


async def run_client_review_post_submission_pipeline(client_review_id: str) -> None:
    """
    Freelancer-reviews-client counterpart to run_post_review_pipeline. Runs
    in background after the freelancer submits their review of the client.
    Runs the same LLM analysis pass as the freelancer side (analyze_client_review_full)
    alongside the three trained review_ml models, then publishes/flags and
    recalculates the client's trust score.
    """
    try:
        logger("CLIENT_REVIEW_PIPELINE", f"Starting client-review post-submission pipeline for review {client_review_id}", level="INFO")

        review = ClientReviewFunctions.get_review_detail(client_review_id)
        if not review:
            logger("CLIENT_REVIEW_PIPELINE", f"Client review {client_review_id} not found, pipeline aborted", level="ERROR")
            return

        client_id = review["client_id"]
        client_name = "the client"
        client_rows = get_db().execute_query(
            "SELECT full_name FROM client WHERE user_id = :uid", {"uid": client_id}
        )
        if client_rows and client_rows[0].get("full_name"):
            client_name = client_rows[0]["full_name"]

        written = review.get("written_content") or {}
        overall_comment = written.get("overall_comment", "")
        freelancer_answer = written.get("freelancer_answer", "")
        ratings = review.get("ratings", [])

        if not ratings:
            logger("CLIENT_REVIEW_PIPELINE", f"No ratings found for client review {client_review_id}, pipeline aborted", level="WARNING")
            return

        avg_stars = round(sum(float(r["score"]) for r in ratings) / len(ratings), 2)
        review_text = f"{overall_comment} {freelancer_answer}".strip()

        # Extract freelancer's explicit communication star rating (1-5) from client_review_ratings
        communication_star_rating = next(
            (float(r["score"]) for r in ratings if r.get("category") == "communication"),
            None,
        )

        responsiveness_score = compute_client_responsiveness_score(client_id)
        dispute_fairness_score = compute_client_dispute_rate_score(client_id)
        performance_summary = {
            "responsiveness": responsiveness_score,
            "dispute_fairness": dispute_fairness_score,
        }

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

        # Single LLM call for full review analysis (symmetric to review_pipeline.py's Step 6)
        analysis_result = await analyze_client_review_full(
            overall_comment=overall_comment,
            freelancer_answer=freelancer_answer,
            avg_star_rating=avg_stars,
            client_name=client_name,
            performance_score_summary=performance_summary,
            message_thread=message_thread,
            responsiveness_score=responsiveness_score,
            communication_star_rating=communication_star_rating,
        )

        # Three trained classical models as independent signals alongside the LLM,
        # same ensemble reasoning as the freelancer side.
        ml_authenticity = predict_authenticity(review_text)
        ml_mismatch = predict_mismatch(review_text, avg_stars)
        ml_sentiment = predict_sentiment(review_text)

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

        # Own trained classifier replaces the LLM's sentiment guess, same as the freelancer side.
        sentiment_score = ml_sentiment["sentiment_score"]
        sentiment_label = ml_sentiment["sentiment_label"]

        # is_flagged_fake is a veto, not just a contributing signal - see the symmetric
        # note in review_pipeline.py.
        overall_pass = (
            authenticity_score >= 0.5
            and not is_flagged_fake
            and not (
                sentiment_mismatch
                and avg_stars == 5.0
                and sentiment_label == "negative"
            )
        )

        ClientReviewFunctions.save_ai_analysis(
            client_review_id=client_review_id,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            sentiment_mismatch=sentiment_mismatch,
            mismatch_severity=ml_mismatch["mismatch_severity"],
            authenticity_score=authenticity_score,
            is_flagged_fake=is_flagged_fake,
            is_flagged_coerced=analysis_result["is_flagged_coerced"],
            flag_reasons=flag_reasons,
            overall_pass=overall_pass,
        )

        if overall_pass:
            ClientReviewFunctions.publish_review(client_review_id)
            try:
                await NotificationFunctions.notify(
                    recipient_user_id=client_id,
                    notif_type="review_published",
                    title="New Review Received ⭐",
                    body=f"You received a new review with an average rating of {avg_stars}★.",
                    data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                )
                await NotificationFunctions.notify(
                    recipient_user_id=review["reviewer_id"],
                    notif_type="review_publish_confirmed",
                    title="Your Review Was Published",
                    body=f"Your review for {client_name} is now live.",
                    data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                )
            except Exception as notif_err:
                logger("CLIENT_REVIEW_PIPELINE", f"Publish notification failed (non-fatal): {notif_err}", level="WARNING")
        else:
            # suppressed = high-confidence bad review (authenticity very low). flagged =
            # didn't pass for a softer reason (is_flagged_fake alone or the mismatch rule) -
            # still held for admin review, not written off as almost-certainly fake.
            suppress = authenticity_score < 0.3
            ClientReviewFunctions.flag_review(client_review_id, suppress=suppress)
            logger("CLIENT_REVIEW_PIPELINE", f"Client review {client_review_id} not published (pass={overall_pass}, suppressed={suppress})", level="WARNING")
            try:
                if suppress:
                    await NotificationFunctions.notify(
                        recipient_user_id=review["reviewer_id"],
                        notif_type="review_suppressed",
                        title="Your Review Couldn't Be Published",
                        body=f"Your review for {client_name} didn't pass our automated review checks and will not be published.",
                        data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                    )
                else:
                    await NotificationFunctions.notify(
                        recipient_user_id=review["reviewer_id"],
                        notif_type="review_flagged",
                        title="Your Review Is Under Review",
                        body=f"Your review for {client_name} is being held for manual review before publishing. We'll notify you once it's resolved.",
                        data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                    )
            except Exception as notif_err:
                logger("CLIENT_REVIEW_PIPELINE", f"Hold-back notification failed (non-fatal): {notif_err}", level="WARNING")
            return

        recalculate_and_persist_client_trust_score(client_id)

        logger("CLIENT_REVIEW_PIPELINE", f"Client-review post-submission pipeline done | review={client_review_id}", level="INFO")

    except Exception as e:
        logger("CLIENT_REVIEW_PIPELINE", f"Post-submission pipeline failed for review {client_review_id}: {str(e)}", level="ERROR")


def recalculate_and_persist_client_trust_score(client_id: str) -> float:
    """
    Recomputes and upserts a client's trust score from every sub-score,
    aggregated live across their full contract/review history (see
    compute_client_responsiveness_score/compute_client_dispute_rate_score -
    both query ALL contracts directly rather than a per-contract snapshot,
    so there's no equivalent to the "single contract dominates" bug that had
    to be fixed on the freelancer side). Shared by the post-submission
    pipeline and the admin override-publish endpoint.
    """
    weighted_avg, total_reviews = calculate_weighted_client_review_avg(client_id)
    responsiveness_score = compute_client_responsiveness_score(client_id)
    dispute_fairness_score = compute_client_dispute_rate_score(client_id)
    ai_trust = calculate_client_ai_trust_components(client_id)

    trust_score = calculate_client_trust_score(
        weighted_review_avg=weighted_avg,
        responsiveness_score=responsiveness_score,
        dispute_fairness_score=dispute_fairness_score,
        authenticity_confidence=ai_trust["authenticity_confidence"],
        consistency_score=ai_trust["consistency_score"],
        communication_sentiment=ai_trust["communication_sentiment"],
    )

    ClientReviewFunctions.upsert_client_trust_score(
        client_id=client_id,
        trust_score=trust_score,
        weighted_review_avg_received=weighted_avg,
        responsiveness_score=responsiveness_score,
        communication_sentiment=ai_trust["communication_sentiment"],
        authenticity_confidence=ai_trust["authenticity_confidence"],
        consistency_score=ai_trust["consistency_score"],
        dispute_fairness_score=dispute_fairness_score,
        total_reviews_received=total_reviews,
    )

    ClientReviewFunctions.check_and_create_red_flag(client_id, trust_score)

    return trust_score
