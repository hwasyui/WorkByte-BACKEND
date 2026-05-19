import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from fastapi import APIRouter, Depends, BackgroundTasks
from typing import Dict, List
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_matching.embedding_manager import upsert_job_role_embedding, mark_job_dirty

job_embedding_router = APIRouter(
    prefix="/job-embeddings",
    tags=["Job Embeddings"],
)


@job_embedding_router.get("/{job_post_id}", response_model=List[Dict])
async def get_job_role_embeddings(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Get embedding metadata for all roles of a job post (no raw vectors)."""
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT jre.embedding_id, jre.job_role_id, jre.job_post_id,
                      jr.role_title, jre.source_text, jre.embedding_metadata,
                      jre.embedding_dirty, jre.created_at, jre.updated_at,
                      jre.embedding_vector IS NOT NULL AS has_vector
               FROM job_role_embedding jre
               JOIN job_role jr ON jr.job_role_id = jre.job_role_id
               WHERE jre.job_post_id = :jpid
               ORDER BY jr.display_order ASC""",
            {"jpid": job_post_id},
        )
        if not rows:
            return ResponseSchema.error(f"No role embeddings found for job {job_post_id}", 404)

        result = []
        for row in rows:
            d = dict(row)
            for k, v in d.items():
                if hasattr(v, "__class__") and "UUID" in v.__class__.__name__:
                    d[k] = str(v)
            result.append(d)

        logger("JOB_EMBEDDING", f"Retrieved {len(result)} role embedding(s) for job {job_post_id}", level="INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.post("/{job_post_id}/embed", response_model=Dict)
async def embed_job_by_id(
    job_post_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trigger re-embedding for all roles of a job post."""
    try:
        background_tasks.add_task(mark_job_dirty, job_post_id)
        logger("JOB_EMBEDDING", f"Queued re-embed for all roles of job {job_post_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued for all roles", "job_post_id": job_post_id}, 202)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.post("/role/{job_role_id}/embed", response_model=Dict)
async def embed_job_role_by_id(
    job_role_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trigger re-embedding for a single job role."""
    try:
        background_tasks.add_task(upsert_job_role_embedding, job_role_id)
        logger("JOB_EMBEDDING", f"Queued re-embed for role {job_role_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "job_role_id": job_role_id}, 202)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)
