import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import SpecialityCreate, SpecialityUpdate, SpecialityResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.specialities.speciality_functions import SpecialityFunctions

speciality_router = APIRouter(prefix="/specialities", tags=["Specialities"])


@speciality_router.get("", response_model=List[SpecialityResponse])
async def get_all_specialities(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all specialities - Authenticated users only - JSON response"""
    try:
        specialities = SpecialityFunctions.get_all_specialities(limit=limit)
        success_msg = f"Retrieved {len(specialities)} specialities" + (f" (limit: {limit})" if limit else "")
        logger("SPECIALITY", success_msg, "GET /specialities", "INFO")
        return ResponseSchema.success(specialities, 200)
    except Exception as e:
        error_msg = f"Failed to fetch specialities: {str(e)}"
        logger("SPECIALITY", error_msg, "GET /specialities", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@speciality_router.get("/search/{search_term}", response_model=Dict)
async def search_specialities(search_term: str, current_user: UserInDB = Depends(get_current_user)):
    """Search specialities by name - Authenticated users only - JSON response"""
    try:
        results = SpecialityFunctions.search_specialities_by_name(search_term)
        success_msg = f"Searched specialities for '{search_term}', found {len(results)} results"
        logger("SPECIALITY", success_msg, "GET /specialities/search/{search_term}", "INFO")
        search_result = {"results": results, "count": len(results)}
        return ResponseSchema.success(search_result, 200)
    except Exception as e:
        error_msg = f"Failed to search specialities with term '{search_term}': {str(e)}"
        logger("SPECIALITY", error_msg, "GET /specialities/search/{search_term}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@speciality_router.get("/{speciality_id}", response_model=SpecialityResponse)
async def get_speciality(speciality_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single speciality by ID - Authenticated users only - JSON response"""
    try:
        speciality = SpecialityFunctions.get_speciality_by_id(speciality_id)
        if not speciality:
            error_msg = f"Speciality {speciality_id} not found"
            logger("SPECIALITY", error_msg, "GET /specialities/{speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved speciality {speciality_id}: {speciality.get('speciality_name', 'unknown')}"
        logger("SPECIALITY", success_msg, "GET /specialities/{speciality_id}", "INFO")
        return ResponseSchema.success(speciality, 200)
    except Exception as e:
        error_msg = f"Failed to fetch speciality {speciality_id}: {str(e)}"
        logger("SPECIALITY", error_msg, "GET /specialities/{speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@speciality_router.post("", response_model=SpecialityResponse, status_code=201)
async def create_speciality(speciality: SpecialityCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new speciality - Authenticated users only - JSON body accepted"""
    try:
        # Generate UUID if not provided
        speciality_id = speciality.speciality_id or str(uuid.uuid4())
        
        new_speciality = SpecialityFunctions.create_speciality(
            speciality_name=speciality.speciality_name,
            description=speciality.description
        )
        
        success_msg = f"Created speciality {speciality_id}: {speciality.speciality_name}"
        logger("SPECIALITY", success_msg, "POST /specialities", "INFO")
        return ResponseSchema.success(new_speciality, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("SPECIALITY", error_msg, "POST /specialities", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create speciality: {str(e)}"
        logger("SPECIALITY", error_msg, "POST /specialities", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@speciality_router.put("/{speciality_id}", response_model=SpecialityResponse)
async def update_speciality(speciality_id: str, speciality_update: SpecialityUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update speciality information - Authenticated users only"""
    try:
        # Check if speciality exists
        existing = SpecialityFunctions.get_speciality_by_id(speciality_id)
        if not existing:
            error_msg = f"Speciality {speciality_id} not found for update"
            logger("SPECIALITY", error_msg, "PUT /specialities/{speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = {k: v for k, v in speciality_update.dict().items() if v is not None}
        updated_speciality = SpecialityFunctions.update_speciality(speciality_id, update_data)
        
        success_msg = f"Updated speciality {speciality_id} with fields: {', '.join(update_data.keys())}"
        logger("SPECIALITY", success_msg, "PUT /specialities/{speciality_id}", "INFO")
        return ResponseSchema.success(updated_speciality, 200)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("SPECIALITY", error_msg, "PUT /specialities/{speciality_id}", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to update speciality {speciality_id}: {str(e)}"
        logger("SPECIALITY", error_msg, "PUT /specialities/{speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@speciality_router.delete("/{speciality_id}", status_code=200)
async def delete_speciality(speciality_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a speciality - Authenticated users only"""
    try:
        # Check if speciality exists
        existing = SpecialityFunctions.get_speciality_by_id(speciality_id)
        if not existing:
            error_msg = f"Speciality {speciality_id} not found for deletion"
            logger("SPECIALITY", error_msg, "DELETE /specialities/{speciality_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        SpecialityFunctions.delete_speciality(speciality_id)
        success_msg = f"Speciality {speciality_id} deleted successfully"
        logger("SPECIALITY", success_msg, "DELETE /specialities/{speciality_id}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except Exception as e:
        error_msg = f"Failed to delete speciality {speciality_id}: {str(e)}"
        logger("SPECIALITY", error_msg, "DELETE /specialities/{speciality_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
