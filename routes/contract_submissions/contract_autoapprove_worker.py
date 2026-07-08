import asyncio

from functions.logger import logger
from routes.contract_submissions.contract_submission_functions import ContractSubmissionFunctions

SWEEP_INTERVAL_SECONDS = 3600  # hourly - fine-grained enough for day-scale reminder thresholds


async def contract_autoapprove_loop() -> None:
    """
    Infinite loop that runs the client-gone-silent auto-approve sweep (reminder day 3,
    final warning day 6, auto-approve day 7) every SWEEP_INTERVAL_SECONDS.
    Launched via asyncio.create_task() in main.py lifespan startup.
    """
    logger("CONTRACT_AUTOAPPROVE_SWEEP", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(ContractSubmissionFunctions.run_autoapprove_sweep)
            logger("CONTRACT_AUTOAPPROVE_SWEEP", "Sweep cycle complete", level="INFO")
        except Exception as e:
            logger("CONTRACT_AUTOAPPROVE_SWEEP", f"Sweep loop unhandled error: {e}", level="ERROR")
