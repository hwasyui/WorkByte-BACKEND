import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Dict

SECTIONS = ["general", "freelancer", "client"]


class GuidelineFunctions:
    """Handle guideline acknowledgement state per user per section."""

    @staticmethod
    def get_ack_status(user_id: str) -> Dict[str, bool]:
        """Return which guideline sections this user has already acknowledged."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="user_guideline_ack",
                columns=["section"],
                conditions=[("user_id", "=", user_id)]
            )

            status = {section: False for section in SECTIONS}
            for row in rows:
                section = dict(row)["section"]
                if section in status:
                    status[section] = True

            logger("GUIDELINES_FUNCTIONS", f"Ack status for user {user_id}: {status}", level="INFO")
            return status

        except Exception as e:
            logger("GUIDELINES_FUNCTIONS", f"Error fetching ack status for user {user_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def ack_section(user_id: str, section: str) -> Dict[str, bool]:
        """Mark a section as acknowledged, then return the full status."""
        try:
            db = get_db()
            query = (
                "INSERT INTO user_guideline_ack (user_id, section) "
                "VALUES (:user_id, :section) "
                "ON CONFLICT (user_id, section) DO UPDATE SET acknowledged_at = NOW()"
            )
            db.execute_query(query, {"user_id": user_id, "section": section})

            logger("GUIDELINES_FUNCTIONS", f"User {user_id} acknowledged section '{section}'", level="INFO")
            return GuidelineFunctions.get_ack_status(user_id)

        except Exception as e:
            logger("GUIDELINES_FUNCTIONS", f"Error acknowledging section '{section}' for user {user_id}: {str(e)}", level="ERROR")
            raise
