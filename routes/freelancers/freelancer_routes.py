import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerCreate, FreelancerUpdate, FreelancerResponse, FreelancerProfileComplete
from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancers.freelancer_functions import FreelancerFunctions, get_comprehensive_freelancer_profile
from ai_related.job_matching.embedding_manager import upsert_freelancer_embedding, mark_freelancer_dirty

freelancer_router = APIRouter(prefix="/freelancers", tags=["Freelancers"])


@freelancer_router.get("", response_model=List[FreelancerResponse])
async def get_all_freelancers(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        logger("FREELANCER", f"Retrieved freelancer profile for user {current_user.user_id}", "GET /freelancers", "INFO")
        return ResponseSchema.success([freelancer], 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancers: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.post("", response_model=FreelancerResponse, status_code=201)
async def create_freelancer(freelancer: FreelancerCreate, current_user: UserInDB = Depends(get_freelancer_user)):
    try:
        freelancer_id = freelancer.freelancer_id or str(uuid.uuid4())
        current_freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if current_freelancer:
            return ResponseSchema.error(f"Freelancer profile already exists for user {current_user.user_id}", 400)
        if freelancer.user_id and str(freelancer.user_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot create a freelancer profile for another user", 403)
        new_freelancer = FreelancerFunctions.create_freelancer(
            freelancer_id=freelancer_id,
            user_id=current_user.user_id,
            full_name=freelancer.full_name,
            bio=freelancer.bio,
            cv_file_url=freelancer.cv_file_url,
            profile_picture_url=freelancer.profile_picture_url,
            estimated_rate=freelancer.estimated_rate,
            rate_time=freelancer.rate_time,
            rate_currency=freelancer.rate_currency,
            create_embedding=True
        )
        asyncio.create_task(upsert_freelancer_embedding(str(new_freelancer["freelancer_id"])))
        logger("FREELANCER", f"Created freelancer {freelancer_id}", "POST /freelancers", "INFO")
        return ResponseSchema.success(new_freelancer, 201)
    except Exception as e:
        error_msg = f"Failed to create freelancer: {str(e)}"
        logger("FREELANCER", error_msg, "POST /freelancers", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.put("/{identifier}", response_model=FreelancerResponse)
async def update_freelancer(identifier: str, freelancer_update: FreelancerUpdate, current_user: UserInDB = Depends(get_freelancer_user)):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        freelancer_id = existing["freelancer_id"]
        assert_freelancer_owns(current_user, freelancer_id)
        update_data = {k: v for k, v in freelancer_update.dict().items() if k in freelancer_update.__fields_set__}
        updated_freelancer = FreelancerFunctions.update_freelancer(freelancer_id=freelancer_id, update_data=update_data)
        mark_freelancer_dirty(freelancer_id)
        logger("FREELANCER", f"Updated freelancer {freelancer_id}", "PUT /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(updated_freelancer, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "PUT /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.delete("/{identifier}", status_code=200)
async def delete_freelancer(identifier: str, current_user: UserInDB = Depends(get_freelancer_user)):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        freelancer_id = existing["freelancer_id"]
        assert_freelancer_owns(current_user, freelancer_id)
        FreelancerFunctions.delete_freelancer(freelancer_id, delete_embedding=True)
        logger("FREELANCER", f"Freelancer {freelancer_id} deleted", "DELETE /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(f"Freelancer {freelancer_id} deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "DELETE /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ✅ Specific routes before wildcard /{identifier}
@freelancer_router.get("/search/{search_term}", response_model=Dict)
async def search_freelancers(search_term: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        results = FreelancerFunctions.search_freelancers_by_name(search_term)
        logger("FREELANCER", f"Found {len(results)} results for '{search_term}'", "GET /freelancers/search/{search_term}", "INFO")
        return ResponseSchema.success({"results": results, "count": len(results)}, 200)
    except Exception as e:
        error_msg = f"Failed to search freelancers: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/search/{search_term}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/{freelancer_id}/skills", response_model=Dict)
async def get_freelancer_skills(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        skills = FreelancerFunctions.get_freelancer_skills_with_names(freelancer_id)
        logger("FREELANCER", f"Retrieved skills for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/skills", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/{freelancer_id}/embedding", response_model=Dict)
async def get_freelancer_embedding(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        embedding = FreelancerFunctions.get_freelancer_embedding(freelancer_id)
        if not embedding:
            return ResponseSchema.error(f"Embedding not found for freelancer {freelancer_id}", 404)
        result = {
            "embedding_id": embedding.get("embedding_id"),
            "freelancer_id": embedding.get("freelancer_id"),
            "source_text": embedding.get("source_text"),
            "created_at": embedding.get("created_at"),
            "updated_at": embedding.get("updated_at")
        }
        logger("FREELANCER", f"Retrieved embedding for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/embedding", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        error_msg = f"Failed to fetch embedding for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/embedding", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/{freelancer_id}/profile", response_model=FreelancerProfileComplete)
async def get_comprehensive_freelancer_profile_endpoint(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        profile = get_comprehensive_freelancer_profile(freelancer_id)
        if not profile:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        logger("FREELANCER", f"Retrieved comprehensive profile for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/profile", "INFO")
        return ResponseSchema.success(profile, 200)
    except Exception as e:
        error_msg = f"Failed to fetch comprehensive profile {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/profile", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ✅ Wildcard last — must come after all /{freelancer_id}/xxx routes
@freelancer_router.get("/{identifier}", response_model=FreelancerResponse)
async def get_freelancer(identifier: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not freelancer:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        logger("FREELANCER", f"Retrieved freelancer {identifier}", "GET /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(freelancer, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)