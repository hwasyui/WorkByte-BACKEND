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


class EducationFunctions:
    """Handle all education-related database operations."""

    @staticmethod
    def get_all_educations(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all educations."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="education",
                columns=["education_id", "freelancer_id", "institution_name", "degree", "field_of_study",
                        "start_date", "end_date", "is_current", "grade", "description", "created_at", "updated_at"],
                order_by="start_date DESC",
                limit=limit,
            )
            
            logger("EDUCATION_FUNCTIONS", f"Fetched {len(rows)} educations", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error fetching educations: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_education_by_id(education_id: str) -> Optional[Dict]:
        """Fetch an education by ID."""
        try:
            db = get_db()
            conditions = [("education_id", "=", education_id)]
            rows = db.fetch_data(
                table_name="education",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("EDUCATION_FUNCTIONS", f"Education {education_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error fetching education: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_educations_by_freelancer_id(freelancer_id: str, visible_only: bool = True) -> List[Dict]:
        """Fetch all educations for a freelancer. visible_only=False is for the owner
        viewing their own list (they must still see blocked/scanning entries)."""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            if visible_only:
                conditions.append(("moderation_status", "=", "visible"))
            rows = db.fetch_data(
                table_name="education",
                conditions=conditions,
                order_by="start_date DESC"
            )

            logger("EDUCATION_FUNCTIONS", f"Fetched {len(rows)} educations for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error fetching educations: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_education(freelancer_id: str, institution_name: str, degree: str,
                         start_date, field_of_study: Optional[str] = None, end_date=None,
                         is_current: Optional[bool] = False, grade: Optional[str] = None,
                         description: Optional[str] = None) -> Dict:
        """Create a new education."""
        try:
            db = get_db()
            education_id = str(uuid.uuid4())

            education_data = {
                "education_id": education_id,
                "freelancer_id": freelancer_id,
                "institution_name": institution_name,
                "degree": degree,
                "field_of_study": field_of_study,
                "start_date": start_date,
                "end_date": end_date,
                "is_current": is_current,
                "grade": grade,
                "description": description,
                "moderation_status": "scanning",
            }

            db.insert_data(table_name="education", data=education_data)

            logger("EDUCATION_FUNCTIONS", f"Education {education_id} created", level="INFO")
            return convert_uuids_to_str(education_data)

        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error creating education: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_education(education_id: str, update_data: Dict) -> Optional[Dict]:
        """Update education information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("EDUCATION_FUNCTIONS", "No data to update", level="WARNING")
                return EducationFunctions.get_education_by_id(education_id)

            conditions = [("education_id", "=", education_id)]
            db.update_data(table_name="education", data=update_data, conditions=conditions)

            logger("EDUCATION_FUNCTIONS", f"Education {education_id} updated", level="INFO")
            return EducationFunctions.get_education_by_id(education_id)

        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error updating education: {str(e)}", level="ERROR")
            raise

    @staticmethod
    async def run_education_scan(education_id: str, short_text: str, long_text: str, freelancer_user_id: str) -> None:
        """scanning -> scan -> visible | blocked. Mirrors ProposalFunctions.run_proposal_scan -
        content_type/content_id are this entry's own ('education', education_id).

        institution_name/degree/field_of_study/grade carry no context - a 1-4 word field
        gives the ML model nothing to condition on, so they are keyword-only (short_text).
        description has real sentence context, so it goes through the ML model (long_text).
        See scan_short_and_long_text() in admin_moderation.py."""
        try:
            EducationFunctions.update_education(education_id, {"moderation_status": "scanning"})

            result = await scan_short_and_long_text(short_text, long_text)
            scan_text = " ".join(filter(None, [short_text, long_text]))

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                EducationFunctions.update_education(education_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                })
                logger(
                    "EDUCATION_FUNCTIONS",
                    f"Education {education_id} blocked, labels={result.get('detected_labels')}",
                    level="WARNING",
                )
                insert_harmful_text_queue_entry(
                    "education", education_id, freelancer_user_id, scan_text, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in result.get("detected_labels", [])]
                try:
                    await NotificationFunctions.notify(
                        recipient_user_id=freelancer_user_id,
                        notif_type="education_blocked",
                        title="Education Entry Needs Changes",
                        body=f"Your education entry was flagged for {', '.join(labels) or 'a policy violation'}. Edit and resubmit.",
                        data={"education_id": education_id},
                    )
                except Exception as notif_err:
                    logger("EDUCATION_FUNCTIONS", f"Blocked-entry notification failed (non-fatal): {notif_err}", level="WARNING")
            else:
                EducationFunctions.update_education(education_id, {
                    "moderation_status": "visible",
                    "scanned_at": scanned_at,
                })

        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Education scan failed for {education_id}: {e}", level="ERROR")

    @staticmethod
    def delete_education(education_id: str) -> bool:
        """Delete an education."""
        try:
            db = get_db()
            conditions = [("education_id", "=", education_id)]
            db.delete_data(table_name="education", conditions=conditions)
            
            logger("EDUCATION_FUNCTIONS", f"Education {education_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("EDUCATION_FUNCTIONS", f"Error deleting education: {str(e)}", level="ERROR")
            raise
