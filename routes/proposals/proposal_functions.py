import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from functions.db_manager import get_db
from functions.logger import logger
from routes.job_posts.job_post_functions import JobPostFunctions
from routes.admin.admin_moderation import scan_harmful_text_with_ml_fallback, insert_harmful_text_queue_entry
from routes.notifications.notification_functions import NotificationFunctions
from typing import List, Optional, Dict
import uuid

# harm labels reported to the freelancer when a proposal gets blocked, never the matched text
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


class ProposalFunctions:
    """Handle all proposal-related database operations."""

    @staticmethod
    def get_all_proposals(limit: Optional[int] = None, visible_only: bool = True) -> List[Dict]:
        """fetch all proposals. visible_only hides scanning/blocked ones (no viewer context here)."""
        try:
            db = get_db()
            conditions = [("moderation_status", "=", "visible")] if visible_only else None
            rows = db.fetch_data(
                table_name="proposal",
                columns=[
                    "proposal_id", "job_post_id", "job_role_id", "freelancer_id",
                    "cover_letter", "proposed_budget", "proposed_duration",
                    "status", "is_ai_generated", "submitted_at",
                    "moderation_status", "scanned_at",
                ],
                conditions=conditions,
                order_by="submitted_at DESC",
                limit=limit,
            )
            logger("PROPOSAL_FUNCTIONS", f"Fetched {len(rows)} proposals", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_by_id(proposal_id: str) -> Optional[Dict]:
        """Fetch a proposal by ID."""
        try:
            db = get_db()
            conditions = [("proposal_id", "=", proposal_id)]
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                limit=1,
            )
            if rows:
                logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            return None

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_job_post_id(job_post_id: str) -> List[Dict]:
        """Fetch all proposals for a job post."""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                order_by="submitted_at DESC",
            )
            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(rows)} proposals for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    _JOB_POST_PROPOSAL_SORT_COLS = {
        "submitted_at":     "p.submitted_at",
        "proposed_budget":  "p.proposed_budget",
        "total_jobs":       "f.total_jobs",
    }

    @staticmethod
    def get_proposals_by_job_post_id_enriched(
        job_post_id: str,
        visible_only: bool = True,
        order_by: str = "submitted_at",
        order_dir: str = "desc",
    ) -> List[Dict]:
        """fetch all proposals for a job post with freelancer info joined.
        visible_only=True is what the client (job post owner) should always get."""
        try:
            db = get_db()
            sort_col = ProposalFunctions._JOB_POST_PROPOSAL_SORT_COLS.get(order_by, "p.submitted_at")
            sort_dir = "ASC" if order_dir == "asc" else "DESC"
            query = """
                SELECT
                    p.proposal_id, p.job_post_id, p.job_role_id, p.freelancer_id,
                    p.cover_letter, p.proposed_budget, p.proposed_duration,
                    p.status, p.is_ai_generated, p.submitted_at,
                    p.moderation_status, p.scanned_at,
                    f.full_name           AS freelancer_name,
                    f.profile_picture_url,
                    f.estimated_rate,
                    f.rate_currency,
                    f.rate_time,
                    f.total_jobs
                FROM proposal p
                JOIN freelancer f ON p.freelancer_id = f.freelancer_id
                WHERE p.job_post_id = :job_post_id
            """ + (" AND p.moderation_status = 'visible'" if visible_only else "") + f"""
                ORDER BY {sort_col} {sort_dir}
            """
            rows = db.execute_query(query, {"job_post_id": job_post_id})
            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(rows)} enriched proposals for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching enriched proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_freelancer_id(freelancer_id: str, visible_only: bool = True) -> List[Dict]:
        """fetch all proposals from a freelancer. visible_only=False is for the
        freelancer viewing their own list (they must still see blocked ones)."""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            if visible_only:
                conditions.append(("moderation_status", "=", "visible"))
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                order_by="submitted_at DESC",
            )
            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(rows)} proposals from freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_for_freelancer_job(freelancer_id: str, job_post_id: str) -> Optional[Dict]:
        """Fetch an existing proposal from a freelancer for one job post."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="proposal",
                conditions=[
                    ("freelancer_id", "=", freelancer_id),
                    ("job_post_id", "=", job_post_id),
                ],
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error checking duplicate proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_for_freelancer_role(
        freelancer_id: str,
        job_post_id: str,
        job_role_id: str,
    ) -> Optional[Dict]:
        """Fetch an existing proposal from a freelancer for one job role."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="proposal",
                conditions=[
                    ("freelancer_id", "=", freelancer_id),
                    ("job_post_id", "=", job_post_id),
                    ("job_role_id", "=", job_role_id),
                ],
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error checking duplicate role proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_proposal(
        job_post_id: str,
        freelancer_id: str,
        cover_letter: str,
        proposed_budget: float,
        job_role_id: Optional[str] = None,
        proposed_duration: Optional[str] = None,
        status: Optional[str] = "pending",
        is_ai_generated: Optional[bool] = False,
    ) -> Dict:
        """Create a new proposal and sync proposal_count on the job post."""
        try:
            db = get_db()
            proposal_id = str(uuid.uuid4())

            proposal_data = {
                "proposal_id":        proposal_id,
                "job_post_id":        job_post_id,
                "job_role_id":        job_role_id,
                "freelancer_id":      freelancer_id,
                "cover_letter":       cover_letter,
                "proposed_budget":    proposed_budget,
                "proposed_duration":  proposed_duration,
                "status":             status,
                "is_ai_generated":    is_ai_generated,
                "moderation_status":  "scanning",
            }

            db.insert_data(table_name="proposal", data=proposal_data)
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} created", level="INFO")

            # Keep job_post.proposal_count in sync.
            JobPostFunctions._sync_proposal_count(job_post_id)

            return convert_uuids_to_str(proposal_data)

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error creating proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_proposal(proposal_id: str, update_data: Dict) -> Optional[Dict]:
        """Update proposal information."""
        try:
            db = get_db()
            filtered = {k: v for k, v in update_data.items() if v is not None}

            if not filtered:
                logger("PROPOSAL_FUNCTIONS", "No data to update", level="WARNING")
                return ProposalFunctions.get_proposal_by_id(proposal_id)

            conditions = [("proposal_id", "=", proposal_id)]
            db.update_data(table_name="proposal", data=filtered, conditions=conditions)
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} updated", level="INFO")

            return ProposalFunctions.get_proposal_by_id(proposal_id)

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error updating proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    async def run_proposal_scan(proposal_id: str, cover_letter: str, freelancer_user_id: str) -> None:
        """shared scan path for create and edit: scanning -> scan -> visible | blocked.
        the row is edited in place, never deleted or resubmitted as a new row."""
        try:
            existing = ProposalFunctions.get_proposal_by_id(proposal_id)
            job_post_id = existing.get("job_post_id") if existing else None

            ProposalFunctions.update_proposal(proposal_id, {"moderation_status": "scanning"})
            if job_post_id:
                JobPostFunctions._sync_proposal_count(job_post_id)

            if cover_letter and cover_letter.strip():
                result = await asyncio.to_thread(scan_harmful_text_with_ml_fallback, cover_letter)
            else:
                result = {"is_flagged": False, "detected_labels": []}

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                ProposalFunctions.update_proposal(proposal_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                })
                if job_post_id:
                    JobPostFunctions._sync_proposal_count(job_post_id)
                logger(
                    "PROPOSAL_FUNCTIONS",
                    f"Proposal {proposal_id} blocked, labels={result.get('detected_labels')}",
                    level="WARNING",
                )
                # audit trail only - proposals stay instant-block/edit-resubmit for the
                # freelancer, this queue entry doesn't gate anything, just makes the
                # flagged labels/scores queryable later (e.g. for admin review or analytics)
                insert_harmful_text_queue_entry(
                    "proposal", proposal_id, freelancer_user_id, cover_letter, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in result.get("detected_labels", [])]
                try:
                    await NotificationFunctions.notify(
                        recipient_user_id=freelancer_user_id,
                        notif_type="proposal_blocked",
                        title="Proposal Needs Changes",
                        body=f"Your cover letter was flagged for {', '.join(labels) or 'a policy violation'}. Edit and resubmit.",
                        data={"proposal_id": proposal_id},
                    )
                except Exception as notif_err:
                    logger("PROPOSAL_FUNCTIONS", f"Blocked-proposal notification failed (non-fatal): {notif_err}", level="WARNING")
            else:
                ProposalFunctions.update_proposal(proposal_id, {
                    "moderation_status": "visible",
                    "scanned_at": scanned_at,
                })
                if job_post_id:
                    JobPostFunctions._sync_proposal_count(job_post_id)

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Proposal scan failed for {proposal_id}: {e}", level="ERROR")

    @staticmethod
    async def notify_proposal_owners_of_job_closure(job_post_id: str, reason: str) -> None:
        """tell freelancers with a pending or accepted proposal that the job post
        they applied to just closed, no matter who or what closed it."""
        try:
            rows = get_db().execute_query(
                """
                SELECT p.proposal_id, f.user_id AS freelancer_user_id
                FROM proposal p
                JOIN freelancer f ON f.freelancer_id = p.freelancer_id
                WHERE p.job_post_id = :jid AND p.status IN ('pending', 'accepted')
                """,
                params={"jid": job_post_id},
            )
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Failed to look up proposals for job closure notify {job_post_id}: {e}", level="ERROR")
            return

        for row in rows or []:
            try:
                await NotificationFunctions.notify(
                    recipient_user_id=str(row["freelancer_user_id"]),
                    notif_type="job_post_closed",
                    title="Job Post Closed",
                    body=f"A job post you applied to has been closed ({reason}).",
                    data={"job_post_id": job_post_id, "proposal_id": str(row["proposal_id"])},
                )
            except Exception as notif_err:
                logger(
                    "PROPOSAL_FUNCTIONS",
                    f"Job-closure notification failed for proposal {row['proposal_id']} (non-fatal): {notif_err}",
                    level="WARNING",
                )

    @staticmethod
    def delete_proposal(proposal_id: str) -> bool:
        """Delete a proposal and sync proposal_count on the job post."""
        try:
            db = get_db()

            # Fetch job_post_id before deleting to sync proposal_count.
            existing = ProposalFunctions.get_proposal_by_id(proposal_id)
            job_post_id = existing.get("job_post_id") if existing else None

            conditions = [("proposal_id", "=", proposal_id)]
            db.delete_data(table_name="proposal", conditions=conditions)
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} deleted", level="INFO")

            # Keep job_post.proposal_count in sync.
            if job_post_id:
                JobPostFunctions._sync_proposal_count(job_post_id)

            return True

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error deleting proposal: {str(e)}", level="ERROR")
            raise
