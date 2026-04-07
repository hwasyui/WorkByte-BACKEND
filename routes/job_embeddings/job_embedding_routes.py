import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional
from functions.schema_model import JobEmbeddingCreate, JobEmbeddingUpdate, JobEmbeddingResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_client_owns, get_client_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_embeddings.job_embedding_functions import JobEmbeddingFunctions
from routes.job_posts.job_post_functions import JobPostFunctions

job_embedding_router = APIRouter(prefix="/job-embeddings", tags=["Job Embeddings"])


@job_embedding_router.get("", response_model=List[JobEmbeddingResponse])
async def get_all_job_embeddings(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job embeddings - Authenticated users only - JSON response"""
    try:
        client = get_client_profile_for_user(current_user)
        job_posts = JobPostFunctions.get_job_posts_by_client_id(client["client_id"])
        job_post_ids = [jp["job_post_id"] for jp in job_posts]
        embeddings = []
        for job_post_id in job_post_ids:
            embedding = JobEmbeddingFunctions.get_job_embedding_by_job_post_id(job_post_id)
            if embedding:
                embeddings.append(embedding)
        success_msg = f"Retrieved {len(embeddings)} job embeddings for client {client['client_id']}"
        logger("JOB_EMBEDDING", success_msg, "GET /job-embeddings", "INFO")
        return ResponseSchema.success(embeddings, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job embeddings: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_embedding_router.get("/{embedding_id}", response_model=JobEmbeddingResponse)
async def get_job_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job embedding by ID - Authenticated users only - JSON response"""
    try:
        embedding = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not embedding:
            error_msg = f"Job embedding {embedding_id} not found"
            logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(embedding["job_post_id"])
        if not job_post:
            error_msg = f"Job post {embedding['job_post_id']} not found"
            logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, job_post["client_id"])
        success_msg = f"Retrieved job embedding {embedding_id}"
        logger("JOB_EMBEDDING", success_msg, "GET /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(embedding, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job embedding {embedding_id}: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_embedding_router.get("/job-post/{job_post_id}", response_model=JobEmbeddingResponse)
async def get_job_embedding_by_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch embedding for a specific job post - Authenticated users only - JSON response"""
    try:
        embedding = JobEmbeddingFunctions.get_job_embedding_by_job_post_id(job_post_id)
        if not embedding:
            error_msg = f"Job embedding for job post {job_post_id} not found"
            logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/job-post/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/job-post/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, job_post["client_id"])
        success_msg = f"Retrieved embedding for job post {job_post_id}"
        logger("JOB_EMBEDDING", success_msg, "GET /job-embeddings/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(embedding, 200)
    except Exception as e:
        error_msg = f"Failed to fetch embedding for job post {job_post_id}: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "GET /job-embeddings/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_embedding_router.post("", response_model=JobEmbeddingResponse, status_code=201)
async def create_job_embedding(embedding: JobEmbeddingCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job embedding - Authenticated users only - JSON body accepted"""
    try:
        job_post = JobPostFunctions.get_job_post_by_id(embedding.job_post_id)
        if not job_post:
            error_msg = f"Job post {embedding.job_post_id} not found"
            logger("JOB_EMBEDDING", error_msg, "POST /job-embeddings", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, job_post["client_id"])
        new_embedding = JobEmbeddingFunctions.create_job_embedding(
            job_post_id=embedding.job_post_id,
            embedding_vector=embedding.embedding_vector,
            source_text=getattr(embedding, 'source_text', None),
            embedding_metadata=getattr(embedding, 'embedding_metadata', None)
        )
        
        success_msg = f"Created job embedding for job post {embedding.job_post_id}"
        logger("JOB_EMBEDDING", success_msg, "POST /job-embeddings", "INFO")
        return ResponseSchema.success(new_embedding, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "POST /job-embeddings", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job embedding: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "POST /job-embeddings", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_embedding_router.put("/{embedding_id}", response_model=JobEmbeddingResponse)
async def update_job_embedding(embedding_id: str, embedding_update: JobEmbeddingUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job embedding information - Authenticated users only"""
    try:
        existing_embedding = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not existing_embedding:
            error_msg = f"Job embedding {embedding_id} not found"
            logger("JOB_EMBEDDING", error_msg, "PUT /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing_embedding["job_post_id"])
        if not job_post:
            error_msg = f"Job post {existing_embedding['job_post_id']} not found"
            logger("JOB_EMBEDDING", error_msg, "PUT /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, job_post["client_id"])
        
        update_data = embedding_update.model_dump(exclude_unset=True)
        updated_embedding = JobEmbeddingFunctions.update_job_embedding(embedding_id, update_data)
        
        success_msg = f"Updated job embedding {embedding_id}"
        logger("JOB_EMBEDDING", success_msg, "PUT /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(updated_embedding, 200)
    except Exception as e:
        error_msg = f"Failed to update job embedding {embedding_id}: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "PUT /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_embedding_router.delete("/{embedding_id}", status_code=200)
async def delete_job_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job embedding - Authenticated users only"""
    try:
        existing_embedding = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not existing_embedding:
            error_msg = f"Job embedding {embedding_id} not found"
            logger("JOB_EMBEDDING", error_msg, "DELETE /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing_embedding["job_post_id"])
        if not job_post:
            error_msg = f"Job post {existing_embedding['job_post_id']} not found"
            logger("JOB_EMBEDDING", error_msg, "DELETE /job-embeddings/{embedding_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, job_post["client_id"])
        
        JobEmbeddingFunctions.delete_job_embedding(embedding_id)
        
        success_msg = f"Deleted job embedding {embedding_id}"
        logger("JOB_EMBEDDING", success_msg, "DELETE /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job embedding {embedding_id}: {str(e)}"
        logger("JOB_EMBEDDING", error_msg, "DELETE /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
