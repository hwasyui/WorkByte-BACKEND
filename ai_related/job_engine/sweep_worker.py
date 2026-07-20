import asyncio
import time
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_engine.embedding_manager import (
    upsert_freelancer_embedding,
    upsert_job_role_embedding,
    upsert_contract_embedding,
    upsert_portfolio_embedding,
)

SWEEP_INTERVAL_SECONDS = 300   # 5 minutes
BATCH_SIZE = 100               # max records processed per sweep cycle

# A row that keeps failing (e.g. a schema mismatch) gets retried with
# exponential backoff instead of every single cycle forever, and only its
# first failure gets a full ERROR/traceback - later attempts while backed
# off are skipped without logging, so one broken row can't spam the log
# indefinitely.
BACKOFF_BASE_SECONDS = SWEEP_INTERVAL_SECONDS
BACKOFF_MAX_SECONDS = 3600

# entity_name -> {row_id: {"failures": int, "retry_after": float}}
# In-memory only: resets on process restart, which is fine since a restart
# is a reasonable point to give a previously-quarantined row a fresh try.
_failure_state: dict[str, dict[str, dict]] = {}


def _should_skip(entity_name: str, row_id: str) -> bool:
    state = _failure_state.get(entity_name, {}).get(row_id)
    return state is not None and time.monotonic() < state["retry_after"]


def _record_failure(entity_name: str, row_id: str) -> int:
    table = _failure_state.setdefault(entity_name, {})
    state = table.setdefault(row_id, {"failures": 0, "retry_after": 0.0})
    state["failures"] += 1
    backoff = min(BACKOFF_BASE_SECONDS * (2 ** (state["failures"] - 1)), BACKOFF_MAX_SECONDS)
    state["retry_after"] = time.monotonic() + backoff
    return state["failures"]


def _clear_failure(entity_name: str, row_id: str) -> None:
    _failure_state.get(entity_name, {}).pop(row_id, None)


async def _sweep_entity(entity_name: str, table: str, id_column: str, upsert_fn) -> dict:
    """
    Re-embed all dirty rows for one embedding table.

    Rows currently in a failure backoff window are skipped (and not
    re-logged) so a persistently broken row doesn't get retried, and
    re-logged, every single cycle forever.
    """
    db = get_db()
    rows = db.execute_query(
        f"""SELECT {id_column} FROM {table}
           WHERE embedding_dirty = TRUE
           LIMIT :batch""",
        {"batch": BATCH_SIZE},
    )
    if not rows:
        logger("SWEEP_WORKER", f"No dirty {entity_name} embeddings to process", level="DEBUG")
        return {"refreshed": 0, "failed": 0, "skipped_backoff": 0}

    logger("SWEEP_WORKER", f"Found {len(rows)} dirty {entity_name} embedding(s) to refresh", level="INFO")

    refreshed = 0
    failed = 0
    skipped = 0
    first_error = None

    for row in rows:
        row_id = str(row[id_column])

        if _should_skip(entity_name, row_id):
            skipped += 1
            continue

        try:
            result = await upsert_fn(row_id)
            logger("SWEEP_WORKER", f"Refreshed {entity_name} embedding | id={row_id} | status={result.get('status')}", level="DEBUG")
            _clear_failure(entity_name, row_id)
            refreshed += 1
        except Exception as e:
            failures = _record_failure(entity_name, row_id)
            first_error = first_error or str(e)
            if failures == 1:
                logger("SWEEP_WORKER", f"Failed to re-embed {entity_name} | id={row_id} | error={e}", level="ERROR")
            else:
                logger(
                    "SWEEP_WORKER",
                    f"{entity_name} {row_id} still failing (attempt {failures}), backing off | last_error={e}",
                    level="WARNING",
                )
            failed += 1

    summary = f"{entity_name.capitalize()} sweep complete | refreshed={refreshed}/{len(rows)} failed={failed} skipped_backoff={skipped}"
    if first_error:
        summary += f" | first_error={first_error}"
    logger("SWEEP_WORKER", summary, level="INFO")

    return {"refreshed": refreshed, "failed": failed, "skipped_backoff": skipped}


async def run_sweep_once() -> dict:
    """
    Run one full sweep cycle across freelancer, job, contract, and portfolio
    embedding tables.

    Returns:
        Dict with freelancers_refreshed, jobs_refreshed, contracts_refreshed,
        portfolios_refreshed, total, and total_failed.
    """
    logger("SWEEP_WORKER", "Sweep cycle started", level="INFO")

    freelancers = await _sweep_entity("freelancer", "freelancer_embedding", "freelancer_id", upsert_freelancer_embedding)
    jobs        = await _sweep_entity("job role",   "job_role_embedding",   "job_role_id",   upsert_job_role_embedding)
    contracts   = await _sweep_entity("contract",   "contract_embedding",   "contract_id",   upsert_contract_embedding)
    portfolios  = await _sweep_entity("portfolio",  "portfolio_embedding",  "portfolio_id",  upsert_portfolio_embedding)

    total = freelancers["refreshed"] + jobs["refreshed"] + contracts["refreshed"] + portfolios["refreshed"]
    total_failed = freelancers["failed"] + jobs["failed"] + contracts["failed"] + portfolios["failed"]

    logger(
        "SWEEP_WORKER",
        f"Sweep cycle done | freelancers={freelancers['refreshed']} jobs={jobs['refreshed']} "
        f"contracts={contracts['refreshed']} portfolios={portfolios['refreshed']} "
        f"total={total} total_failed={total_failed}",
        level="INFO",
    )
    return {
        "freelancers_refreshed": freelancers["refreshed"],
        "jobs_refreshed":        jobs["refreshed"],
        "contracts_refreshed":   contracts["refreshed"],
        "portfolios_refreshed":  portfolios["refreshed"],
        "total":                 total,
        "total_failed":          total_failed,
    }


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
