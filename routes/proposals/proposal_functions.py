import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.job_posts.job_post_functions import JobPostFunctions
from ai_related.job_engine.applicant_ranker import (
    score_proposals_for_job_post,
    score_proposals_for_job_role,
    empty_score,
)
from typing import List, Optional, Dict
from datetime import datetime
import uuid

_EPOCH = datetime.min

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

    # dev function - no callers.
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

    _ENRICHED_SELECT = """
        SELECT
            p.proposal_id, p.job_post_id, p.job_role_id, p.freelancer_id,
            p.cover_letter, p.proposed_budget, p.proposed_duration,
            p.status, p.is_ai_generated, p.submitted_at,
            f.full_name           AS freelancer_name,
            f.title               AS freelancer_title,
            f.profile_picture_url,
            f.estimated_rate,
            f.rate_currency,
            f.rate_time,
            f.total_jobs,
            fts.display_star_avg  AS freelancer_rating,
            COALESCE(fts.total_reviews, 0) AS freelancer_review_count,
            jr.role_title,
            jr.role_budget,
            jr.budget_currency    AS role_budget_currency,
            jr.display_order      AS role_display_order
        FROM proposal p
        JOIN freelancer f  ON p.freelancer_id = f.freelancer_id
        JOIN job_role   jr ON jr.job_role_id  = p.job_role_id
        LEFT JOIN freelancer_trust_scores fts ON fts.freelancer_id = p.freelancer_id
    """

    @staticmethod
    def _sort_scored_proposals(proposals: List[Dict], sort_by: str, sort_order: str) -> List[Dict]:
        """Order an already-scored list."""
        descending = (sort_order or "").lower() != "asc"

        def submitted(p: Dict):
            # submitted_at defaults to NOW() so it is effectively never null; the fallback
            # only keeps the comparison total if a row ever comes back without one.
            return p.get("submitted_at") or _EPOCH

        def by_nullable(field: str, cast) -> None:
            proposals.sort(key=submitted, reverse=True)
            proposals.sort(
                key=lambda p: (p.get(field) is not None, cast(p.get(field) or 0)),
                reverse=descending,
            )
            if not descending:
                # Ascending means worst first, but a missing value still goes last.
                proposals.sort(key=lambda p: p.get(field) is None)

        if sort_by == "relevance":
            by_nullable("relevance_score", int)
        elif sort_by == "rating":
            by_nullable("freelancer_rating", float)
        elif sort_by == "proposed_budget":
            proposals.sort(key=submitted, reverse=True)
            proposals.sort(key=lambda p: float(p.get("proposed_budget") or 0), reverse=descending)
        elif sort_by == "total_jobs":
            proposals.sort(key=submitted, reverse=True)
            proposals.sort(key=lambda p: int(p.get("total_jobs") or 0), reverse=descending)
        else:
            proposals.sort(key=submitted, reverse=descending)

        return proposals

    @staticmethod
    def _attach_relevance(proposals: List[Dict], scores: Dict[str, Dict]) -> List[Dict]:
        """Merge ranker output into each proposal, defaulting to the unavailable payload
        so every row carries the same keys whether or not the embeddings are ready."""
        for proposal in proposals:
            proposal.update(scores.get(proposal["proposal_id"]) or empty_score())
        return proposals

    @staticmethod
    def get_proposals_by_job_post_id_enriched(
        job_post_id: str,
        job_role_id: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "submitted_at",
        sort_order: str = "desc",
    ) -> List[Dict]:
        """Fetch a job post's proposals with freelancer + role info and a relevance score."""
        try:
            db = get_db()

            where = ["p.job_post_id = :job_post_id"]
            params: Dict = {"job_post_id": job_post_id}
            if job_role_id:
                where.append("p.job_role_id = :job_role_id")
                params["job_role_id"] = job_role_id
            if status:
                where.append("p.status::text = :status")
                params["status"] = status

            rows = db.execute_query(
                f"{ProposalFunctions._ENRICHED_SELECT} WHERE {' AND '.join(where)}",
                params,
            )
            proposals = [convert_uuids_to_str(dict(row)) for row in rows]

            scores = (
                score_proposals_for_job_role(db, job_role_id)
                if job_role_id
                else score_proposals_for_job_post(db, job_post_id)
            )
            ProposalFunctions._attach_relevance(proposals, scores)
            ProposalFunctions._sort_scored_proposals(proposals, sort_by, sort_order)

            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(proposals)} enriched proposals for job post {job_post_id} "
                   f"| role={job_role_id or 'all'} | sort={sort_by} {sort_order}", level="INFO")
            return proposals

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching enriched proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_job_role_id_enriched(
        job_role_id: str,
        status: Optional[str] = None,
        sort_by: str = "relevance",
        sort_order: str = "desc",
    ) -> List[Dict]:
        """Fetch one role's proposals, ranked. This is the scope where relevance scores
        are directly comparable, so it defaults to best-fit first."""
        try:
            db = get_db()

            where = ["p.job_role_id = :job_role_id"]
            params: Dict = {"job_role_id": job_role_id}
            if status:
                where.append("p.status::text = :status")
                params["status"] = status

            rows = db.execute_query(
                f"{ProposalFunctions._ENRICHED_SELECT} WHERE {' AND '.join(where)}",
                params,
            )
            proposals = [convert_uuids_to_str(dict(row)) for row in rows]

            ProposalFunctions._attach_relevance(
                proposals, score_proposals_for_job_role(db, job_role_id)
            )
            ProposalFunctions._sort_scored_proposals(proposals, sort_by, sort_order)

            logger("PROPOSAL_FUNCTIONS",
                   f"Fetched {len(proposals)} enriched proposals for role {job_role_id} "
                   f"| sort={sort_by} {sort_order}", level="INFO")
            return proposals

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching role proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_job_post_grouped_by_role(
        job_post_id: str,
        status: Optional[str] = None,
        sort_by: str = "relevance",
        sort_order: str = "desc",
    ) -> Dict:
        """A job post's bids split into one bucket per role, each bucket ranked on its own."""
        try:
            db = get_db()

            role_rows = db.execute_query(
                """
                SELECT job_role_id, role_title, role_description, role_budget,
                       budget_currency, budget_type, positions_available,
                       positions_filled, is_required, display_order
                FROM job_role
                WHERE job_post_id = :job_post_id
                ORDER BY display_order ASC, created_at ASC
                """,
                {"job_post_id": job_post_id},
            )

            proposals = ProposalFunctions.get_proposals_by_job_post_id_enriched(
                job_post_id, status=status, sort_by=sort_by, sort_order=sort_order
            )

            by_role: Dict[str, List[Dict]] = {}
            for proposal in proposals:
                by_role.setdefault(str(proposal["job_role_id"]), []).append(proposal)

            roles = []
            for role_row in role_rows:
                role = convert_uuids_to_str(dict(role_row))
                role_id = str(role["job_role_id"])
                # Already ordered by the shared sort; grouping preserves it.
                role_proposals = by_role.get(role_id, [])

                statuses = [p.get("status") for p in role_proposals]
                scored = [p for p in role_proposals if p.get("relevance_score") is not None]

                role.update({
                    "proposal_count":     len(role_proposals),
                    "pending_count":      statuses.count("pending"),
                    "accepted_count":     statuses.count("accepted"),
                    "rejected_count":     statuses.count("rejected"),
                    "ranked_count":       len(scored),
                    "positions_open":     max(
                        (role.get("positions_available") or 0) - (role.get("positions_filled") or 0), 0
                    ),
                    "top_proposal_id":    role_proposals[0]["proposal_id"] if role_proposals else None,
                    "proposals":          role_proposals,
                })
                roles.append(role)

            logger("PROPOSAL_FUNCTIONS",
                   f"Grouped {len(proposals)} proposals into {len(roles)} roles "
                   f"for job post {job_post_id}", level="INFO")

            return {
                "job_post_id":     job_post_id,
                "total_proposals": len(proposals),
                "role_count":      len(roles),
                "sort_by":         sort_by,
                "sort_order":      sort_order,
                "roles":           roles,
            }

        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error grouping proposals by role: {str(e)}", level="ERROR")
            raise

    # Sortable columns for a freelancer's proposal list. Keys are what the API accepts,
    # values are the SQL columns (interpolated, so never user input).
    _SORT_COLUMNS = {
        "submitted_at": "p.submitted_at",
        "proposed_budget": "p.proposed_budget",
    }

    @staticmethod
    def get_proposals_by_freelancer_id(
        freelancer_id: str,
        proposal_status: Optional[str] = None,
        job_post_status: Optional[str] = None,
        sort_by: str = "submitted_at",
        sort_order: str = "desc",
    ) -> List[Dict]:
        """Fetch a freelancer's proposals joined to the job post, so each one carries the
        job's current status and title. A proposal stays 'pending' even after its post
        closes, so the freelancer reads that from job_post_status instead.

        Optional filters: proposal_status, job_post_status. sort_by is one of
        _SORT_COLUMNS, sort_order is asc/desc (both whitelisted before interpolation)."""
        try:
            db = get_db()

            sort_column = ProposalFunctions._SORT_COLUMNS.get(
                (sort_by or "").lower(), "p.submitted_at"
            )
            direction = "ASC" if (sort_order or "").lower() == "asc" else "DESC"

            where = ["p.freelancer_id = :fid"]
            params: Dict = {"fid": freelancer_id}
            if proposal_status:
                where.append("p.status::text = :pstatus")
                params["pstatus"] = proposal_status
            if job_post_status:
                where.append("jp.status::text = :jpstatus")
                params["jpstatus"] = job_post_status

            query = f"""
                SELECT
                    p.proposal_id, p.job_post_id, p.job_role_id, p.freelancer_id,
                    p.cover_letter, p.proposed_budget, p.proposed_duration,
                    p.status, p.is_ai_generated, p.submitted_at,
                    jp.status     AS job_post_status,
                    jp.job_title  AS job_title,
                    jr.role_title AS role_title
                FROM proposal p
                JOIN job_post jp ON p.job_post_id = jp.job_post_id
                JOIN job_role jr ON p.job_role_id = jr.job_role_id
                WHERE {' AND '.join(where)}
                ORDER BY {sort_column} {direction}
            """
            rows = db.execute_query(query, params)
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

    # dev function - no callers.
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
    def auto_reject_pending_proposals_for_filled_role(job_role_id: str, exclude_proposal_id: str, db=None) -> List[Dict]:
        """
        Auto-reject the remaining pending proposals once a role's last position is taken,
        so those freelancers aren't left hanging. Returns each rejected proposal's
        freelancer user_id and the role title for the caller to notify.

        Split into two queries because execute_query only commits when the text starts
        with INSERT, UPDATE or DELETE, so a WITH would never commit.

        Pass `db` an open Transaction to make these rejections part of a larger unit of
        work - contract activation does this, so the seat and the rejections commit
        together. Only execute_query is used here, which both Database and Transaction
        provide.
        """
        try:
            db = db or get_db()
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
                freelancer_rows = db.execute_query(
                    "SELECT user_id FROM freelancer WHERE freelancer_id = :fid LIMIT 1",
                    {"fid": str(row["freelancer_id"])},
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

