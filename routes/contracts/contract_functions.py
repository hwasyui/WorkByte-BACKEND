from datetime import date, datetime, timezone
import asyncio
import os
import sys

from routes.dm.dm_functions import DMFunctions
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid
from routes.proposals.proposal_functions import ProposalFunctions
from routes.notifications.notification_functions import NotificationFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from ai_related.job_engine.embedding_manager import mark_contract_dirty

def _fire_notification(coro) -> None:
    """Schedule a notify() coroutine from sync code, whether this runs on the
    event loop thread (route handlers) or a plain worker thread (sweep loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(coro)
    else:
        asyncio.run(coro)


def _already_notified(notif_type: str, key_field: str, key_value: str) -> bool:
    """Dedup check against the existing `notifications` table (data JSONB column) -
    avoids adding purpose-built dedup columns for one-shot sweep notifications.
    key_field must always be a trusted literal ("contract_id", "submission_id", ...),
    never user input - it's interpolated into the JSONB path, not bound as a param."""
    rows = get_db().execute_query(
        f"SELECT 1 FROM notifications WHERE type = :ntype AND data->>'{key_field}' = :key LIMIT 1",
        {"ntype": notif_type, "key": key_value},
    )
    return bool(rows)


def _count_notifications(notif_type: str, recipient_user_id: str) -> int:
    """Lifetime count of a given notification type sent to one recipient - reused as a
    strike counter (e.g. how many times a client has let a contract auto-approve) so
    penalty logic doesn't need a dedicated counter column."""
    rows = get_db().execute_query(
        "SELECT COUNT(*) AS cnt FROM notifications WHERE type = :ntype AND recipient_id = :rid",
        {"ntype": notif_type, "rid": recipient_user_id},
    )
    return int(rows[0]["cnt"]) if rows else 0


def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings."""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


class ContractFunctions:
    """Handle all contract-related database operations."""

    @staticmethod
    def attach_job_closure(contracts: List[Dict]) -> List[Dict]:
        """Enrich contract rows with their parent job post's title and closure state, so a
        contract still running under a job an admin took down can be flagged in the UI.
        Mutates and returns the given rows; missing/deleted job posts leave the fields None."""
        if not contracts:
            return contracts
        job_ids = list({str(c["job_post_id"]) for c in contracts if c.get("job_post_id")})
        if not job_ids:
            return contracts
        try:
            placeholders = ", ".join(f":jid_{i}" for i in range(len(job_ids)))
            params = {f"jid_{i}": jid for i, jid in enumerate(job_ids)}
            rows = get_db().execute_query(
                f"""
                SELECT job_post_id, job_title, status, closure_reason, closure_note, closed_at
                FROM job_post
                WHERE job_post_id IN ({placeholders})
                """,
                params,
            )
            by_id = {str(row["job_post_id"]): row for row in rows or []}
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Failed to attach job closure state (non-fatal): {e}", level="WARNING")
            by_id = {}
        for contract in contracts:
            job = by_id.get(str(contract.get("job_post_id")))
            contract["job_title"]          = job["job_title"] if job else None
            contract["job_status"]         = job["status"] if job else None
            contract["job_closure_reason"] = job["closure_reason"] if job else None
            contract["job_closure_note"]   = job["closure_note"] if job else None
            contract["job_closed_at"]      = job["closed_at"] if job else None
        return contracts

    @staticmethod
    def get_contract_by_id(contract_id: str) -> Optional[Dict]:
        """Fetch a contract by ID."""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(table_name="contract", conditions=conditions, limit=1)
            if rows:
                logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contract_by_proposal_id(proposal_id: str) -> Optional[Dict]:
        """Fetch the contract already created from a given proposal, if any."""
        try:
            db = get_db()
            conditions = [("proposal_id", "=", proposal_id)]
            rows = db.fetch_data(table_name="contract", conditions=conditions, limit=1)
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error checking existing contract for proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contracts_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all contracts for a freelancer."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract",
                conditions=[("freelancer_id", "=", freelancer_id)],
                order_by="created_at DESC",
            )
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts for freelancer {freelancer_id}", level="INFO")
            return ContractFunctions.attach_job_closure([convert_uuids_to_str(dict(row)) for row in rows])
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contracts_by_client_id(client_id: str) -> List[Dict]:
        """Fetch all contracts for a client."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract",
                conditions=[("client_id", "=", client_id)],
                order_by="created_at DESC",
            )
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts for client {client_id}", level="INFO")
            return ContractFunctions.attach_job_closure([convert_uuids_to_str(dict(row)) for row in rows])
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_contract(
        job_post_id: str,
        job_role_id: str,
        proposal_id: str,
        freelancer_id: str,
        client_id: str,
        contract_title: str,
        agreed_budget: float,
        payment_structure: str,
        start_date,
        terms: Dict,
        contract_id: Optional[str] = None,
        role_title: Optional[str] = None,
        budget_currency: Optional[str] = "USD",
        agreed_duration: Optional[str] = None,
        end_date=None,
        actual_completion_date=None,
        total_hours_worked: Optional[float] = None,
        total_paid: Optional[float] = 0,
        contract_pdf_url: Optional[str] = None,
        contract_pdf_generated_at=None,
    ) -> Dict:
        """Create a contract and everything that follows from the hire, atomically.

        A contract is only ever created whole. The row, the terms it was generated
        from, the job_role seat it fills, the rejection of the proposals it beat and
        the closing of a fully staffed job post are one unit of work: all of it
        commits or none of it does. There is no in-between state for anything else to
        observe, which is why no status like 'draft' is needed to describe one.

        The PDF is rendered and uploaded by the caller BEFORE this runs, and its
        storage path passed in. That ordering is deliberate. Object storage cannot
        take part in a database transaction, so the only way to avoid a contract that
        exists without its document is to produce the document first and let the
        database be the last thing that happens. A failure here leaves an unreferenced
        file in the bucket, which costs nothing; the reverse would leave a live
        contract nobody can read.

        Returns the stored row plus the proposals that were auto-rejected, so the
        caller can notify those freelancers once the work has actually committed.
        """
        # Imported here rather than at module scope: contract_generation_functions
        # imports this module, so a top-level import either way is circular.
        from routes.contracts.contract_generation_functions import ContractGenerationFunctions

        try:
            contract_id = contract_id or str(uuid.uuid4())
            rejected: List[Dict] = []

            with get_db().transaction() as tx:
                rows = tx.execute_query(
                    """
                    INSERT INTO contract (
                        contract_id, job_post_id, job_role_id, proposal_id, freelancer_id,
                        client_id, contract_title, role_title, agreed_budget, budget_currency,
                        payment_structure, agreed_duration, status, start_date, end_date,
                        actual_completion_date, total_hours_worked, total_paid,
                        contract_pdf_url, contract_pdf_generated_at
                    ) VALUES (
                        :contract_id, :job_post_id, :job_role_id, :proposal_id, :freelancer_id,
                        :client_id, :contract_title, :role_title, :agreed_budget, :budget_currency,
                        :payment_structure, :agreed_duration, 'active', :start_date, :end_date,
                        :actual_completion_date, :total_hours_worked, :total_paid,
                        :contract_pdf_url, :contract_pdf_generated_at
                    )
                    RETURNING *
                    """,
                    {
                        "contract_id": contract_id,
                        "job_post_id": job_post_id,
                        "job_role_id": job_role_id,
                        "proposal_id": proposal_id,
                        "freelancer_id": freelancer_id,
                        "client_id": client_id,
                        "contract_title": contract_title,
                        "role_title": role_title,
                        "agreed_budget": agreed_budget,
                        "budget_currency": budget_currency,
                        "payment_structure": payment_structure,
                        "agreed_duration": agreed_duration,
                        "start_date": start_date,
                        "end_date": end_date,
                        "actual_completion_date": actual_completion_date,
                        "total_hours_worked": total_hours_worked,
                        "total_paid": total_paid,
                        "contract_pdf_url": contract_pdf_url,
                        "contract_pdf_generated_at": contract_pdf_generated_at,
                    },
                )

                # After the insert, so that a second contract for the same proposal fails
                # on UNIQUE(proposal_id) and is reported as the duplicate it is, rather
                # than as a staffing problem when the role happens to be full.
                role_fill_rows = tx.execute_query(
                    """
                    UPDATE job_role
                    SET positions_filled = positions_filled + 1
                    WHERE job_role_id = :jrid AND positions_filled < positions_available
                    RETURNING positions_filled, positions_available
                    """,
                    {"jrid": job_role_id},
                )
                if not role_fill_rows:
                    raise ValueError("This role has already been fully staffed - no remaining positions to contract.")

                ContractGenerationFunctions.upsert_contract_terms(contract_id, terms or {}, db=tx)

                filled = role_fill_rows[0]["positions_filled"]
                available = role_fill_rows[0]["positions_available"]
                logger("CONTRACT_FUNCTIONS", f"Role {job_role_id} now {filled}/{available} positions filled", level="INFO")

                if filled >= available:
                    rejected = ProposalFunctions.auto_reject_pending_proposals_for_filled_role(
                        job_role_id, exclude_proposal_id=proposal_id, db=tx
                    )
                    all_roles_filled = tx.execute_query(
                        """
                        SELECT NOT EXISTS (
                            SELECT 1 FROM job_role
                            WHERE job_post_id = :jpid AND positions_filled < positions_available
                        ) AS all_filled
                        """,
                        {"jpid": job_post_id},
                    )[0]["all_filled"]
                    if all_roles_filled:
                        tx.execute_query(
                            "UPDATE job_post SET status = 'filled' WHERE job_post_id = :jpid AND status = 'active'",
                            {"jpid": job_post_id},
                        )
                        logger("CONTRACT_FUNCTIONS", f"Job post {job_post_id} auto-marked 'filled' - all roles fully staffed", level="INFO")

                created = convert_uuids_to_str(dict(rows[0]))

            for row in rejected:
                _fire_notification(NotificationFunctions.notify(
                    recipient_user_id=str(row["freelancer_user_id"]),
                    notif_type="role_filled",
                    title="Position Filled",
                    body=f"The \"{row['role_title']}\" position you applied for has been filled by another freelancer.",
                    data={"job_role_id": job_role_id},
                ))

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} created", level="INFO")
            return {"contract": created, "rejected": rejected}
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error creating contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_contract(contract_id: str, update_data: Dict) -> Optional[Dict]:
        """Update contract information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("CONTRACT_FUNCTIONS", "No data to update", level="WARNING")
                return ContractFunctions.get_contract_by_id(contract_id)

            # Auto-set actual_completion_date when marking as completed.
            if update_data.get("status") == "completed":
                update_data.setdefault(
                    "actual_completion_date",
                    datetime.utcnow().strftime("%Y-%m-%d"),
                )

            existing_contract = ContractFunctions.get_contract_by_id(contract_id)
            conditions = [("contract_id", "=", contract_id)]
            db.update_data(table_name="contract", data=update_data, conditions=conditions)

            # If the contract transitions into completed, update counters
            status_transition = False
            new_status = update_data.get("status")
            if new_status and existing_contract:
                old_status = existing_contract.get("status")
                if new_status in {"completed"} and old_status not in {"completed"}:
                    status_transition = True

            if status_transition and existing_contract:
                client_id = existing_contract.get("client_id")
                freelancer_id = existing_contract.get("freelancer_id")

                if client_id:
                    client_rows = db.fetch_data(
                        table_name="client",
                        conditions=[("client_id", "=", client_id)],
                        limit=1,
                    )
                    if client_rows:
                        current_completed = client_rows[0].get("total_jobs_completed") or 0
                        db.update_data(
                            table_name="client",
                            data={"total_jobs_completed": current_completed + 1},
                            conditions=[("client_id", "=", client_id)],
                        )

                if freelancer_id:
                    freelancer_rows = db.fetch_data(
                        table_name="freelancer",
                        conditions=[("freelancer_id", "=", freelancer_id)],
                        limit=1,
                    )
                    if freelancer_rows:
                        current_total = freelancer_rows[0].get("total_jobs") or 0
                        db.update_data(
                            table_name="freelancer",
                            data={"total_jobs": current_total + 1},
                            conditions=[("freelancer_id", "=", freelancer_id)],
                        )
                ContractFunctions._create_auto_portfolio_entry(
                    contract_id=contract_id,
                    contract=existing_contract,
                )
                mark_contract_dirty(contract_id)

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} updated", level="INFO")
            return ContractFunctions.get_contract_by_id(contract_id)
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error updating contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _create_auto_portfolio_entry(contract_id: str, contract: Dict) -> None:
        """
        Create a portfolio row tied to a completed contract.

        Idempotent, and flagged is_auto_generated so portfolio_embedding stays limited to
        user-curated items. Failures are swallowed so completion is never blocked.
        """
        try:
            freelancer_id = contract.get("freelancer_id")
            if not freelancer_id:
                return

            db = get_db()
            existing = db.fetch_data(
                table_name="portfolio",
                conditions=[("contract_id", "=", contract_id)],
                limit=1,
            )
            if existing:
                logger(
                    "CONTRACT_FUNCTIONS",
                    f"Auto-portfolio already exists for contract {contract_id}, skip",
                    level="DEBUG",
                )
                return

            role_title = contract.get("role_title") or contract.get("contract_title") or "Completed Project"
            project_title = f"{role_title}".strip()
            project_description = (
                f"Completed project: {role_title}. "
                "Full project details, client rating, and review are linked through the contract record."
            )
            completion_date = datetime.utcnow().strftime("%Y-%m-%d")

            portfolio_id = str(uuid.uuid4())
            db.execute_query(
                """INSERT INTO portfolio
                     (portfolio_id, freelancer_id, project_title, project_description,
                      completion_date, is_auto_generated, contract_id)
                   VALUES (:pid, :fid, :title, :desc, :cdate, TRUE, :cid)""",
                {
                    "pid":   portfolio_id,
                    "fid":   freelancer_id,
                    "title": project_title,
                    "desc":  project_description,
                    "cdate": completion_date,
                    "cid":   contract_id,
                },
            )
            logger(
                "CONTRACT_FUNCTIONS",
                f"Auto-portfolio created | portfolio_id={portfolio_id} | contract_id={contract_id} "
                f"| freelancer_id={freelancer_id} (NOT embedded; contract_embedding covers it)",
                level="INFO",
            )
        except Exception as e:
            logger(
                "CONTRACT_FUNCTIONS",
                f"Could not auto-create portfolio for contract {contract_id} | error={e}",
                level="WARNING",
            )

    @staticmethod
    def _notify_role_reopened(job_role_id: str) -> None:
        """Tell freelancers auto-rejected when this role filled up that a slot freed up.
        The 'role_filled' notification log doubles as the recipient list."""
        try:
            role = get_db().execute_query(
                "SELECT role_title FROM job_role WHERE job_role_id = :jrid",
                {"jrid": job_role_id},
            )
            role_title = role[0]["role_title"] if role else "a role"

            recipients = get_db().execute_query(
                """
                SELECT DISTINCT recipient_id
                FROM notifications
                WHERE type = 'role_filled' AND data->>'job_role_id' = :jrid
                """,
                {"jrid": job_role_id},
            )
            for row in recipients or []:
                _fire_notification(NotificationFunctions.notify(
                    recipient_user_id=str(row["recipient_id"]),
                    notif_type="role_reopened",
                    title="Position open again",
                    body=f"The \"{role_title}\" position you previously applied for has opened up again.",
                    data={"job_role_id": job_role_id},
                ))
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Failed to notify role-reopened for {job_role_id} (non-fatal): {e}", level="WARNING")

    @staticmethod
    def _revert_proposal_on_contract_removal(
        proposal_id: str, job_role_id: Optional[str] = None, release_seat: bool = True
    ) -> None:
        """Flip a proposal back to 'rejected' once its contract is gone, since 'accepted'
        should only mean there's a live contract behind it. Also frees the role's filled
        slot so it can be rehired. Non-fatal, never breaks the cancel or delete.

        Pass release_seat=False for a draft: it never reached activation, so it holds no
        slot, and decrementing here would hand away a position belonging to a real hire."""
        try:
            proposal = ProposalFunctions.get_proposal_by_id(str(proposal_id))
            # Slot release rides on the accepted to rejected flip so it runs once per
            # hire. An already-rejected proposal would double-decrement positions_filled.
            if not proposal or proposal.get("status") != "accepted":
                return
            ProposalFunctions.update_proposal(str(proposal_id), {"status": "rejected"})
            logger(
                "CONTRACT_FUNCTIONS",
                f"Proposal {proposal_id} reverted to 'rejected' after its contract was removed",
                level="INFO",
            )
            if job_role_id and release_seat:
                role_rows = get_db().execute_query(
                    """
                    UPDATE job_role
                    SET positions_filled = GREATEST(positions_filled - 1, 0)
                    WHERE job_role_id = :jrid
                    RETURNING job_post_id, positions_filled, positions_available
                    """,
                    {"jrid": job_role_id},
                )
                logger("CONTRACT_FUNCTIONS", f"Role {job_role_id} released one filled position after contract removal", level="INFO")
                ContractFunctions._notify_role_reopened(job_role_id)

                # A slot just reopened, so a job post sitting at 'filled' goes back
                # to 'active'.
                if role_rows and role_rows[0]["positions_filled"] < role_rows[0]["positions_available"]:
                    get_db().execute_query(
                        "UPDATE job_post SET status = 'active' WHERE job_post_id = :jpid AND status = 'filled'",
                        {"jpid": role_rows[0]["job_post_id"]},
                    )
        except Exception as e:
            logger(
                "CONTRACT_FUNCTIONS",
                f"Failed to revert proposal {proposal_id} after contract removal (non-fatal): {e}",
                level="WARNING",
            )

    @staticmethod
    def delete_contract(contract_id: str) -> bool:
        """Delete a contract."""
        try:
            db = get_db()
            contract = ContractFunctions.get_contract_by_id(contract_id)
            db.delete_data(table_name="contract", conditions=[("contract_id", "=", contract_id)])
            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} deleted", level="INFO")

            if contract and contract.get("proposal_id"):
                ContractFunctions._revert_proposal_on_contract_removal(
                    contract["proposal_id"],
                    contract.get("job_role_id"),
                    release_seat=contract.get("status") != "draft",
                )

            return True
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error deleting contract: {str(e)}", level="ERROR")
            raise

    # Cancellable from the route, plus 'disputed' so arbitration can cancel. The write
    # below is conditional on these, which is what makes the check in the route safe:
    # the autoapprove sweep can complete a contract between that check and this write.
    _CANCELLABLE_FROM = (
        "active", "under_review", "revision_requested", "disputed",
        "pending_payment", "payment_review", "payment_rejected",
    )

    @staticmethod
    def cancel_contract(
        contract_id: str,
        cancelled_by: str,
        reason: Optional[str] = None,
    ) -> Optional[Dict]:
        """Cancel a contract."""
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            rows = get_db().execute_query(
                """
                UPDATE contract
                SET status              = 'cancelled',
                    end_date            = :end_date,
                    cancelled_by        = :cancelled_by,
                    cancellation_reason = COALESCE(:reason, cancellation_reason)
                WHERE contract_id = :cid
                  AND status::text = ANY(:statuses)
                RETURNING contract_id
                """,
                {
                    "cid": contract_id,
                    "end_date": datetime.now(timezone.utc).date(),
                    "cancelled_by": cancelled_by,
                    "reason": reason or None,
                    "statuses": list(ContractFunctions._CANCELLABLE_FROM),
                },
            )
            if not rows:
                current = ContractFunctions.get_contract_by_id(contract_id)
                raise ValueError(
                    f"This contract is no longer in a cancellable state "
                    f"(it is now '{(current or {}).get('status')}')."
                )
            updated_contract = ContractFunctions.get_contract_by_id(contract_id)

            if contract.get("proposal_id"):
                ContractFunctions._revert_proposal_on_contract_removal(
                    contract["proposal_id"], contract.get("job_role_id")
                )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=cancelled_by,
                    message_text="Contract cancelled.",
                    event_type="contract_cancelled",
                    metadata={"cancelled_by": cancelled_by, "reason": reason},
                )
            except Exception:
                pass

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} cancelled by {cancelled_by}", level="INFO")
            return updated_contract
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error cancelling contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_cancelled_at(contract_id: str) -> Optional[datetime]:
        """When the contract was actually cancelled, read off the cancellation system
        event. contract.updated_at is not a stand-in for this: every later write to the
        row moves it, which would silently restart the dispute window. Returns None when
        no cancellation event exists, so callers keep their own fallback."""
        try:
            rows = get_db().execute_query(
                """
                SELECT dm.sent_at
                FROM dm_message dm
                JOIN dm_thread dt ON dt.thread_id = dm.thread_id
                WHERE dm.metadata::jsonb->>'type' = 'contract_cancelled'
                  AND COALESCE(dm.metadata::jsonb->>'contract_id', dt.contract_id::text) = :cid
                ORDER BY dm.sent_at DESC
                LIMIT 1
                """,
                {"cid": str(contract_id)},
            )
            return rows[0]["sent_at"] if rows else None
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Failed to resolve cancellation time for {contract_id}: {e}", level="WARNING")
            return None

    @staticmethod
    def _restore_proposal_on_contract_reinstated(proposal_id: str, job_role_id: Optional[str] = None) -> None:
        """Inverse of _revert_proposal_on_contract_removal, for a cancellation that
        arbitration overturned. Only acts on a proposal still sitting at 'rejected', so
        it stays a no-op when the contract was never cancelled. Non-fatal."""
        try:
            proposal = ProposalFunctions.get_proposal_by_id(str(proposal_id))
            if not proposal or proposal.get("status") != "rejected":
                return
            ProposalFunctions.update_proposal(str(proposal_id), {"status": "accepted"})
            logger(
                "CONTRACT_FUNCTIONS",
                f"Proposal {proposal_id} restored to 'accepted' after its cancellation was overturned",
                level="INFO",
            )
            if job_role_id:
                role_rows = get_db().execute_query(
                    """
                    UPDATE job_role
                    SET positions_filled = LEAST(positions_filled + 1, positions_available)
                    WHERE job_role_id = :jrid
                    RETURNING job_post_id, positions_filled, positions_available
                    """,
                    {"jrid": job_role_id},
                )
                if role_rows and role_rows[0]["positions_filled"] >= role_rows[0]["positions_available"]:
                    get_db().execute_query(
                        "UPDATE job_post SET status = 'filled' WHERE job_post_id = :jpid AND status = 'active'",
                        {"jpid": role_rows[0]["job_post_id"]},
                    )
        except Exception as e:
            logger(
                "CONTRACT_FUNCTIONS",
                f"Failed to restore proposal {proposal_id} after reinstating contract (non-fatal): {e}",
                level="WARNING",
            )

    # Statuses a dispute can be raised from. 'cancelled' is the recourse against a
    # cancellation; the route decides whether that window is still open.
    _DISPUTABLE_FROM = (
        "under_review", "revision_requested", "cancelled",
        "pending_payment", "payment_review", "payment_rejected",
    )

    @staticmethod
    def raise_dispute(contract_id: str, raised_by: str, reason: str) -> Optional[Dict]:
        """Flip a contract into 'disputed' (status/value both already exist in the
        contract_status enum - see create_table.sql). The reason and every subsequent
        arbitration action are kept as DM system-event history on the contract's thread
        rather than dedicated columns, per the no-new-schema constraint for this pass."""
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            rows = get_db().execute_query(
                """
                UPDATE contract SET status = 'disputed'
                WHERE contract_id = :cid
                  AND status::text = ANY(:statuses)
                RETURNING contract_id
                """,
                {"cid": contract_id, "statuses": list(ContractFunctions._DISPUTABLE_FROM)},
            )
            if not rows:
                current = ContractFunctions.get_contract_by_id(contract_id)
                raise ValueError(
                    f"This contract can no longer be disputed "
                    f"(it is now '{(current or {}).get('status')}')."
                )
            updated_contract = ContractFunctions.get_contract_by_id(contract_id)

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=raised_by,
                    message_text=f"Dispute raised: {reason}",
                    event_type="dispute_raised",
                    metadata={"raised_by": raised_by, "reason": reason},
                )
            except Exception:
                pass

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} disputed by {raised_by}", level="INFO")
            return updated_contract
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error raising dispute: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def arbitrate_dispute(
        contract_id: str,
        outcome: str,
        admin_user_id: str,
        note: Optional[str] = None,
        new_deadline: Optional[date] = None,
    ) -> Optional[Dict]:
        """Admin resolves a disputed contract. Reuses the exact same completion/cancel/
        revision-request functions the manual flows use, so rating/AI-review/portfolio
        side effects stay consistent regardless of how the contract got there."""
        from routes.contract_submissions.contract_submission_functions import ContractSubmissionFunctions

        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            overturns_cancellation = (
                outcome in {"approve", "revise"}
                and contract.get("cancelled_by")
                and contract.get("proposal_id")
            )

            latest_submission = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)

            if outcome == "approve":
                if latest_submission and latest_submission.get("status") == "submitted":
                    ContractSubmissionFunctions.approve_latest_submission(contract_id)
                elif latest_submission and latest_submission.get("status") == "approved":
                    ContractFunctions.update_contract(contract_id, {"status": "pending_payment"})
                else:
                    ContractFunctions.update_contract(contract_id, {"status": "completed"})
                    from ai_related.review_analysis.review_pipeline import run_post_completion_pipeline
                    from ai_related.review_analysis.client_review_pipeline import run_client_review_post_completion_pipeline
                    _fire_notification(run_post_completion_pipeline(contract_id))
                    _fire_notification(run_client_review_post_completion_pipeline(contract_id))
            elif outcome == "cancel":
                ContractFunctions.cancel_contract(contract_id, cancelled_by=admin_user_id, reason=note)
            elif outcome == "revise":
                if not new_deadline:
                    raise ValueError("new_deadline is required when outcome='revise'")
                if latest_submission and latest_submission.get("status") == "approved":
                    raise ValueError(
                        "Work was already approved for this contract - this dispute is about payment, "
                        "not deliverables. Use 'approve' or 'cancel' instead of 'revise'."
                    )
                revised = ContractSubmissionFunctions.request_revision_for_latest_submission(contract_id, note=note)
                if not revised:
                    raise ValueError(
                        "Cannot resolve as 'revise': this contract has no submitted work to send back. "
                        "Use 'approve' or 'cancel' instead."
                    )
                ContractFunctions.update_contract(contract_id, {"end_date": new_deadline})
            else:
                raise ValueError(f"Invalid outcome: {outcome}")

            if overturns_cancellation:
                ContractFunctions._restore_proposal_on_contract_reinstated(
                    contract["proposal_id"], contract.get("job_role_id")
                )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=admin_user_id,
                    message_text=f"Dispute resolved by admin: {outcome}." + (f" {note}" if note else ""),
                    event_type="dispute_resolved",
                    metadata={"outcome": outcome, "note": note, "resolved_by": admin_user_id},
                )
            except Exception:
                pass

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} dispute arbitrated: {outcome}", level="INFO")
            return ContractFunctions.get_contract_by_id(contract_id)
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error arbitrating dispute: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def notify_overdue_contracts() -> int:
        """Flag overdue contracts without changing status, leaving the parties to resolve
        it. Dedup goes through the notifications table, one per contract ever."""
        try:
            overdue = get_db().execute_query(
                """
                SELECT contract_id, freelancer_id, client_id, contract_title, end_date
                FROM contract
                WHERE status IN ('active', 'under_review', 'revision_requested')
                  AND end_date < CURRENT_DATE
                """,
                {},
            )
            notified = 0
            for row in overdue or []:
                contract_id = str(row["contract_id"])
                if _already_notified("contract_overdue", "contract_id", contract_id):
                    continue

                freelancer = FreelancerFunctions.get_freelancer_by_id(str(row["freelancer_id"]))
                client = ClientFunctions.get_client_by_id(str(row["client_id"]))
                title = row.get("contract_title") or "your contract"
                body = f"\"{title}\" is past its deadline ({row['end_date']}). Status hasn't changed automatically - please coordinate directly."

                if client:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(client["user_id"]),
                        notif_type="contract_overdue",
                        title="Contract past deadline",
                        body=body,
                        data={"contract_id": contract_id},
                    ))
                if freelancer:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(freelancer["user_id"]),
                        notif_type="contract_overdue",
                        title="Contract past deadline",
                        body=body,
                        data={"contract_id": contract_id},
                    ))
                notified += 1

            if notified:
                logger("CONTRACT_FUNCTIONS", f"Overdue sweep: notified {notified} contract(s)", level="INFO")
            return notified
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error in overdue contract sweep: {str(e)}", level="ERROR")
            return 0

    _RELIABILITY_WARNING_THRESHOLD = 2

    @staticmethod
    def get_client_reliability_label(client_user_id: str) -> str:
        """Qualitative signal for freelancers deciding whether to work with a client -
        derived on read from the same `contract_auto_approved` notification count used
        for the ban penalty, no separate storage needed."""
        count = _count_notifications("contract_auto_approved", client_user_id)
        if count >= ContractFunctions._RELIABILITY_WARNING_THRESHOLD:
            return "Less Responsive"
        return "Responsive"

    @staticmethod
    def get_client_autoapprove_history(client_user_id: str) -> List[Dict]:
        """Which contracts triggered an auto-approve strike and when, for admin review of
        an appeal. Read-only, derived from the notifications and contract tables."""
        rows = get_db().execute_query(
            """
            SELECT n.data->>'contract_id' AS contract_id, n.created_at AS notified_at,
                   n.body, c.contract_title, c.status AS contract_status
            FROM notifications n
            LEFT JOIN contract c ON c.contract_id = (n.data->>'contract_id')::uuid
            WHERE n.recipient_id = :uid AND n.type = 'contract_auto_approved'
            ORDER BY n.created_at ASC
            """,
            {"uid": client_user_id},
        )
        return [dict(row) for row in rows or []]