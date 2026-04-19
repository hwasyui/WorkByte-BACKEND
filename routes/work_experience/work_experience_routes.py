import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import WorkExperienceCreate, WorkExperienceUpdate, WorkExperienceResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.work_experience.work_experience_functions import WorkExperienceFunctions
from ai_related.job_matching.embedding_manager import mark_freelancer_dirty

work_experience_router = APIRouter(prefix="/work-experiences", tags=["Work Experiences"])


@work_experience_router.get("", response_model=List[WorkExperienceResponse])
async def get_all_work_experiences(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all work experiences - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        experiences = WorkExperienceFunctions.get_work_experiences_by_freelancer_id(freelancer["freelancer_id"])
        success_msg = f"Retrieved {len(experiences)} work experiences for freelancer {freelancer['freelancer_id']}"
        logger("WORK_EXPERIENCE", success_msg, "GET /work-experiences", "INFO")
        return ResponseSchema.success(experiences, 200)
    except Exception as e:
        error_msg = f"Failed to fetch work experiences: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "GET /work-experiences", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@work_experience_router.get("/{work_experience_id}", response_model=WorkExperienceResponse)
async def get_work_experience(work_experience_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single work experience by ID - Authenticated users only - JSON response"""
    try:
        experience = WorkExperienceFunctions.get_work_experience_by_id(work_experience_id)
        if not experience:
            error_msg = f"Work experience {work_experience_id} not found"
            logger("WORK_EXPERIENCE", error_msg, "GET /work-experiences/{work_experience_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved work experience {work_experience_id}"
        logger("WORK_EXPERIENCE", success_msg, "GET /work-experiences/{work_experience_id}", "INFO")
        return ResponseSchema.success(experience, 200)
    except Exception as e:
        error_msg = f"Failed to fetch work experience {work_experience_id}: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "GET /work-experiences/{work_experience_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@work_experience_router.get("/freelancer/{freelancer_id}", response_model=List[WorkExperienceResponse])
async def get_work_experiences_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all work experiences for a specific freelancer - Authenticated users only - JSON response"""
    try:
        experiences = WorkExperienceFunctions.get_work_experiences_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(experiences)} work experiences for freelancer {freelancer_id}"
        logger("WORK_EXPERIENCE", success_msg, "GET /work-experiences/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(experiences, 200)
    except Exception as e:
        error_msg = f"Failed to fetch work experiences for freelancer {freelancer_id}: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "GET /work-experiences/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@work_experience_router.post("", response_model=WorkExperienceResponse, status_code=201)
async def create_work_experience(work_experience: WorkExperienceCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new work experience - Authenticated users only - JSON body accepted"""
    try:
        work_experience_id = work_experience.work_experience_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, work_experience.freelancer_id)
        new_experience = WorkExperienceFunctions.create_work_experience(
            freelancer_id=work_experience.freelancer_id,
            job_title=work_experience.job_title,
            company_name=work_experience.company_name,
            start_date=work_experience.start_date,
            end_date=work_experience.end_date,
            location=work_experience.location,
            is_current=work_experience.is_current,
            description=work_experience.description
        )
        
        mark_freelancer_dirty(str(work_experience.freelancer_id))
        success_msg = f"Created work experience {work_experience_id} for freelancer {work_experience.freelancer_id}"
        logger("WORK_EXPERIENCE", success_msg, "POST /work-experiences", "INFO")
        return ResponseSchema.success(new_experience, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "POST /work-experiences", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create work experience: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "POST /work-experiences", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@work_experience_router.put("/{work_experience_id}", response_model=WorkExperienceResponse)
async def update_work_experience(work_experience_id: str, work_experience_update: WorkExperienceUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update work experience information - Authenticated users only"""
    try:
        existing_experience = WorkExperienceFunctions.get_work_experience_by_id(work_experience_id)
        if not existing_experience:
            error_msg = f"Work experience {work_experience_id} not found"
            logger("WORK_EXPERIENCE", error_msg, "PUT /work-experiences/{work_experience_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_experience["freelancer_id"])
        
        update_data = work_experience_update.model_dump(exclude_unset=True)
        updated_experience = WorkExperienceFunctions.update_work_experience(work_experience_id, update_data)
        
        mark_freelancer_dirty(str(existing_experience["freelancer_id"]))
        success_msg = f"Updated work experience {work_experience_id}"
        logger("WORK_EXPERIENCE", success_msg, "PUT /work-experiences/{work_experience_id}", "INFO")
        return ResponseSchema.success(updated_experience, 200)
    except Exception as e:
        error_msg = f"Failed to update work experience {work_experience_id}: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "PUT /work-experiences/{work_experience_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@work_experience_router.delete("/{work_experience_id}", status_code=200)
async def delete_work_experience(work_experience_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a work experience - Authenticated users only"""
    try:
        existing_experience = WorkExperienceFunctions.get_work_experience_by_id(work_experience_id)
        if not existing_experience:
            error_msg = f"Work experience {work_experience_id} not found"
            logger("WORK_EXPERIENCE", error_msg, "DELETE /work-experiences/{work_experience_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_experience["freelancer_id"])
        
        fid = str(existing_experience["freelancer_id"])
        WorkExperienceFunctions.delete_work_experience(work_experience_id)
        mark_freelancer_dirty(fid)
        success_msg = f"Deleted work experience {work_experience_id}"
        logger("WORK_EXPERIENCE", success_msg, "DELETE /work-experiences/{work_experience_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete work experience {work_experience_id}: {str(e)}"
        logger("WORK_EXPERIENCE", error_msg, "DELETE /work-experiences/{work_experience_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)