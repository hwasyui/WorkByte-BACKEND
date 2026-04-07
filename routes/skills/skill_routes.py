import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import SkillCreate, SkillUpdate, SkillResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.skills.skill_functions import SkillFunctions

skill_router = APIRouter(prefix="/skills", tags=["Skills"])


@skill_router.get("", response_model=List[SkillResponse])
async def get_all_skills(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all skills - Authenticated users only - JSON response"""
    try:
        skills = SkillFunctions.get_all_skills(limit=limit)
        success_msg = f"Retrieved {len(skills)} skills" + (f" (limit: {limit})" if limit else "")
        logger("SKILL", success_msg, "GET /skills", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills: {str(e)}"
        logger("SKILL", error_msg, "GET /skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.get("/search/{search_term}", response_model=Dict)
async def search_skills(search_term: str, current_user: UserInDB = Depends(get_current_user)):
    """Search skills by name - Authenticated users only - JSON response"""
    try:
        results = SkillFunctions.search_skills_by_name(search_term)
        success_msg = f"Searched skills for '{search_term}', found {len(results)} results"
        logger("SKILL", success_msg, "GET /skills/search/{search_term}", "INFO")
        search_result = {"results": results, "count": len(results)}
        return ResponseSchema.success(search_result, 200)
    except Exception as e:
        error_msg = f"Failed to search skills with term '{search_term}': {str(e)}"
        logger("SKILL", error_msg, "GET /skills/search/{search_term}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.get("/category/{category}", response_model=List[SkillResponse])
async def get_skills_by_category(category: str, current_user: UserInDB = Depends(get_current_user)):
    """Get all skills in a category - Authenticated users only - JSON response"""
    try:
        skills = SkillFunctions.get_skills_by_category(category)
        success_msg = f"Retrieved {len(skills)} skills in category '{category}'"
        logger("SKILL", success_msg, "GET /skills/category/{category}", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills by category '{category}': {str(e)}"
        logger("SKILL", error_msg, "GET /skills/category/{category}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single skill by ID - Authenticated users only - JSON response"""
    try:
        skill = SkillFunctions.get_skill_by_id(skill_id)
        if not skill:
            error_msg = f"Skill {skill_id} not found"
            logger("SKILL", error_msg, "GET /skills/{skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved skill {skill_id}: {skill.get('skill_name', 'unknown')}"
        logger("SKILL", success_msg, "GET /skills/{skill_id}", "INFO")
        return ResponseSchema.success(skill, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skill {skill_id}: {str(e)}"
        logger("SKILL", error_msg, "GET /skills/{skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(skill: SkillCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new skill - Authenticated users only - JSON body accepted"""
    try:
        # Generate UUID if not provided
        skill_id = skill.skill_id or str(uuid.uuid4())
        
        new_skill = SkillFunctions.create_skill(
            skill_name=skill.skill_name,
            skill_category=skill.skill_category,
            description=skill.description
        )
        
        success_msg = f"Created skill {skill_id}: {skill.skill_name} in category {skill.skill_category}"
        logger("SKILL", success_msg, "POST /skills", "INFO")
        return ResponseSchema.success(new_skill, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("SKILL", error_msg, "POST /skills", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create skill: {str(e)}"
        logger("SKILL", error_msg, "POST /skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, skill_update: SkillUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update skill information - Authenticated users only - JSON body accepted"""
    try:
        # Check if skill exists
        existing = SkillFunctions.get_skill_by_id(skill_id)
        if not existing:
            error_msg = f"Skill {skill_id} not found for update"
            logger("SKILL", error_msg, "PUT /skills/{skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = {k: v for k, v in skill_update.dict().items() if v is not None}
        updated_skill = SkillFunctions.update_skill(skill_id, update_data)
        
        success_msg = f"Updated skill {skill_id} with fields: {', '.join(update_data.keys())}"
        logger("SKILL", success_msg, "PUT /skills/{skill_id}", "INFO")
        return ResponseSchema.success(updated_skill, 200)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("SKILL", error_msg, "PUT /skills/{skill_id}", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to update skill {skill_id}: {str(e)}"
        logger("SKILL", error_msg, "PUT /skills/{skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@skill_router.delete("/{skill_id}", status_code=200)
async def delete_skill(skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a skill - Authenticated users only - JSON response"""
    try:
        # Check if skill exists
        existing = SkillFunctions.get_skill_by_id(skill_id)
        if not existing:
            error_msg = f"Skill {skill_id} not found for deletion"
            logger("SKILL", error_msg, "DELETE /skills/{skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        SkillFunctions.delete_skill(skill_id)
        success_msg = f"Skill {skill_id} deleted successfully"
        logger("SKILL", success_msg, "DELETE /skills/{skill_id}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except Exception as e:
        error_msg = f"Failed to delete skill {skill_id}: {str(e)}"
        logger("SKILL", error_msg, "DELETE /skills/{skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
