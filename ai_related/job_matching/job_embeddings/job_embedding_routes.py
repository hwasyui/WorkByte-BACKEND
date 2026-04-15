import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from fastapi import APIRouter, Depends, BackgroundTasks
from typing import Dict
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_matching.embedding_manager import upsert_job_embedding

job_embedding_router = APIRouter(
    prefix="/job-embeddings",
    tags=["Job Embeddings"],
)


@job_embedding_router.get("/{job_post_id}", response_model=Dict)
async def get_job_embedding_by_id(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Get embedding metadata for a job post (no raw vector)."""
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT embedding_id, job_post_id, source_text, embedding_metadata,
                      embedding_dirty, created_at, updated_at,
                      embedding_vector IS NOT NULL AS has_vector
               FROM job_embedding
               WHERE job_post_id = :jpid""",
            {"jpid": job_post_id},
        )
        if not rows:
            return ResponseSchema.error(f"No embedding found for job {job_post_id}", 404)

        row = dict(rows[0])
        for k, v in row.items():
            if hasattr(v, "__class__") and "UUID" in v.__class__.__name__:
                row[k] = str(v)

        logger("JOB_EMBEDDING", f"Retrieved embedding metadata for job {job_post_id}", level="INFO")
        return ResponseSchema.success(row, 200)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@job_embedding_router.post("/{job_post_id}/embed", response_model=Dict)
async def embed_job_by_id(
    job_post_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trigger re-embedding for a specific job post."""
    try:
        background_tasks.add_task(upsert_job_embedding, job_post_id)
        logger("JOB_EMBEDDING", f"Queued re-embed for job {job_post_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "job_post_id": job_post_id}, 202)
    except Exception as e:
        logger("JOB_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)
