"""
Handles embedding lifecycle — marking records dirty after profile/job mutations,
and upserting new vectors into the DB (called by route handlers and the sweep worker).
"""

import asyncio
import json
import time
import uuid
from typing import Optional

from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.embedding_service import get_embedding
from ai_related.job_matching.source_text_builder import (
    build_freelancer_source_text,
    build_job_role_source_text,
    build_contract_source_text,
    build_portfolio_source_text,
)


_THRESHOLD_FREELANCER  = 500
_THRESHOLD_JOB         = 1000
_THRESHOLD_CONTRACT    = 2000
_THRESHOLD_TTL_SECONDS = 7200  # re-check every 2 hours

_cached_immediate: bool | None = None
_cache_loaded_at: float = 0.0  # epoch seconds; 0 means never loaded


def _should_embed_immediately() -> bool:
    """
    Return True when all entity counts are below their thresholds.
    Result is cached for 2 hours so the DB is queried at most once per TTL window,
    starting from the first call after backend startup.
    """
    global _cached_immediate, _cache_loaded_at

    now = time.monotonic()
    if _cached_immediate is not None and (now - _cache_loaded_at) < _THRESHOLD_TTL_SECONDS:
        return _cached_immediate

    try:
        db = get_db()
        result = db.execute_query(
            """SELECT
                 (SELECT COUNT(*) FROM freelancer) AS freelancer_count,
                 (SELECT COUNT(*) FROM job_post)   AS job_count,
                 (SELECT COUNT(*) FROM contract)   AS contract_count"""
        )
        if not result:
            _cached_immediate = False
            _cache_loaded_at = now
            return False
        row = result[0]
        below = (
            int(row["freelancer_count"]) < _THRESHOLD_FREELANCER
            and int(row["job_count"]) < _THRESHOLD_JOB
            and int(row["contract_count"]) < _THRESHOLD_CONTRACT
        )
        _cached_immediate = below
        _cache_loaded_at = now
        logger(
            "EMBEDDING_MANAGER",
            f"Threshold cache refreshed | freelancers={row['freelancer_count']}/{_THRESHOLD_FREELANCER} "
            f"| jobs={row['job_count']}/{_THRESHOLD_JOB} "
            f"| contracts={row['contract_count']}/{_THRESHOLD_CONTRACT} "
            f"| mode={'immediate' if below else 'sweep'} | next_check_in=2h",
            level="INFO",
        )
        return below
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Threshold check failed — defaulting to sweep mode | error={e}", level="WARNING")
        return False


def _schedule_immediate(coro) -> None:
    """Fire-and-forget a coroutine on the running event loop. Falls back gracefully if no loop is available."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        logger("EMBEDDING_MANAGER", "No running event loop — cannot schedule immediate embedding, will rely on sweep", level="WARNING")
        coro.close()


def mark_freelancer_dirty(freelancer_id: str) -> None:
    """
    Flag a freelancer's embedding as stale, or embed immediately if below the size threshold.
    Swallows exceptions so a dirty-flag failure never breaks the calling mutation.
    """
    try:
        if _should_embed_immediately():
            logger("EMBEDDING_MANAGER", f"Immediate embed scheduled | freelancer_id={freelancer_id}", level="INFO")
            _schedule_immediate(upsert_freelancer_embedding(freelancer_id))
            return
        db = get_db()
        db.execute_query(
            "UPDATE freelancer_embedding SET embedding_dirty = TRUE WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )
        logger("EMBEDDING_MANAGER", f"Marked dirty | freelancer_id={freelancer_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark freelancer dirty | freelancer_id={freelancer_id} | error={e}", level="ERROR")


def _upsert_role_dirty_row(db, job_role_id: str, job_post_id: str) -> None:
    """Update an existing role embedding row to dirty, or insert a new dirty row if none exists."""
    existing = db.execute_query(
        "SELECT embedding_id FROM job_role_embedding WHERE job_role_id = :jrid",
        {"jrid": job_role_id},
    )
    if existing:
        db.execute_query(
            "UPDATE job_role_embedding SET embedding_dirty = TRUE WHERE job_role_id = :jrid",
            {"jrid": job_role_id},
        )
    else:
        db.execute_query(
            """INSERT INTO job_role_embedding (embedding_id, job_role_id, job_post_id, embedding_dirty)
               VALUES (:eid, :jrid, :jpid, TRUE)""",
            {"eid": str(uuid.uuid4()), "jrid": job_role_id, "jpid": job_post_id},
        )


def mark_job_dirty(job_post_id: str) -> None:
    """
    Flag all role embeddings for a job post as stale, creating dirty rows for any
    roles that don't have an embedding row yet. Embeds immediately if below threshold.
    Swallows exceptions so a dirty-flag failure never breaks the calling mutation.
    """
    try:
        db = get_db()
        role_rows = db.execute_query(
            "SELECT job_role_id FROM job_role WHERE job_post_id = :jpid",
            {"jpid": job_post_id},
        )
        if not role_rows:
            logger("EMBEDDING_MANAGER", f"No roles found for job_post_id={job_post_id} — nothing to dirty", level="DEBUG")
            return

        for role in role_rows:
            jrid = str(role["job_role_id"])
            if _should_embed_immediately():
                logger("EMBEDDING_MANAGER", f"Immediate embed scheduled | job_role_id={jrid}", level="INFO")
                _schedule_immediate(upsert_job_role_embedding(jrid))
            else:
                _upsert_role_dirty_row(db, jrid, job_post_id)

        logger("EMBEDDING_MANAGER", f"Marked dirty | job_post_id={job_post_id} | roles={len(role_rows)}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark job dirty | job_post_id={job_post_id} | error={e}", level="ERROR")


def mark_job_dirty_by_role(job_role_id: str) -> None:
    """
    Flag a single role embedding as stale, or embed it immediately if below threshold.
    Creates a dirty row if the role has no embedding row yet.
    Swallows exceptions.
    """
    try:
        db = get_db()
        role_rows = db.execute_query(
            "SELECT job_post_id FROM job_role WHERE job_role_id = :jrid",
            {"jrid": job_role_id},
        )
        if not role_rows:
            logger("EMBEDDING_MANAGER", f"job_role_id={job_role_id} not found — skipping dirty mark", level="WARNING")
            return
        job_post_id = str(role_rows[0]["job_post_id"])

        if _should_embed_immediately():
            logger("EMBEDDING_MANAGER", f"Immediate embed scheduled | job_role_id={job_role_id}", level="INFO")
            _schedule_immediate(upsert_job_role_embedding(job_role_id))
        else:
            _upsert_role_dirty_row(db, job_role_id, job_post_id)
            logger("EMBEDDING_MANAGER", f"Marked dirty | job_role_id={job_role_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark role dirty | job_role_id={job_role_id} | error={e}", level="ERROR")


def mark_contract_dirty(contract_id: str) -> None:
    """
    Flag a contract's embedding as stale, or embed immediately if below the size threshold.
    Called when a contract is completed or when a rating is added/updated.
    Swallows exceptions.
    """
    try:
        if _should_embed_immediately():
            logger("EMBEDDING_MANAGER", f"Immediate embed scheduled | contract_id={contract_id}", level="INFO")
            _schedule_immediate(upsert_contract_embedding(contract_id))
            return
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


async def upsert_job_role_embedding(job_role_id: str) -> dict:
    """Build source text for one role, get an embedding vector, and upsert into job_role_embedding."""
    logger("EMBEDDING_MANAGER", f"Upserting job role embedding | job_role_id={job_role_id}", level="INFO")
    try:
        source_text = build_job_role_source_text(job_role_id)
        if not source_text:
            logger("EMBEDDING_MANAGER", f"No source text for role {job_role_id} — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "no_source_text"}

        db = get_db()
        role_rows = db.execute_query(
            "SELECT job_post_id FROM job_role WHERE job_role_id = :jrid",
            {"jrid": job_role_id},
        )
        if not role_rows:
            logger("EMBEDDING_MANAGER", f"Role {job_role_id} not found — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "role_not_found"}
        job_post_id = str(role_rows[0]["job_post_id"])

        logger("EMBEDDING_MANAGER", f"Requesting embedding vector | job_role_id={job_role_id} | source_chars={len(source_text)}", level="DEBUG")
        vector = await get_embedding(source_text)
        vector_pg = _vector_to_pg(vector)
        metadata = json.dumps({"dim": len(vector)})
        logger("EMBEDDING_MANAGER", f"Embedding vector received | job_role_id={job_role_id} | dim={len(vector)}", level="DEBUG")

        existing = db.execute_query(
            "SELECT embedding_id FROM job_role_embedding WHERE job_role_id = :jrid",
            {"jrid": job_role_id},
        )

        if existing:
            db.execute_query(
                """UPDATE job_role_embedding
                   SET embedding_vector   = CAST(:vec AS vector),
                       source_text        = :txt,
                       embedding_metadata = CAST(:meta AS jsonb),
                       embedding_dirty    = FALSE,
                       updated_at         = NOW()
                   WHERE job_role_id = :jrid""",
                {"vec": vector_pg, "txt": source_text, "meta": metadata, "jrid": job_role_id},
            )
            logger("EMBEDDING_MANAGER", f"Job role embedding UPDATED | job_role_id={job_role_id} | dim={len(vector)}", level="INFO")
            return {"status": "updated", "job_role_id": job_role_id, "job_post_id": job_post_id, "dim": len(vector)}
        else:
            embedding_id = str(uuid.uuid4())
            db.execute_query(
                """INSERT INTO job_role_embedding
                     (embedding_id, job_role_id, job_post_id, embedding_vector,
                      source_text, embedding_metadata, embedding_dirty)
                   VALUES (:eid, :jrid, :jpid, CAST(:vec AS vector), :txt, CAST(:meta AS jsonb), FALSE)""",
                {"eid": embedding_id, "jrid": job_role_id, "jpid": job_post_id,
                 "vec": vector_pg, "txt": source_text, "meta": metadata},
            )
            logger("EMBEDDING_MANAGER", f"Job role embedding CREATED | job_role_id={job_role_id} | dim={len(vector)}", level="INFO")
            return {"status": "created", "job_role_id": job_role_id, "job_post_id": job_post_id, "dim": len(vector)}

    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Error upserting job role embedding | job_role_id={job_role_id} | error={e}", level="ERROR")
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


def _is_manual_portfolio(portfolio_id: str) -> bool:
    """
    Return True only when the portfolio row exists and is_auto_generated = FALSE.
    Auto-generated rows mirror contract data — they are NOT embedded here, they
    are covered by contract_embedding. Returns False if the row is missing.
    """
    try:
        db = get_db()
        rows = db.execute_query(
            "SELECT is_auto_generated FROM portfolio WHERE portfolio_id = :pid",
            {"pid": portfolio_id},
        )
        if not rows:
            return False
        return not bool(rows[0].get("is_auto_generated"))
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"is_auto_generated check failed | portfolio_id={portfolio_id} | error={e}", level="WARNING")
        return False


def mark_portfolio_dirty(portfolio_id: str) -> None:
    """
    Flag a manual portfolio entry's embedding as stale, or embed immediately
    if below the size threshold. Auto-generated rows are skipped silently —
    their semantic content lives in contract_embedding.
    Swallows exceptions so a dirty-flag failure never breaks the calling mutation.
    """
    try:
        if not _is_manual_portfolio(portfolio_id):
            logger(
                "EMBEDDING_MANAGER",
                f"Portfolio {portfolio_id} is auto-generated or missing — not embedded "
                "(contract_embedding covers auto rows)",
                level="DEBUG",
            )
            return

        if _should_embed_immediately():
            logger("EMBEDDING_MANAGER", f"Immediate embed scheduled | portfolio_id={portfolio_id}", level="INFO")
            _schedule_immediate(upsert_portfolio_embedding(portfolio_id))
            return
        db = get_db()
        db.execute_query(
            "UPDATE portfolio_embedding SET embedding_dirty = TRUE WHERE portfolio_id = :pid",
            {"pid": portfolio_id},
        )
        logger("EMBEDDING_MANAGER", f"Marked dirty | portfolio_id={portfolio_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not mark portfolio dirty | portfolio_id={portfolio_id} | error={e}", level="ERROR")


async def upsert_portfolio_embedding(portfolio_id: str) -> dict:
    """
    Build source text for a manual portfolio entry, generate an embedding, and
    upsert into portfolio_embedding. Skips auto-generated rows.
    """
    logger("EMBEDDING_MANAGER", f"Upserting portfolio embedding | portfolio_id={portfolio_id}", level="INFO")
    try:
        if not _is_manual_portfolio(portfolio_id):
            logger(
                "EMBEDDING_MANAGER",
                f"Portfolio {portfolio_id} is auto-generated or missing — skip upsert",
                level="INFO",
            )
            return {"status": "skipped", "reason": "auto_generated_or_missing"}

        source_text = build_portfolio_source_text(portfolio_id)
        if not source_text:
            logger("EMBEDDING_MANAGER", f"No source text for portfolio {portfolio_id} — skipping upsert", level="WARNING")
            return {"status": "skipped", "reason": "no_source_text"}

        db = get_db()
        fid_rows = db.execute_query(
            "SELECT freelancer_id FROM portfolio WHERE portfolio_id = :pid",
            {"pid": portfolio_id},
        )
        if not fid_rows:
            return {"status": "skipped", "reason": "portfolio_not_found"}
        freelancer_id = str(fid_rows[0]["freelancer_id"])

        logger("EMBEDDING_MANAGER", f"Requesting embedding vector | portfolio_id={portfolio_id} | source_chars={len(source_text)}", level="DEBUG")
        vector = await get_embedding(source_text)
        vector_pg = _vector_to_pg(vector)
        metadata = json.dumps({"dim": len(vector)})

        existing = db.execute_query(
            "SELECT embedding_id FROM portfolio_embedding WHERE portfolio_id = :pid",
            {"pid": portfolio_id},
        )

        if existing:
            db.execute_query(
                """UPDATE portfolio_embedding
                   SET embedding_vector   = CAST(:vec AS vector),
                       source_text        = :txt,
                       embedding_metadata = CAST(:meta AS jsonb),
                       embedding_dirty    = FALSE,
                       updated_at         = NOW()
                   WHERE portfolio_id = :pid""",
                {"vec": vector_pg, "txt": source_text, "meta": metadata, "pid": portfolio_id},
            )
            logger("EMBEDDING_MANAGER", f"Portfolio embedding UPDATED | portfolio_id={portfolio_id} | dim={len(vector)}", level="INFO")
            return {"status": "updated", "portfolio_id": portfolio_id, "dim": len(vector)}
        else:
            embedding_id = str(uuid.uuid4())
            db.execute_query(
                """INSERT INTO portfolio_embedding
                     (embedding_id, portfolio_id, freelancer_id, embedding_vector,
                      source_text, embedding_metadata, embedding_dirty)
                   VALUES (:eid, :pid, :fid, CAST(:vec AS vector), :txt, CAST(:meta AS jsonb), FALSE)""",
                {
                    "eid": embedding_id,
                    "pid": portfolio_id,
                    "fid": freelancer_id,
                    "vec": vector_pg,
                    "txt": source_text,
                    "meta": metadata,
                },
            )
            logger("EMBEDDING_MANAGER", f"Portfolio embedding CREATED | portfolio_id={portfolio_id} | dim={len(vector)}", level="INFO")
            return {"status": "created", "portfolio_id": portfolio_id, "dim": len(vector)}

    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Error upserting portfolio embedding | portfolio_id={portfolio_id} | error={e}", level="ERROR")
        raise


def delete_portfolio_embedding(portfolio_id: str) -> None:
    """
    Remove a portfolio embedding row when the source portfolio entry is deleted.
    Safe to call on auto-generated rows (they have no embedding to begin with).
    """
    try:
        db = get_db()
        db.execute_query(
            "DELETE FROM portfolio_embedding WHERE portfolio_id = :pid",
            {"pid": portfolio_id},
        )
        logger("EMBEDDING_MANAGER", f"Portfolio embedding deleted | portfolio_id={portfolio_id}", level="DEBUG")
    except Exception as e:
        logger("EMBEDDING_MANAGER", f"Could not delete portfolio embedding | portfolio_id={portfolio_id} | error={e}", level="WARNING")
