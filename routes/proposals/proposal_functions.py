import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.job_posts.job_post_functions import JobPostFunctions
from typing import List, Optional, Dict
import uuid


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
    def get_all_proposals(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all proposals."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="proposal",
                columns=[
                    "proposal_id", "job_post_id", "job_role_id", "freelancer_id",
                    "cover_letter", "proposed_budget", "proposed_duration",
                    "status", "is_ai_generated", "submitted_at",
                ],
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

    @staticmethod
    def get_proposals_by_job_post_id_enriched(job_post_id: str) -> List[Dict]:
        """Fetch all proposals for a job post with freelancer info joined."""
        try:
            db = get_db()
            query = """
                SELECT
                    p.proposal_id, p.job_post_id, p.job_role_id, p.freelancer_id,
                    p.cover_letter, p.proposed_budget, p.proposed_duration,
                    p.status, p.is_ai_generated, p.submitted_at,
                    f.full_name           AS freelancer_name,
                    f.profile_picture_url,
                    f.estimated_rate,
                    f.rate_currency,
                    f.rate_time,
                    f.total_jobs
                FROM proposal p
                JOIN freelancer f ON p.freelancer_id = f.freelancer_id
                WHERE p.job_post_id = :job_post_id
                ORDER BY p.submitted_at DESC
            """
            rows = db.execute_query(query, {"job_post_id": job_post_id})
            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(rows)} enriched proposals for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching enriched proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all proposals from a freelancer."""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
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
                "proposal_id":       proposal_id,
                "job_post_id":       job_post_id,
                "job_role_id":       job_role_id,
                "freelancer_id":     freelancer_id,
                "cover_letter":      cover_letter,
                "proposed_budget":   proposed_budget,
                "proposed_duration": proposed_duration,
                "status":            status,
                "is_ai_generated":   is_ai_generated,
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
    def auto_reject_pending_proposals_for_filled_role(job_role_id: str, exclude_proposal_id: str) -> List[Dict]:
        """
        Auto-rejects every other still-pending proposal for a role once its last
        open position gets taken (by exclude_proposal_id's contract), so those
        freelancers see 'rejected' instead of being left hanging on a role that's
        already fully staffed. Returns each rejected proposal's freelancer user_id
        and the role title for the caller to notify.

        Two queries, not one WITH...UPDATE...RETURNING - Database.execute_query
        only commits when the query text starts with INSERT/UPDATE/DELETE, and a
        query starting with WITH would silently never commit the update.
        """
        try:
            db = get_db()
            rejected_rows = db.execute_query(
                """
                UPDATE proposal
                SET status = 'rejected'
                WHERE job_role_id = :jrid
                  AND status = 'pending'
                  AND proposal_id != :exclude_pid
                RETURNING proposal_id, freelancer_id
                """,
                {"jrid": job_role_id, "exclude_pid": exclude_proposal_id},
            )
            if not rejected_rows:
                return []

            role_rows = db.execute_query(
                "SELECT role_title FROM job_role WHERE job_role_id = :jrid",
                {"jrid": job_role_id},
            )
            role_title = role_rows[0]["role_title"] if role_rows else "a role"

            result = []
            for row in rejected_rows:
                freelancer_rows = db.fetch_data(
                    table_name="freelancer",
                    conditions=[("freelancer_id", "=", str(row["freelancer_id"]))],
                    limit=1,
                )
                if freelancer_rows:
                    result.append({
                        "proposal_id": str(row["proposal_id"]),
                        "freelancer_user_id": str(freelancer_rows[0]["user_id"]),
                        "role_title": role_title,
                    })

            logger(
                "PROPOSAL_FUNCTIONS",
                f"Auto-rejected {len(result)} pending proposal(s) for filled role {job_role_id}",
                level="INFO",
            )
            return result

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error auto-rejecting proposals for role {job_role_id}: {str(e)}", level="ERROR")
            raise

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
