import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerSkillCreate, FreelancerSkillUpdate, FreelancerSkillResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancer_skills.freelancer_skill_functions import FreelancerSkillFunctions

freelancer_skill_router = APIRouter(prefix="/freelancer-skills", tags=["Freelancer Skills"])


@freelancer_skill_router.get("", response_model=List[FreelancerSkillResponse])
async def get_all_freelancer_skills(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all freelancer skills - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        skills = FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer["freelancer_id"])
        success_msg = f"Retrieved {len(skills)} freelancer skills for freelancer {freelancer['freelancer_id']}"
        logger("FREELANCER_SKILL", success_msg, "GET /freelancer-skills", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer skills: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "GET /freelancer-skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.get("/{freelancer_skill_id}", response_model=FreelancerSkillResponse)
async def get_freelancer_skill(freelancer_skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single freelancer skill by ID - Authenticated users only - JSON response"""
    try:
        skill = FreelancerSkillFunctions.get_freelancer_skill_by_id(freelancer_skill_id)
        if not skill:
            error_msg = f"Freelancer skill {freelancer_skill_id} not found"
            logger("FREELANCER_SKILL", error_msg, "GET /freelancer-skills/{freelancer_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved freelancer skill {freelancer_skill_id}"
        logger("FREELANCER_SKILL", success_msg, "GET /freelancer-skills/{freelancer_skill_id}", "INFO")
        return ResponseSchema.success(skill, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer skill {freelancer_skill_id}: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "GET /freelancer-skills/{freelancer_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.get("/freelancer/{freelancer_id}", response_model=List[FreelancerSkillResponse])
async def get_freelancer_skills_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all skills for a specific freelancer - Authenticated users only - JSON response"""
    try:
        skills = FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(skills)} skills for freelancer {freelancer_id}"
        logger("FREELANCER_SKILL", success_msg, "GET /freelancer-skills/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "GET /freelancer-skills/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.post("", response_model=FreelancerSkillResponse, status_code=201)
async def create_freelancer_skill(freelancer_skill: FreelancerSkillCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new freelancer skill - Authenticated users only - JSON body accepted"""
    try:
        freelancer_skill_id = freelancer_skill.freelancer_skill_id or str(uuid.uuid4())
        assert_freelancer_owns(current_user, freelancer_skill.freelancer_id)
        
        new_skill = FreelancerSkillFunctions.create_freelancer_skill(
            freelancer_id=freelancer_skill.freelancer_id,
            skill_id=freelancer_skill.skill_id,
            proficiency_level=freelancer_skill.proficiency_level
        )
        
        success_msg = f"Created freelancer skill {freelancer_skill_id} for freelancer {freelancer_skill.freelancer_id}"
        logger("FREELANCER_SKILL", success_msg, "POST /freelancer-skills", "INFO")
        return ResponseSchema.success(new_skill, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "POST /freelancer-skills", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create freelancer skill: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "POST /freelancer-skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.put("/{freelancer_skill_id}", response_model=FreelancerSkillResponse)
async def update_freelancer_skill(freelancer_skill_id: str, freelancer_skill_update: FreelancerSkillUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update freelancer skill information - Authenticated users only"""
    try:
        existing_skill = FreelancerSkillFunctions.get_freelancer_skill_by_id(freelancer_skill_id)
        if not existing_skill:
            error_msg = f"Freelancer skill {freelancer_skill_id} not found"
            logger("FREELANCER_SKILL", error_msg, "PUT /freelancer-skills/{freelancer_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_skill["freelancer_id"])
        
        update_data = freelancer_skill_update.model_dump(exclude_unset=True)
        updated_skill = FreelancerSkillFunctions.update_freelancer_skill(freelancer_skill_id, update_data)
        
        success_msg = f"Updated freelancer skill {freelancer_skill_id}"
        logger("FREELANCER_SKILL", success_msg, "PUT /freelancer-skills/{freelancer_skill_id}", "INFO")
        return ResponseSchema.success(updated_skill, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer skill {freelancer_skill_id}: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "PUT /freelancer-skills/{freelancer_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.delete("/{freelancer_skill_id}", status_code=200)
async def delete_freelancer_skill(freelancer_skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer skill - Authenticated users only"""
    try:
        existing_skill = FreelancerSkillFunctions.get_freelancer_skill_by_id(freelancer_skill_id)
        if not existing_skill:
            error_msg = f"Freelancer skill {freelancer_skill_id} not found"
            logger("FREELANCER_SKILL", error_msg, "DELETE /freelancer-skills/{freelancer_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_skill["freelancer_id"])
        
        FreelancerSkillFunctions.delete_freelancer_skill(freelancer_skill_id)
        
        success_msg = f"Deleted freelancer skill {freelancer_skill_id}"
        logger("FREELANCER_SKILL", success_msg, "DELETE /freelancer-skills/{freelancer_skill_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer skill {freelancer_skill_id}: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "DELETE /freelancer-skills/{freelancer_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_skill_router.delete("/freelancer/{freelancer_id}/skill/{skill_id}", status_code=200)
async def delete_freelancer_skill_by_ids(freelancer_id: str, skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a freelancer skill by freelancer_id and skill_id - Authenticated users only"""
    try:
        assert_freelancer_owns(current_user, freelancer_id)
        FreelancerSkillFunctions.delete_freelancer_skill_by_freelancer_and_skill(freelancer_id, skill_id)
        
        success_msg = f"Deleted skill {skill_id} from freelancer {freelancer_id}"
        logger("FREELANCER_SKILL", success_msg, "DELETE /freelancer-skills/freelancer/{freelancer_id}/skill/{skill_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer skill: {str(e)}"
        logger("FREELANCER_SKILL", error_msg, "DELETE /freelancer-skills/freelancer/{freelancer_id}/skill/{skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
