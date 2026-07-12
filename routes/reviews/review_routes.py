import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typing import Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import (
    assert_current_user_is_contract_party,
    get_freelancer_profile_for_user,
)
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from routes.reviews.review_functions import ReviewFunctions
from ai_related.review_analysis.review_pipeline import (
    run_post_completion_pipeline,
    run_post_review_pipeline,
)
from routes.admin.admin_moderation import (
    scan_harmful_text_with_ml_fallback,
    insert_harmful_text_queue_entry,
    ML_SCAN_TIMEOUT_BLOCKING_SECONDS,
)

review_router = APIRouter(prefix="/reviews", tags=["Reviews"])

_REVIEW_LABEL_NAMES = {
    "toxic": "toxicity",
    "toxicity": "toxicity",
    "obscene": "obscenity",
    "threat": "threats",
    "insult": "insults",
    "identity_hate": "identity-based hate speech",
}


async def _reject_review_text_if_harmful(
    review_id: str,
    reviewer_id: str,
    client_answer: str,
    overall_comment: str,
) -> Optional[Dict]:
    """client_answer and overall_comment both end up on a published review of a specific
    named freelancer - unlike a DM or contract clause between two already-matched parties,
    the freelancer being reviewed has no say in whether this goes live, so this is sync
    reject-outright rather than the save-then-block pattern run_post_review_pipeline (fake/
    bias detection, a different concern) already uses. Fails open on a scan error - same
    blast-radius reasoning as bio/DM/contract text (one review, not a shared table)."""
    combined = " ".join(t for t in (client_answer, overall_comment) if t and t.strip())
    if not combined.strip():
        return None
    try:
        harm_result = await scan_harmful_text_with_ml_fallback(combined, timeout=ML_SCAN_TIMEOUT_BLOCKING_SECONDS)
    except Exception as e:
        logger("REVIEW", f"Review-text scan errored, failing open (allowing save): {e}", level="WARNING")
        return None
    if not harm_result["is_flagged"]:
        return None
    detected_labels = harm_result.get("detected_labels", [])
    labels = [_REVIEW_LABEL_NAMES.get(l, l) for l in detected_labels]
    logger("REVIEW", f"Blocked review submission, labels={detected_labels}", level="WARNING")
    insert_harmful_text_queue_entry("review", review_id, reviewer_id, combined, harm_result)
    return {
        "message": f"This review couldn't be submitted. It was flagged for {', '.join(labels) or 'a policy violation'}.",
        "detected_labels": detected_labels,
    }


# INTERNAL HELPER: import and call this from contract_routes.py

async def trigger_review_pipeline_on_completion(
    contract_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Queue the post-completion review pipeline as a background task.

    Call this from contract_routes when a contract status transitions to 'completed'.
    """
    background_tasks.add_task(run_post_completion_pipeline, contract_id)
    logger("REVIEW", f"Post-completion pipeline queued for contract {contract_id}", level="INFO")


@review_router.get("/contract/{contract_id}")
async def get_review_for_contract(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Returns the pending review record for a completed contract.
    Called by Flutter to load the review form after contract completion.
    Includes the AI-generated targeted question and pre-suggested skill tags.
    Only accessible by the client party of the contract.
    """
    try:
        db = get_db()
        contract_rows = db.fetch_data("contract", conditions=[("contract_id", "=", contract_id)], limit=1)
        if not contract_rows:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        contract = dict(contract_rows[0])
        assert_current_user_is_contract_party(current_user, contract)

        review = ReviewFunctions.get_review_by_contract_id(contract_id)
        if not review:
            return ResponseSchema.error(
                f"Review for contract {contract_id} is not ready yet - it may still be processing.",
                404,
            )

        detail = ReviewFunctions.get_review_detail(review["id"])
        detail["suggested_skill_tags"] = ReviewFunctions.get_suggested_skill_tags(contract_id)

        logger("REVIEW", f"Fetched review form for contract {contract_id}", "GET /reviews/contract/{contract_id}", "INFO")
        return ResponseSchema.success(detail, 200)
    except HTTPException as e:
        logger("REVIEW", f"HTTP {e.status_code}: {e.detail}", "GET /reviews/contract/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# POST /reviews/{review_id}/submit

@review_router.post("/{review_id}/submit")
async def submit_review(
    review_id: str,
    payload: dict,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Client submits their completed review. Triggers AI analysis pipeline in background.

    Request body:
    {
      "ratings": [
        {"category": "communication",   "score": 4.5},
        {"category": "quality",         "score": 5.0},
        {"category": "professionalism", "score": 5.0},
        {"category": "value_for_money", "score": 4.0}
      ],
      "client_answer":    "Yes, the code was very clean and well-documented.",
      "overall_comment":  "Great experience working with this freelancer.",
      "extra_skill_tags": ["Clean Code", "Fast Delivery"]
    }
    """
    try:
        review = ReviewFunctions.get_review_by_id(review_id)
        if not review:
            return ResponseSchema.error(f"Review {review_id} not found", 404)
        if review["status"] != "pending":
            return ResponseSchema.error(f"Review has already been {review['status']}.", 400)
        if review["reviewer_id"] != str(current_user.user_id):
            return ResponseSchema.error("Only the client who owns this contract can submit a review.", 403)

        # Validate all 4 rating categories are present
        ratings = payload.get("ratings", [])
        required_categories = {"communication", "quality", "professionalism", "value_for_money"}
        provided_categories = {r["category"] for r in ratings}
        missing = required_categories - provided_categories
        if missing:
            return ResponseSchema.error(f"Missing rating categories: {missing}", 400)

        # Validate score range
        for r in ratings:
            if not (1.0 <= float(r["score"]) <= 5.0):
                return ResponseSchema.error(f"Score for '{r['category']}' must be between 1.0 and 5.0.", 400)

        overall_comment = payload.get("overall_comment", "").strip()
        client_answer   = payload.get("client_answer", "").strip()
        extra_tags      = payload.get("extra_skill_tags", [])

        if not overall_comment:
            return ResponseSchema.error("overall_comment is required.", 400)

        rejection = await _reject_review_text_if_harmful(review_id, review["reviewer_id"], client_answer, overall_comment)
        if rejection:
            return ResponseSchema.error(rejection["message"], 400, extra={"detected_labels": rejection["detected_labels"]})

        # Fetch pre-suggested tags from job_role_skill
        suggested_tags = ReviewFunctions.get_suggested_skill_tags(review["contract_id"])

        # Step 5: Save the client review
        ReviewFunctions.save_client_review(
            review_id=review_id,
            ratings=ratings,
            client_answer=client_answer,
            overall_comment=overall_comment,
            confirmed_skill_tags=suggested_tags,
            extra_skill_tags=extra_tags,
        )

        # Steps 6-9: Queue AI analysis + publish pipeline in background
        background_tasks.add_task(run_post_review_pipeline, review_id)

        logger("REVIEW", f"Review {review_id} submitted. AI pipeline queued.", "POST /reviews/{review_id}/submit", "INFO")
        return ResponseSchema.success(
            {"message": "Review submitted successfully. It will be published shortly after AI verification."},
            201,
        )
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "POST /reviews/{review_id}/submit", "ERROR")
        return ResponseSchema.error(str(e), 500)

# GET /reviews/freelancer/{freelancer_id}

@review_router.get("/freelancer/{freelancer_id}")
async def get_freelancer_reviews(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """All published reviews for a freelancer. Used on the public freelancer profile page."""
    try:
        db = get_db()
        fl_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", freelancer_id)], limit=1)
        if not fl_rows:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        freelancer_user_id = str(fl_rows[0]["user_id"])

        reviews = ReviewFunctions.get_reviews_by_freelancer_id(freelancer_user_id)
        logger("REVIEW", f"Fetched {len(reviews)} reviews for freelancer {freelancer_id}", "GET /reviews/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(reviews, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# GET /reviews/trust-score/{freelancer_id}

@review_router.get("/trust-score/{freelancer_id}")
async def get_trust_score(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Live AI-computed trust score for a freelancer including component breakdown and category rank."""
    try:
        db = get_db()
        fl_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", freelancer_id)], limit=1)
        if not fl_rows:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        freelancer_user_id = str(fl_rows[0]["user_id"])

        trust_score = ReviewFunctions.get_trust_score(freelancer_user_id)
        if not trust_score:
            return ResponseSchema.success({
                "freelancer_id": freelancer_user_id,
                "overall_score": 0,
                "total_reviews": 0,
                "message": "No reviews yet.",
            }, 200)

        logger("REVIEW", f"Fetched trust score for freelancer {freelancer_id}", "GET /reviews/trust-score/{freelancer_id}", "INFO")
        return ResponseSchema.success(trust_score, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/trust-score/{freelancer_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# GET /reviews/red-flags/{freelancer_id}

@review_router.get("/red-flags/{freelancer_id}")
async def get_red_flags(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Unresolved red flag alerts for a freelancer. Intended for admin dashboards."""
    try:
        db = get_db()
        fl_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", freelancer_id)], limit=1)
        if not fl_rows:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        freelancer_user_id = str(fl_rows[0]["user_id"])

        alerts = ReviewFunctions.get_red_flags(freelancer_user_id)
        logger("REVIEW", f"Fetched {len(alerts)} red flags for freelancer {freelancer_id}", "GET /reviews/red-flags/{freelancer_id}", "INFO")
        return ResponseSchema.success(alerts, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/red-flags/{freelancer_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)

# GET /reviews/{review_id}  ← wildcard last so specific routes above match first
@review_router.get("/{review_id}")
async def get_review(
    review_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Full review detail including ratings, written content, skill tags, and AI analysis results."""
    try:
        review = ReviewFunctions.get_review_detail(review_id)
        if not review:
            return ResponseSchema.error(f"Review {review_id} not found", 404)
        logger("REVIEW", f"Fetched review {review_id}", "GET /reviews/{review_id}", "INFO")
        return ResponseSchema.success(review, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/{review_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)
