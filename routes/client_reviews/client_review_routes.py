import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_current_user_is_contract_party
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from routes.client_reviews.client_review_functions import ClientReviewFunctions
from ai_related.review_analysis.client_review_pipeline import (
    run_client_review_post_completion_pipeline,
    run_client_review_post_submission_pipeline,
)

client_review_router = APIRouter(prefix="/client-reviews", tags=["Client Reviews"])

_REQUIRED_CATEGORIES = {"communication", "clarity_of_requirements", "responsiveness", "professionalism"}


# INTERNAL HELPER: import and call this from contract_routes.py

async def trigger_client_review_pipeline_on_completion(
    contract_id: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Queue the post-completion client-review pipeline as a background task.
    Call this from contract_routes when a contract status transitions to 'completed',
    alongside trigger_review_pipeline_on_completion (the freelancer-side counterpart)."""
    background_tasks.add_task(run_client_review_post_completion_pipeline, contract_id)
    logger("CLIENT_REVIEW", f"Post-completion client-review pipeline queued for contract {contract_id}", level="INFO")


@client_review_router.get("/contract/{contract_id}")
async def get_client_review_for_contract(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Returns the pending client-review shell for a completed contract - the review
    a freelancer writes about the client they just worked for. Only the freelancer
    party of the contract may load this."""
    try:
        db = get_db()
        contract_rows = db.fetch_data("contract", conditions=[("contract_id", "=", contract_id)], limit=1)
        if not contract_rows:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        contract = dict(contract_rows[0])
        assert_current_user_is_contract_party(current_user, contract)

        review = ClientReviewFunctions.get_client_review_by_contract_id(contract_id)
        if not review:
            return ResponseSchema.error(
                f"Client review for contract {contract_id} is not ready yet - it may still be processing.",
                404,
            )

        detail = ClientReviewFunctions.get_review_detail(review["id"])
        logger("CLIENT_REVIEW", f"Fetched client-review form for contract {contract_id}", "GET /client-reviews/contract/{contract_id}", "INFO")
        return ResponseSchema.success(detail, 200)
    except HTTPException as e:
        logger("CLIENT_REVIEW", f"HTTP {e.status_code}: {e.detail}", "GET /client-reviews/contract/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "GET /client-reviews/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@client_review_router.post("/{client_review_id}/submit")
async def submit_client_review(
    client_review_id: str,
    payload: dict,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Freelancer submits their completed review of the client. Triggers AI
    analysis pipeline in background (review_ml models only - see
    client_review_pipeline.py).

    Request body:
    {
      "ratings": [
        {"category": "communication",           "score": 4.5},
        {"category": "clarity_of_requirements",  "score": 4.0},
        {"category": "responsiveness",           "score": 5.0},
        {"category": "professionalism",          "score": 5.0}
      ],
      "freelancer_answer": "Requirements were clear from the start.",
      "overall_comment":   "Great client, would work with again."
    }
    """
    try:
        review = ClientReviewFunctions.get_client_review_by_id(client_review_id)
        if not review:
            return ResponseSchema.error(f"Client review {client_review_id} not found", 404)
        if review["status"] != "pending":
            return ResponseSchema.error(f"Review has already been {review['status']}.", 400)
        if review["reviewer_id"] != str(current_user.user_id):
            return ResponseSchema.error("Only the freelancer who owns this contract can submit this review.", 403)

        ratings = payload.get("ratings", [])
        provided_categories = {r["category"] for r in ratings}
        missing = _REQUIRED_CATEGORIES - provided_categories
        if missing:
            return ResponseSchema.error(f"Missing rating categories: {missing}", 400)

        for r in ratings:
            if not (1.0 <= float(r["score"]) <= 5.0):
                return ResponseSchema.error(f"Score for '{r['category']}' must be between 1.0 and 5.0.", 400)

        overall_comment = payload.get("overall_comment", "").strip()
        freelancer_answer = payload.get("freelancer_answer", "").strip()

        if not overall_comment:
            return ResponseSchema.error("overall_comment is required.", 400)

        ClientReviewFunctions.save_freelancer_review(
            client_review_id=client_review_id,
            ratings=ratings,
            freelancer_answer=freelancer_answer,
            overall_comment=overall_comment,
        )

        background_tasks.add_task(run_client_review_post_submission_pipeline, client_review_id)

        logger("CLIENT_REVIEW", f"Client review {client_review_id} submitted. AI pipeline queued.", "POST /client-reviews/{client_review_id}/submit", "INFO")
        return ResponseSchema.success(
            {"message": "Review submitted successfully. It will be published shortly after AI verification."},
            201,
        )
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "POST /client-reviews/{client_review_id}/submit", "ERROR")
        return ResponseSchema.error(str(e), 500)


@client_review_router.get("/client/{client_id}")
async def get_reviews_for_client(
    client_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """All published reviews for a client, written by freelancers they worked with.
    Used on the public client profile page."""
    try:
        db = get_db()
        cl_rows = db.fetch_data("client", conditions=[("client_id", "=", client_id)], limit=1)
        if not cl_rows:
            return ResponseSchema.error(f"Client {client_id} not found", 404)
        client_user_id = str(cl_rows[0]["user_id"])

        reviews = ClientReviewFunctions.get_reviews_by_client_id(client_user_id)
        logger("CLIENT_REVIEW", f"Fetched {len(reviews)} reviews for client {client_id}", "GET /client-reviews/client/{client_id}", "INFO")
        return ResponseSchema.success(reviews, 200)
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "GET /client-reviews/client/{client_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@client_review_router.get("/trust-score/{client_id}")
async def get_client_trust_score_route(
    client_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Live AI-computed trust score for a client including component breakdown."""
    try:
        db = get_db()
        cl_rows = db.fetch_data("client", conditions=[("client_id", "=", client_id)], limit=1)
        if not cl_rows:
            return ResponseSchema.error(f"Client {client_id} not found", 404)
        client_user_id = str(cl_rows[0]["user_id"])

        trust_score = ClientReviewFunctions.get_client_trust_score(client_user_id)
        if not trust_score:
            return ResponseSchema.success({
                "client_id": client_user_id,
                "trust_score": 0,
                "total_reviews_received": 0,
                "message": "No reviews yet.",
            }, 200)

        logger("CLIENT_REVIEW", f"Fetched trust score for client {client_id}", "GET /client-reviews/trust-score/{client_id}", "INFO")
        return ResponseSchema.success(trust_score, 200)
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "GET /client-reviews/trust-score/{client_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@client_review_router.get("/red-flags/{client_id}")
async def get_client_red_flags(
    client_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Unresolved red flag alerts for a client. Intended for admin dashboards."""
    try:
        db = get_db()
        cl_rows = db.fetch_data("client", conditions=[("client_id", "=", client_id)], limit=1)
        if not cl_rows:
            return ResponseSchema.error(f"Client {client_id} not found", 404)
        client_user_id = str(cl_rows[0]["user_id"])

        alerts = ClientReviewFunctions.get_red_flags(client_user_id)
        logger("CLIENT_REVIEW", f"Fetched {len(alerts)} red flags for client {client_id}", "GET /client-reviews/red-flags/{client_id}", "INFO")
        return ResponseSchema.success(alerts, 200)
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "GET /client-reviews/red-flags/{client_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# GET /client-reviews/{client_review_id}  ← wildcard last so specific routes above match first
@client_review_router.get("/{client_review_id}")
async def get_client_review(
    client_review_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Full client-review detail including ratings, written content, and AI analysis."""
    try:
        review = ClientReviewFunctions.get_review_detail(client_review_id)
        if not review:
            return ResponseSchema.error(f"Client review {client_review_id} not found", 404)
        logger("CLIENT_REVIEW", f"Fetched client review {client_review_id}", "GET /client-reviews/{client_review_id}", "INFO")
        return ResponseSchema.success(review, 200)
    except Exception as e:
        logger("CLIENT_REVIEW", f"Error: {str(e)}", "GET /client-reviews/{client_review_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)
