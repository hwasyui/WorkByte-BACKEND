import asyncio

from functions.logger import logger
from routes.contracts.contract_functions import ContractFunctions

SWEEP_INTERVAL_SECONDS = 86400  # daily: deadlines move on a day-scale, no need for tighter polling


async def contract_deadline_loop() -> None:
    """
    Infinite loop that checks for contracts past their end_date every
    SWEEP_INTERVAL_SECONDS. Informational only (business decision) - never changes
    contract.status, just notifies both parties once per overdue contract.
    Launched via asyncio.create_task() in main.py lifespan startup.
    """
    logger("CONTRACT_DEADLINE_SWEEP", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(ContractFunctions.notify_overdue_contracts)
            logger("CONTRACT_DEADLINE_SWEEP", "Sweep cycle complete", level="INFO")
        except Exception as e:
            logger("CONTRACT_DEADLINE_SWEEP", f"Sweep loop unhandled error: {e}", level="ERROR")
