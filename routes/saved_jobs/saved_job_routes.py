import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import SavedJobCreate, SavedJobResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.saved_jobs.saved_job_functions import SavedJobFunctions

saved_job_router = APIRouter(prefix="/saved-jobs", tags=["Saved Jobs"])


@saved_job_router.get("", response_model=List[SavedJobResponse])
async def get_all_saved_jobs(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all saved jobs - Authenticated users only - JSON response"""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        saved_jobs = SavedJobFunctions.get_saved_jobs_by_freelancer_id(freelancer["freelancer_id"], limit=limit)
        success_msg = f"Retrieved {len(saved_jobs)} saved jobs for freelancer {freelancer['freelancer_id']}" + (f" (limit: {limit})" if limit else "")
        logger("SAVED_JOB", success_msg, "GET /saved-jobs", "INFO")
        return ResponseSchema.success(saved_jobs, 200)
    except Exception as e:
        error_msg = f"Failed to fetch saved jobs: {str(e)}"
        logger("SAVED_JOB", error_msg, "GET /saved-jobs", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@saved_job_router.get("/{saved_job_id}", response_model=SavedJobResponse)
async def get_saved_job(saved_job_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single saved job by ID - Authenticated users only - JSON response"""
    try:
        saved_job = SavedJobFunctions.get_saved_job_by_id(saved_job_id)
        if not saved_job:
            error_msg = f"Saved job {saved_job_id} not found"
            logger("SAVED_JOB", error_msg, "GET /saved-jobs/{saved_job_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, saved_job["freelancer_id"])
        success_msg = f"Retrieved saved job {saved_job_id}"
        logger("SAVED_JOB", success_msg, "GET /saved-jobs/{saved_job_id}", "INFO")
        return ResponseSchema.success(saved_job, 200)
    except Exception as e:
        error_msg = f"Failed to fetch saved job {saved_job_id}: {str(e)}"
        logger("SAVED_JOB", error_msg, "GET /saved-jobs/{saved_job_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@saved_job_router.get("/freelancer/{freelancer_id}", response_model=List[SavedJobResponse])
async def get_saved_jobs_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all saved jobs for a specific freelancer - Authenticated users only - JSON response"""
    try:
        assert_freelancer_owns(current_user, freelancer_id)
        saved_jobs = SavedJobFunctions.get_saved_jobs_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(saved_jobs)} saved jobs for freelancer {freelancer_id}"
        logger("SAVED_JOB", success_msg, "GET /saved-jobs/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(saved_jobs, 200)
    except Exception as e:
        error_msg = f"Failed to fetch saved jobs for freelancer {freelancer_id}: {str(e)}"
        logger("SAVED_JOB", error_msg, "GET /saved-jobs/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@saved_job_router.post("", response_model=SavedJobResponse, status_code=201)
async def create_saved_job(saved_job: SavedJobCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new saved job - Authenticated users only - JSON body accepted"""
    try:
        assert_freelancer_owns(current_user, saved_job.freelancer_id)
        new_saved_job = SavedJobFunctions.create_saved_job(
            freelancer_id=saved_job.freelancer_id,
            job_post_id=saved_job.job_post_id,
            notes=getattr(saved_job, 'notes', None)
        )
        
        success_msg = f"Created saved job for freelancer {saved_job.freelancer_id} and job {saved_job.job_post_id}"
        logger("SAVED_JOB", success_msg, "POST /saved-jobs", "INFO")
        return ResponseSchema.success(new_saved_job, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("SAVED_JOB", error_msg, "POST /saved-jobs", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create saved job: {str(e)}"
        logger("SAVED_JOB", error_msg, "POST /saved-jobs", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@saved_job_router.delete("/{saved_job_id}", status_code=200)
async def delete_saved_job(saved_job_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a saved job - Authenticated users only"""
    try:
        existing_saved_job = SavedJobFunctions.get_saved_job_by_id(saved_job_id)
        if not existing_saved_job:
            error_msg = f"Saved job {saved_job_id} not found"
            logger("SAVED_JOB", error_msg, "DELETE /saved-jobs/{saved_job_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_freelancer_owns(current_user, existing_saved_job["freelancer_id"])
        
        SavedJobFunctions.delete_saved_job(saved_job_id)
        
        success_msg = f"Deleted saved job {saved_job_id}"
        logger("SAVED_JOB", success_msg, "DELETE /saved-jobs/{saved_job_id}", "INFO")
        return ResponseSchema.success(None, 200)
    except Exception as e:
        error_msg = f"Failed to delete saved job {saved_job_id}: {str(e)}"
        logger("SAVED_JOB", error_msg, "DELETE /saved-jobs/{saved_job_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
