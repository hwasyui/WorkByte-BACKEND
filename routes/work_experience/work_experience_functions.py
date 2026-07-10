import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from functions.db_manager import get_db
from functions.logger import logger
from routes.admin.admin_moderation import scan_short_and_long_text, insert_harmful_text_queue_entry
from routes.notifications.notification_functions import NotificationFunctions
from typing import List, Optional, Dict
import uuid

# harm labels reported to the freelancer when an entry gets blocked, never the matched text
_LABEL_DISPLAY_NAMES = {
    "toxic": "toxicity",
    "toxicity": "toxicity",
    "obscene": "obscenity",
    "threat": "threats",
    "insult": "insults",
    "identity_hate": "identity-based hate speech",
}

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


class WorkExperienceFunctions:
    """Handle all work experience-related database operations."""

    @staticmethod
    def get_all_work_experiences(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all work experiences."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="work_experience",
                columns=["work_experience_id", "freelancer_id", "job_title", "company_name", "location", 
                        "start_date", "end_date", "is_current", "description", "created_at", "updated_at"],
                order_by="start_date DESC",
                limit=limit,
            )
            
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Fetched {len(rows)} work experiences", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error fetching work experiences: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_work_experience_by_id(work_experience_id: str) -> Optional[Dict]:
        """Fetch a work experience by ID."""
        try:
            db = get_db()
            conditions = [("work_experience_id", "=", work_experience_id)]
            rows = db.fetch_data(
                table_name="work_experience",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("WORK_EXPERIENCE_FUNCTIONS", f"Work experience {work_experience_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error fetching work experience: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_work_experiences_by_freelancer_id(freelancer_id: str, visible_only: bool = True) -> List[Dict]:
        """Fetch all work experiences for a freelancer. visible_only=False is for the owner
        viewing their own list (they must still see blocked/scanning entries)."""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            if visible_only:
                conditions.append(("moderation_status", "=", "visible"))
            rows = db.fetch_data(
                table_name="work_experience",
                conditions=conditions,
                order_by="start_date DESC"
            )

            logger("WORK_EXPERIENCE_FUNCTIONS", f"Fetched {len(rows)} work experiences for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error fetching work experiences: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_work_experience(freelancer_id: str, job_title: str, company_name: str,
                               start_date, end_date=None, location: Optional[str] = None,
                               is_current: Optional[bool] = False, description: Optional[str] = None) -> Dict:
        """Create a new work experience."""
        try:
            db = get_db()
            work_experience_id = str(uuid.uuid4())

            work_experience_data = {
                "work_experience_id": work_experience_id,
                "freelancer_id": freelancer_id,
                "job_title": job_title,
                "company_name": company_name,
                "location": location,
                "start_date": start_date,
                "end_date": end_date,
                "is_current": is_current,
                "description": description,
                "moderation_status": "scanning",
            }

            db.insert_data(table_name="work_experience", data=work_experience_data)

            logger("WORK_EXPERIENCE_FUNCTIONS", f"Work experience {work_experience_id} created", level="INFO")
            return convert_uuids_to_str(work_experience_data)

        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error creating work experience: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_work_experience(work_experience_id: str, update_data: Dict) -> Optional[Dict]:
        """Update work experience information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("WORK_EXPERIENCE_FUNCTIONS", "No data to update", level="WARNING")
                return WorkExperienceFunctions.get_work_experience_by_id(work_experience_id)

            conditions = [("work_experience_id", "=", work_experience_id)]
            db.update_data(table_name="work_experience", data=update_data, conditions=conditions)

            logger("WORK_EXPERIENCE_FUNCTIONS", f"Work experience {work_experience_id} updated", level="INFO")
            return WorkExperienceFunctions.get_work_experience_by_id(work_experience_id)

        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error updating work experience: {str(e)}", level="ERROR")
            raise

    @staticmethod
    async def run_work_experience_scan(work_experience_id: str, short_text: str, long_text: str, freelancer_user_id: str) -> None:
        """scanning -> scan -> visible | blocked. Mirrors ProposalFunctions.run_proposal_scan -
        content_type/content_id are this entry's own ('work_experience', work_experience_id).

        job_title/company_name/location carry no context - a 1-4 word field gives the ML
        model nothing to condition on, so they are keyword-only (short_text). description
        has real sentence context, so it goes through the ML model (long_text). See
        scan_short_and_long_text() in admin_moderation.py."""
        try:
            WorkExperienceFunctions.update_work_experience(work_experience_id, {"moderation_status": "scanning"})

            result = await scan_short_and_long_text(short_text, long_text)
            scan_text = " ".join(filter(None, [short_text, long_text]))

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                WorkExperienceFunctions.update_work_experience(work_experience_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                })
                logger(
                    "WORK_EXPERIENCE_FUNCTIONS",
                    f"Work experience {work_experience_id} blocked, labels={result.get('detected_labels')}",
                    level="WARNING",
                )
                insert_harmful_text_queue_entry(
                    "work_experience", work_experience_id, freelancer_user_id, scan_text, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in result.get("detected_labels", [])]
                try:
                    await NotificationFunctions.notify(
                        recipient_user_id=freelancer_user_id,
                        notif_type="work_experience_blocked",
                        title="Work Experience Entry Needs Changes",
                        body=f"Your work experience entry was flagged for {', '.join(labels) or 'a policy violation'}. Edit and resubmit.",
                        data={"work_experience_id": work_experience_id},
                    )
                except Exception as notif_err:
                    logger("WORK_EXPERIENCE_FUNCTIONS", f"Blocked-entry notification failed (non-fatal): {notif_err}", level="WARNING")
            else:
                WorkExperienceFunctions.update_work_experience(work_experience_id, {
                    "moderation_status": "visible",
                    "scanned_at": scanned_at,
                })

        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Work experience scan failed for {work_experience_id}: {e}", level="ERROR")

    @staticmethod
    def delete_work_experience(work_experience_id: str) -> bool:
        """Delete a work experience."""
        try:
            db = get_db()
            conditions = [("work_experience_id", "=", work_experience_id)]
            db.delete_data(table_name="work_experience", conditions=conditions)
            
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Work experience {work_experience_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("WORK_EXPERIENCE_FUNCTIONS", f"Error deleting work experience: {str(e)}", level="ERROR")
            raise
