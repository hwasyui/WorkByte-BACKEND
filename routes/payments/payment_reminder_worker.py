import asyncio

from functions.logger import logger
from functions.db_manager import get_db
from routes.payments.payment_functions import notify_admins

SWEEP_INTERVAL_SECONDS = 3600
VERIFICATION_SLA_DAYS = 3
EVASION_THRESHOLD_DAYS = 3


def run_payment_reminder_sweep() -> int:
    flagged = 0
    try:
        stale_review_rows = get_db().execute_query(
            """
            SELECT contract_id, contract_title
            FROM contract
            WHERE status = 'payment_review'
              AND updated_at < NOW() - make_interval(days => :days)
            """,
            {"days": VERIFICATION_SLA_DAYS},
        )
        for row in stale_review_rows or []:
            notify_admins(
                notif_type="payment_verification_overdue",
                title="Payment verification overdue",
                body=f"\"{row['contract_title']}\" has been awaiting payment verification for over {VERIFICATION_SLA_DAYS} days.",
                data={"contract_id": str(row["contract_id"])},
            )
            flagged += 1

        evasion_rows = get_db().execute_query(
            """
            SELECT c.contract_id, c.contract_title
            FROM contract c
            WHERE c.freelancer_confirmed_receipt_at IS NOT NULL
              AND c.freelancer_confirmed_receipt_at < NOW() - make_interval(days => :days)
              AND c.status != 'completed'
              AND NOT EXISTS (
                  SELECT 1 FROM payment_proof pp
                  WHERE pp.contract_id = c.contract_id
                    AND pp.payee = 'admin'
                    AND pp.status = 'verified'
              )
            """,
            {"days": EVASION_THRESHOLD_DAYS},
        )
        for row in evasion_rows or []:
            notify_admins(
                notif_type="commission_at_risk",
                title="Commission possibly unpaid",
                body=f"Freelancer confirmed receipt on \"{row['contract_title']}\" but the admin's commission payment is still unverified after {EVASION_THRESHOLD_DAYS} days.",
                data={"contract_id": str(row["contract_id"])},
            )
            flagged += 1

        if flagged:
            logger("PAYMENT_REMINDER_SWEEP", f"Flagged {flagged} contract(s) for admin attention", level="INFO")
        return flagged
    except Exception as e:
        logger("PAYMENT_REMINDER_SWEEP", f"Error in payment reminder sweep: {str(e)}", level="ERROR")
        return 0


async def payment_reminder_loop() -> None:
    logger("PAYMENT_REMINDER_SWEEP", f"Sweep loop started | interval={SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(run_payment_reminder_sweep)
            logger("PAYMENT_REMINDER_SWEEP", "Sweep cycle complete", level="INFO")
        except Exception as e:
            logger("PAYMENT_REMINDER_SWEEP", f"Sweep loop unhandled error: {e}", level="ERROR")
