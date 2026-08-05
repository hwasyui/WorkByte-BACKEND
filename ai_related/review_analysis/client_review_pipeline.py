import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from functions.profile_ids import user_id_for_client, user_id_for_freelancer
from routes.client_reviews.client_review_functions import ClientReviewFunctions
from routes.reviews.review_functions import ReviewFunctions
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.review_analysis.client_review_ai_functions import (
    generate_client_targeted_question,
    compute_client_responsiveness_score,
    compute_client_dispute_rate_score,
    measure_client_responsiveness,
    measure_client_dispute_fairness,
    calculate_weighted_client_review_avg,
    calculate_client_ai_trust_components,
    calculate_client_coerced_ratio,
    calculate_client_trust_score,
    analyze_client_review_full,
    generate_client_review_summary,
)
from ai_related.review_analysis.judgment_log import log_pipeline_judgment
from ai_related.review_analysis.review_decision import (
    blend_authenticity,
    compute_overall_pass,
    resolve_flags,
    should_suppress,
    split_ml_inputs,
)
from ai_related.review_analysis.review_ai_functions import (
    MIN_REVIEWS_FOR_SUMMARY,
    SUMMARY_REGEN_INTERVAL,
    calculate_review_fairness,
    compute_revision_scores,
    shrink_toward_prior,
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
            """SELECT c.contract_id, c.freelancer_id, c.client_id, c.contract_title,
                      jp.job_title, jp.job_description,
                      jr.role_title
               FROM contract c
               JOIN job_post jp ON jp.job_post_id = c.job_post_id
               JOIN job_role jr ON jr.job_role_id = c.job_role_id
               WHERE c.contract_id = :cid""",
            {"cid": contract_id},
        )
        if not rows:
            logger("CLIENT_REVIEW_PIPELINE", f"Contract {contract_id} not found, pipeline aborted", level="ERROR")
            return
        contract = rows[0]

        # contract.freelancer_id/client_id are already the profile ids the
        # client_reviews table keys on - no resolution step needed.
        review = ClientReviewFunctions.create_pending_client_review(
            contract_id=contract_id,
            reviewer_id=str(contract["freelancer_id"]),
            client_id=str(contract["client_id"]),
        )
        review_id = review["id"]

        # Project-specific question about the client, falling back to the rotating
        # static list if generation fails or the result fails validation.
        revision_count, _ = compute_revision_scores(contract_id)
        question = await generate_client_targeted_question(
            job_title=contract.get("job_title") or "",
            role_title=contract.get("role_title") or "",
            job_description=contract.get("job_description") or "",
            contract_title=contract.get("contract_title") or "",
            role_skills=ReviewFunctions.get_suggested_skill_tags(contract_id),
            revision_count=revision_count,
        )
        ClientReviewFunctions.save_ai_question(review_id, question)

        logger("CLIENT_REVIEW_PIPELINE", f"Client-review post-completion pipeline done | contract={contract_id}", level="INFO")

    except Exception as e:
        logger("CLIENT_REVIEW_PIPELINE", f"Pipeline failed for contract {contract_id}: {str(e)}", level="ERROR")


async def run_client_review_post_submission_pipeline(client_review_id: str, is_retry: bool = False) -> None:
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
            "SELECT full_name FROM client WHERE client_id = :cid", {"cid": client_id}
        )
        if client_rows and client_rows[0].get("full_name"):
            client_name = client_rows[0]["full_name"]

        # Notifications address users, not profiles - resolve both parties once.
        client_user_id   = user_id_for_client(client_id)
        reviewer_user_id = user_id_for_freelancer(review["reviewer_id"])

        written = review.get("written_content") or {}
        overall_comment = written.get("overall_comment", "")
        freelancer_answer = written.get("freelancer_answer", "")
        ratings = review.get("ratings", [])

        if not ratings:
            logger("CLIENT_REVIEW_PIPELINE", f"No ratings found for client review {client_review_id}, pipeline aborted", level="WARNING")
            return

        avg_stars = round(sum(float(r["score"]) for r in ratings) / len(ratings), 2)
        review_text = (overall_comment or "").strip()

        # Extract freelancer's explicit communication star rating (1-5) from client_review_ratings
        communication_star_rating = next(
            (float(r["score"]) for r in ratings if r.get("category") == "communication"),
            None,
        )

        # Measured values, so an unmeasured client reaches the prompt as "not
        # recorded" (_fmt_metric) instead of as the neutral 0.8/1.0 the scoring
        # entry points substitute. The prompt explicitly tells the model that
        # "not recorded" is missing data and not evidence against the reviewer;
        # feeding it the fallback instead states the client WAS measured at 0.8
        # responsiveness and a spotless dispute record, and the model has been
        # observed citing exactly those figures back in its flag reasons. The
        # trust score keeps the fallbacks - see recalculate_and_persist_client_trust_score.
        performance_summary = {
            "responsiveness": measure_client_responsiveness(client_id),
            "dispute_fairness": measure_client_dispute_fairness(client_id),
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
            communication_star_rating=communication_star_rating,
            ai_question=written.get("ai_question") or "",
        )

        # Trained models as independent signals alongside the LLM. Everything from
        # here to overall_pass is shared with review_pipeline.py via
        # review_decision.py - the two pipelines used to carry identical copies.
        review_text_for_ml, review_text_full = split_ml_inputs(review_text, freelancer_answer)

        ml_authenticity = predict_authenticity(review_text_for_ml)
        ml_sentiment = predict_sentiment(review_text_for_ml)
        ml_mismatch = predict_mismatch(review_text_full, avg_stars)

        # ml_authenticity no longer feeds this - it is kept above because it still
        # sets an advisory flag reason and is logged for the in-domain retrain. See
        # blend_authenticity for the audit that removed it.
        authenticity_score = blend_authenticity(
            llm_authenticity_score=analysis_result["authenticity_score"],
            answer_groundedness=analysis_result.get("answer_groundedness"),
            answer_text=freelancer_answer,
            analysis_unavailable=bool(analysis_result.get("analysis_unavailable")),
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

        # In-domain training data capture, same reasoning as the freelancer side.
        log_pipeline_judgment(
            review_id=client_review_id,
            review_kind="client_review",
            review_text=review_text,
            answer_text=(freelancer_answer or "").strip(),
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

        ClientReviewFunctions.save_ai_analysis(
            client_review_id=client_review_id,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            sentiment_mismatch=sentiment_mismatch,
            # 0-1 P(disagree) from the classifier, not the old 0-4 star gap.
            # See review_pipeline.py for why, and for the pending column rename.
            disagreement_probability=ml_mismatch["disagreement_probability"],
            authenticity_score=authenticity_score,
            is_flagged_fake=is_flagged_fake,
            is_flagged_coerced=is_flagged_coerced,
            flag_reasons=flag_reasons,
            overall_pass=overall_pass,
        )

        if overall_pass:
            ClientReviewFunctions.publish_review(client_review_id)
            try:
                await NotificationFunctions.notify(
                    recipient_user_id=client_user_id,
                    notif_type="review_published",
                    title="New review received",
                    body=f"You received a new review with an average rating of {avg_stars} stars.",
                    data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                )
                await NotificationFunctions.notify(
                    recipient_user_id=reviewer_user_id,
                    notif_type="review_publish_confirmed",
                    title="Your review was published",
                    body=f"Your review for {client_name} is now live.",
                    data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                )
            except Exception as notif_err:
                logger("CLIENT_REVIEW_PIPELINE", f"Publish notification failed (non-fatal): {notif_err}", level="WARNING")
        else:
            # suppressed = high-confidence bad review (authenticity very low). flagged =
            # didn't pass for a softer reason (is_flagged_fake or is_flagged_coerced alone,
            # or the mismatch rule) - still held for admin review, not written off as
            # almost-certainly fake.
            suppress = should_suppress(analysis_result, authenticity_score)
            ClientReviewFunctions.flag_review(client_review_id, suppress=suppress)
            logger("CLIENT_REVIEW_PIPELINE", f"Client review {client_review_id} not published (pass={overall_pass}, suppressed={suppress})", level="WARNING")
            try:
                if is_retry:
                    logger("CLIENT_REVIEW_PIPELINE", f"Retry still not passing for {client_review_id}, hold-back notification suppressed", level="INFO")
                elif suppress:
                    await NotificationFunctions.notify(
                        recipient_user_id=reviewer_user_id,
                        notif_type="review_suppressed",
                        title="Your review was not published",
                        body=f"Your review for {client_name} didn't pass our automated review checks and will not be published.",
                        data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                    )
                else:
                    await NotificationFunctions.notify(
                        recipient_user_id=reviewer_user_id,
                        notif_type="review_flagged",
                        title="Your review is under review",
                        body=f"Your review for {client_name} is being held for manual review before publishing. We'll notify you once it's resolved.",
                        data={"contract_id": review["contract_id"], "client_review_id": client_review_id},
                    )
            except Exception as notif_err:
                logger("CLIENT_REVIEW_PIPELINE", f"Hold-back notification failed (non-fatal): {notif_err}", level="WARNING")
            return

        await recalculate_and_persist_client_trust_score(client_id)

        logger("CLIENT_REVIEW_PIPELINE", f"Client-review post-submission pipeline done | review={client_review_id}", level="INFO")

    except Exception as e:
        logger("CLIENT_REVIEW_PIPELINE", f"Post-submission pipeline failed for review {client_review_id}: {str(e)}", level="ERROR")


async def recalculate_and_persist_client_trust_score(client_id: str) -> float:
    """
    Recomputes and upserts a client's trust score from every sub-score,
    aggregated live across their full contract/review history (see
    compute_client_responsiveness_score/compute_client_dispute_rate_score -
    both query ALL contracts directly rather than a per-contract snapshot,
    so there's no equivalent to the "single contract dominates" bug that had
    to be fixed on the freelancer side). Shared by the post-submission
    pipeline and the admin override-publish endpoint.

    Async because the AI review summary (below) needs an LLM call - it only
    actually fires every SUMMARY_REGEN_INTERVAL reviews, not on every publish.
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
        total_reviews=total_reviews,
        coerced_ratio=calculate_client_coerced_ratio(client_id),
        # Conduct as a REVIEWER, not reputation as a client: the share of reviews
        # this client wrote that are materially harsher than the objective contract
        # record. 0.0 when nothing was comparable, so no penalty on absent evidence.
        unfair_review_ratio=calculate_review_fairness(client_id),
        # Component 4: this client's received ratings against their own objective
        # record. None when nothing was comparable, and dropped with its weight
        # redistributed rather than credited.
        record_consistency=ai_trust.get("record_consistency"),
    )

    # Regenerate the profile-level AI summary only every SUMMARY_REGEN_INTERVAL
    # reviews (3, 8, 13...), not on every single publish - same cadence and
    # reasoning as the freelancer side.
    ai_review_summary = None
    if (
        total_reviews >= MIN_REVIEWS_FOR_SUMMARY
        and (total_reviews - MIN_REVIEWS_FOR_SUMMARY) % SUMMARY_REGEN_INTERVAL == 0
    ):
        client_name = "the client"
        name_rows = get_db().execute_query(
            "SELECT full_name FROM client WHERE client_id = :cid", {"cid": client_id}
        )
        if name_rows and name_rows[0].get("full_name"):
            client_name = name_rows[0]["full_name"]
        ai_review_summary = await generate_client_review_summary(client_id, client_name)

    ClientReviewFunctions.upsert_client_trust_score(
        client_id=client_id,
        trust_score=trust_score,
        weighted_review_avg_received=weighted_avg,
        effective_review_avg_received=round(shrink_toward_prior(weighted_avg, total_reviews), 3),
        responsiveness_score=responsiveness_score,
        communication_sentiment=ai_trust["communication_sentiment"],
        authenticity_confidence=ai_trust["authenticity_confidence"],
        consistency_score=ai_trust["consistency_score"],
        dispute_fairness_score=dispute_fairness_score,
        total_reviews_received=total_reviews,
        ai_review_summary=ai_review_summary,
    )

    ClientReviewFunctions.check_and_create_red_flag(client_id, trust_score)

    return trust_score
