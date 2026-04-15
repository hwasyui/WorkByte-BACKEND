import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from fastapi import APIRouter, Depends, BackgroundTasks
from typing import Dict
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import get_freelancer_profile_for_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_matching.embedding_manager import upsert_freelancer_embedding

freelancer_embedding_router = APIRouter(
    prefix="/freelancer-embeddings",
    tags=["Freelancer Embeddings"],
)


@freelancer_embedding_router.get("", response_model=Dict)
async def get_my_embedding(current_user: UserInDB = Depends(get_current_user)):
    """Get the embedding metadata for the current freelancer (no raw vector)."""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = freelancer["freelancer_id"]

        db = get_db()
        rows = db.execute_query(
            """SELECT embedding_id, freelancer_id, source_text, embedding_metadata,
                      embedding_dirty, created_at, updated_at,
                      embedding_vector IS NOT NULL AS has_vector
               FROM freelancer_embedding
               WHERE freelancer_id = :fid""",
            {"fid": fid},
        )
        if not rows:
            return ResponseSchema.error("No embedding found for this freelancer", 404)

        row = dict(rows[0])
        for k, v in row.items():
            if hasattr(v, "__class__") and "UUID" in v.__class__.__name__:
                row[k] = str(v)

        logger("FREELANCER_EMBEDDING", f"Retrieved embedding metadata for {fid}", level="INFO")
        return ResponseSchema.success(row, 200)
    except Exception as e:
        logger("FREELANCER_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@freelancer_embedding_router.get("/{freelancer_id}", response_model=Dict)
async def get_freelancer_embedding_by_id(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Get embedding metadata for any freelancer by ID (no raw vector)."""
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT embedding_id, freelancer_id, source_text, embedding_metadata,
                      embedding_dirty, created_at, updated_at,
                      embedding_vector IS NOT NULL AS has_vector
               FROM freelancer_embedding
               WHERE freelancer_id = :fid""",
            {"fid": freelancer_id},
        )
        if not rows:
            return ResponseSchema.error(f"No embedding found for freelancer {freelancer_id}", 404)

        row = dict(rows[0])
        for k, v in row.items():
            if hasattr(v, "__class__") and "UUID" in v.__class__.__name__:
                row[k] = str(v)

        logger("FREELANCER_EMBEDDING", f"Retrieved embedding metadata for {freelancer_id}", level="INFO")
        return ResponseSchema.success(row, 200)
    except Exception as e:
        logger("FREELANCER_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@freelancer_embedding_router.post("/embed", response_model=Dict)
async def embed_my_profile(
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trigger re-embedding of the current freelancer's profile."""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = freelancer["freelancer_id"]
        background_tasks.add_task(upsert_freelancer_embedding, fid)
        logger("FREELANCER_EMBEDDING", f"Queued re-embed for freelancer {fid}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "freelancer_id": fid}, 202)
    except Exception as e:
        logger("FREELANCER_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@freelancer_embedding_router.post("/{freelancer_id}/embed", response_model=Dict)
async def embed_freelancer_by_id(
    freelancer_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    """Trigger re-embedding for a specific freelancer (admin / same user)."""
    try:
        background_tasks.add_task(upsert_freelancer_embedding, freelancer_id)
        logger("FREELANCER_EMBEDDING", f"Queued re-embed for freelancer {freelancer_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "freelancer_id": freelancer_id}, 202)
    except Exception as e:
        logger("FREELANCER_EMBEDDING", f"Error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)
