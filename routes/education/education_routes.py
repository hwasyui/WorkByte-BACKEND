import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import EducationCreate, EducationUpdate, EducationResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.education.education_functions import EducationFunctions
from ai_related.job_matching.embedding_manager import mark_freelancer_dirty

education_router = APIRouter(prefix="/educations", tags=["Educations"])


@education_router.get("", response_model=List[EducationResponse])
async def get_all_educations(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all educations - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        educations = EducationFunctions.get_educations_by_freelancer_id(freelancer["freelancer_id"])
        success_msg = f"Retrieved {len(educations)} educations for freelancer {freelancer['freelancer_id']}"
        logger("EDUCATION", success_msg, "GET /educations", "INFO")
        return ResponseSchema.success(educations, 200)
    except Exception as e:
        error_msg = f"Failed to fetch educations: {str(e)}"
        logger("EDUCATION", error_msg, "GET /educations", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@education_router.get("/{education_id}", response_model=EducationResponse)
async def get_education(education_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single education by ID - Authenticated users only - JSON response"""
    try:
        education = EducationFunctions.get_education_by_id(education_id)
        if not education:
            error_msg = f"Education {education_id} not found"
            logger("EDUCATION", error_msg, "GET /educations/{education_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved education {education_id}"
        logger("EDUCATION", success_msg, "GET /educations/{education_id}", "INFO")
        return ResponseSchema.success(education, 200)
    except Exception as e:
        error_msg = f"Failed to fetch education {education_id}: {str(e)}"
        logger("EDUCATION", error_msg, "GET /educations/{education_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@education_router.get("/freelancer/{freelancer_id}", response_model=List[EducationResponse])
async def get_educations_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all educations for a specific freelancer - Authenticated users only - JSON response"""
    try:
        educations = EducationFunctions.get_educations_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(educations)} educations for freelancer {freelancer_id}"
        logger("EDUCATION", success_msg, "GET /educations/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(educations, 200)
    except Exception as e:
        error_msg = f"Failed to fetch educations for freelancer {freelancer_id}: {str(e)}"
        logger("EDUCATION", error_msg, "GET /educations/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@education_router.post("", response_model=EducationResponse, status_code=201)
async def create_education(education: EducationCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new education - Authenticated users only - JSON body accepted"""
    try:
        education_id = education.education_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, education.freelancer_id)
        new_education = EducationFunctions.create_education(
            freelancer_id=education.freelancer_id,
            institution_name=education.institution_name,
            degree=education.degree,
            start_date=education.start_date,
            field_of_study=education.field_of_study,
            end_date=education.end_date,
            is_current=education.is_current,
            grade=education.grade,
            description=education.description
        )
        
        mark_freelancer_dirty(str(education.freelancer_id))
        success_msg = f"Created education {education_id} for freelancer {education.freelancer_id}"
        logger("EDUCATION", success_msg, "POST /educations", "INFO")
        return ResponseSchema.success(new_education, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("EDUCATION", error_msg, "POST /educations", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create education: {str(e)}"
        logger("EDUCATION", error_msg, "POST /educations", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@education_router.put("/{education_id}", response_model=EducationResponse)
async def update_education(education_id: str, education_update: EducationUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update education information - Authenticated users only"""
    try:
        existing_education = EducationFunctions.get_education_by_id(education_id)
        if not existing_education:
            error_msg = f"Education {education_id} not found"
            logger("EDUCATION", error_msg, "PUT /educations/{education_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_education["freelancer_id"])
        
        update_data = education_update.model_dump(exclude_unset=True)
        updated_education = EducationFunctions.update_education(education_id, update_data)
        
        mark_freelancer_dirty(str(existing_education["freelancer_id"]))
        success_msg = f"Updated education {education_id}"
        logger("EDUCATION", success_msg, "PUT /educations/{education_id}", "INFO")
        return ResponseSchema.success(updated_education, 200)
    except Exception as e:
        error_msg = f"Failed to update education {education_id}: {str(e)}"
        logger("EDUCATION", error_msg, "PUT /educations/{education_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@education_router.delete("/{education_id}", status_code=200)
async def delete_education(education_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete an education - Authenticated users only"""
    try:
        existing_education = EducationFunctions.get_education_by_id(education_id)
        if not existing_education:
            error_msg = f"Education {education_id} not found"
            logger("EDUCATION", error_msg, "DELETE /educations/{education_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_education["freelancer_id"])
        
        fid = str(existing_education["freelancer_id"])
        EducationFunctions.delete_education(education_id)
        mark_freelancer_dirty(fid)
        success_msg = f"Deleted education {education_id}"
        logger("EDUCATION", success_msg, "DELETE /educations/{education_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete education {education_id}: {str(e)}"
        logger("EDUCATION", error_msg, "DELETE /educations/{education_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)