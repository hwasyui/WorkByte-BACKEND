"""
Background sweep worker.
Re-embeds all dirty freelancer/job embeddings every SWEEP_INTERVAL_SECONDS.
Started as an asyncio task inside FastAPI's lifespan.
"""

import asyncio
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.embedding_manager import (
    upsert_freelancer_embedding,
    upsert_job_embedding,
    upsert_contract_embedding,
)

SWEEP_INTERVAL_SECONDS = 300   # 5 minutes
BATCH_SIZE = 50                # max records processed per sweep cycle


async def _sweep_freelancers() -> int:
    """
    Re-embed all dirty freelancer embedding rows in one batch.

    Returns:
        Number of freelancer embeddings successfully refreshed in this cycle.
    """
    db = get_db()
    rows = db.execute_query(
        """SELECT freelancer_id FROM freelancer_embedding
           WHERE embedding_dirty = TRUE
           LIMIT :batch""",
        {"batch": BATCH_SIZE},
    )
    if not rows:
        logger("SWEEP_WORKER", "No dirty freelancer embeddings to process", level="DEBUG")
        return 0

    logger("SWEEP_WORKER", f"Found {len(rows)} dirty freelancer embedding(s) to refresh", level="INFO")
    count = 0
    for row in rows:
        fid = str(row["freelancer_id"])
        try:
            result = await upsert_freelancer_embedding(fid)
            logger("SWEEP_WORKER", f"Refreshed freelancer embedding | freelancer_id={fid} | status={result.get('status')}", level="DEBUG")
            count += 1
        except Exception as e:
            logger("SWEEP_WORKER", f"Failed to re-embed freelancer | freelancer_id={fid} | error={e}", level="ERROR")

    logger("SWEEP_WORKER", f"Freelancer sweep complete | refreshed={count}/{len(rows)}", level="INFO")
    return count


async def _sweep_jobs() -> int:
    """
    Re-embed all dirty job embedding rows in one batch.

    Returns:
        Number of job embeddings successfully refreshed in this cycle.
    """
    db = get_db()
    rows = db.execute_query(
        """SELECT job_post_id FROM job_embedding
           WHERE embedding_dirty = TRUE
           LIMIT :batch""",
        {"batch": BATCH_SIZE},
    )
    if not rows:
        logger("SWEEP_WORKER", "No dirty job embeddings to process", level="DEBUG")
        return 0

    logger("SWEEP_WORKER", f"Found {len(rows)} dirty job embedding(s) to refresh", level="INFO")
    count = 0
    for row in rows:
        jpid = str(row["job_post_id"])
        try:
            result = await upsert_job_embedding(jpid)
            logger("SWEEP_WORKER", f"Refreshed job embedding | job_post_id={jpid} | status={result.get('status')}", level="DEBUG")
            count += 1
        except Exception as e:
            logger("SWEEP_WORKER", f"Failed to re-embed job | job_post_id={jpid} | error={e}", level="ERROR")

    logger("SWEEP_WORKER", f"Job sweep complete | refreshed={count}/{len(rows)}", level="INFO")
    return count


async def _sweep_contracts() -> int:
    """
    Re-embed all dirty contract embedding rows in one batch.

    Returns:
        Number of contract embeddings successfully refreshed in this cycle.
    """
    db = get_db()
    rows = db.execute_query(
        """SELECT contract_id FROM contract_embedding
           WHERE embedding_dirty = TRUE
           LIMIT :batch""",
        {"batch": BATCH_SIZE},
    )
    if not rows:
        logger("SWEEP_WORKER", "No dirty contract embeddings to process", level="DEBUG")
        return 0

    logger("SWEEP_WORKER", f"Found {len(rows)} dirty contract embedding(s) to refresh", level="INFO")
    count = 0
    for row in rows:
        cid = str(row["contract_id"])
        try:
            result = await upsert_contract_embedding(cid)
            logger("SWEEP_WORKER", f"Refreshed contract embedding | contract_id={cid} | status={result.get('status')}", level="DEBUG")
            count += 1
        except Exception as e:
            logger("SWEEP_WORKER", f"Failed to re-embed contract | contract_id={cid} | error={e}", level="ERROR")

    logger("SWEEP_WORKER", f"Contract sweep complete | refreshed={count}/{len(rows)}", level="INFO")
    return count


async def run_sweep_once() -> dict:
    """
    Run one full sweep cycle across freelancer, job, and contract embedding tables.

    Returns:
        Dict with freelancers_refreshed, jobs_refreshed, contracts_refreshed, and total counts.
    """
    logger("SWEEP_WORKER", "Sweep cycle started", level="INFO")
    f_count = await _sweep_freelancers()
    j_count = await _sweep_jobs()
    c_count = await _sweep_contracts()
    total = f_count + j_count + c_count
    logger("SWEEP_WORKER", f"Sweep cycle done | freelancers={f_count} jobs={j_count} contracts={c_count} total={total}", level="INFO")
    return {"freelancers_refreshed": f_count, "jobs_refreshed": j_count, "contracts_refreshed": c_count, "total": total}


async def embedding_sweep_loop() -> None:
    """
    Infinite loop that sweeps dirty embeddings every SWEEP_INTERVAL_SECONDS.
    Launched via asyncio.create_task() in main.py lifespan startup.
    """
    logger("SWEEP_WORKER", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s | batch_size={BATCH_SIZE}", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await run_sweep_once()
        except Exception as e:
            logger("SWEEP_WORKER", f"Sweep loop unhandled error: {e}", level="ERROR")
