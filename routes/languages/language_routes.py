import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import LanguageCreate, LanguageUpdate, LanguageResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.languages.language_functions import LanguageFunctions

language_router = APIRouter(prefix="/languages", tags=["Languages"])


@language_router.get("", response_model=List[LanguageResponse])
async def get_all_languages(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all languages - Authenticated users only - JSON response"""
    try:
        languages = LanguageFunctions.get_all_languages(limit=limit)
        success_msg = f"Retrieved {len(languages)} languages" + (f" (limit: {limit})" if limit else "")
        logger("LANGUAGE", success_msg, "GET /languages", "INFO")
        return ResponseSchema.success(languages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch languages: {str(e)}"
        logger("LANGUAGE", error_msg, "GET /languages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@language_router.get("/search/{search_term}", response_model=Dict)
async def search_languages(search_term: str, current_user: UserInDB = Depends(get_current_user)):
    """Search languages by name - Authenticated users only - JSON response"""
    try:
        results = LanguageFunctions.search_languages_by_name(search_term)
        success_msg = f"Searched languages for '{search_term}', found {len(results)} results"
        logger("LANGUAGE", success_msg, "GET /languages/search/{search_term}", "INFO")
        search_result = {"results": results, "count": len(results)}
        return ResponseSchema.success(search_result, 200)
    except Exception as e:
        error_msg = f"Failed to search languages with term '{search_term}': {str(e)}"
        logger("LANGUAGE", error_msg, "GET /languages/search/{search_term}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@language_router.get("/{language_id}", response_model=LanguageResponse)
async def get_language(language_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single language by ID - Authenticated users only - JSON response"""
    try:
        language = LanguageFunctions.get_language_by_id(language_id)
        if not language:
            error_msg = f"Language {language_id} not found"
            logger("LANGUAGE", error_msg, "GET /languages/{language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved language {language_id}: {language.get('language_name', 'unknown')}"
        logger("LANGUAGE", success_msg, "GET /languages/{language_id}", "INFO")
        return ResponseSchema.success(language, 200)
    except Exception as e:
        error_msg = f"Failed to fetch language {language_id}: {str(e)}"
        logger("LANGUAGE", error_msg, "GET /languages/{language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@language_router.post("", response_model=LanguageResponse, status_code=201)
async def create_language(language: LanguageCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new language - Authenticated users only - JSON body accepted"""
    try:
        # Generate UUID if not provided
        language_id = language.language_id or str(uuid.uuid4())
        
        new_language = LanguageFunctions.create_language(
            language_name=language.language_name,
            iso_code=language.iso_code
        )
        
        success_msg = f"Created language {language_id}: {language.language_name} (ISO: {language.iso_code})"
        logger("LANGUAGE", success_msg, "POST /languages", "INFO")
        return ResponseSchema.success(new_language, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("LANGUAGE", error_msg, "POST /languages", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create language: {str(e)}"
        logger("LANGUAGE", error_msg, "POST /languages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@language_router.put("/{language_id}", response_model=LanguageResponse)
async def update_language(language_id: str, language_update: LanguageUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update language information - Authenticated users only"""
    try:
        # Check if language exists
        existing = LanguageFunctions.get_language_by_id(language_id)
        if not existing:
            error_msg = f"Language {language_id} not found for update"
            logger("LANGUAGE", error_msg, "PUT /languages/{language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = {k: v for k, v in language_update.dict().items() if v is not None}
        updated_language = LanguageFunctions.update_language(language_id, update_data)
        
        success_msg = f"Updated language {language_id} with fields: {', '.join(update_data.keys())}"
        logger("LANGUAGE", success_msg, "PUT /languages/{language_id}", "INFO")
        return ResponseSchema.success(updated_language, 200)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("LANGUAGE", error_msg, "PUT /languages/{language_id}", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to update language {language_id}: {str(e)}"
        logger("LANGUAGE", error_msg, "PUT /languages/{language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@language_router.delete("/{language_id}", status_code=200)
async def delete_language(language_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a language - Authenticated users only"""
    try:
        # Check if language exists
        existing = LanguageFunctions.get_language_by_id(language_id)
        if not existing:
            error_msg = f"Language {language_id} not found for deletion"
            logger("LANGUAGE", error_msg, "DELETE /languages/{language_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        LanguageFunctions.delete_language(language_id)
        success_msg = f"Language {language_id} deleted successfully"
        logger("LANGUAGE", success_msg, "DELETE /languages/{language_id}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except Exception as e:
        error_msg = f"Failed to delete language {language_id}: {str(e)}"
        logger("LANGUAGE", error_msg, "DELETE /languages/{language_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
