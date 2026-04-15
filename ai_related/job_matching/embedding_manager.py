"""
Handles embedding lifecycle — marking records dirty after profile/job mutations,
and upserting new vectors into the DB (called by route handlers and the sweep worker).
"""

import json
import uuid
from typing import Optional

from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.embedding_service import get_embedding
from ai_related.job_matching.source_text_builder import (
    build_freelancer_source_text,
    build_job_source_text,
    build_contract_source_text,
)


def mark_freelancer_dirty(freelancer_id: str) -> None:
    """
    Flag a freelancer's embedding as stale so the sweep worker picks it up.
    No-op if no embedding row exists yet. Swallows exceptions so a dirty-flag
    failure never breaks the calling mutation.
    """
    try:
        db = get_db()
        db.execute_query(
            "UPDATE freelancer_embedding SET embedding_dirty = TRUE WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )
        logger("EMBEDDING_MANAGER", f"Marked dirty | freelancer_id={freelancer_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark freelancer dirty | freelancer_id={freelancer_id} | error={e}", level="ERROR")


def mark_job_dirty(job_post_id: str) -> None:
    """
    Flag a job's embedding as stale so the sweep worker picks it up.
    No-op if no embedding row exists yet. Swallows exceptions.
    """
    try:
        db = get_db()
        db.execute_query(
            "UPDATE job_embedding SET embedding_dirty = TRUE WHERE job_post_id = :jpid",
            {"jpid": job_post_id},
        )
        logger("EMBEDDING_MANAGER", f"Marked dirty | job_post_id={job_post_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark job dirty | job_post_id={job_post_id} | error={e}", level="ERROR")


def get_job_post_id_from_role(job_role_id: str) -> Optional[str]:
    """
    Look up the parent job_post_id for a given job_role_id.

    Args:
        job_role_id: UUID string of the job role.

    Returns:
        The job_post_id string if found, None otherwise.
    """
    try:
        db = get_db()
        rows = db.execute_query(
            "SELECT job_post_id FROM job_role WHERE job_role_id = :jrid",
            {"jrid": job_role_id},
        )
        if rows:
            job_post_id = str(rows[0]["job_post_id"])
            logger("EMBEDDING_MANAGER", f"Resolved job_role_id={job_role_id} → job_post_id={job_post_id}", level="DEBUG")
            return job_post_id
        logger("EMBEDDING_MANAGER", f"job_role_id={job_role_id} has no parent job_post — skipping dirty mark", level="WARNING")
        return None
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not resolve job_post_id from role {job_role_id}: {e}", level="ERROR")
        return None


def mark_job_dirty_by_role(job_role_id: str) -> None:
    """
    Mark the parent job post embedding as dirty given a job_role_id.

    Resolves the job_post_id from the role and calls mark_job_dirty. No-op if the role has no parent.

    Args:
        job_role_id: UUID string of the job role whose parent job should be marked dirty.
    """
    job_post_id = get_job_post_id_from_role(job_role_id)
    if job_post_id:
        mark_job_dirty(job_post_id)


def mark_contract_dirty(contract_id: str) -> None:
    """
    Flag a contract's embedding as stale so the sweep worker picks it up.
    Called when a contract is completed or when a rating is added/updated
    (review text feeds into the contract source text).
    No-op if no embedding row exists yet. Swallows exceptions.
    """
    try:
        db = get_db()
        db.execute_query(
            "UPDATE contract_embedding SET embedding_dirty = TRUE WHERE contract_id = :cid",
            {"cid": contract_id},
        )
        logger("EMBEDDING_MANAGER", f"Marked dirty | contract_id={contract_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark contract dirty | contract_id={contract_id} | error={e}", level="ERROR")


def _vector_to_pg(vector: list[float]) -> str:
    """Format a float list as a pgvector literal: '[0.1,0.2,...]'"""
    return "[" + ",".join(str(v) for v in vector) + "]"


async def upsert_freelancer_embedding(freelancer_id: str) -> dict:
    """Build source text, get an embedding vector, and upsert into freelancer_embedding."""
    logger("EMBEDDING_MANAGER", f"Upserting freelancer embedding | freelancer_id={freelancer_id}", level="INFO")
    try:
        source_text = build_freelancer_source_text(freelancer_id)
        if not source_text:
            logger("EMBEDDING_MANAGER", f"No source text for freelancer {freelancer_id} — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "no_source_text"}

        logger("EMBEDDING_MANAGER", f"Requesting embedding vector | freelancer_id={freelancer_id} | source_chars={len(source_text)}", level="DEBUG")
        vector = await get_embedding(source_text)
        vector_pg = _vector_to_pg(vector)
        metadata = json.dumps({"dim": len(vector)})
        logger("EMBEDDING_MANAGER", f"Embedding vector received | freelancer_id={freelancer_id} | dim={len(vector)}", level="DEBUG")

        db = get_db()
        existing = db.execute_query(
            "SELECT embedding_id FROM freelancer_embedding WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )

        if existing:
            logger("EMBEDDING_MANAGER", f"Updating existing embedding record | freelancer_id={freelancer_id}", level="DEBUG")
            db.execute_query(
                """UPDATE freelancer_embedding
                   SET embedding_vector   = CAST(:vec AS vector),
                       source_text        = :txt,
                       embedding_metadata = CAST(:meta AS jsonb),
                       embedding_dirty    = FALSE,
                       updated_at         = NOW()
                   WHERE freelancer_id = :fid""",
                {"vec": vector_pg, "txt": source_text, "meta": metadata, "fid": freelancer_id},
            )
            logger("EMBEDDING_MANAGER", f"Freelancer embedding UPDATED | freelancer_id={freelancer_id} | dim={len(vector)}", level="INFO")
            return {"status": "updated", "freelancer_id": freelancer_id, "dim": len(vector)}
        else:
            embedding_id = str(uuid.uuid4())
            logger("EMBEDDING_MANAGER", f"Creating new embedding record | freelancer_id={freelancer_id} | embedding_id={embedding_id}", level="DEBUG")
            db.execute_query(
                """INSERT INTO freelancer_embedding
                     (embedding_id, freelancer_id, embedding_vector, source_text, embedding_metadata, embedding_dirty)
                   VALUES (:eid, :fid, CAST(:vec AS vector), :txt, CAST(:meta AS jsonb), FALSE)""",
                {"eid": embedding_id, "fid": freelancer_id,
                 "vec": vector_pg, "txt": source_text, "meta": metadata},
            )
            logger("EMBEDDING_MANAGER", f"Freelancer embedding CREATED | freelancer_id={freelancer_id} | dim={len(vector)}", level="INFO")
            return {"status": "created", "freelancer_id": freelancer_id, "dim": len(vector)}

    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Error upserting freelancer embedding | freelancer_id={freelancer_id} | error={e}", level="ERROR")
        raise


async def upsert_job_embedding(job_post_id: str) -> dict:
    """Build source text, get an embedding vector, and upsert into job_embedding."""
    logger("EMBEDDING_MANAGER", f"Upserting job embedding | job_post_id={job_post_id}", level="INFO")
    try:
        source_text = build_job_source_text(job_post_id)
        if not source_text:
            logger("EMBEDDING_MANAGER", f"No source text for job {job_post_id} — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "no_source_text"}

        logger("EMBEDDING_MANAGER", f"Requesting embedding vector | job_post_id={job_post_id} | source_chars={len(source_text)}", level="DEBUG")
        vector = await get_embedding(source_text)
        vector_pg = _vector_to_pg(vector)
        metadata = json.dumps({"dim": len(vector)})
        logger("EMBEDDING_MANAGER", f"Embedding vector received | job_post_id={job_post_id} | dim={len(vector)}", level="DEBUG")

        db = get_db()
        existing = db.execute_query(
            "SELECT embedding_id FROM job_embedding WHERE job_post_id = :jpid",
            {"jpid": job_post_id},
        )

        if existing:
            logger("EMBEDDING_MANAGER", f"Updating existing embedding record | job_post_id={job_post_id}", level="DEBUG")
            db.execute_query(
                """UPDATE job_embedding
                   SET embedding_vector   = CAST(:vec AS vector),
                       source_text        = :txt,
                       embedding_metadata = CAST(:meta AS jsonb),
                       embedding_dirty    = FALSE,
                       updated_at         = NOW()
                   WHERE job_post_id = :jpid""",
                {"vec": vector_pg, "txt": source_text, "meta": metadata, "jpid": job_post_id},
            )
            logger("EMBEDDING_MANAGER", f"Job embedding UPDATED | job_post_id={job_post_id} | dim={len(vector)}", level="INFO")
            return {"status": "updated", "job_post_id": job_post_id, "dim": len(vector)}
        else:
            embedding_id = str(uuid.uuid4())
            logger("EMBEDDING_MANAGER", f"Creating new embedding record | job_post_id={job_post_id} | embedding_id={embedding_id}", level="DEBUG")
            db.execute_query(
                """INSERT INTO job_embedding
                     (embedding_id, job_post_id, embedding_vector, source_text, embedding_metadata, embedding_dirty)
                   VALUES (:eid, :jpid, CAST(:vec AS vector), :txt, CAST(:meta AS jsonb), FALSE)""",
                {"eid": embedding_id, "jpid": job_post_id,
                 "vec": vector_pg, "txt": source_text, "meta": metadata},
            )
            logger("EMBEDDING_MANAGER", f"Job embedding CREATED | job_post_id={job_post_id} | dim={len(vector)}", level="INFO")
            return {"status": "created", "job_post_id": job_post_id, "dim": len(vector)}

    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Error upserting job embedding | job_post_id={job_post_id} | error={e}", level="ERROR")
        raise


async def upsert_contract_embedding(contract_id: str) -> dict:
    """
    Build source text for a completed contract, generate an embedding, and upsert
    into contract_embedding. freelancer_id is denormalised from the contract row
    so we can do fast per-freelancer lookups.
    """
    logger("EMBEDDING_MANAGER", f"Upserting contract embedding | contract_id={contract_id}", level="INFO")
    try:
        source_text = build_contract_source_text(contract_id)
        if not source_text:
            logger("EMBEDDING_MANAGER", f"No source text for contract {contract_id} — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "no_source_text"}

        db = get_db()
        fid_rows = db.execute_query(
            "SELECT freelancer_id FROM contract WHERE contract_id = :cid",
            {"cid": contract_id},
        )
        if not fid_rows:
            logger("EMBEDDING_MANAGER", f"Contract {contract_id} not found — cannot upsert embedding", level="WARNING")
            return {"status": "skipped", "reason": "contract_not_found"}
        freelancer_id = str(fid_rows[0]["freelancer_id"])

        logger("EMBEDDING_MANAGER", f"Requesting embedding vector | contract_id={contract_id} | source_chars={len(source_text)}", level="DEBUG")
        vector = await get_embedding(source_text)
        vector_pg = _vector_to_pg(vector)
        metadata = json.dumps({"dim": len(vector)})
        logger("EMBEDDING_MANAGER", f"Embedding vector received | contract_id={contract_id} | dim={len(vector)}", level="DEBUG")

        existing = db.execute_query(
            "SELECT embedding_id FROM contract_embedding WHERE contract_id = :cid",
            {"cid": contract_id},
        )

        if existing:
            logger("EMBEDDING_MANAGER", f"Updating existing embedding record | contract_id={contract_id}", level="DEBUG")
            db.execute_query(
                """UPDATE contract_embedding
                   SET embedding_vector   = CAST(:vec AS vector),
                       source_text        = :txt,
                       embedding_metadata = CAST(:meta AS jsonb),
                       embedding_dirty    = FALSE,
                       updated_at         = NOW()
                   WHERE contract_id = :cid""",
                {"vec": vector_pg, "txt": source_text, "meta": metadata, "cid": contract_id},
            )
            logger("EMBEDDING_MANAGER", f"Contract embedding UPDATED | contract_id={contract_id} | dim={len(vector)}", level="INFO")
            return {"status": "updated", "contract_id": contract_id, "dim": len(vector)}
        else:
            embedding_id = str(uuid.uuid4())
            logger("EMBEDDING_MANAGER", f"Creating new embedding record | contract_id={contract_id} | embedding_id={embedding_id}", level="DEBUG")
            db.execute_query(
                """INSERT INTO contract_embedding
                     (embedding_id, contract_id, freelancer_id, embedding_vector,
                      source_text, embedding_metadata, embedding_dirty)
                   VALUES (:eid, :cid, :fid, CAST(:vec AS vector), :txt, CAST(:meta AS jsonb), FALSE)""",
                {
                    "eid": embedding_id,
                    "cid": contract_id,
                    "fid": freelancer_id,
                    "vec": vector_pg,
                    "txt": source_text,
                    "meta": metadata,
                },
            )
            logger("EMBEDDING_MANAGER", f"Contract embedding CREATED | contract_id={contract_id} | dim={len(vector)}", level="INFO")
            return {"status": "created", "contract_id": contract_id, "dim": len(vector)}

    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Error upserting contract embedding | contract_id={contract_id} | error={e}", level="ERROR")
        raise
