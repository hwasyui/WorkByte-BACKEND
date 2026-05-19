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
from routes.job_roles.job_role_functions import JobRoleFunctions

job_embedding_router = APIRouter(prefix="/job-embeddings", tags=["Job Embeddings"])


@job_embedding_router.get("", response_model=List[JobEmbeddingResponse])
async def get_all_job_embeddings(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job role embeddings for the authenticated client's job posts"""
    try:
        client = get_client_profile_for_user(current_user)
        job_posts = JobPostFunctions.get_job_posts_by_client_id(client["client_id"])
        job_post_ids = [jp["job_post_id"] for jp in job_posts]
        embeddings = []
        for job_post_id in job_post_ids:
            embeddings.extend(JobEmbeddingFunctions.get_job_embedding_by_job_post_id(job_post_id))
        logger("JOB_EMBEDDING", f"Retrieved {len(embeddings)} role embedding(s) for client {client['client_id']}", "GET /job-embeddings", "INFO")
        return ResponseSchema.success(embeddings, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to fetch job role embeddings: {str(e)}", "GET /job-embeddings", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.get("/{embedding_id}", response_model=JobEmbeddingResponse)
async def get_job_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job role embedding by embedding_id"""
    try:
        embedding = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not embedding:
            return ResponseSchema.error(f"Job role embedding {embedding_id} not found", 404)
        job_post = JobPostFunctions.get_job_post_by_id(embedding["job_post_id"])
        if not job_post:
            return ResponseSchema.error(f"Job post {embedding['job_post_id']} not found", 404)
        assert_client_owns(current_user, job_post["client_id"])
        logger("JOB_EMBEDDING", f"Retrieved job role embedding {embedding_id}", "GET /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(embedding, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to fetch job role embedding {embedding_id}: {str(e)}", "GET /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.get("/job-post/{job_post_id}", response_model=List[JobEmbeddingResponse])
async def get_job_embeddings_by_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all role embeddings for a specific job post"""
    try:
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job_post:
            return ResponseSchema.error(f"Job post {job_post_id} not found", 404)
        assert_client_owns(current_user, job_post["client_id"])
        embeddings = JobEmbeddingFunctions.get_job_embedding_by_job_post_id(job_post_id)
        logger("JOB_EMBEDDING", f"Retrieved {len(embeddings)} role embedding(s) for post {job_post_id}", "GET /job-embeddings/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(embeddings, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to fetch role embeddings for job post {job_post_id}: {str(e)}", "GET /job-embeddings/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.post("", response_model=JobEmbeddingResponse, status_code=201)
async def create_job_embedding(embedding: JobEmbeddingCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job role embedding"""
    try:
        job_role = JobRoleFunctions.get_job_role_by_id(embedding.job_role_id)
        if not job_role:
            return ResponseSchema.error(f"Job role {embedding.job_role_id} not found", 404)
        job_post = JobPostFunctions.get_job_post_by_id(job_role["job_post_id"])
        if not job_post:
            return ResponseSchema.error(f"Job post {job_role['job_post_id']} not found", 404)
        assert_client_owns(current_user, job_post["client_id"])
        new_embedding = JobEmbeddingFunctions.create_job_embedding(
            job_role_id=embedding.job_role_id,
            job_post_id=job_role["job_post_id"],
            embedding_vector=embedding.embedding_vector,
            source_text=getattr(embedding, 'source_text', None),
            embedding_metadata=getattr(embedding, 'embedding_metadata', None),
        )
        logger("JOB_EMBEDDING", f"Created job role embedding for role {embedding.job_role_id}", "POST /job-embeddings", "INFO")
        return ResponseSchema.success(new_embedding, 201)
    except ValueError as e:
        return ResponseSchema.error(f"Validation error: {str(e)}", 400)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to create job role embedding: {str(e)}", "POST /job-embeddings", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.put("/{embedding_id}", response_model=JobEmbeddingResponse)
async def update_job_embedding(embedding_id: str, embedding_update: JobEmbeddingUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job role embedding information"""
    try:
        existing = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not existing:
            return ResponseSchema.error(f"Job role embedding {embedding_id} not found", 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing["job_post_id"])
        if not job_post:
            return ResponseSchema.error(f"Job post {existing['job_post_id']} not found", 404)
        assert_client_owns(current_user, job_post["client_id"])
        update_data = embedding_update.model_dump(exclude_unset=True)
        updated = JobEmbeddingFunctions.update_job_embedding(embedding_id, update_data)
        logger("JOB_EMBEDDING", f"Updated job role embedding {embedding_id}", "PUT /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to update job role embedding {embedding_id}: {str(e)}", "PUT /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.delete("/{embedding_id}", status_code=200)
async def delete_job_embedding(embedding_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job role embedding"""
    try:
        existing = JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        if not existing:
            return ResponseSchema.error(f"Job role embedding {embedding_id} not found", 404)
        job_post = JobPostFunctions.get_job_post_by_id(existing["job_post_id"])
        if not job_post:
            return ResponseSchema.error(f"Job post {existing['job_post_id']} not found", 404)
        assert_client_owns(current_user, job_post["client_id"])
        JobEmbeddingFunctions.delete_job_embedding(embedding_id)
        logger("JOB_EMBEDDING", f"Deleted job role embedding {embedding_id}", "DELETE /job-embeddings/{embedding_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Failed to delete job role embedding {embedding_id}: {str(e)}", "DELETE /job-embeddings/{embedding_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)
