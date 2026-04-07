import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import JobPostCreate, JobPostUpdate, JobPostResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_client_owns, get_client_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_posts.job_post_functions import JobPostFunctions

job_post_router = APIRouter(prefix="/job-posts", tags=["Job Posts"])


@job_post_router.get("", response_model=List[JobPostResponse])
async def get_all_job_posts(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job posts - Authenticated users only - JSON response"""
    try:
        job_posts = JobPostFunctions.get_all_job_posts(limit=limit)
        success_msg = f"Retrieved {len(job_posts)} job posts" + (f" (limit: {limit})" if limit else "")
        logger("JOB_POST", success_msg, "GET /job-posts", "INFO")
        return ResponseSchema.success(job_posts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job posts: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.get("/{job_post_id}", response_model=JobPostResponse)
async def get_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job post by ID - Authenticated users only - JSON response"""
    try:
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "GET /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved job post {job_post_id}"
        logger("JOB_POST", success_msg, "GET /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success(job_post, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.get("/client/{client_id}", response_model=List[JobPostResponse])
async def get_job_posts_by_client(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job posts for a specific client - Authenticated users only - JSON response"""
    try:
        job_posts = JobPostFunctions.get_job_posts_by_client_id(client_id)
        success_msg = f"Retrieved {len(job_posts)} job posts for client {client_id}"
        logger("JOB_POST", success_msg, "GET /job-posts/client/{client_id}", "INFO")
        return ResponseSchema.success(job_posts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job posts for client {client_id}: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts/client/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.post("", response_model=JobPostResponse, status_code=201)
async def create_job_post(job_post: JobPostCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job post - Authenticated users only - JSON body accepted"""
    try:
        job_post_id = job_post.job_post_id or str(uuid.uuid4())
        client = get_client_profile_for_user(current_user)
        if job_post.client_id and str(job_post.client_id) != str(client["client_id"]):
            return ResponseSchema.error("Cannot create a job post for another client", 403)
        
        new_job_post = JobPostFunctions.create_job_post(
            client_id=client["client_id"],
            job_title=job_post.job_title,
            job_description=job_post.job_description,
            project_type=job_post.project_type,
            project_scope=job_post.project_scope,
            estimated_duration=job_post.estimated_duration,
            working_days=job_post.working_days,
            deadline=job_post.deadline,
            experience_level=job_post.experience_level,
            status=job_post.status,
            is_ai_generated=job_post.is_ai_generated
        )
        
        success_msg = f"Created job post {job_post_id} for client {job_post.client_id}"
        logger("JOB_POST", success_msg, "POST /job-posts", "INFO")
        return ResponseSchema.success(new_job_post, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_POST", error_msg, "POST /job-posts", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job post: {str(e)}"
        logger("JOB_POST", error_msg, "POST /job-posts", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.put("/{job_post_id}", response_model=JobPostResponse)
async def update_job_post(job_post_id: str, job_post_update: JobPostUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job post information - Authenticated users only"""
    try:
        existing_job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not existing_job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "PUT /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, existing_job_post["client_id"])
        
        update_data = job_post_update.model_dump(exclude_unset=True)
        updated_job_post = JobPostFunctions.update_job_post(job_post_id, update_data)
        
        success_msg = f"Updated job post {job_post_id}"
        logger("JOB_POST", success_msg, "PUT /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success(updated_job_post, 200)
    except Exception as e:
        error_msg = f"Failed to update job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "PUT /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.delete("/{job_post_id}", status_code=200)
async def delete_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job post - Authenticated users only"""
    try:
        existing_job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not existing_job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "DELETE /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, existing_job_post["client_id"])
        
        JobPostFunctions.delete_job_post(job_post_id)
        
        success_msg = f"Deleted job post {job_post_id}"
        logger("JOB_POST", success_msg, "DELETE /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "DELETE /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
