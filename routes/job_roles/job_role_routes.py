import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import JobRoleCreate, JobRoleUpdate, JobRoleResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_client_owns, get_client_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_posts.job_post_functions import JobPostFunctions
from routes.job_roles.job_role_functions import JobRoleFunctions

job_role_router = APIRouter(prefix="/job-roles", tags=["Job Roles"])


@job_role_router.get("", response_model=List[JobRoleResponse])
async def get_all_job_roles(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job roles - Authenticated users only - JSON response"""
    try:
        client = get_client_profile_for_user(current_user)
        job_roles = JobRoleFunctions.get_job_roles_by_client_id(client["client_id"])
        success_msg = f"Retrieved {len(job_roles)} job roles for client {client['client_id']}"
        logger("JOB_ROLE", success_msg, "GET /job-roles", "INFO")
        return ResponseSchema.success(job_roles, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job roles: {str(e)}"
        logger("JOB_ROLE", error_msg, "GET /job-roles", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_router.get("/{job_role_id}", response_model=JobRoleResponse)
async def get_job_role(job_role_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job role by ID - Authenticated users only - JSON response"""
    try:
        job_role = JobRoleFunctions.get_job_role_by_id(job_role_id)
        if not job_role:
            error_msg = f"Job role {job_role_id} not found"
            logger("JOB_ROLE", error_msg, "GET /job-roles/{job_role_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        success_msg = f"Retrieved job role {job_role_id}"
        logger("JOB_ROLE", success_msg, "GET /job-roles/{job_role_id}", "INFO")
        return ResponseSchema.success(job_role, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job role {job_role_id}: {str(e)}"
        logger("JOB_ROLE", error_msg, "GET /job-roles/{job_role_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_router.get("/job-post/{job_post_id}", response_model=List[JobRoleResponse])
async def get_job_roles_by_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job roles for a specific job post - Authenticated users only - JSON response"""
    try:
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        assert_client_owns(current_user, job_post["client_id"])
        job_roles = JobRoleFunctions.get_job_roles_by_job_post_id(job_post_id)
        success_msg = f"Retrieved {len(job_roles)} job roles for job post {job_post_id}"
        logger("JOB_ROLE", success_msg, "GET /job-roles/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(job_roles, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job roles for job post {job_post_id}: {str(e)}"
        logger("JOB_ROLE", error_msg, "GET /job-roles/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_router.post("", response_model=JobRoleResponse, status_code=201)
async def create_job_role(job_role: JobRoleCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job role - Authenticated users only - JSON body accepted"""
    try:
        job_role_id = job_role.job_role_id or str(uuid.uuid4())
        job_post = JobPostFunctions.get_job_post_by_id(job_role.job_post_id)
        assert_client_owns(current_user, job_post["client_id"])
        
        new_job_role = JobRoleFunctions.create_job_role(
            job_post_id=job_role.job_post_id,
            role_title=job_role.role_title,
            budget_type=job_role.budget_type,
            role_budget=job_role.role_budget,
            budget_currency=job_role.budget_currency,
            role_description=job_role.role_description,
            positions_available=job_role.positions_available,
            is_required=job_role.is_required,
            display_order=job_role.display_order
        )
        
        success_msg = f"Created job role {job_role_id} for job post {job_role.job_post_id}"
        logger("JOB_ROLE", success_msg, "POST /job-roles", "INFO")
        return ResponseSchema.success(new_job_role, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_ROLE", error_msg, "POST /job-roles", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job role: {str(e)}"
        logger("JOB_ROLE", error_msg, "POST /job-roles", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_router.put("/{job_role_id}", response_model=JobRoleResponse)
async def update_job_role(job_role_id: str, job_role_update: JobRoleUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job role information - Authenticated users only"""
    try:
        existing_job_role = JobRoleFunctions.get_job_role_by_id(job_role_id)
        if not existing_job_role:
            error_msg = f"Job role {job_role_id} not found"
            logger("JOB_ROLE", error_msg, "PUT /job-roles/{job_role_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing_job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        
        update_data = job_role_update.model_dump(exclude_unset=True)
        updated_job_role = JobRoleFunctions.update_job_role(job_role_id, update_data)
        
        success_msg = f"Updated job role {job_role_id}"
        logger("JOB_ROLE", success_msg, "PUT /job-roles/{job_role_id}", "INFO")
        return ResponseSchema.success(updated_job_role, 200)
    except Exception as e:
        error_msg = f"Failed to update job role {job_role_id}: {str(e)}"
        logger("JOB_ROLE", error_msg, "PUT /job-roles/{job_role_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_role_router.delete("/{job_role_id}", status_code=200)
async def delete_job_role(job_role_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job role - Authenticated users only"""
    try:
        existing_job_role = JobRoleFunctions.get_job_role_by_id(job_role_id)
        if not existing_job_role:
            error_msg = f"Job role {job_role_id} not found"
            logger("JOB_ROLE", error_msg, "DELETE /job-roles/{job_role_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing_job_role["job_post_id"])
        assert_client_owns(current_user, job_post["client_id"])
        
        JobRoleFunctions.delete_job_role(job_role_id)
        
        success_msg = f"Deleted job role {job_role_id}"
        logger("JOB_ROLE", success_msg, "DELETE /job-roles/{job_role_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job role {job_role_id}: {str(e)}"
        logger("JOB_ROLE", error_msg, "DELETE /job-roles/{job_role_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
