import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
from functions.review_views import public_review, public_reviews, public_trust_score, review_confidence
from routes.reviews.review_functions import ReviewFunctions
from ai_related.review_analysis.review_pipeline import (
    run_post_completion_pipeline,
    run_post_review_pipeline,
)

review_router = APIRouter(prefix="/reviews", tags=["Reviews"])

# `timeliness` is required. It is the counterpart to on_time_score - the most
# objective measurement the platform has, computed against original_end_date and
# deliberately immune to dispute-granted extensions - and the only rating category
# that maps onto contract telemetry at full weight in review_consistency.py. Without
# it, a review claiming great delivery on a job that shipped nine days late is
# undetectable, because none of the other four categories has a tight objective
# counterpart.
#
# BREAKING for any client that does not send it: submission returns 400 until the
# FRONTEND form ships the field. That was a deliberate call, not an oversight.
#
# Reviews submitted BEFORE this became required keep their four ratings. Nothing
# backfills them, and the comparator simply skips the dimension it cannot find, so
# they stay valid and their record-consistency check stays looser.
REQUIRED_RATING_CATEGORIES = {
    "communication",
    "quality",
    "professionalism",
    "value_for_money",
    "timeliness",
}

# Staging slot for a category the backend should accept and use before the form is
# ready to send it. Empty now that timeliness has been promoted; put a new category
# here first, then move it into REQUIRED once the frontend ships it.
OPTIONAL_RATING_CATEGORIES: set[str] = set()

# review_ratings.category is VARCHAR(50) with no enum and no whitelist, so a typo
# used to be stored silently and then averaged into avg_stars - which drives the
# review's rating, the trust score, and the mismatch check. uq_review_rating_category
# does not catch it, because a misspelling is a distinct category.
KNOWN_RATING_CATEGORIES = REQUIRED_RATING_CATEGORIES | OPTIONAL_RATING_CATEGORIES


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

        detail = public_review(ReviewFunctions.get_review_detail(review["id"]))
        detail["suggested_skill_tags"] = ReviewFunctions.get_suggested_skill_tags(contract_id)

        logger("REVIEW", f"Fetched review form for contract {contract_id}", "GET /reviews/contract/{contract_id}", "INFO")
        return ResponseSchema.success(detail, 200)
    except HTTPException as e:
        logger("REVIEW", f"HTTP {e.status_code}: {e.detail}", "GET /reviews/contract/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/contract/{contract_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch review. Please try again.", 500)


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
        {"category": "value_for_money", "score": 4.0},
        {"category": "timeliness",      "score": 4.0}
      ],
      "client_answer":    "Yes, the code was very clean and well-documented.",
      "overall_comment":  "Great experience working with this freelancer.",
      "extra_skill_tags": ["Clean Code", "Fast Delivery"]
    }

    All five categories are REQUIRED - `timeliness` included. It is the only rating
    that maps directly onto an objective measurement (on_time_score), which is what
    lets review_consistency.py check a delivery claim against the contract record.

    Any category outside REQUIRED | OPTIONAL is rejected with 400, so a typo cannot
    silently end up averaged into avg_stars.
    """
    try:
        review = ReviewFunctions.get_review_by_id(review_id)
        if not review:
            return ResponseSchema.error(f"Review {review_id} not found", 404)
        if review["status"] != "pending":
            return ResponseSchema.error(f"Review has already been {review['status']}.", 400)
        # reviews.reviewer_id is a client.client_id, so compare against the
        # caller's client profile rather than their user_id.
        if not current_user.client_id or review["reviewer_id"] != str(current_user.client_id):
            return ResponseSchema.error("Only the client who owns this contract can submit a review.", 403)

        ratings = payload.get("ratings", [])
        provided_categories = {r["category"] for r in ratings}
        missing = REQUIRED_RATING_CATEGORIES - provided_categories
        if missing:
            return ResponseSchema.error(f"Missing rating categories: {sorted(missing)}", 400)

        unknown = provided_categories - KNOWN_RATING_CATEGORIES
        if unknown:
            return ResponseSchema.error(
                f"Unknown rating categories: {sorted(unknown)}. "
                f"Allowed: {sorted(KNOWN_RATING_CATEGORIES)}",
                400,
            )

        # Validate score range
        for r in ratings:
            if not (1.0 <= float(r["score"]) <= 5.0):
                return ResponseSchema.error(f"Score for '{r['category']}' must be between 1.0 and 5.0.", 400)

        overall_comment = payload.get("overall_comment", "").strip()
        client_answer   = payload.get("client_answer", "").strip()
        extra_tags      = payload.get("extra_skill_tags", [])

        if not overall_comment:
            return ResponseSchema.error("Please write an overall comment.", 400)

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
        return ResponseSchema.error("Failed to submit review. Please try again.", 500)

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

        reviews = public_reviews(ReviewFunctions.get_reviews_by_freelancer_id(freelancer_id))
        logger("REVIEW", f"Fetched {len(reviews)} reviews for freelancer {freelancer_id}", "GET /reviews/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(reviews, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch freelancer reviews. Please try again.", 500)


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

        trust_score = public_trust_score(ReviewFunctions.get_trust_score(freelancer_id))
        distribution = ReviewFunctions.get_sentiment_distribution(freelancer_id)
        if not trust_score:
            return ResponseSchema.success({
                "freelancer_id": freelancer_id,
                "overall_score": 0,
                "total_reviews": 0,
                "confidence": review_confidence(0),
                "sentiment_distribution": distribution,
                "message": "No reviews yet.",
            }, 200)
        trust_score["sentiment_distribution"] = distribution

        logger("REVIEW", f"Fetched trust score for freelancer {freelancer_id}", "GET /reviews/trust-score/{freelancer_id}", "INFO")
        return ResponseSchema.success(trust_score, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/trust-score/{freelancer_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch trust score. Please try again.", 500)


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

        alerts = ReviewFunctions.get_red_flags(freelancer_id)
        logger("REVIEW", f"Fetched {len(alerts)} red flags for freelancer {freelancer_id}", "GET /reviews/red-flags/{freelancer_id}", "INFO")
        return ResponseSchema.success(alerts, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/red-flags/{freelancer_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch red flags. Please try again.", 500)

# GET /reviews/{review_id}  ← wildcard last so specific routes above match first
@review_router.get("/{review_id}")
async def get_review(
    review_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Full review detail including ratings, written content, skill tags, and AI analysis results."""
    try:
        review = public_review(ReviewFunctions.get_review_detail(review_id))
        if not review:
            return ResponseSchema.error(f"Review {review_id} not found", 404)
        logger("REVIEW", f"Fetched review {review_id}", "GET /reviews/{review_id}", "INFO")
        return ResponseSchema.success(review, 200)
    except Exception as e:
        logger("REVIEW", f"Error: {str(e)}", "GET /reviews/{review_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch review. Please try again.", 500)
