from datetime import datetime, date, timezone
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


def _format_contract_created_text(data: dict) -> str:
    budget = data.get("agreed_budget") or 0
    currency = data.get("budget_currency") or "USD"
    payment = (data.get("payment_structure") or "").replace("_", " ").title()
    duration = data.get("agreed_duration") or "Not specified"
    start = data.get("start_date") or "Not specified"
    role = data.get("role_title") or "Not specified"
    status = (data.get("status") or "").title()
    return (
        f"Contract started\n\n"
        f"Title         : {data.get('contract_title')}\n"
        f"Role          : {role}\n"
        f"Budget        : {currency} {budget:,.2f}\n"
        f"Payment type  : {payment}\n"
        f"Start date    : {start}\n"
        f"Duration      : {duration}\n"
        f"Status        : {status}"
    )


class ContractFunctions:
    """Handle all contract-related database operations."""

    @staticmethod
    def get_all_contracts(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all contracts."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract",
                columns=[
                    "contract_id", "job_post_id", "job_role_id", "proposal_id",
                    "freelancer_id", "client_id", "contract_title", "role_title",
                    "agreed_budget", "budget_currency", "payment_structure",
                    "agreed_duration", "status", "start_date", "end_date",
                    "actual_completion_date", "total_hours_worked", "total_paid",
                    "contract_pdf_url", "contract_pdf_generated_at", "created_at", "updated_at",
                ],
                order_by="created_at DESC",
                limit=limit,
            )
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

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
    def get_contracts_by_job_post_id(job_post_id: str) -> List[Dict]:
        """Fetch all contracts under a job post, any status - used to pre-check
        deletability, since contract.job_post_id is ON DELETE RESTRICT."""
        try:
            db = get_db()
            rows = db.fetch_data(table_name="contract", conditions=[("job_post_id", "=", job_post_id)])
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts for job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contracts_by_job_role_id(job_role_id: str) -> List[Dict]:
        """Fetch all contracts under a job role, any status - used to pre-check
        deletability, since contract.job_role_id is ON DELETE RESTRICT."""
        try:
            db = get_db()
            rows = db.fetch_data(table_name="contract", conditions=[("job_role_id", "=", job_role_id)])
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts for job role: {str(e)}", level="ERROR")
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
            return [convert_uuids_to_str(dict(row)) for row in rows]
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
            return [convert_uuids_to_str(dict(row)) for row in rows]
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
        contract_id: Optional[str] = None,
        role_title: Optional[str] = None,
        budget_currency: Optional[str] = "USD",
        agreed_duration: Optional[str] = None,
        status: Optional[str] = "active",
        end_date=None,
        actual_completion_date=None,
        total_hours_worked: Optional[float] = None,
        total_paid: Optional[float] = 0,
    ) -> Dict:
        try:
            db = get_db()
            contract_id = contract_id or str(uuid.uuid4())

            contract_data = {
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
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "actual_completion_date": actual_completion_date,
                "total_hours_worked": total_hours_worked,
                "total_paid": total_paid,
            }

            db.insert_data(table_name="contract", data=contract_data)

            # Resolve actual user_id from client profile
            client_rows = db.fetch_data(
                table_name="client",
                conditions=[("client_id", "=", client_id)],
                limit=1,
            )
            if not client_rows:
                raise Exception(f"Client profile not found for client_id: {client_id}")

            actor_user_id = str(client_rows[0]["user_id"])

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} created", level="INFO")
            return convert_uuids_to_str(contract_data)
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

                # Auto-create portfolio entry from completed contract.
                # The portfolio row is a flat link to the contract; frontend
                # joins back via contract_id to display the full work record.
                # NOT embedded in portfolio_embedding: contract_embedding
                # already covers this (rating + review + description); duplicating
                # would create a sync problem when the rating updates later.
                ContractFunctions._create_auto_portfolio_entry(
                    contract_id=contract_id,
                    contract=existing_contract,
                )

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} updated", level="INFO")
            return ContractFunctions.get_contract_by_id(contract_id)
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error updating contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _create_auto_portfolio_entry(contract_id: str, contract: Dict) -> None:
        """
        Create a portfolio row tied to a completed contract.

        Idempotent: does nothing if a portfolio row for this contract already
        exists. The new row is flagged is_auto_generated=TRUE; embeddings for
        these rows live in contract_embedding, not portfolio_embedding, so
        portfolio_embedding only ever holds user-curated showcase items.

        Failures are swallowed: the contract completion must not be blocked by
        a portfolio insert error.
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
    def _revert_proposal_on_contract_removal(proposal_id: str) -> None:
        """Keep proposal.status truthful once its contract is gone: 'accepted'
        is only supposed to mean there's a live contract behind it, so once
        that contract is cancelled/deleted, flip the proposal to 'rejected'
        instead of leaving it stuck showing 'accepted' for work that no
        longer exists. Non-fatal - this must never break the actual
        cancel/delete operation."""
        try:
            proposal = ProposalFunctions.get_proposal_by_id(str(proposal_id))
            if proposal and proposal.get("status") == "accepted":
                ProposalFunctions.update_proposal(str(proposal_id), {"status": "rejected"})
                logger(
                    "CONTRACT_FUNCTIONS",
                    f"Proposal {proposal_id} reverted to 'rejected' after its contract was removed",
                    level="INFO",
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
                ContractFunctions._revert_proposal_on_contract_removal(contract["proposal_id"])

            return True
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error deleting contract: {str(e)}", level="ERROR")
            raise

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

            update_data = {
                "status": "cancelled",
                "end_date": datetime.now(timezone.utc).date(),
                "cancelled_by": cancelled_by,
            }
            if reason:
                update_data["cancellation_reason"] = reason
            updated_contract = ContractFunctions.update_contract(contract_id, update_data)

            if contract.get("proposal_id"):
                ContractFunctions._revert_proposal_on_contract_removal(contract["proposal_id"])

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
    def report_payment(contract_id: str, amount: float, reported_by: str, note: Optional[str] = None) -> Optional[Dict]:
        """Client self-reports a payment made off-platform (payment stays outside the
        system by design - see business decision). Cumulative: adds to whatever total_paid
        already holds, so partial payments before a later cancel remain on record as
        context the freelancer can dispute if it's wrong."""
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            current_total = float(contract.get("total_paid") or 0)
            new_total = current_total + amount
            updated_contract = ContractFunctions.update_contract(contract_id, {"total_paid": new_total})

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=reported_by,
                    message_text=f"Payment of {amount:,.2f} reported. Total paid so far: {new_total:,.2f}.",
                    event_type="payment_reported",
                    metadata={"amount": amount, "total_paid": new_total, "note": note},
                )
            except Exception:
                pass

            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} payment reported: +{amount} (total {new_total})", level="INFO")
            return updated_contract
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error reporting payment: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def notify_overdue_contracts() -> int:
        """Informational-only deadline sweep (business decision: no automatic status
        change - freelancer/client resolve it themselves, this just turns on visibility).
        Dedup goes through the existing `notifications` table instead of a dedicated
        column: one notification per contract, ever, is enough since the deadline
        itself doesn't move unless the contract is edited."""
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
                        title="Contract Past Deadline",
                        body=body,
                        data={"contract_id": contract_id},
                    ))
                if freelancer:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(freelancer["user_id"]),
                        notif_type="contract_overdue",
                        title="Contract Past Deadline",
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

    # A client sits at this many lifetime auto-approved contracts one strike away
    # from AUTO_APPROVE_BAN_THRESHOLD (3, see contract_submission_functions.py) before
    # the qualitative label below flips - gives freelancers a signal right when it
    # matters, without exposing the raw strike count (product decision).
    _RELIABILITY_WARNING_THRESHOLD = 2

    @staticmethod
    def get_client_reliability_label(client_user_id: str) -> str:
        """Qualitative signal for freelancers deciding whether to work with a client -
        derived on read from the same `contract_auto_approved` notification count used
        for the ban penalty, no separate storage needed."""
        count = _count_notifications("contract_auto_approved", client_user_id)
        if count >= ContractFunctions._RELIABILITY_WARNING_THRESHOLD:
            return "Kurang Responsif"
        return "Responsif"

    @staticmethod
    def get_client_autoapprove_history(client_user_id: str) -> List[Dict]:
        """Read-only audit trail for admin review (e.g. when a client appeals the
        3-strike auto-ban) - which contracts triggered a strike and when, so admin
        doesn't have to reconstruct it by hand from raw notification rows. Derived
        entirely from the existing `notifications` + `contract` tables, nothing new
        stored. This is monitoring only - it does not expose any approve/cancel
        action, the ban itself already happens automatically without admin input."""
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

            updated_contract = ContractFunctions.update_contract(contract_id, {"status": "disputed"})

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

            if outcome == "approve":
                latest_submission = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)
                if latest_submission and latest_submission.get("status") == "submitted":
                    ContractSubmissionFunctions.approve_latest_submission(contract_id)
                else:
                    ContractFunctions.update_contract(contract_id, {"status": "completed"})
                from ai_related.review_analysis.review_pipeline import run_post_completion_pipeline
                _fire_notification(run_post_completion_pipeline(contract_id))
            elif outcome == "cancel":
                ContractFunctions.cancel_contract(contract_id, cancelled_by=admin_user_id, reason=note)
            elif outcome == "revise":
                if not new_deadline:
                    raise ValueError("new_deadline is required when outcome='revise'")
                ContractSubmissionFunctions.request_revision_for_latest_submission(contract_id, note=note)
                ContractFunctions.update_contract(contract_id, {"end_date": new_deadline})
            else:
                raise ValueError(f"Invalid outcome: {outcome}")

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