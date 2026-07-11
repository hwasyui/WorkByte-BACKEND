import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from functions.db_manager import get_db
from functions.logger import logger
from routes.admin.admin_moderation import scan_harmful_text_with_ml_fallback, insert_harmful_text_queue_entry
from routes.notifications.notification_functions import NotificationFunctions
from typing import List, Optional, Dict, Any
import uuid
import math
import re
from datetime import datetime, timezone

# harm labels reported to the client when a job post gets blocked, never the matched text
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



# Shared SELECT columns
_JOB_POST_SELECT = """
    SELECT
        jp.job_post_id, jp.client_id, jp.job_title, jp.job_description,
        jp.project_type, jp.project_scope, jp.estimated_duration,
        jp.working_days, jp.deadline, jp.experience_level, jp.status,
        jp.is_ai_generated, jp.view_count, jp.project_category,
        jp.created_at, jp.updated_at, jp.posted_at, jp.closed_at,
        jp.closure_reason, jp.closure_note,
        jp.moderation_status, jp.scanned_at,
        COUNT(DISTINCT jr.job_role_id) AS role_count,
        COALESCE(SUM(jr.positions_available), 0) AS available_positions,
        c.full_name AS client_name,
        c.profile_picture_url AS profile_picture_url,
        (
            SELECT COUNT(*)
            FROM proposal p
            WHERE p.job_post_id = jp.job_post_id
              AND p.moderation_status = 'visible'
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
    def _estimate_total_positions(role_count: int, roles: Optional[List[Dict[str, Any]]]) -> int:
        """Total headcount the job post is asking for - summed across every role's
        positions_available, not just a count of distinct roles. This is the real
        "how many people" signal (a team of 2 roles needing 5 freelancers each is a
        bigger project than 2 roles needing 1 each). Falls back to role_count when
        no per-role detail was sent."""
        if not roles:
            return max(role_count, 1)

        total = 0
        for role in roles:
            try:
                total += max(int(role.get("positions_available") or 1), 1)
            except (ValueError, TypeError):
                total += 1

        return max(total, role_count, 1)


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
        job_title: str,
        job_description: str,
        project_type: str,
        estimated_duration: Optional[str] = None,
        working_days: Optional[int] = None,
        experience_level: Optional[str] = None,
        role_count: Optional[int] = 1,
        roles: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Heuristic scope calculator based on timeline, role/headcount complexity,
        seniority, and description depth. Deliberately budget-free: two jobs with
        identical scope but very different pay shouldn't get different scope labels.
        Returns a recommendation only; does not persist anything.
        """
        score = 0
        reasons: List[str] = []

        normalized_project_type = (project_type or "").strip().lower()
        normalized_experience = (experience_level or "").strip().lower()
        normalized_role_count = max(int(role_count or len(roles or []) or 1), 1)
        total_positions = JobPostFunctions._estimate_total_positions(normalized_role_count, roles)
        description_word_count = len((job_description or "").split())
        duration_days = working_days or JobPostFunctions._estimate_days_from_duration(estimated_duration)

        if duration_days is not None:
            if duration_days >= 61:
                score += 3
                reasons.append(f"Long timeline detected ({duration_days} days).")
            elif duration_days >= 31:
                score += 2
                reasons.append(f"Moderate-to-long timeline detected ({duration_days} days).")
            elif duration_days >= 11:
                score += 1
                reasons.append(f"Short-to-moderate timeline detected ({duration_days} days).")

        if normalized_role_count >= 4:
            score += 3
            reasons.append(f"High role complexity detected ({normalized_role_count} roles).")
        elif normalized_role_count >= 2:
            score += 1
            reasons.append(f"Multiple roles detected ({normalized_role_count} roles).")

        if normalized_project_type == "team":
            score += 1
            reasons.append("Team-based project increases coordination complexity.")

        if normalized_experience == "expert":
            score += 2
            reasons.append("Expert-level experience requirement suggests higher complexity.")
        elif normalized_experience == "intermediate":
            score += 1
            reasons.append("Intermediate-level experience requirement suggests moderate complexity.")

        if total_positions >= 6:
            score += 3
            reasons.append(f"Large headcount requested ({total_positions} position(s) across all roles).")
        elif total_positions >= 3:
            score += 2
            reasons.append(f"Moderate headcount requested ({total_positions} position(s) across all roles).")
        elif total_positions >= 2:
            score += 1
            reasons.append(f"More than one position requested ({total_positions} position(s) across all roles).")

        if description_word_count >= 180:
            score += 2
            reasons.append(f"Detailed job description detected ({description_word_count} words).")
        elif description_word_count >= 80:
            score += 1
            reasons.append(f"Moderately detailed job description detected ({description_word_count} words).")

        if score >= 7:
            recommended_scope = "large"
        elif score >= 3:
            recommended_scope = "medium"
        else:
            recommended_scope = "small"

        non_empty_signals = sum([
            1 if duration_days is not None else 0,
            1 if normalized_role_count is not None else 0,
            1 if normalized_project_type else 0,
            1 if normalized_experience else 0,
            1 if description_word_count > 0 else 0,
        ])
        confidence = "high" if non_empty_signals >= 5 else "medium" if non_empty_signals >= 3 else "low"

        return {
            "recommended_project_scope": recommended_scope,
            "score": score,
            "confidence": confidence,
            "factors": {
                "job_title": job_title,
                "project_type": normalized_project_type,
                "working_days": working_days,
                "estimated_duration": estimated_duration,
                "duration_days_estimate": duration_days,
                "experience_level": normalized_experience or None,
                "role_count": normalized_role_count,
                "total_positions_estimate": total_positions,
                "job_description_word_count": description_word_count,
            },
            "reasons": reasons or ["Insufficient complexity signals found; defaulting to small scope."],
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
                      AND moderation_status = 'visible'
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


    # Valid sort fields → SQL expression
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

            # A 'scanning'/'blocked' post is hidden from everyone except its own client -
            # blocking content must never mean an owner can't see their own post to fix it,
            # but no one else should see it at all while it's not cleared.
            if requesting_client_id:
                where += " AND (jp.moderation_status = 'visible' OR jp.client_id = :rcid)"
                params.setdefault("rcid", requesting_client_id)
            else:
                where += " AND jp.moderation_status = 'visible'"

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
                  AND jp.moderation_status = 'visible'
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
        """Fetch a job post by ID with role_count, client_name, and live proposal_count.
        No visibility gating - this is the trusted/internal fetch used by every caller
        that already has its own authorization (ownership asserts, role/file/contract
        operations, etc.) and just needs the record regardless of moderation_status.
        For a viewer-facing fetch that should hide non-visible posts from strangers, use
        get_job_post_by_id_for_viewer() instead."""
        try:
            db = get_db()
            query = _JOB_POST_SELECT + """
                WHERE jp.job_post_id = :job_post_id
                GROUP BY jp.job_post_id, c.full_name, c.profile_picture_url
            """
            rows = db.execute_query(query, {"job_post_id": job_post_id})

            if not rows:
                return None

            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} found", level="INFO")
            return convert_uuids_to_str(dict(rows[0]))

        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_post_by_id_for_viewer(job_post_id: str, viewer_user_id: Optional[str]) -> Optional[Dict]:
        """Same fetch as get_job_post_by_id(), but with visibility gating applied for a
        specific viewer (or no viewer at all - pass None for an anonymous/public caller
        like the share-link page).

        If the post is not 'visible' (still 'scanning' or 'blocked'), only two viewers get
        the real data: the owning client, or a freelancer with an active contract already
        tied to this job (blocking content must never disrupt work already in progress).

        For anyone else: a 'blocked' post returns None, same as if it genuinely didn't
        exist - no leak that a hidden post is under moderation. A 'scanning' post (the
        background moderation scan kicked off by PUT .../status=active hasn't resolved
        yet - usually well under a second, see thread_analysis.md) instead returns a
        minimal stub carrying only job_post_id and moderation_status, so a caller can
        tell "not ready yet, try again shortly" apart from "doesn't exist" or "was
        rejected" - without exposing content that hasn't cleared moderation."""
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job_post:
            return None

        status = job_post.get("moderation_status")
        if status != "visible":
            privileged = bool(viewer_user_id) and JobPostFunctions._viewer_can_see_hidden_job_post(
                job_post_id, job_post.get("client_id"), viewer_user_id
            )
            if not privileged:
                logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} hidden from viewer {viewer_user_id} (moderation_status={status})", level="INFO")
                if status == "scanning":
                    return {"job_post_id": job_post_id, "moderation_status": "scanning"}
                return None

        return job_post

    @staticmethod
    def increment_view_count(job_post_id: str) -> None:
        try:
            db = get_db()
            db.execute_query(
                "UPDATE job_post SET view_count = view_count + 1 WHERE job_post_id = :job_post_id AND status = 'active'",
                {"job_post_id": job_post_id},
            )
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error incrementing view_count for {job_post_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_posts_by_client_id(client_id: str, viewer_user_id: Optional[str] = None) -> List[Dict]:
        """Fetch all job posts for a client with role_count, client_name, and live proposal_count.
        The client themselves sees everything including 'scanning'/'blocked' posts; anyone
        else only sees 'visible' ones."""
        try:
            db = get_db()
            is_owner = bool(viewer_user_id) and bool(get_db().execute_query(
                "SELECT 1 FROM client WHERE client_id = :cid AND user_id = :uid",
                {"cid": client_id, "uid": viewer_user_id},
            ))
            moderation_clause = "" if is_owner else "AND jp.moderation_status = 'visible'"
            query = _JOB_POST_SELECT + f"""
                WHERE jp.client_id = :client_id
                {moderation_clause}
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
                WHERE jp.status = 'active' AND jp.moderation_status = 'visible'
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
            resolved_project_scope = project_scope
            if not resolved_project_scope:
                calculation = JobPostFunctions.calculate_project_scope(
                    job_title=job_title,
                    job_description=job_description,
                    project_type=project_type,
                    estimated_duration=estimated_duration,
                    working_days=working_days,
                    experience_level=experience_level,
                    role_count=1,
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
                "estimated_duration": estimated_duration,
                "working_days":       working_days,
                "deadline":           deadline,
                "experience_level":   experience_level,
                "status":             status,
                "is_ai_generated":    is_ai_generated,
                "proposal_count":     0,
                "project_category":   project_category,  # ← NEW
                "moderation_status":  "scanning",
            }


            db.insert_data(table_name="job_post", data=job_post_data)


            # Increment the client's total jobs posted count
            client_rows = db.fetch_data(
                table_name="client",
                conditions=[("client_id", "=", client_id)],
                limit=1
            )
            if client_rows:
                current_count = client_rows[0].get("total_jobs_posted") or 0
                db.update_data(
                    table_name="client",
                    data={"total_jobs_posted": current_count + 1},
                    conditions=[("client_id", "=", client_id)]
                )


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
                "scanned_at": None,
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

            conditions = [("job_post_id", "=", job_post_id)]
            db.update_data(table_name="job_post", data=update_data, conditions=conditions)


            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} updated", level="INFO")
            return JobPostFunctions.get_job_post_by_id(job_post_id)


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error updating job post: {str(e)}", level="ERROR")
            raise


    @staticmethod
    async def run_job_post_scan(job_post_id: str, scan_text: str, client_user_id: str) -> None:
        """scanning -> scan -> visible | blocked. Mirrors ProposalFunctions.run_proposal_scan.

        Replaces the old pre-moderation-queue + held_active_contract path entirely: a job
        post is never auto-closed for content reasons anymore. 'blocked' only hides it from
        new/public viewers (see get_job_post_by_id's viewer_user_id handling and
        browse/search's moderation filter) - the owning client and any freelancer already
        under an active contract on this job keep full access, so live work is never
        disrupted. The client is notified immediately so they can fix and resubmit, same as
        proposals/portfolio entries."""
        try:
            JobPostFunctions.update_job_post(job_post_id, {"moderation_status": "scanning"})

            if scan_text and scan_text.strip():
                result = await scan_harmful_text_with_ml_fallback(scan_text)
            else:
                result = {"is_flagged": False, "detected_labels": []}

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                JobPostFunctions.update_job_post(job_post_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                })
                logger(
                    "JOB_POST_FUNCTIONS",
                    f"Job post {job_post_id} blocked, labels={result.get('detected_labels')}",
                    level="WARNING",
                )
                insert_harmful_text_queue_entry(
                    "job_post", job_post_id, client_user_id, scan_text, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in result.get("detected_labels", [])]
                try:
                    await NotificationFunctions.notify(
                        recipient_user_id=client_user_id,
                        notif_type="job_post_blocked",
                        title="Job Post Needs Changes",
                        body=f"Your job post was flagged for {', '.join(labels) or 'a policy violation'}. Edit and resubmit.",
                        data={"job_post_id": job_post_id},
                    )
                except Exception as notif_err:
                    logger("JOB_POST_FUNCTIONS", f"Blocked-job-post notification failed (non-fatal): {notif_err}", level="WARNING")
            else:
                JobPostFunctions.update_job_post(job_post_id, {
                    "moderation_status": "visible",
                    "scanned_at": scanned_at,
                })

        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Job post scan failed for {job_post_id}: {e}", level="ERROR")

    @staticmethod
    def _viewer_can_see_hidden_job_post(job_post_id: str, client_id: Optional[str], viewer_user_id: str) -> bool:
        """True if viewer_user_id is either the job post's owning client, or a freelancer
        with a live (not cancelled/completed) contract tied to this job post - the two
        parties for whom a 'blocked' post must never disappear, since blocking is about
        stopping new exposure, not disrupting work already underway."""
        try:
            row = get_db().execute_query(
                """
                SELECT
                    EXISTS(
                        SELECT 1 FROM client
                        WHERE client_id = :cid AND user_id = :uid
                    ) AS is_owner,
                    EXISTS(
                        SELECT 1 FROM contract c
                        JOIN freelancer f ON f.freelancer_id = c.freelancer_id
                        WHERE c.job_post_id = :jid AND f.user_id = :uid
                          AND c.status IN ('active', 'under_review', 'revision_requested')
                    ) AS is_contracted_freelancer
                """,
                {"cid": client_id, "uid": viewer_user_id, "jid": job_post_id},
            )
            if not row:
                return False
            r = dict(row[0])
            return bool(r.get("is_owner")) or bool(r.get("is_contracted_freelancer"))
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error checking viewer access for hidden job {job_post_id}: {str(e)}", level="ERROR")
            return False

    @staticmethod
    async def notify_stakeholders_of_material_edit(job_post_id: str, changed_fields: List[str]) -> None:
        """Tell freelancers with a pending proposal or an active contract on this
        job post that a material field (budget/deadline/requirements-shaped) just
        changed under them - status changes (close) already have their own notify
        path, so this is only for edits to fields that actually affect the work."""
        try:
            db = get_db()
            pending_rows = db.execute_query(
                """
                SELECT DISTINCT f.user_id AS freelancer_user_id
                FROM proposal p
                JOIN freelancer f ON f.freelancer_id = p.freelancer_id
                WHERE p.job_post_id = :jid AND p.status = 'pending'
                """,
                params={"jid": job_post_id},
            )
            contract_rows = db.execute_query(
                """
                SELECT DISTINCT f.user_id AS freelancer_user_id
                FROM contract c
                JOIN freelancer f ON f.freelancer_id = c.freelancer_id
                WHERE c.job_post_id = :jid
                  AND c.status IN ('active', 'under_review', 'revision_requested', 'disputed')
                """,
                params={"jid": job_post_id},
            )
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Failed to look up stakeholders for job edit notify {job_post_id}: {e}", level="ERROR")
            return

        recipient_user_ids = {str(r["freelancer_user_id"]) for r in (pending_rows or []) + (contract_rows or [])}
        fields_text = ", ".join(sorted(changed_fields))

        for user_id in recipient_user_ids:
            try:
                await NotificationFunctions.notify(
                    recipient_user_id=user_id,
                    notif_type="job_post_edited",
                    title="Job Post Updated",
                    body=f"A job post you're involved in was updated ({fields_text}). Review the changes to make sure they still work for you.",
                    data={"job_post_id": job_post_id, "changed_fields": changed_fields},
                )
            except Exception as notif_err:
                logger(
                    "JOB_POST_FUNCTIONS",
                    f"Job-edit notification failed for user {user_id} (non-fatal): {notif_err}",
                    level="WARNING",
                )

    @staticmethod
    def delete_job_post(job_post_id: str) -> bool:
        """Delete a job post."""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            db.delete_data(table_name="job_post", conditions=conditions)


            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} deleted", level="INFO")
            return True


        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error deleting job post: {str(e)}", level="ERROR")
            raise