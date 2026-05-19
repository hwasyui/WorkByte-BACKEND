from datetime import datetime
import os
import sys

from routes.dm.dm_functions import DMFunctions
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid


def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings"""
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
    """Handle all contract-related database operations"""

    @staticmethod
    def get_all_contracts(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all contracts"""
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
        """Fetch a contract by ID"""
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
    def get_contracts_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all contracts for a freelancer"""
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
        """Fetch all contracts for a client"""
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
        """Update contract information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("CONTRACT_FUNCTIONS", "No data to update", level="WARNING")
                return ContractFunctions.get_contract_by_id(contract_id)

            # ── Auto-set actual_completion_date when marking as completed ──
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

                # ── Auto-create portfolio entry from completed contract ──────
                # The portfolio row is a flat link to the contract — frontend
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

        Idempotent — does nothing if a portfolio row for this contract already
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
                    f"Auto-portfolio already exists for contract {contract_id} — skip",
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
                f"| freelancer_id={freelancer_id} (NOT embedded — contract_embedding covers it)",
                level="INFO",
            )
        except Exception as e:
            logger(
                "CONTRACT_FUNCTIONS",
                f"Could not auto-create portfolio for contract {contract_id} | error={e}",
                level="WARNING",
            )

    @staticmethod
    def delete_contract(contract_id: str) -> bool:
        """Delete a contract"""
        try:
            db = get_db()
            db.delete_data(table_name="contract", conditions=[("contract_id", "=", contract_id)])
            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} deleted", level="INFO")
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
        """Cancel a contract"""
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            from datetime import date
            update_data = {
                "status": "cancelled",
                "end_date": date.today(),
                "cancelled_by": cancelled_by,
            }
            if reason:
                update_data["cancellation_reason"] = reason
            updated_contract = ContractFunctions.update_contract(contract_id, update_data)

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