import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerLanguageCreate, FreelancerLanguageUpdate, FreelancerLanguageResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancer_languages.freelancer_language_functions import FreelancerLanguageFunctions

freelancer_language_router = APIRouter(prefix="/freelancer-languages", tags=["Freelancer Languages"])


@freelancer_language_router.get("", response_model=List[FreelancerLanguageResponse])
async def get_all_freelancer_languages(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch current user's freelancer languages - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        languages = FreelancerLanguageFunctions.get_freelancer_languages_by_freelancer_id(freelancer["freelancer_id"])
        success_msg = f"Retrieved {len(languages)} freelancer languages for freelancer {freelancer['freelancer_id']}"
        logger("FREELANCER_LANGUAGE", success_msg, "GET /freelancer-languages", "INFO")
        return ResponseSchema.success(languages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer languages: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "GET /freelancer-languages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_language_router.get("/{freelancer_language_id}", response_model=FreelancerLanguageResponse)
async def get_freelancer_language(freelancer_language_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single freelancer language by ID - Authenticated users only - JSON response"""
    try:
        language = FreelancerLanguageFunctions.get_freelancer_language_by_id(freelancer_language_id)
        if not language:
            error_msg = f"Freelancer language {freelancer_language_id} not found"
            logger("FREELANCER_LANGUAGE", error_msg, "GET /freelancer-languages/{freelancer_language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved freelancer language {freelancer_language_id}"
        logger("FREELANCER_LANGUAGE", success_msg, "GET /freelancer-languages/{freelancer_language_id}", "INFO")
        return ResponseSchema.success(language, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer language {freelancer_language_id}: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "GET /freelancer-languages/{freelancer_language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_language_router.get("/freelancer/{freelancer_id}", response_model=List[FreelancerLanguageResponse])
async def get_freelancer_languages_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all languages for a specific freelancer - Authenticated users only - JSON response"""
    try:
        languages = FreelancerLanguageFunctions.get_freelancer_languages_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(languages)} languages for freelancer {freelancer_id}"
        logger("FREELANCER_LANGUAGE", success_msg, "GET /freelancer-languages/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(languages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch languages for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "GET /freelancer-languages/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_language_router.post("", response_model=FreelancerLanguageResponse, status_code=201)
async def create_freelancer_language(freelancer_language: FreelancerLanguageCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new freelancer language - Authenticated users only - JSON body accepted"""
    try:
        freelancer_language_id = freelancer_language.freelancer_language_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, freelancer_language.freelancer_id)
        new_language = FreelancerLanguageFunctions.create_freelancer_language(
            freelancer_id=freelancer_language.freelancer_id,
            language_id=freelancer_language.language_id,
            proficiency_level=freelancer_language.proficiency_level
        )
        
        success_msg = f"Created freelancer language {freelancer_language_id} for freelancer {freelancer_language.freelancer_id}"
        logger("FREELANCER_LANGUAGE", success_msg, "POST /freelancer-languages", "INFO")
        return ResponseSchema.success(new_language, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "POST /freelancer-languages", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create freelancer language: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "POST /freelancer-languages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_language_router.put("/{freelancer_language_id}", response_model=FreelancerLanguageResponse)
async def update_freelancer_language(freelancer_language_id: str, freelancer_language_update: FreelancerLanguageUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update freelancer language information - Authenticated users only"""
    try:
        existing_language = FreelancerLanguageFunctions.get_freelancer_language_by_id(freelancer_language_id)
        if not existing_language:
            error_msg = f"Freelancer language {freelancer_language_id} not found"
            logger("FREELANCER_LANGUAGE", error_msg, "PUT /freelancer-languages/{freelancer_language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_language["freelancer_id"])
        
        update_data = freelancer_language_update.model_dump(exclude_unset=True)
        updated_language = FreelancerLanguageFunctions.update_freelancer_language(freelancer_language_id, update_data)
        
        success_msg = f"Updated freelancer language {freelancer_language_id}"
        logger("FREELANCER_LANGUAGE", success_msg, "PUT /freelancer-languages/{freelancer_language_id}", "INFO")
        return ResponseSchema.success(updated_language, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer language {freelancer_language_id}: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "PUT /freelancer-languages/{freelancer_language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_language_router.delete("/{freelancer_language_id}", status_code=200)
async def delete_freelancer_language(freelancer_language_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer language - Authenticated users only"""
    try:
        existing_language = FreelancerLanguageFunctions.get_freelancer_language_by_id(freelancer_language_id)
        if not existing_language:
            error_msg = f"Freelancer language {freelancer_language_id} not found"
            logger("FREELANCER_LANGUAGE", error_msg, "DELETE /freelancer-languages/{freelancer_language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_language["freelancer_id"])
        
        FreelancerLanguageFunctions.delete_freelancer_language(freelancer_language_id)
        
        success_msg = f"Deleted freelancer language {freelancer_language_id}"
        logger("FREELANCER_LANGUAGE", success_msg, "DELETE /freelancer-languages/{freelancer_language_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer language {freelancer_language_id}: {str(e)}"
        logger("FREELANCER_LANGUAGE", error_msg, "DELETE /freelancer-languages/{freelancer_language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
