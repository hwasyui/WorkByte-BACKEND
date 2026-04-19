import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional, Dict
from functions.schema_model import JobFileCreate, JobFileUpdate, JobFileResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_files.job_file_functions import JobFileFunctions
from functions.supabase_client import upload_job_file
from mimetypes import guess_type as guess_mime

job_file_router = APIRouter(prefix="/job-files", tags=["Job Files"])


@job_file_router.get("", response_model=List[JobFileResponse])
async def get_all_job_files(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job files - Authenticated users only - JSON response"""
    try:
        job_files = JobFileFunctions.get_all_job_files(limit=limit)
        success_msg = f"Retrieved {len(job_files)} job files" + (f" (limit: {limit})" if limit else "")
        logger("JOB_FILE", success_msg, "GET /job-files", "INFO")
        return ResponseSchema.success(job_files, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job files: {str(e)}"
        logger("JOB_FILE", error_msg, "GET /job-files", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_file_router.get("/{job_file_id}", response_model=JobFileResponse)
async def get_job_file(job_file_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job file by ID - Authenticated users only - JSON response"""
    try:
        job_file = JobFileFunctions.get_job_file_by_id(job_file_id)
        if not job_file:
            error_msg = f"Job file {job_file_id} not found"
            logger("JOB_FILE", error_msg, "GET /job-files/{job_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved job file {job_file_id}"
        logger("JOB_FILE", success_msg, "GET /job-files/{job_file_id}", "INFO")
        return ResponseSchema.success(job_file, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job file {job_file_id}: {str(e)}"
        logger("JOB_FILE", error_msg, "GET /job-files/{job_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_file_router.get("/job-post/{job_post_id}", response_model=List[JobFileResponse])
async def get_job_files_by_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all files for a specific job post - Authenticated users only - JSON response"""
    try:
        job_files = JobFileFunctions.get_job_files_by_job_post_id(job_post_id)
        success_msg = f"Retrieved {len(job_files)} files for job post {job_post_id}"
        logger("JOB_FILE", success_msg, "GET /job-files/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(job_files, 200)
    except Exception as e:
        error_msg = f"Failed to fetch files for job post {job_post_id}: {str(e)}"
        logger("JOB_FILE", error_msg, "GET /job-files/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_file_router.post("", response_model=List[JobFileResponse], status_code=201)
async def create_job_file(job_file: JobFileCreate = Depends(), current_user: UserInDB = Depends(get_current_user)):
    """Upload one or more files for a job post - Authenticated users only"""
    try:
        if not job_file.files:
            return ResponseSchema.error("At least one file must be uploaded", 400)

        created_files = []
        for upload in job_file.files:
            contents = await upload.read()
            if not contents:
                return ResponseSchema.error(f"Uploaded file '{upload.filename or 'unnamed'}' must not be empty", 400)

            mime_type = upload.content_type or guess_mime(upload.filename or "attachment.bin")[0]
            file_url = upload_job_file(
                job_post_id=job_file.job_post_id,
                file_name=upload.filename or "attachment.bin",
                file_bytes=contents,
                content_type=mime_type,
            )
            created_files.append(
                JobFileFunctions.create_job_file(
                    job_post_id=job_file.job_post_id,
                    file_url=file_url,
                    file_type=mime_type or "application/octet-stream",
                    file_name=upload.filename or "attachment.bin",
                    file_size=len(contents),
                )
            )

        success_msg = f"Created {len(created_files)} job file(s) for job post {job_file.job_post_id}"
        logger("JOB_FILE", success_msg, "POST /job-files", "INFO")
        return ResponseSchema.success(created_files, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_FILE", error_msg, "POST /job-files", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job file: {str(e)}"
        logger("JOB_FILE", error_msg, "POST /job-files", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_file_router.put("/{job_file_id}", response_model=JobFileResponse)
async def update_job_file(job_file_id: str, job_file_update: JobFileUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job file information - Authenticated users only"""
    try:
        existing_job_file = JobFileFunctions.get_job_file_by_id(job_file_id)
        if not existing_job_file:
            error_msg = f"Job file {job_file_id} not found"
            logger("JOB_FILE", error_msg, "PUT /job-files/{job_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = job_file_update.model_dump(exclude_unset=True)
        updated_job_file = JobFileFunctions.update_job_file(job_file_id, update_data)
        
        success_msg = f"Updated job file {job_file_id}"
        logger("JOB_FILE", success_msg, "PUT /job-files/{job_file_id}", "INFO")
        return ResponseSchema.success(updated_job_file, 200)
    except Exception as e:
        error_msg = f"Failed to update job file {job_file_id}: {str(e)}"
        logger("JOB_FILE", error_msg, "PUT /job-files/{job_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_file_router.delete("/{job_file_id}", status_code=200)
async def delete_job_file(job_file_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job file - Authenticated users only"""
    try:
        existing_job_file = JobFileFunctions.get_job_file_by_id(job_file_id)
        if not existing_job_file:
            error_msg = f"Job file {job_file_id} not found"
            logger("JOB_FILE", error_msg, "DELETE /job-files/{job_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        JobFileFunctions.delete_job_file(job_file_id)
        
        success_msg = f"Deleted job file {job_file_id}"
        logger("JOB_FILE", success_msg, "DELETE /job-files/{job_file_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job file {job_file_id}: {str(e)}"
        logger("JOB_FILE", error_msg, "DELETE /job-files/{job_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
