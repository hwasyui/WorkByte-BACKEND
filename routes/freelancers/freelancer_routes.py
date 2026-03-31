import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerCreate, FreelancerUpdate, FreelancerResponse
from functions.schema_model import UserInDB
from functions.authentication import get_freelancer_user
from functions.logger import logger
from routes.freelancers.freelancer_functions import FreelancerFunctions, EmbeddingFunctions

freelancer_router = APIRouter(prefix="/freelancers", tags=["Freelancers"])


@freelancer_router.get("", response_model=List[FreelancerResponse])
async def get_all_freelancers(limit: Optional[int] = None, current_user: UserInDB = Depends(get_freelancer_user)):
    """Fetch all freelancers - Freelancers only"""
    try:
        freelancers = FreelancerFunctions.get_all_freelancers(limit=limit)
        success_msg = f"Retrieved {len(freelancers)} freelancers" + (f" (limit: {limit})" if limit else "")
        logger("FREELANCER", success_msg, "GET /freelancers", "INFO")
        return freelancers
    except Exception as e:
        error_msg = f"Failed to fetch freelancers: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.post("", response_model=FreelancerResponse, status_code=status.HTTP_201_CREATED)
async def create_freelancer(freelancer: FreelancerCreate, current_user: UserInDB = Depends(get_freelancer_user)):
    """Create a new freelancer profile - Freelancers only - JSON body accepted"""
    try:
        freelancer_id = freelancer.freelancer_id or str(uuid.uuid4())
        
        # Check if freelancer already exists for this user
        existing = FreelancerFunctions.get_freelancer_by_user_id(freelancer.user_id)
        if existing:
            error_msg = f"Freelancer profile already exists for user {freelancer.user_id}"
            logger("FREELANCER", error_msg, "POST /freelancers", "WARNING")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"status": "error", "reason": error_msg})
        
        new_freelancer = FreelancerFunctions.create_freelancer(
            freelancer_id=freelancer_id,
            user_id=freelancer.user_id,
            full_name=freelancer.full_name,
            bio=freelancer.bio,
            cv_file_url=freelancer.cv_file_url,
            profile_picture_url=freelancer.profile_picture_url,
            estimated_rate=freelancer.estimated_rate,
            rate_time=freelancer.rate_time,
            rate_currency=freelancer.rate_currency,
            create_embedding=True
        )
        
        success_msg = f"Created freelancer {freelancer_id} for user {freelancer.user_id} - {freelancer.full_name}"
        logger("FREELANCER", success_msg, "POST /freelancers", "INFO")
        return new_freelancer
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to create freelancer: {str(e)}"
        logger("FREELANCER", error_msg, "POST /freelancers", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.put("/{identifier}", response_model=FreelancerResponse)
async def update_freelancer(identifier: str, freelancer_update: FreelancerUpdate, current_user: UserInDB = Depends(get_freelancer_user)):
    """Update freelancer information (supports both freelancer_id and user_id) - Freelancers only"""
    try:
        # Check if freelancer exists and get actual freelancer_id if user_id was provided
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Freelancer {identifier} not found for update"
            logger("FREELANCER", error_msg, "PUT /freelancers/{identifier}", "WARNING")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "reason": error_msg})
        
        freelancer_id = existing["freelancer_id"]
        
        update_data = {k: v for k, v in freelancer_update.dict().items() if v is not None}
        updated_freelancer = FreelancerFunctions.update_freelancer(
            freelancer_id=freelancer_id,
            update_data=update_data,
            update_embedding=True
        )
        
        success_msg = f"Updated freelancer {freelancer_id} with fields: {', '.join(update_data.keys())}"
        logger("FREELANCER", success_msg, "PUT /freelancers/{identifier}", "INFO")
        return updated_freelancer
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to update freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "PUT /freelancers/{identifier}", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.delete("/{identifier}", status_code=status.HTTP_200_OK)
async def delete_freelancer(identifier: str, current_user: UserInDB = Depends(get_freelancer_user)):
    """Delete a freelancer profile (supports both freelancer_id and user_id) - Freelancers only"""
    try:
        # Check if freelancer exists and get actual freelancer_id if user_id was provided
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            error_msg = f"Freelancer {identifier} not found for deletion"
            logger("FREELANCER", error_msg, "DELETE /freelancers/{identifier}", "WARNING")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "reason": error_msg})
        
        freelancer_id = existing["freelancer_id"]
        FreelancerFunctions.delete_freelancer(freelancer_id, delete_embedding=True)
        success_msg = f"Freelancer {freelancer_id} deleted successfully"
        logger("FREELANCER", success_msg, "DELETE /freelancers/{identifier}", "INFO")
        return {"status": "success", "reason": success_msg, "deleted_id": freelancer_id}
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to delete freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "DELETE /freelancers/{identifier}", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.get("/search/{search_term}", response_model=Dict)
async def search_freelancers(search_term: str):
    """Search freelancers by name - JSON response"""
    try:
        results = FreelancerFunctions.search_freelancers_by_name(search_term)
        success_msg = f"Searched freelancers for '{search_term}', found {len(results)} results"
        logger("FREELANCER", success_msg, "GET /freelancers/search/{search_term}", "INFO")
        return {"status": "success", "reason": success_msg, "results": results, "count": len(results)}
    except Exception as e:
        error_msg = f"Failed to search freelancers with term '{search_term}': {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/search/{search_term}", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.get("/{freelancer_id}/embedding", response_model=Dict)
async def get_freelancer_embedding(freelancer_id: str):
    """Get freelancer embedding - JSON response"""
    try:
        embedding = FreelancerFunctions.get_freelancer_embedding(freelancer_id)
        if not embedding:
            error_msg = f"Embedding not found for freelancer {freelancer_id}"
            logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/embedding", "WARNING")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "reason": error_msg})
        
        # Return embedding metadata without the actual vector for brevity
        result = {
            "embedding_id": embedding.get("embedding_id"),
            "freelancer_id": embedding.get("freelancer_id"),
            "source_text": embedding.get("source_text"),
            "created_at": embedding.get("created_at"),
            "updated_at": embedding.get("updated_at")
        }
        success_msg = f"Retrieved embedding for freelancer {freelancer_id}"
        logger("FREELANCER", success_msg, "GET /freelancers/{freelancer_id}/embedding", "INFO")
        return result
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to fetch embedding for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/embedding", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})


@freelancer_router.get("/{identifier}", response_model=FreelancerResponse)
async def get_freelancer(identifier: str, current_user: UserInDB = Depends(get_freelancer_user)):
    """Fetch a single freelancer by ID (supports both freelancer_id and user_id) - Freelancers only - JSON response"""
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not freelancer:
            error_msg = f"Freelancer {identifier} not found"
            logger("FREELANCER", error_msg, "GET /freelancers/{identifier}", "WARNING")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"status": "error", "reason": error_msg})
        success_msg = f"Retrieved freelancer {identifier}"
        logger("FREELANCER", success_msg, "GET /freelancers/{identifier}", "INFO")
        return freelancer
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to fetch freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{identifier}", "ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"status": "error", "reason": error_msg})
