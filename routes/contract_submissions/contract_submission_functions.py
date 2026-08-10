import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid
from routes.contracts.contract_functions import ContractFunctions, _fire_notification, _already_notified, _count_notifications
from functions.profile_ids import user_id_for_freelancer
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from routes.clients.client_functions import ClientFunctions

REMINDER_DAYS = 3        # first nudge to the client
FINAL_WARNING_DAYS = 6   # last warning before auto-approve
# Auto-approve protects the freelancer from a silent client. A revision request or
# dispute before day 7 drops the submission out of this sweep and resets the timer.
AUTO_APPROVE_DAYS = 7
AUTO_APPROVE_BAN_THRESHOLD = 3  # lifetime auto-approvals before the client's account is closed

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


class ContractSubmissionFunctions:
    """Handle all contract submission-related database operations."""

    @staticmethod
    def get_contract_by_id(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract",
                conditions=[("contract_id", "=", contract_id)],
                limit=1,
            )
            if rows:
                logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Contract {contract_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error fetching contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_submission(
        contract_id: str,
        submitted_by: str,
        note: Optional[str] = None,
        status: str = "submitted",
    ) -> Dict:
        try:
            db = get_db()
            submission_id = str(uuid.uuid4())

            submission_data = {
                "submission_id": submission_id,
                "contract_id": contract_id,
                "submitted_by": submitted_by,
                "note": note,
                "status": status,
            }

            db.insert_data(table_name="contract_submission", data=submission_data)

            # Update contract status to under_review
            db.update_data(
                table_name="contract",
                data={"status": "under_review"},
                conditions=[("contract_id", "=", contract_id)],
            )

            # WORK SUBMITTED: system message after all DB ops succeed
            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=submitted_by,
                    message_text="Work submitted for review.",
                    event_type="submission_created",
                    metadata={"submission_id": submission_id, "submitted_by": submitted_by},
                )
            except Exception:
                pass

            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Submission {submission_id} created with system message", level="INFO")
            return ContractSubmissionFunctions.get_submission_by_id(submission_id)
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error creating submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def add_submission_file(
        submission_id: str,
        file_url: str,
        file_name: str,
        file_size_bytes: Optional[int] = None,
        mime_type: Optional[str] = None,
    ) -> Dict:
        try:
            db = get_db()
            file_id = str(uuid.uuid4())

            file_data = {
                "file_id": file_id,
                "submission_id": submission_id,
                "file_url": file_url,
                "file_name": file_name,
                "file_size_bytes": file_size_bytes,
                "mime_type": mime_type,
            }

            db.insert_data(table_name="contract_submission_file", data=file_data)
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"File {file_name} added to submission {submission_id}", level="INFO")
            return convert_uuids_to_str(file_data)
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error adding submission file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_submission_files(submission_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission_file",
                conditions=[("submission_id", "=", submission_id)],
                order_by="uploaded_at ASC",
            )
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error fetching submission files: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_submission_by_id(submission_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission",
                conditions=[("submission_id", "=", submission_id)],
                limit=1,
            )
            if rows:
                submission = convert_uuids_to_str(dict(rows[0]))
                submission["files"] = ContractSubmissionFunctions.get_submission_files(submission_id)
                logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Submission {submission_id} found", level="INFO")
                return submission
            return None
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error fetching submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_submissions_by_contract_id(contract_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission",
                conditions=[("contract_id", "=", contract_id)],
                order_by="submitted_at DESC",
            )
            submissions = []
            for row in rows:
                submission = convert_uuids_to_str(dict(row))
                submission["files"] = ContractSubmissionFunctions.get_submission_files(submission["submission_id"])
                submissions.append(submission)
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Fetched {len(submissions)} submissions for contract {contract_id}", level="INFO")
            return submissions
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error fetching submissions: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_latest_submission_by_contract_id(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission",
                conditions=[("contract_id", "=", contract_id)],
                order_by="submitted_at DESC",
                limit=1,
            )
            if rows:
                submission = convert_uuids_to_str(dict(rows[0]))
                submission["files"] = ContractSubmissionFunctions.get_submission_files(submission["submission_id"])
                logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Latest submission found for contract {contract_id}", level="INFO")
                return submission
            return None
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error fetching latest submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_submission(submission_id: str, update_data: Dict) -> Optional[Dict]:
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("CONTRACT_SUBMISSION_FUNCTIONS", "No submission data to update", level="WARNING")
                return ContractSubmissionFunctions.get_submission_by_id(submission_id)

            db.update_data(
                table_name="contract_submission",
                data=update_data,
                conditions=[("submission_id", "=", submission_id)],
            )
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Submission {submission_id} updated", level="INFO")
            return ContractSubmissionFunctions.get_submission_by_id(submission_id)
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error updating submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def count_revision_rounds(contract_id: str) -> int:
        """Count revision rounds already requested on this contract. Each round leaves
        one submission at 'revision_requested' or 'superseded', so counting those
        avoids a dedicated counter column."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission",
                conditions=[("contract_id", "=", contract_id)],
            )
            return sum(1 for r in rows if r.get("status") in ("revision_requested", "superseded"))
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error counting revision rounds: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def supersede_latest_revision_requested_submission(contract_id: str) -> Optional[Dict]:
        try:
            latest_submission = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)
            if not latest_submission:
                return None
            if latest_submission.get("status") != "revision_requested":
                return latest_submission
            return ContractSubmissionFunctions.update_submission(
                latest_submission["submission_id"],
                {"status": "superseded"},
            )
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error superseding latest submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def request_revision_for_latest_submission(contract_id: str, note: Optional[str] = None) -> Optional[Dict]:
        try:
            latest_submission = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)
            if not latest_submission:
                return None

            submission_id = latest_submission["submission_id"]

            contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            # Resolve client's user_id from client profile
            db = get_db()
            client_rows = db.fetch_data("client", conditions=[("client_id", "=", str(contract["client_id"]))], limit=1)
            if not client_rows:
                raise Exception("Client not found")
            actor_user_id = str(client_rows[0]["user_id"])  # actual user_id, not client_id

            # Persist the note and review time here so there's a structured record,
            # not just the DM system message below.
            ContractSubmissionFunctions.update_submission(
                submission_id,
                {
                    "status": "revision_requested",
                    "revision_note": note,
                    "reviewed_at": datetime.now(timezone.utc),
                },
            )

            db.update_data(
                table_name="contract",
                data={"status": "revision_requested"},
                conditions=[("contract_id", "=", contract_id)],
            )

            message_text = f"Revision requested: {note}" if note else "Revision requested."

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=actor_user_id,
                    message_text=message_text,
                    event_type="revision_requested",
                    metadata={"submission_id": submission_id, "note": note},
                )
            except Exception:
                pass

            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Revision requested for submission {submission_id}", level="INFO")
            return ContractSubmissionFunctions.get_submission_by_id(submission_id)

        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error requesting revision: {str(e)}", level="ERROR")
            raise
        
    @staticmethod
    def approve_latest_submission(contract_id: str) -> Optional[Dict]:
        try:
            latest_submission = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)
            if not latest_submission:
                return None

            submission_id = latest_submission["submission_id"]

            contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            # Resolve client's actual user_id from client profile
            db = get_db()
            client_rows = db.fetch_data("client", conditions=[("client_id", "=", str(contract["client_id"]))], limit=1)
            if not client_rows:
                raise Exception("Client not found")
            actor_user_id = str(client_rows[0]["user_id"])  # user_id, not client_id

            ContractSubmissionFunctions.update_submission(
                submission_id,
                {"status": "approved", "reviewed_at": datetime.now(timezone.utc)},
            )

            ContractFunctions.update_contract(
                contract_id=contract_id,
                update_data={"status": "pending_payment"},
            )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=actor_user_id,
                    message_text="Work approved. Waiting for payment.",
                    event_type="submission_approved",
                    metadata={"submission_id": submission_id, "approved_by": actor_user_id},
                )
            except Exception:
                pass

            try:
                _fire_notification(NotificationFunctions.notify(
                    recipient_user_id=actor_user_id,
                    notif_type="payment_due",
                    title="Time to pay",
                    body=f"All work on \"{contract.get('contract_title')}\" has been approved. Please upload your payment proof to complete the contract.",
                    data={"contract_id": contract_id},
                ))
            except Exception:
                pass

            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Latest submission {submission_id} approved for contract {contract_id}", level="INFO")
            return ContractSubmissionFunctions.get_submission_by_id(submission_id)

        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error approving submission: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def run_autoapprove_sweep() -> int:
        """Protects the freelancer from a silent client: reminder at day 3, final warning
        at day 6, auto-approve at day 7 through approve_latest_submission.

        A revision request or dispute moves the row out of the WHERE clause below, so the
        timer resets on its own. Reminder dedup goes through the notifications table."""
        try:
            rows = get_db().execute_query(
                """
                SELECT DISTINCT ON (cs.contract_id)
                    cs.submission_id, cs.contract_id, cs.submitted_at,
                    c.freelancer_id, c.client_id, c.contract_title
                FROM contract_submission cs
                JOIN contract c ON c.contract_id = cs.contract_id
                WHERE c.status = 'under_review' AND cs.status = 'submitted'
                ORDER BY cs.contract_id, cs.submitted_at DESC
                """,
                {},
            )

            now = datetime.now(timezone.utc)
            auto_approved = 0
            for row in rows or []:
                contract_id = str(row["contract_id"])
                submission_id = str(row["submission_id"])
                submitted_at = row["submitted_at"]
                if submitted_at.tzinfo is None:
                    submitted_at = submitted_at.replace(tzinfo=timezone.utc)
                days_elapsed = (now - submitted_at).days

                client = ClientFunctions.get_client_by_id(str(row["client_id"]))
                if not client:
                    continue
                client_user_id = str(client["user_id"])
                title = row.get("contract_title") or "your contract"

                if days_elapsed >= AUTO_APPROVE_DAYS:
                    ContractSubmissionFunctions.approve_latest_submission(contract_id)

                    # Strikes are counted off the notifications table rather than a
                    # counter column. Count before sending so the message matches the
                    # strike this occurrence becomes.
                    strike_count = _count_notifications("contract_auto_approved", client_user_id) + 1
                    if strike_count >= AUTO_APPROVE_BAN_THRESHOLD:
                        strike_body = (
                            f"\"{title}\" was auto-approved after {AUTO_APPROVE_DAYS} days without a response - "
                            f"that's {strike_count} contract(s) auto-approved from inactivity, so your account has been closed."
                        )
                    elif strike_count == AUTO_APPROVE_BAN_THRESHOLD - 1:
                        strike_body = (
                            f"Warning: \"{title}\" was auto-approved after {AUTO_APPROVE_DAYS} days without a response - "
                            f"that's {strike_count} contracts auto-approved from inactivity. One more and your account will be closed automatically."
                        )
                    else:
                        strike_body = f"\"{title}\" was auto-approved after {AUTO_APPROVE_DAYS} days without a response from you. Please respond to submissions promptly to avoid account restrictions."

                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=client_user_id,
                        notif_type="contract_auto_approved",
                        title="Contract auto-approved",
                        body=strike_body,
                        data={"contract_id": contract_id},
                    ))

                    # The client's notification above is a strike notice, so it cannot
                    # double as the freelancer's. Without this the contract reaches the
                    # same completed state as a manual approval but only one side hears
                    # about it. user_id_for_freelancer returns None on a deleted profile
                    # rather than raising, which would strand the rest of the sweep.
                    freelancer_user_id = user_id_for_freelancer(str(row["freelancer_id"]))
                    if freelancer_user_id:
                        _fire_notification(NotificationFunctions.notify(
                            recipient_user_id=freelancer_user_id,
                            notif_type="work_auto_approved",
                            title="Work auto-approved",
                            body=f"\"{title}\" was auto-approved after {AUTO_APPROVE_DAYS} days without a response from the client. Waiting on payment now.",
                            data={"contract_id": contract_id},
                        ))
                    if strike_count >= AUTO_APPROVE_BAN_THRESHOLD:
                        from routes.admin.admin_functions import admin_close_account
                        admin_close_account(
                            user_id=client_user_id,
                            admin_user_id="system:autoapprove_penalty",
                            reason=f"Account automatically closed: {strike_count} contracts auto-approved from client inactivity.",
                        )
                        logger(
                            "CONTRACT_SUBMISSION_FUNCTIONS",
                            f"Client {client_user_id} auto-banned after {strike_count} auto-approved contracts",
                            level="WARNING",
                        )

                    auto_approved += 1
                    continue

                if days_elapsed >= FINAL_WARNING_DAYS:
                    if not _already_notified("contract_autoapprove_final_warning", "submission_id", submission_id):
                        _fire_notification(NotificationFunctions.notify(
                            recipient_user_id=str(client["user_id"]),
                            notif_type="contract_autoapprove_final_warning",
                            title="Final reminder: review pending work",
                            body=f"\"{title}\" will be auto-approved in {AUTO_APPROVE_DAYS - days_elapsed} day(s) if you don't act.",
                            data={"contract_id": contract_id, "submission_id": submission_id},
                        ))
                elif days_elapsed >= REMINDER_DAYS:
                    if not _already_notified("contract_autoapprove_reminder", "submission_id", submission_id):
                        _fire_notification(NotificationFunctions.notify(
                            recipient_user_id=str(client["user_id"]),
                            notif_type="contract_autoapprove_reminder",
                            title="Reminder: review pending work",
                            body=f"Submitted work on \"{title}\" is still waiting for your review.",
                            data={"contract_id": contract_id, "submission_id": submission_id},
                        ))

            if auto_approved:
                logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Autoapprove sweep: auto-approved {auto_approved} submission(s)", level="INFO")
            return auto_approved
        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error in autoapprove sweep: {str(e)}", level="ERROR")
            return 0