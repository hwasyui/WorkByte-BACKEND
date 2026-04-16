import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import RatingCreate, RatingUpdate, RatingResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import get_client_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.ratings.rating_functions import RatingFunctions
from routes.contracts.contract_functions import ContractFunctions

rating_router = APIRouter(prefix="/ratings", tags=["Ratings"])


@rating_router.get("", response_model=List[RatingResponse])
async def get_all_ratings(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all ratings - Authenticated users only - JSON response"""
    try:
        ratings = RatingFunctions.get_all_ratings(limit=limit)
        success_msg = f"Retrieved {len(ratings)} ratings" + (f" (limit: {limit})" if limit else "")
        logger("RATING", success_msg, "GET /ratings", "INFO")
        return ResponseSchema.success(ratings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch ratings: {str(e)}"
        logger("RATING", error_msg, "GET /ratings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.get("/{rating_id}", response_model=RatingResponse)
async def get_rating(rating_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single rating by ID - Authenticated users only - JSON response"""
    try:
        rating = RatingFunctions.get_rating_by_id(rating_id)
        if not rating:
            error_msg = f"Rating {rating_id} not found"
            logger("RATING", error_msg, "GET /ratings/{rating_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved rating {rating_id}"
        logger("RATING", success_msg, "GET /ratings/{rating_id}", "INFO")
        return ResponseSchema.success(rating, 200)
    except Exception as e:
        error_msg = f"Failed to fetch rating {rating_id}: {str(e)}"
        logger("RATING", error_msg, "GET /ratings/{rating_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.get("/freelancer/{freelancer_id}", response_model=List[RatingResponse])
async def get_ratings_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all ratings for a specific freelancer - Authenticated users only - JSON response"""
    try:
        ratings = RatingFunctions.get_ratings_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(ratings)} ratings for freelancer {freelancer_id}"
        logger("RATING", success_msg, "GET /ratings/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(ratings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch ratings for freelancer {freelancer_id}: {str(e)}"
        logger("RATING", error_msg, "GET /ratings/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.get("/client/{client_id}", response_model=List[RatingResponse])
async def get_ratings_by_client(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all ratings given by a specific client - Authenticated users only - JSON response"""
    try:
        ratings = RatingFunctions.get_ratings_by_client_id(client_id)
        success_msg = f"Retrieved {len(ratings)} ratings from client {client_id}"
        logger("RATING", success_msg, "GET /ratings/client/{client_id}", "INFO")
        return ResponseSchema.success(ratings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch ratings from client {client_id}: {str(e)}"
        logger("RATING", error_msg, "GET /ratings/client/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.post("", response_model=RatingResponse, status_code=201)
async def create_rating(rating: RatingCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new rating - only contract owner client can rate; contract must be complete/cancelled/disputed"""
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create ratings", 403)

        client = get_client_profile_for_user(current_user)
        contract = ContractFunctions.get_contract_by_id(rating.contract_id)
        if not contract:
            return ResponseSchema.error("Contract not found", 404)

        if str(contract["client_id"]) != str(client["client_id"]):
            return ResponseSchema.error("Client does not own this contract", 403)

        if str(contract["freelancer_id"]) != str(rating.freelancer_id):
            return ResponseSchema.error("Freelancer mismatch for contract", 400)

        if contract.get("status") not in ["completed", "cancelled", "disputed"]:
            return ResponseSchema.error("Contract must be completed/cancelled/disputed to rate", 400)

        new_rating = RatingFunctions.create_rating(
            contract_id=rating.contract_id,
            client_id=client["client_id"],
            freelancer_id=rating.freelancer_id,
            communication_score=rating.communication_score,
            result_quality_score=rating.result_quality_score,
            professionalism_score=rating.professionalism_score,
            timeline_compliance_score=rating.timeline_compliance_score,
            overall_rating=rating.overall_rating,
            review_text=rating.review_text
        )

        success_msg = f"Created rating {new_rating.get('rating_id')}"
        logger("RATING", success_msg, "POST /ratings", "INFO")
        return ResponseSchema.success(new_rating, 201)

    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("RATING", error_msg, "POST /ratings", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create rating: {str(e)}"
        logger("RATING", error_msg, "POST /ratings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.put("/{rating_id}", response_model=RatingResponse)
async def update_rating(rating_id: str, rating_update: RatingUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update rating information - only contract owner client can update once"""
    try:
        existing_rating = RatingFunctions.get_rating_by_id(rating_id)
        if not existing_rating:
            error_msg = f"Rating {rating_id} not found"
            logger("RATING", error_msg, "PUT /ratings/{rating_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)

        contract = ContractFunctions.get_contract_by_id(existing_rating["contract_id"])
        if not contract:
            return ResponseSchema.error("Associated contract not found", 404)

        if current_user.type != "client":
            return ResponseSchema.error("Only contract owner client can update rating", 403)
        client = get_client_profile_for_user(current_user)
        if str(contract["client_id"]) != str(client["client_id"]):
            return ResponseSchema.error("Only contract owner client can update rating", 403)

        update_data = rating_update.model_dump(exclude_unset=True)
        updated_rating = RatingFunctions.update_rating(rating_id, update_data)

        success_msg = f"Updated rating {rating_id}"
        logger("RATING", success_msg, "PUT /ratings/{rating_id}", "INFO")
        return ResponseSchema.success(updated_rating, 200)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("RATING", error_msg, "PUT /ratings/{rating_id}", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to update rating {rating_id}: {str(e)}"
        logger("RATING", error_msg, "PUT /ratings/{rating_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@rating_router.delete("/{rating_id}", status_code=200)
async def delete_rating(rating_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a rating - Authenticated users only"""
    try:
        existing_rating = RatingFunctions.get_rating_by_id(rating_id)
        if not existing_rating:
            error_msg = f"Rating {rating_id} not found"
            logger("RATING", error_msg, "DELETE /ratings/{rating_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        RatingFunctions.delete_rating(rating_id)
        
        success_msg = f"Deleted rating {rating_id}"
        logger("RATING", success_msg, "DELETE /ratings/{rating_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete rating {rating_id}: {str(e)}"
        logger("RATING", error_msg, "DELETE /ratings/{rating_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
