import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional
from functions.schema_model import PerformanceRatingCreate, PerformanceRatingUpdate, PerformanceRatingResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.performance_ratings.performance_rating_functions import PerformanceRatingFunctions
from routes.contracts.contract_functions import ContractFunctions

performance_rating_router = APIRouter(prefix="/performance-ratings", tags=["Performance Ratings"])


@performance_rating_router.get("", response_model=List[PerformanceRatingResponse])
async def get_all_performance_ratings(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all performance ratings - Authenticated users only - JSON response"""
    try:
        ratings = PerformanceRatingFunctions.get_all_performance_ratings(limit=limit)
        success_msg = f"Retrieved {len(ratings)} performance ratings" + (f" (limit: {limit})" if limit else "")
        logger("PERFORMANCE_RATING", success_msg, "GET /performance-ratings", "INFO")
        return ResponseSchema.success(ratings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch performance ratings: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "GET /performance-ratings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@performance_rating_router.get("/freelancer/{freelancer_id}", response_model=PerformanceRatingResponse)
async def get_performance_rating(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch performance rating for a specific freelancer - Authenticated users only - JSON response"""
    try:
        rating = PerformanceRatingFunctions.get_performance_rating_by_freelancer_id(freelancer_id)
        if not rating:
            error_msg = f"Performance rating for freelancer {freelancer_id} not found"
            logger("PERFORMANCE_RATING", error_msg, "GET /performance-ratings/freelancer/{freelancer_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved performance rating for freelancer {freelancer_id}"
        logger("PERFORMANCE_RATING", success_msg, "GET /performance-ratings/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(rating, 200)
    except Exception as e:
        error_msg = f"Failed to fetch performance rating for freelancer {freelancer_id}: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "GET /performance-ratings/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@performance_rating_router.post("", response_model=PerformanceRatingResponse, status_code=201)
async def create_performance_rating(rating: PerformanceRatingCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new performance rating - only client can create for completed contract relationship"""
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create performance ratings", 403)

        contracts = ContractFunctions.get_contracts_by_freelancer_id(rating.freelancer_id)
        completed_contracts = [c for c in contracts if c.get("client_id") == current_user.user_id and c.get("status") in ["completed", "cancelled", "disputed"]]

        if not completed_contracts:
            return ResponseSchema.error("No completed contract exists between this client and freelancer", 400)

        new_rating = PerformanceRatingFunctions.create_performance_rating(
            freelancer_id=rating.freelancer_id,
            overall_performance_score=rating.overall_performance_score or 0.0,
            confidence_score=rating.confidence_score or 0.0,
            total_ratings_received=rating.total_ratings_received or 0,
        )

        success_msg = f"Created performance rating for freelancer {rating.freelancer_id}"
        logger("PERFORMANCE_RATING", success_msg, "POST /performance-ratings", "INFO")
        return ResponseSchema.success(new_rating, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "POST /performance-ratings", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create performance rating: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "POST /performance-ratings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@performance_rating_router.put("/freelancer/{freelancer_id}", response_model=PerformanceRatingResponse)
async def update_performance_rating(freelancer_id: str, rating_update: PerformanceRatingUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update performance rating - only client can update if they have completed contract relationship"""
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can update performance ratings", 403)

        existing_rating = PerformanceRatingFunctions.get_performance_rating_by_freelancer_id(freelancer_id)
        if not existing_rating:
            error_msg = f"Performance rating for freelancer {freelancer_id} not found"
            logger("PERFORMANCE_RATING", error_msg, "PUT /performance-ratings/freelancer/{freelancer_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)

        contracts = ContractFunctions.get_contracts_by_freelancer_id(freelancer_id)
        has_completed = any(c for c in contracts if c.get("client_id") == current_user.user_id and c.get("status") in ["completed", "cancelled", "disputed"])
        if not has_completed:
            return ResponseSchema.error("No completed contract exists between this client and freelancer", 403)

        update_data = rating_update.model_dump(exclude_unset=True)
        updated_rating = PerformanceRatingFunctions.update_performance_rating(freelancer_id, update_data)

        success_msg = f"Updated performance rating for freelancer {freelancer_id}"
        logger("PERFORMANCE_RATING", success_msg, "PUT /performance-ratings/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(updated_rating, 200)
    except Exception as e:
        error_msg = f"Failed to update performance rating for freelancer {freelancer_id}: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "PUT /performance-ratings/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@performance_rating_router.delete("/freelancer/{freelancer_id}", status_code=200)
async def delete_performance_rating(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a performance rating - Authenticated users only"""
    try:
        existing_rating = PerformanceRatingFunctions.get_performance_rating_by_freelancer_id(freelancer_id)
        if not existing_rating:
            error_msg = f"Performance rating for freelancer {freelancer_id} not found"
            logger("PERFORMANCE_RATING", error_msg, "DELETE /performance-ratings/freelancer/{freelancer_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        PerformanceRatingFunctions.delete_performance_rating(freelancer_id)
        
        success_msg = f"Deleted performance rating for freelancer {freelancer_id}"
        logger("PERFORMANCE_RATING", success_msg, "DELETE /performance-ratings/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete performance rating for freelancer {freelancer_id}: {str(e)}"
        logger("PERFORMANCE_RATING", error_msg, "DELETE /performance-ratings/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
