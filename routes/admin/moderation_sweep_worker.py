import asyncio

from functions.logger import logger
from routes.admin.admin_functions import run_moderation_sweeps

SWEEP_INTERVAL_SECONDS = 3600   # 1 hour: sweeps only act on 30-day-scale deadlines


async def moderation_sweep_loop() -> None:
    """
    Infinite loop that runs the moderation auto-approve/auto-remove/report
    sweeps every SWEEP_INTERVAL_SECONDS, regardless of whether an admin is
    logged in. Launched via asyncio.create_task() in main.py lifespan startup.

    These sweeps also still run lazily inside admin dashboard/queue endpoints
    (via run_moderation_sweeps() and the individual sweep calls), so this loop
    just guarantees they eventually run even with zero admin traffic.
    """
    logger("MODERATION_SWEEP", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(run_moderation_sweeps)
            logger("MODERATION_SWEEP", "Sweep cycle complete", level="INFO")
        except Exception as e:
            logger("MODERATION_SWEEP", f"Sweep loop unhandled error: {e}", level="ERROR")
