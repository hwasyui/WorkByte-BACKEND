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
    def get_active_proposals_by_job_role_id(job_role_id: str) -> List[Dict]:
        """Fetch proposals still in play ('pending' or 'accepted') for a role -
        used to pre-check whether a role is safe to delete. proposal.job_role_id
        is ON DELETE SET NULL, so deleting a role with these still outstanding
        would silently orphan them instead of raising any error."""
        try:
            rows = get_db().execute_query(
                """
                SELECT proposal_id, freelancer_id, status
                FROM proposal
                WHERE job_role_id = :jrid AND status IN ('pending', 'accepted')
                """,
                {"jrid": job_role_id},
            )
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error checking active proposals for role: {str(e)}", level="ERROR")
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
    def update_status_if_pending(proposal_id: str, new_status: str) -> Optional[Dict]:
        """Atomically move a proposal off 'pending' - the WHERE status='pending'
        guard makes accept/reject/withdraw race-safe: if a client's accept and
        the freelancer's withdraw land at nearly the same moment, only the one
        that actually matches a still-pending row succeeds; the loser gets zero
        rows back instead of silently overwriting the winner's decision."""
        rows = get_db().execute_query(
            """
            UPDATE proposal
            SET status = :new_status
            WHERE proposal_id = :pid AND status = 'pending'
            RETURNING *
            """,
            {"pid": proposal_id, "new_status": new_status},
        )
        return dict(rows[0]) if rows else None

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
                result = await scan_harmful_text_with_ml_fallback(cover_letter)
            else:
                result = {"is_flagged": False, "detected_labels": []}

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                detected_labels = result.get("detected_labels", [])
                ProposalFunctions.update_proposal(proposal_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                    "detected_labels": detected_labels,
                })
                if job_post_id:
                    JobPostFunctions._sync_proposal_count(job_post_id)
                logger(
                    "PROPOSAL_FUNCTIONS",
                    f"Proposal {proposal_id} blocked, labels={detected_labels}",
                    level="WARNING",
                )
                # audit trail only - proposals stay instant-block/edit-resubmit for the
                # freelancer, this queue entry doesn't gate anything, just makes the
                # flagged labels/scores queryable later (e.g. for admin review or analytics)
                insert_harmful_text_queue_entry(
                    "proposal", proposal_id, freelancer_user_id, cover_letter, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in detected_labels]
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
                    "detected_labels": [],
                })
                if job_post_id:
                    JobPostFunctions._sync_proposal_count(job_post_id)

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Proposal scan failed for {proposal_id}: {e}", level="ERROR")

    @staticmethod
    def auto_reject_pending_proposals_on_job_closure(job_post_id: str) -> int:
        """When a job post closes, its still-'pending' proposals can never be
        decided (accept/reject already requires job_post.status == 'active') -
        without this they'd stay 'pending' forever, pointing at a closed job with
        no way for the freelancer to know it's a dead end. Only 'pending' is
        touched; 'accepted' proposals are left alone since closing a job post
        must never silently affect an engagement already in progress."""
        try:
            rows = get_db().fetch_data(
                table_name="proposal",
                conditions=[("job_post_id", "=", job_post_id), ("status", "=", "pending")],
            )
            for row in rows or []:
                ProposalFunctions.update_proposal(str(row["proposal_id"]), {"status": "rejected"})
            if rows:
                logger(
                    "PROPOSAL_FUNCTIONS",
                    f"Auto-rejected {len(rows)} pending proposal(s) for closed job post {job_post_id}",
                    level="INFO",
                )
            return len(rows) if rows else 0
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Failed to auto-reject pending proposals for job post {job_post_id}: {e}", level="ERROR")
            return 0

    @staticmethod
    def auto_reject_pending_proposals_for_filled_role(job_role_id: str, exclude_proposal_id: Optional[str] = None) -> List[Dict]:
        """When a role's positions_available is fully hired, its still-'pending'
        proposals for that SAME role can never be decided (accept would double-book
        a slot that no longer exists) - without this they'd sit unresolved forever
        with no signal to the applicant that the role moved on. Other roles under
        the same job post (team projects) are untouched - only this specific role
        is done hiring. exclude_proposal_id skips the just-accepted proposal that
        triggered the fill, since it's already 'accepted', not 'pending'."""
        try:
            rows = get_db().execute_query(
                """
                SELECT p.proposal_id, f.user_id AS freelancer_user_id, jr.role_title
                FROM proposal p
                JOIN freelancer f ON f.freelancer_id = p.freelancer_id
                JOIN job_role jr  ON jr.job_role_id   = p.job_role_id
                WHERE p.job_role_id = :jrid AND p.status = 'pending'
                  AND (:exclude_id IS NULL OR p.proposal_id != CAST(:exclude_id AS uuid))
                """,
                params={"jrid": job_role_id, "exclude_id": exclude_proposal_id},
            )
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Failed to look up pending proposals for filled role {job_role_id}: {e}", level="ERROR")
            return []

        for row in rows or []:
            ProposalFunctions.update_proposal(str(row["proposal_id"]), {"status": "rejected"})

        if rows:
            logger(
                "PROPOSAL_FUNCTIONS",
                f"Auto-rejected {len(rows)} pending proposal(s) for filled role {job_role_id}",
                level="INFO",
            )
        return [dict(r) for r in (rows or [])]

    @staticmethod
    def auto_reject_pending_proposals_for_freelancer(freelancer_id: str) -> List[Dict]:
        """When a freelancer's account gets banned, their still-'pending' proposals
        sitting on OTHER clients' jobs are left dangling otherwise - a client could
        unknowingly accept a proposal from someone already banned, with no signal
        anything is wrong. Only 'pending' is touched; 'accepted' proposals are left
        alone (freeze-asymmetric ban design - banning one party must never silently
        cancel an engagement already underway).

        Returns the affected rows (proposal_id, job_post_id, job_title, client_user_id)
        so the caller can notify each affected client."""
        try:
            rows = get_db().execute_query(
                """
                SELECT p.proposal_id, p.job_post_id, jp.job_title, cl.user_id AS client_user_id
                FROM proposal p
                JOIN job_post jp ON jp.job_post_id = p.job_post_id
                JOIN client cl   ON cl.client_id   = jp.client_id
                WHERE p.freelancer_id = :fid AND p.status = 'pending'
                """,
                params={"fid": freelancer_id},
            )
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Failed to look up pending proposals for banned freelancer {freelancer_id}: {e}", level="ERROR")
            return []

        for row in rows or []:
            ProposalFunctions.update_proposal(str(row["proposal_id"]), {"status": "rejected"})

        if rows:
            logger(
                "PROPOSAL_FUNCTIONS",
                f"Auto-rejected {len(rows)} pending proposal(s) for banned freelancer {freelancer_id}",
                level="INFO",
            )
        return [dict(r) for r in (rows or [])]

    @staticmethod
    async def notify_proposal_owners_of_job_closure(
        job_post_id: str,
        reason: str,
        notif_type: str = "job_post_closed",
        title: str = "Job Post Closed",
        verb: str = "closed",
    ) -> None:
        """tell freelancers with a pending or accepted proposal that the job post
        they applied to just closed (or was removed), no matter who or what did it."""
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
                    notif_type=notif_type,
                    title=title,
                    body=f"A job post you applied to has been {verb} ({reason}).",
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

            # proposal_file rows cascade away with the proposal row below - clean up
            # their MinIO objects first, since the DB delete never touches storage.
            from routes.proposal_files.proposal_file_functions import ProposalFileFunctions
            ProposalFileFunctions.purge_minio_files_for_proposal(proposal_id)

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
