import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import JobRoleSkillCreate, JobRoleSkillUpdate, JobRoleSkillResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_client_owns
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_posts.job_post_functions import JobPostFunctions
from routes.job_role_skills.job_role_skill_functions import JobRoleSkillFunctions
from routes.job_roles.job_role_functions import JobRoleFunctions

job_role_skill_router = APIRouter(prefix="/job-role-skills", tags=["Job Role Skills"])


@job_role_skill_router.get("", response_model=List[JobRoleSkillResponse])
async def get_all_job_role_skills(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job role skills - Authenticated users only - JSON response"""
    try:
        job_role_skills = JobRoleSkillFunctions.get_all_job_role_skills(limit=limit)
        success_msg = f"Retrieved {len(job_role_skills)} job role skills" + (f" (limit: {limit})" if limit else "")
        logger("JOB_ROLE_SKILL", success_msg, "GET /job-role-skills", "INFO")
        return ResponseSchema.success(job_role_skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job role skills: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "GET /job-role-skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_skill_router.get("/{job_role_skill_id}", response_model=JobRoleSkillResponse)
async def get_job_role_skill(job_role_skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job role skill by ID - Authenticated users only - JSON response"""
    try:
        job_role_skill = JobRoleSkillFunctions.get_job_role_skill_by_id(job_role_skill_id)
        if not job_role_skill:
            error_msg = f"Job role skill {job_role_skill_id} not found"
            logger("JOB_ROLE_SKILL", error_msg, "GET /job-role-skills/{job_role_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_role = JobRoleFunctions.get_job_role_by_id(job_role_skill["job_role_id"])
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        success_msg = f"Retrieved job role skill {job_role_skill_id}"
        logger("JOB_ROLE_SKILL", success_msg, "GET /job-role-skills/{job_role_skill_id}", "INFO")
        return ResponseSchema.success(job_role_skill, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job role skill {job_role_skill_id}: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "GET /job-role-skills/{job_role_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_skill_router.get("/job-role/{job_role_id}", response_model=List[JobRoleSkillResponse])
async def get_job_role_skills_by_job_role(job_role_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all skills for a specific job role - Authenticated users only - JSON response"""
    try:
        job_role = JobRoleFunctions.get_job_role_by_id(job_role_id)
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        job_role_skills = JobRoleSkillFunctions.get_job_role_skills_by_job_role_id(job_role_id)
        success_msg = f"Retrieved {len(job_role_skills)} skills for job role {job_role_id}"
        logger("JOB_ROLE_SKILL", success_msg, "GET /job-role-skills/job-role/{job_role_id}", "INFO")
        return ResponseSchema.success(job_role_skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills for job role {job_role_id}: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "GET /job-role-skills/job-role/{job_role_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_skill_router.post("", response_model=JobRoleSkillResponse, status_code=201)
async def create_job_role_skill(job_role_skill: JobRoleSkillCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job role skill - Authenticated users only - JSON body accepted"""
    try:
        job_role_skill_id = job_role_skill.job_role_skill_id or str(uuid.uuid4())
        job_role = JobRoleFunctions.get_job_role_by_id(job_role_skill.job_role_id)
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        
        new_job_role_skill = JobRoleSkillFunctions.create_job_role_skill(
            job_role_id=job_role_skill.job_role_id,
            skill_id=job_role_skill.skill_id,
            is_required=job_role_skill.is_required,
            importance_level=job_role_skill.importance_level
        )
        
        success_msg = f"Created job role skill {job_role_skill_id} for job role {job_role_skill.job_role_id}"
        logger("JOB_ROLE_SKILL", success_msg, "POST /job-role-skills", "INFO")
        return ResponseSchema.success(new_job_role_skill, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "POST /job-role-skills", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job role skill: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "POST /job-role-skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_skill_router.put("/{job_role_skill_id}", response_model=JobRoleSkillResponse)
async def update_job_role_skill(job_role_skill_id: str, job_role_skill_update: JobRoleSkillUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job role skill information - Authenticated users only"""
    try:
        existing_job_role_skill = JobRoleSkillFunctions.get_job_role_skill_by_id(job_role_skill_id)
        if not existing_job_role_skill:
            error_msg = f"Job role skill {job_role_skill_id} not found"
            logger("JOB_ROLE_SKILL", error_msg, "PUT /job-role-skills/{job_role_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_role = JobRoleFunctions.get_job_role_by_id(existing_job_role_skill["job_role_id"])
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        
        update_data = job_role_skill_update.model_dump(exclude_unset=True)
        updated_job_role_skill = JobRoleSkillFunctions.update_job_role_skill(job_role_skill_id, update_data)
        
        success_msg = f"Updated job role skill {job_role_skill_id}"
        logger("JOB_ROLE_SKILL", success_msg, "PUT /job-role-skills/{job_role_skill_id}", "INFO")
        return ResponseSchema.success(updated_job_role_skill, 200)
    except Exception as e:
        error_msg = f"Failed to update job role skill {job_role_skill_id}: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "PUT /job-role-skills/{job_role_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_skill_router.delete("/{job_role_skill_id}", status_code=200)
async def delete_job_role_skill(job_role_skill_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job role skill - Authenticated users only"""
    try:
        existing_job_role_skill = JobRoleSkillFunctions.get_job_role_skill_by_id(job_role_skill_id)
        if not existing_job_role_skill:
            error_msg = f"Job role skill {job_role_skill_id} not found"
            logger("JOB_ROLE_SKILL", error_msg, "DELETE /job-role-skills/{job_role_skill_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_role = JobRoleFunctions.get_job_role_by_id(existing_job_role_skill["job_role_id"])
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        
        JobRoleSkillFunctions.delete_job_role_skill(job_role_skill_id)
        
        success_msg = f"Deleted job role skill {job_role_skill_id}"
        logger("JOB_ROLE_SKILL", success_msg, "DELETE /job-role-skills/{job_role_skill_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job role skill {job_role_skill_id}: {str(e)}"
        logger("JOB_ROLE_SKILL", error_msg, "DELETE /job-role-skills/{job_role_skill_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
