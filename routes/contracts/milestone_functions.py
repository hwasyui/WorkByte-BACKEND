import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import uuid
from typing import Dict, List, Optional

from functions.db_manager import get_db
from functions.logger import logger


def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


# Statuses that mirror contract.status, kept as a plain column (not an enum) so a
# contract's schedule can hold milestones that haven't started yet - 'locked' has no
# equivalent in contract_status, which only ever describes the one contract that is
# "in progress" right now.
LOCKED = "locked"
LIVE_STATUSES = (
    "active", "under_review", "revision_requested",
    "pending_payment", "payment_review", "payment_rejected",
)


class MilestoneFunctions:
    """Handle all milestone-related database operations.

    Milestones unlock strictly in sequence_order: exactly one milestone per contract
    is ever 'live' (LOCKED or 'completed'). get_current_milestone resolves that one
    milestone, which is what lets every submission/payment route keep taking just a
    contract_id - the milestone it applies to is never ambiguous.
    """

    @staticmethod
    def create_milestones_for_contract(tx, contract_id: str, milestones: List[Dict]) -> List[Dict]:
        created = []
        for index, m in enumerate(milestones):
            milestone_id = str(uuid.uuid4())
            rows = tx.execute_query(
                """
                INSERT INTO milestone (
                    milestone_id, contract_id, title, description, amount,
                    sequence_order, status, due_date
                ) VALUES (
                    :milestone_id, :contract_id, :title, :description, :amount,
                    :sequence_order, :status, :due_date
                )
                RETURNING *
                """,
                {
                    "milestone_id": milestone_id,
                    "contract_id": contract_id,
                    "title": m["title"],
                    "description": m.get("description"),
                    "amount": m["amount"],
                    "sequence_order": index + 1,
                    # Only the first milestone starts unlocked; the rest wait their turn.
                    "status": "active" if index == 0 else LOCKED,
                    "due_date": m.get("due_date"),
                },
            )
            created.append(convert_uuids_to_str(dict(rows[0])))
        return created

    @staticmethod
    def get_milestones_by_contract_id(contract_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="milestone",
                conditions=[("contract_id", "=", contract_id)],
                order_by="sequence_order ASC",
            )
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error fetching milestones: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_milestone_by_id(milestone_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="milestone",
                conditions=[("milestone_id", "=", milestone_id)],
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error fetching milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_current_milestone(contract_id: str) -> Optional[Dict]:
        """The one milestone this contract is currently working through: the lowest
        sequence_order not yet 'completed'. None once every milestone is paid."""
        try:
            db = get_db()
            rows = db.execute_query(
                """
                SELECT * FROM milestone
                WHERE contract_id = :cid AND status != 'completed'
                ORDER BY sequence_order ASC
                LIMIT 1
                """,
                {"cid": contract_id},
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error fetching current milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_milestone(milestone_id: str, update_data: Dict, tx=None) -> Optional[Dict]:
        try:
            db = tx or get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            if not update_data:
                return MilestoneFunctions.get_milestone_by_id(milestone_id)
            db.update_data(
                table_name="milestone",
                data=update_data,
                conditions=[("milestone_id", "=", milestone_id)],
            )
            return MilestoneFunctions.get_milestone_by_id(milestone_id)
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error updating milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def unlock_next_milestone(contract_id: str, completed_sequence_order: int) -> Optional[Dict]:
        """Called right after a milestone is marked 'completed'. Returns the newly
        unlocked milestone, or None if that was the last one."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="milestone",
                conditions=[
                    ("contract_id", "=", contract_id),
                    ("sequence_order", "=", completed_sequence_order + 1),
                ],
                limit=1,
            )
            if not rows:
                return None
            next_milestone = convert_uuids_to_str(dict(rows[0]))
            db.update_data(
                table_name="milestone",
                data={"status": "active"},
                conditions=[("milestone_id", "=", next_milestone["milestone_id"])],
            )
            logger("MILESTONE_FUNCTIONS", f"Milestone {next_milestone['milestone_id']} unlocked for contract {contract_id}", level="INFO")
            return MilestoneFunctions.get_milestone_by_id(next_milestone["milestone_id"])
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error unlocking next milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def count_revision_rounds(milestone_id: str) -> int:
        """Revision rounds already requested on this one milestone. Each round leaves
        one submission at 'revision_requested' or 'superseded', mirroring
        ContractSubmissionFunctions.count_revision_rounds but scoped down from the
        whole contract's lifetime to just the milestone currently in play."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_submission",
                conditions=[("milestone_id", "=", milestone_id)],
            )
            return sum(1 for r in rows if r.get("status") in ("revision_requested", "superseded"))
        except Exception as e:
            logger("MILESTONE_FUNCTIONS", f"Error counting revision rounds: {str(e)}", level="ERROR")
            raise
