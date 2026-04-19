import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerSpecialityCreate, FreelancerSpecialityUpdate, FreelancerSpecialityResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancer_specialities.freelancer_speciality_functions import FreelancerSpecialityFunctions
from ai_related.job_matching.embedding_manager import mark_freelancer_dirty

freelancer_speciality_router = APIRouter(prefix="/freelancer-specialities", tags=["Freelancer Specialities"])


@freelancer_speciality_router.get("", response_model=List[FreelancerSpecialityResponse])
async def get_all_freelancer_specialities(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch current user's freelancer specialities - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        specialities = FreelancerSpecialityFunctions.get_freelancer_specialities_by_freelancer_id(freelancer["freelancer_id"], limit=limit)
        success_msg = f"Retrieved {len(specialities)} freelancer specialities for freelancer {freelancer['freelancer_id']}" + (f" (limit: {limit})" if limit else "")
        logger("FREELANCER_SPECIALITY", success_msg, "GET /freelancer-specialities", "INFO")
        return ResponseSchema.success(specialities, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer specialities: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "GET /freelancer-specialities", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_speciality_router.get("/{freelancer_speciality_id}", response_model=FreelancerSpecialityResponse)
async def get_freelancer_speciality(freelancer_speciality_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single freelancer speciality by ID - Authenticated users only - JSON response"""
    try:
        speciality = FreelancerSpecialityFunctions.get_freelancer_speciality_by_id(freelancer_speciality_id)
        if not speciality:
            error_msg = f"Freelancer speciality {freelancer_speciality_id} not found"
            logger("FREELANCER_SPECIALITY", error_msg, "GET /freelancer-specialities/{freelancer_speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved freelancer speciality {freelancer_speciality_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "GET /freelancer-specialities/{freelancer_speciality_id}", "INFO")
        return ResponseSchema.success(speciality, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer speciality {freelancer_speciality_id}: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "GET /freelancer-specialities/{freelancer_speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@freelancer_speciality_router.get("/freelancer/{freelancer_id}", response_model=List[FreelancerSpecialityResponse])
async def get_freelancer_specialities_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all specialities for a specific freelancer - Authenticated users only - JSON response"""
    try:
        specialities = FreelancerSpecialityFunctions.get_freelancer_specialities_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(specialities)} specialities for freelancer {freelancer_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "GET /freelancer-specialities/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(specialities, 200)
    except Exception as e:
        error_msg = f"Failed to fetch specialities for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "GET /freelancer-specialities/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_speciality_router.post("", response_model=FreelancerSpecialityResponse, status_code=201)
async def create_freelancer_speciality(freelancer_speciality: FreelancerSpecialityCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new freelancer speciality - Authenticated users only - JSON body accepted"""
    try:
        freelancer_speciality_id = freelancer_speciality.freelancer_speciality_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, freelancer_speciality.freelancer_id)
        new_speciality = FreelancerSpecialityFunctions.create_freelancer_speciality(
            freelancer_id=freelancer_speciality.freelancer_id,
            speciality_id=freelancer_speciality.speciality_id,
            is_primary=freelancer_speciality.is_primary
        )
        
        mark_freelancer_dirty(str(freelancer_speciality.freelancer_id))
        success_msg = f"Created freelancer speciality {freelancer_speciality_id} for freelancer {freelancer_speciality.freelancer_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "POST /freelancer-specialities", "INFO")
        return ResponseSchema.success(new_speciality, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "POST /freelancer-specialities", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create freelancer speciality: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "POST /freelancer-specialities", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_speciality_router.put("/{freelancer_speciality_id}", response_model=FreelancerSpecialityResponse)
async def update_freelancer_speciality(freelancer_speciality_id: str, freelancer_speciality_update: FreelancerSpecialityUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update freelancer speciality information - Authenticated users only"""
    try:
        existing_speciality = FreelancerSpecialityFunctions.get_freelancer_speciality_by_id(freelancer_speciality_id)
        if not existing_speciality:
            error_msg = f"Freelancer speciality {freelancer_speciality_id} not found"
            logger("FREELANCER_SPECIALITY", error_msg, "PUT /freelancer-specialities/{freelancer_speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_speciality["freelancer_id"])
        
        update_data = freelancer_speciality_update.model_dump(exclude_unset=True)
        updated_speciality = FreelancerSpecialityFunctions.update_freelancer_speciality(freelancer_speciality_id, update_data)
        
        mark_freelancer_dirty(str(existing_speciality["freelancer_id"]))
        success_msg = f"Updated freelancer speciality {freelancer_speciality_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "PUT /freelancer-specialities/{freelancer_speciality_id}", "INFO")
        return ResponseSchema.success(updated_speciality, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer speciality {freelancer_speciality_id}: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "PUT /freelancer-specialities/{freelancer_speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@freelancer_speciality_router.delete("/{freelancer_speciality_id}", status_code=200)
async def delete_freelancer_speciality(freelancer_speciality_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer speciality - Authenticated users only"""
    try:
        existing_speciality = FreelancerSpecialityFunctions.get_freelancer_speciality_by_id(freelancer_speciality_id)
        if not existing_speciality:
            error_msg = f"Freelancer speciality {freelancer_speciality_id} not found"
            logger("FREELANCER_SPECIALITY", error_msg, "DELETE /freelancer-specialities/{freelancer_speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_speciality["freelancer_id"])
        
        fid = str(existing_speciality["freelancer_id"])
        FreelancerSpecialityFunctions.delete_freelancer_speciality(freelancer_speciality_id)
        mark_freelancer_dirty(fid)
        success_msg = f"Deleted freelancer speciality {freelancer_speciality_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "DELETE /freelancer-specialities/{freelancer_speciality_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer speciality {freelancer_speciality_id}: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "DELETE /freelancer-specialities/{freelancer_speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@freelancer_speciality_router.delete("/freelancer/{freelancer_id}/speciality/{speciality_id}", status_code=200)
async def delete_freelancer_speciality_by_ids(freelancer_id: str, speciality_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer speciality by freelancer_id and speciality_id - Authenticated users only"""
    try:
        assert_freelancer_owns(current_user, freelancer_id)
        FreelancerSpecialityFunctions.delete_freelancer_speciality_by_freelancer_and_speciality(freelancer_id, speciality_id)
        mark_freelancer_dirty(freelancer_id)
        success_msg = f"Deleted speciality {speciality_id} from freelancer {freelancer_id}"
        logger("FREELANCER_SPECIALITY", success_msg, "DELETE /freelancer-specialities/freelancer/{freelancer_id}/speciality/{speciality_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer speciality: {str(e)}"
        logger("FREELANCER_SPECIALITY", error_msg, "DELETE /freelancer-specialities/freelancer/{freelancer_id}/speciality/{speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
