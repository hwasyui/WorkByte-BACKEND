import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid
from routes.contracts.contract_functions import ContractFunctions
from routes.dm.dm_functions import DMFunctions


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


class ContractSubmissionFunctions:
    """Handle all contract submission-related database operations"""

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
            actor_user_id = str(client_rows[0]["user_id"])  # ← actual user_id, not client_id

            ContractSubmissionFunctions.update_submission(
                submission_id,
                {"status": "revision_requested"},
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
            actor_user_id = str(client_rows[0]["user_id"])  # ← user_id, not client_id

            ContractSubmissionFunctions.update_submission(
                submission_id,
                {"status": "approved"},
            )

            ContractFunctions.update_contract(
                contract_id=contract_id,
                update_data={"status": "completed"},
            )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=actor_user_id,
                    message_text="Work approved. Contract completed.",
                    event_type="submission_approved",
                    metadata={"submission_id": submission_id, "approved_by": actor_user_id},
                )
            except Exception:
                pass

            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Latest submission {submission_id} approved for contract {contract_id}", level="INFO")
            return ContractSubmissionFunctions.get_submission_by_id(submission_id)

        except Exception as e:
            logger("CONTRACT_SUBMISSION_FUNCTIONS", f"Error approving submission: {str(e)}", level="ERROR")
            raise