import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict, Any
import uuid
import math
import re



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



# Shared SELECT columns
_JOB_POST_SELECT = """
    SELECT
        jp.job_post_id, jp.client_id, jp.job_title, jp.job_description,
        jp.project_type, jp.project_scope, jp.estimated_duration,
        jp.working_days, jp.deadline, jp.experience_level, jp.status,
        jp.is_ai_generated, jp.view_count, jp.project_category,
        jp.created_at, jp.updated_at, jp.posted_at, jp.closed_at,
        jp.closure_reason, jp.closure_note,
        COUNT(DISTINCT jr.job_role_id) AS role_count,
        COALESCE(SUM(jr.positions_available), 0) AS available_positions,
        c.full_name AS client_name,
        c.profile_picture_url AS profile_picture_url,
        (
            SELECT COUNT(*)
            FROM proposal p
            WHERE p.job_post_id = jp.job_post_id
        ) AS proposal_count
    FROM job_post jp
    LEFT JOIN job_role jr ON jr.job_post_id = jp.job_post_id
    LEFT JOIN client c ON c.client_id = jp.client_id
"""


class JobPostFunctions:
    """Handle all job post-related database operations."""


    @staticmethod
    def _estimate_days_from_duration(duration: Optional[str]) -> Optional[int]:
        """Best-effort parsing of strings like '2 months', '3 weeks', '10 days'."""
        if not duration:
            return None


        text = duration.strip().lower()
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None


        value = float(match.group(1))
        if "day" in text:
            return math.ceil(value)
        if "week" in text:
            return math.ceil(value * 7)
        if "month" in text:
            return math.ceil(value * 30)
        if "year" in text:
            return math.ceil(value * 365)
        return None


    @staticmethod
    def recompute_project_scope(job_post_id: str) -> Optional[str]:
        """Recalculate and persist project_scope for a post, if it is still auto.

        A post is created before its roles exist, so the scope computed at creation is
        based on an incomplete picture and needs revisiting once roles are added.

        Skips posts where project_scope_is_auto is FALSE so a client's own choice is never
        overwritten. Returns the scope in force after the call, or None if the post is gone.
        """
        try:
            db = get_db()
            rows = db.execute_query(
                """SELECT jp.estimated_duration, jp.working_days,
                          jp.project_scope, jp.project_scope_is_auto,
                          (SELECT COALESCE(SUM(jr.positions_available), 0)
                             FROM job_role jr WHERE jr.job_post_id = jp.job_post_id) AS position_count
                   FROM job_post jp WHERE jp.job_post_id = :id""",
                {"id": job_post_id},
            )
            if not rows:
                return None

            post = dict(rows[0])
            if not post.get("project_scope_is_auto"):
                return post.get("project_scope")

            calculation = JobPostFunctions.calculate_project_scope(
                estimated_duration=post.get("estimated_duration"),
                working_days=post.get("working_days"),
                position_count=int(post.get("position_count") or 0) or 1,
            )
            recommended = calculation["recommended_project_scope"]

            if recommended != post.get("project_scope"):
                db.execute_query(
                    "UPDATE job_post SET project_scope = :scope WHERE job_post_id = :id",
                    {"scope": recommended, "id": job_post_id},
                )
                logger(
                    "JOB_POST_FUNCTIONS",
                    f"Recomputed project_scope for {job_post_id}: {post.get('project_scope')} -> {recommended}",
                    level="INFO",
                )
            return recommended
        except Exception as e:
            # Never fail the caller's operation over a scope recalculation.
            logger("JOB_POST_FUNCTIONS", f"Could not recompute project_scope for {job_post_id}: {e}", level="WARNING")
            return None


    # NEW: Category inference
    @staticmethod
    def infer_project_category(job_title: str, job_description: str) -> str:
        """Infer one primary project category from job title and description using weighted scoring."""
        title = (job_title or "").lower()
        desc = (job_description or "").lower()
        full_text = f"{title} {desc}"

        category_keywords = {
            "mobile_dev": [
                "mobile", "android", "ios", "flutter", "react native",
                "swift", "kotlin", "dart"
            ],
            "web_dev": [
                "frontend", "front-end", "front end", "web", "website",
                "landing page", "html", "css", "javascript", "typescript",
                "react", "vue", "nextjs", "next.js", "angular"
            ],
            "backend_dev": [
                "backend", "back-end", "back end", "api", "server",
                "database", "fastapi", "django", "flask", "node",
                "express", "postgresql", "postgres", "mysql", "mongodb"
            ],
            "ui_ux_design": [
                "ui/ux", "ui ux", "user interface", "user experience",
                "figma", "wireframe", "prototype", "mockup"
            ],
            "graphic_design": [
                "graphic", "logo", "branding", "illustration",
                "photoshop", "poster", "banner"
            ],
            "copy_writing": [
                "copywriting", "copy writing", "writing", "content",
                "blog", "article", "seo"
            ],
            "data_analytics": [
                "data", "analytics", "dashboard", "machine learning",
                "ai", "python", "tableau", "power bi"
            ],
            "video_editing": [
                "video", "motion", "animation", "premiere",
                "after effects", "reels", "shorts"
            ],
            "marketing": [
                "marketing", "social media", "ads", "advertisement",
                "instagram", "campaign", "tiktok"
            ],
        }

        scores = {category: 0 for category in category_keywords}

        for category, keywords in category_keywords.items():
            for keyword in keywords:
                if keyword in full_text:
                    scores[category] += 1

                # Title is more important than description
                if keyword in title:
                    scores[category] += 3

        # Extra rules for common conflicts

        # Flutter / Android / iOS should usually be mobile, not web.
        if any(k in full_text for k in ["flutter", "android", "ios", "react native", "kotlin", "swift"]):
            scores["mobile_dev"] += 4

        # Frontend terms should usually go to web_dev,
        # even if the description mentions API/database integration.
        if any(k in full_text for k in [
            "frontend", "front-end", "front end", "react", "vue",
            "html", "css", "nextjs", "next.js", "angular"
        ]):
            scores["web_dev"] += 4

        # Backend title should strongly override weak frontend/web mentions.
        if any(k in title for k in ["backend", "back-end", "back end", "api developer"]):
            scores["backend_dev"] += 5

        best_category = max(scores, key=scores.get)

        if scores[best_category] == 0:
            return "general"

        return best_category


    @staticmethod
    def calculate_project_scope(
        estimated_duration: Optional[str] = None,
        working_days: Optional[int] = None,
        position_count: Optional[int] = 1,
    ) -> Dict[str, Any]:
        """Recommend a scope from timeline (0-3) and positions (0-3), max 6. Persists nothing.

        Counts positions, not roles - one role hiring five people is five people's work.
        Budget was dropped because market price for the same work differs per country, so
        converting currencies does not make the figure comparable. Thresholds are tuning knobs.
        """
        score = 0
        reasons: List[str] = []

        normalized_positions = max(int(position_count or 1), 1)
        duration_days = working_days or JobPostFunctions._estimate_days_from_duration(estimated_duration)
        duration_months_estimate = max((duration_days or 30) / 30.0, 1.0)

        if duration_days is not None:
            if duration_days >= 61:
                score += 3
                reasons.append(f"Long timeline ({duration_days} days).")
            elif duration_days >= 31:
                score += 2
                reasons.append(f"Moderate-to-long timeline ({duration_days} days).")
            elif duration_days >= 11:
                score += 1
                reasons.append(f"Short-to-moderate timeline ({duration_days} days).")
            else:
                reasons.append(f"Short timeline ({duration_days} days).")

        if normalized_positions >= 5:
            score += 3
            reasons.append(f"Large team ({normalized_positions} positions).")
        elif normalized_positions >= 3:
            score += 2
            reasons.append(f"Mid-sized team ({normalized_positions} positions).")
        elif normalized_positions == 2:
            score += 1
            reasons.append("Two positions to fill.")
        else:
            reasons.append("Single position.")

        if score >= 5:
            recommended_scope = "large"
        elif score >= 3:
            recommended_scope = "medium"
        else:
            recommended_scope = "small"

        # Only the timeline can actually be missing - position count defaults to 1, which is
        # a real answer (a solo post), not a gap.
        confidence = "high" if duration_days is not None else "low"

        return {
            "recommended_project_scope": recommended_scope,
            "score": score,
            "confidence": confidence,
            "factors": {
                "working_days": working_days,
                "estimated_duration": estimated_duration,
                "duration_days_estimate": duration_days,
                "duration_months_estimate": round(duration_months_estimate, 2),
                "position_count": normalized_positions,
            },
            "reasons": reasons,
        }


    # Internal helper


    @staticmethod
    def _sync_proposal_count(job_post_id: str) -> None:
        """
        Recalculate and update the stored proposal_count column
        on job_post to match the actual count in the proposal table.
        Call this after any proposal insert, delete, or status change.
        """
        try:
            db = get_db()
            query = """
                UPDATE job_post
                SET proposal_count = (
                    SELECT COUNT(*)
                    FROM proposal
                    WHERE job_post_id = :job_post_id
                )
                WHERE job_post_id = :job_post_id
            """
            db.execute_query(query, {"job_post_id": job_post_id})
            logger("JOB_POST_FUNCTIONS",
                   f"Synced proposal_count for job_post {job_post_id}", level="INFO")
        except Exception as e:
            logger("JOB_POST_FUNCTIONS",
                   f"Failed to sync proposal_count for {job_post_id}: {str(e)}", level="WARNING")


    # Fetch operations


    # Valid sort fields mapped to their SQL expression
    _JOB_SORT_FIELDS = {
        "created_at":     "jp.created_at",
        "posted_at":      "jp.posted_at",
        "deadline":       "jp.deadline",
        "job_title":      "jp.job_title",
        "proposal_count": "proposal_count",
        "view_count":     "jp.view_count",
    }

    _VALID_STATUSES = {"active", "closed", "filled", "draft", "all"}


    @staticmethod
    def browse_job_posts(
        status: str = "active",
        order_by: str = "created_at",
        order_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
        requesting_client_id: Optional[str] = None,
        category: Optional[str] = None,
        project_type: Optional[str] = None,
        project_scope: Optional[str] = None,
        experience_level: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        budget_min: Optional[float] = None,
        budget_max: Optional[float] = None,
        budget_type: Optional[str] = None,
        budget_currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            db = get_db()

            sort_col = JobPostFunctions._JOB_SORT_FIELDS.get(order_by, "jp.created_at")
            direction = "DESC" if order_dir.lower() == "desc" else "ASC"
            offset = (page - 1) * page_size

            if status == "all":
                if requesting_client_id:
                    where = "WHERE (jp.status != 'draft' OR jp.client_id = :rcid)"
                    params: Dict = {"rcid": requesting_client_id}
                else:
                    where = "WHERE jp.status != 'draft'"
                    params = {}
            elif status == "draft":
                if not requesting_client_id:
                    return {"items": [], "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}}
                where = "WHERE jp.status = 'draft' AND jp.client_id = :rcid"
                params = {"rcid": requesting_client_id}
            else:
                where = "WHERE jp.status = :status"
                params = {"status": status}

            if category:
                where += " AND jp.project_category = :category"
                params["category"] = category
            if project_type:
                where += " AND jp.project_type = :project_type"
                params["project_type"] = project_type
            if project_scope:
                where += " AND jp.project_scope = :project_scope"
                params["project_scope"] = project_scope
            if experience_level:
                where += " AND jp.experience_level = :experience_level"
                params["experience_level"] = experience_level
            if date_from:
                where += " AND jp.created_at >= :date_from"
                params["date_from"] = date_from
            if date_to:
                where += " AND jp.created_at <= :date_to"
                params["date_to"] = date_to
            if budget_min is not None:
                where += " AND jr.role_budget >= :budget_min"
                params["budget_min"] = budget_min
            if budget_max is not None:
                where += " AND jr.role_budget <= :budget_max"
                params["budget_max"] = budget_max
            if budget_type:
                where += " AND jr.budget_type = :budget_type"
                params["budget_type"] = budget_type
            if budget_currency:
                where += " AND jr.budget_currency = :budget_currency"
                params["budget_currency"] = budget_currency

            count_query = f"""
                SELECT COUNT(DISTINCT jp.job_post_id) AS total
                FROM job_post jp
                LEFT JOIN job_role jr ON jr.job_post_id = jp.job_post_id
                {where}
            """
            count_rows = db.execute_query(count_query, params)
            total = int(count_rows[0]["total"]) if count_rows else 0

            data_query = _JOB_POST_SELECT + f"""
                {where}
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
                ORDER BY {sort_col} {direction} NULLS LAST, jp.view_count DESC, jp.created_at DESC
                LIMIT :limit OFFSET :offset
            """
            data_rows = db.execute_query(data_query, {**params, "limit": page_size, "offset": offset})
            items = [convert_uuids_to_str(dict(row)) for row in data_rows]

            logger("JOB_POST_FUNCTIONS", f"browse_job_posts: {total} total, page {page}/{math.ceil(total/page_size) or 1}", level="INFO")
            return {
                "items": items,
                "pagination": {
                    "page":        page,
                    "page_size":   page_size,
                    "total":       total,
                    "total_pages": math.ceil(total / page_size) if total else 0,
                },
            }
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error browsing job posts: {str(e)}", level="ERROR")
            raise


    @staticmethod
    def search_job_posts(search_term: str, limit: int = 20) -> List[Dict]:
        """Full-text search over job_title and job_description (active posts only)."""
        try:
            db = get_db()
            query = _JOB_POST_SELECT + """
                WHERE jp.status = 'active'
                  AND (jp.job_title ILIKE '%' || :term || '%'
                    OR jp.job_description ILIKE '%' || :term || '%')
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
                ORDER BY jp.created_at DESC
                LIMIT :limit
            """
            rows = db.execute_query(query, {"term": search_term, "limit": limit})
            logger("JOB_POST_FUNCTIONS", f"search_job_posts: {len(rows)} results for '{search_term}'", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error searching job posts: {str(e)}", level="ERROR")
            raise


    @staticmethod
    def get_all_job_posts(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all job posts with role_count, client_name, and live proposal_count."""
        try:
            db = get_db()
            query = _JOB_POST_SELECT + """
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
                ORDER BY jp.created_at DESC
                {limit_clause}
            """.format(limit_clause=f"LIMIT {limit}" if limit else "")


            rows = db.execute_query(query)
            logger("JOB_POST_FUNCTIONS", f"Fetched {len(rows)} job posts", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job posts: {str(e)}", level="ERROR")
            raise


    @staticmethod
    def get_job_post_by_id(job_post_id: str) -> Optional[Dict]:
        """Fetch a job post by ID with role_count, client_name, and live proposal_count."""
        try:
            db = get_db()
            query = _JOB_POST_SELECT + """
                WHERE jp.job_post_id = :job_post_id
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
            """
            rows = db.execute_query(query, {"job_post_id": job_post_id})


            if rows:
                logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))


            return None


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def increment_view_count(job_post_id: str) -> None:
        try:
            db = get_db()
            db.execute_query(
                "UPDATE job_post SET view_count = view_count + 1 WHERE job_post_id = :job_post_id",
                {"job_post_id": job_post_id},
            )
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error incrementing view_count for {job_post_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_posts_by_client_id(client_id: str, include_drafts: bool = False) -> List[Dict]:
        """Fetch a client's job posts with role_count, client_name, and live proposal_count.

        Anyone viewing the profile can read this, so drafts are excluded by default.
        Pass include_drafts=True for the owner's own drafts view.
        """
        try:
            db = get_db()
            draft_filter = "" if include_drafts else " AND jp.status <> 'draft'"
            query = _JOB_POST_SELECT + f"""
                WHERE jp.client_id = :client_id{draft_filter}
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
                ORDER BY jp.created_at DESC
            """
            rows = db.execute_query(query, {"client_id": client_id})


            logger("JOB_POST_FUNCTIONS",
                   f"Fetched {len(rows)} job posts for client {client_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job posts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_category_counts() -> list:
        """Return list of {category, count} for active job posts, sorted by count desc."""
        try:
            db = get_db()
            query = """
                SELECT jp.project_category AS category, COUNT(*) AS count
                FROM job_post jp
                WHERE jp.status = 'active'
                GROUP BY jp.project_category
                ORDER BY count DESC
            """
            rows = db.execute_query(query)
            return [{"category": row["category"], "count": int(row["count"])} for row in rows]
        except Exception as e:
            logger("JOBPOSTFUNCTIONS", f"Error fetching category counts: {str(e)}", level="ERROR")
            raise

    # Write operations

    @staticmethod
    def _adjust_client_jobs_posted(db, client_id: str, delta: int) -> None:
        """Keep client.total_jobs_posted in sync as job posts cross the draft boundary.

        The counter tracks published posts only, so it moves on create, publish, unpublish
        and delete. Clamped at 0 so drift can't push it negative.
        """
        if not delta:
            return
        client_rows = db.fetch_data(
            table_name="client",
            conditions=[("client_id", "=", client_id)],
            limit=1,
        )
        if not client_rows:
            return
        current_count = client_rows[0].get("total_jobs_posted") or 0
        new_count = max(0, current_count + delta)
        db.update_data(
            table_name="client",
            data={"total_jobs_posted": new_count},
            conditions=[("client_id", "=", client_id)],
        )

    @staticmethod
    def create_job_post(client_id: str, job_title: str, job_description: str,
                        project_type: str, project_scope: Optional[str] = None,
                        estimated_duration: Optional[str] = None,
                        working_days: Optional[int] = None,
                        deadline=None,
                        experience_level: Optional[str] = None,
                        status: Optional[str] = "draft",
                        is_ai_generated: Optional[bool] = False) -> Dict:
        """Create a new job post."""
        try:
            db = get_db()
            job_post_id = str(uuid.uuid4())
            # A client-supplied scope is never recomputed. An omitted one is a
            # recommendation and may be revised as the post gains roles.
            project_scope_is_auto = not project_scope
            resolved_project_scope = project_scope
            if not resolved_project_scope:
                # Headcount is 1 by necessity: a post is inserted before its roles exist.
                # recompute_project_scope() corrects it on the first POST /job-roles.
                calculation = JobPostFunctions.calculate_project_scope(
                    estimated_duration=estimated_duration,
                    working_days=working_days,
                    position_count=1,
                )
                resolved_project_scope = calculation["recommended_project_scope"]
                logger(
                    "JOB_POST_FUNCTIONS",
                    f"Auto-calculated project_scope={resolved_project_scope} for new job post",
                    level="INFO",
                )

            # NEW: infer project category
            project_category = JobPostFunctions.infer_project_category(job_title, job_description)
            logger(
                "JOB_POST_FUNCTIONS",
                f"Inferred project_category={project_category} for new job post",
                level="INFO",
            )

            job_post_data = {
                "job_post_id":        job_post_id,
                "client_id":          client_id,
                "job_title":          job_title,
                "job_description":    job_description,
                "project_type":       project_type,
                "project_scope":      resolved_project_scope,
                "project_scope_is_auto": project_scope_is_auto,
                "estimated_duration": estimated_duration,
                "working_days":       working_days,
                "deadline":           deadline,
                "experience_level":   experience_level,
                "status":             status,
                "is_ai_generated":    is_ai_generated,
                "proposal_count":     0,
                "project_category":   project_category,
            }


            db.insert_data(table_name="job_post", data=job_post_data)


            # Only count published (non-draft) posts toward total_jobs_posted.
            # A draft that is later published gets counted in update_job_post.
            if status != "draft":
                JobPostFunctions._adjust_client_jobs_posted(db, client_id, +1)


            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} created", level="INFO")
            return {
                **convert_uuids_to_str(job_post_data),
                "role_count":  0,
                "available_positions": 0,
                "client_name": None,
                "profile_picture_url": None,
                "proposal_count": 0,
                "closure_reason": None,
                "closure_note": None,
            }


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error creating job post: {str(e)}", level="ERROR")
            raise


    @staticmethod
    def update_job_post(job_post_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job post information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}


            if not update_data:
                logger("JOB_POST_FUNCTIONS", "No data to update", level="WARNING")
                return JobPostFunctions.get_job_post_by_id(job_post_id)

            # An explicit scope in the payload is a deliberate client choice: pin it
            # so later role changes stop moving it.
            if "project_scope" in update_data:
                update_data["project_scope_is_auto"] = False

            # NEW: re-infer category if title or description changed
            if "job_title" in update_data or "job_description" in update_data:
                existing = JobPostFunctions.get_job_post_by_id(job_post_id)
                new_title = update_data.get("job_title", existing["job_title"] if existing else "")
                new_desc  = update_data.get("job_description", existing["job_description"] if existing else "")
                update_data["project_category"] = JobPostFunctions.infer_project_category(new_title, new_desc)
                logger(
                    "JOB_POST_FUNCTIONS",
                    f"Re-inferred project_category={update_data['project_category']} on update for {job_post_id}",
                    level="INFO",
                )

            # Keep total_jobs_posted in sync when this update crosses the draft
            # boundary. Read the old status before updating.
            draft_delta = 0
            transition_client_id = None
            if "status" in update_data:
                before = JobPostFunctions.get_job_post_by_id(job_post_id)
                if before:
                    was_draft = before["status"] == "draft"
                    now_draft = update_data["status"] == "draft"
                    if was_draft and not now_draft:
                        draft_delta = +1
                    elif not was_draft and now_draft:
                        draft_delta = -1
                    transition_client_id = before["client_id"]

            conditions = [("job_post_id", "=", job_post_id)]
            db.update_data(table_name="job_post", data=update_data, conditions=conditions)

            if draft_delta and transition_client_id:
                JobPostFunctions._adjust_client_jobs_posted(db, transition_client_id, draft_delta)

            # Timeline fields only - headcount lives on job_role, so POST/DELETE /job-roles
            # runs its own recompute. No-ops when the client pinned the scope.
            _SCOPE_SIGNAL_FIELDS = {"estimated_duration", "working_days"}
            if "project_scope" not in update_data and (_SCOPE_SIGNAL_FIELDS & update_data.keys()):
                JobPostFunctions.recompute_project_scope(job_post_id)

            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} updated", level="INFO")
            return JobPostFunctions.get_job_post_by_id(job_post_id)


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error updating job post: {str(e)}", level="ERROR")
            raise


    @staticmethod
    def delete_job_post(job_post_id: str) -> bool:
        """Delete a job post."""
        try:
            db = get_db()

            # Read status and client before deleting, so total_jobs_posted can be
            # decremented when a published post is removed.
            existing = JobPostFunctions.get_job_post_by_id(job_post_id)

            conditions = [("job_post_id", "=", job_post_id)]
            db.delete_data(table_name="job_post", conditions=conditions)

            if existing and existing["status"] != "draft":
                JobPostFunctions._adjust_client_jobs_posted(db, existing["client_id"], -1)


            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} deleted", level="INFO")
            return True


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error deleting job post: {str(e)}", level="ERROR")
            raise