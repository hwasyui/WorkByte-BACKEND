import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict, Any
import uuid
import math
import re
import json
import urllib.request
from datetime import datetime, timezone



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


_SCOPE_MARKET_CACHE_PATH = os.path.join(os.path.dirname(__file__), "project_scope_market_cache.json")
_SCOPE_CACHE_REFRESH_SECONDS = 7 * 24 * 60 * 60  # weekly refresh
_scope_market_cache: dict[str, Any] | None = None
_GLOBAL_MONTHLY_ROLE_BUDGET_USD = {
    "entry": 1600.0,
    "intermediate": 3200.0,
    "expert": 6000.0,
    "default": 2800.0,
}


class JobPostFunctions:
    """Handle all job post-related database operations."""


    @staticmethod
    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


    @staticmethod
    def _load_scope_market_cache() -> Dict[str, Any]:
        global _scope_market_cache
        if _scope_market_cache is not None:
            return _scope_market_cache


        try:
            with open(_SCOPE_MARKET_CACHE_PATH, encoding="utf-8") as fh:
                _scope_market_cache = json.load(fh)
        except Exception:
            _scope_market_cache = {
                "fx_rates_fetched_at": None,
                "fx_rates": {"USD": 1.0},
                "country_income_benchmarks": {},
            }
        return _scope_market_cache


    @staticmethod
    def _save_scope_market_cache() -> None:
        global _scope_market_cache
        cache = JobPostFunctions._load_scope_market_cache()
        with open(_SCOPE_MARKET_CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
        _scope_market_cache = cache


    @staticmethod
    def _cache_is_stale(fetched_at: Optional[str]) -> bool:
        if not fetched_at:
            return True
        try:
            ts = datetime.fromisoformat(fetched_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() >= _SCOPE_CACHE_REFRESH_SECONDS
        except Exception:
            return True


    @staticmethod
    def _refresh_scope_fx_rates_if_needed() -> Dict[str, float]:
        cache = JobPostFunctions._load_scope_market_cache()
        if not JobPostFunctions._cache_is_stale(cache.get("fx_rates_fetched_at")) and cache.get("fx_rates"):
            return {k.upper(): float(v) for k, v in cache["fx_rates"].items()}


        try:
            with urllib.request.urlopen(
                "https://api.frankfurter.app/latest?from=USD", timeout=4
            ) as resp:
                data = json.loads(resp.read())
            rates = {k.upper(): float(v) for k, v in data.get("rates", {}).items()}
            rates["USD"] = 1.0
            cache["fx_rates"] = rates
            cache["fx_rates_fetched_at"] = JobPostFunctions._now_utc_iso()
            JobPostFunctions._save_scope_market_cache()
            logger("JOB_POST_FUNCTIONS", f"Project-scope FX cache refreshed | {len(rates)} currencies", level="INFO")
            return rates
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Project-scope FX refresh failed ({e}); using cached rates", level="WARNING")
            return {k.upper(): float(v) for k, v in cache.get("fx_rates", {"USD": 1.0}).items()}


    @staticmethod
    def _to_usd_scope(amount: float, currency: Optional[str]) -> float:
        if amount is None or amount <= 0:
            return 0.0
        code = (currency or "USD").upper()
        rates = JobPostFunctions._refresh_scope_fx_rates_if_needed()
        rate = float(rates.get(code, 1.0))
        if rate <= 0:
            return float(amount)
        return float(amount) / rate


    @staticmethod
    def _fetch_country_income_benchmark(country_code: str) -> Optional[Dict[str, Any]]:
        """
        Fetch annual GNI per capita in current USD from World Bank,
        then derive a monthly benchmark in USD.
        """
        normalized = (country_code or "").strip().upper()
        if not normalized:
            return None


        url = (
            f"https://api.worldbank.org/v2/country/{normalized}/indicator/NY.GNP.PCAP.CD"
            "?format=json&per_page=10"
        )
        try:
            with urllib.request.urlopen(url, timeout=6) as resp:
                payload = json.loads(resp.read())


            rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
            for row in rows:
                value = row.get("value")
                year = row.get("date")
                if value is not None:
                    annual_usd = float(value)
                    return {
                        "country_code": normalized,
                        "source": "world_bank_gni_per_capita_current_usd",
                        "indicator": "NY.GNP.PCAP.CD",
                        "year": year,
                        "annual_income_usd": annual_usd,
                        "monthly_income_usd": annual_usd / 12.0,
                        "fetched_at": JobPostFunctions._now_utc_iso(),
                    }
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Income benchmark fetch failed for {normalized}: {e}", level="WARNING")
        return None


    @staticmethod
    def _get_country_income_benchmark(country_code: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = (country_code or "").strip().upper()
        if not normalized:
            return None


        cache = JobPostFunctions._load_scope_market_cache()
        benchmarks = cache.setdefault("country_income_benchmarks", {})
        cached = benchmarks.get(normalized)
        if cached and not JobPostFunctions._cache_is_stale(cached.get("fetched_at")):
            return cached


        live = JobPostFunctions._fetch_country_income_benchmark(normalized)
        if live:
            benchmarks[normalized] = live
            if not cache.get("fetched_at"):
                cache["fetched_at"] = JobPostFunctions._now_utc_iso()
            JobPostFunctions._save_scope_market_cache()
            return live


        return cached


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
    def _estimate_contributor_count(project_type: str, role_count: int) -> int:
        normalized_project_type = (project_type or "").strip().lower()
        minimum_for_type = 2 if normalized_project_type == "team" else 1
        return max(role_count, minimum_for_type)


    @staticmethod
    def _calculate_roles_budget_usd(roles: Optional[List[Dict[str, Any]]]) -> Optional[float]:
        if not roles:
            return None


        total_budget_usd = 0.0
        has_budget = False
        for role in roles:
            role_budget = role.get("role_budget")
            if role_budget is None:
                continue
            try:
                positions_available = max(int(role.get("positions_available") or 1), 1)
            except (ValueError, TypeError):
                positions_available = 1
            budget_currency = role.get("budget_currency") or "USD"
            total_budget_usd += JobPostFunctions._to_usd_scope(float(role_budget), budget_currency) * positions_available
            has_budget = True


        return total_budget_usd if has_budget else None


    @staticmethod
    def _get_global_monthly_role_budget_usd(experience_level: str) -> float:
        normalized_experience = (experience_level or "").strip().lower()
        return _GLOBAL_MONTHLY_ROLE_BUDGET_USD.get(
            normalized_experience,
            _GLOBAL_MONTHLY_ROLE_BUDGET_USD["default"],
        )


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
        Heuristic scope calculator.
        Returns a recommendation only; does not persist anything.
        """
        score = 0
        reasons: List[str] = []


        normalized_project_type = (project_type or "").strip().lower()
        normalized_experience = (experience_level or "").strip().lower()
        normalized_role_count = max(int(role_count or len(roles or []) or 1), 1)
        contributor_count = JobPostFunctions._estimate_contributor_count(
            normalized_project_type,
            normalized_role_count,
        )
        description_word_count = len((job_description or "").split())
        duration_days = working_days or JobPostFunctions._estimate_days_from_duration(estimated_duration)
        duration_months_estimate = max((duration_days or 30) / 30.0, 1.0)
        budget_usd = JobPostFunctions._calculate_roles_budget_usd(roles)
        monthly_budget_benchmark_usd = JobPostFunctions._get_global_monthly_role_budget_usd(normalized_experience)
        budget_to_market_multiple = None


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


        if budget_usd is not None:
            baseline = monthly_budget_benchmark_usd * duration_months_estimate * contributor_count
            if baseline > 0:
                budget_to_market_multiple = budget_usd / baseline
                if budget_to_market_multiple >= 2.25:
                    score += 3
                    reasons.append(
                        f"Combined role budget is high versus a global freelance benchmark: about {budget_to_market_multiple:.2f}x for {contributor_count} contributor(s) over {duration_months_estimate:.1f} months."
                    )
                elif budget_to_market_multiple >= 1.0:
                    score += 2
                    reasons.append(
                        f"Combined role budget is moderate-to-high versus a global freelance benchmark: about {budget_to_market_multiple:.2f}x for {contributor_count} contributor(s) over {duration_months_estimate:.1f} months."
                    )
                elif budget_to_market_multiple >= 0.55:
                    score += 1
                    reasons.append(
                        f"Combined role budget is moderate versus a global freelance benchmark: about {budget_to_market_multiple:.2f}x for {contributor_count} contributor(s) over {duration_months_estimate:.1f} months."
                    )
                else:
                    reasons.append(
                        f"Combined role budget is relatively low versus a global freelance benchmark: about {budget_to_market_multiple:.2f}x for {contributor_count} contributor(s) over {duration_months_estimate:.1f} months."
                    )

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
            1 if budget_usd is not None else 0,
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
                "duration_months_estimate": round(duration_months_estimate, 2),
                "experience_level": normalized_experience or None,
                "role_count": normalized_role_count,
                "contributor_count_estimate": contributor_count,
                "budget_usd": round(budget_usd, 2) if budget_usd is not None else None,
                "roles_budget_summary": roles or [],
                "global_monthly_role_budget_benchmark_usd": monthly_budget_benchmark_usd,
                "budget_to_market_multiple": round(budget_to_market_multiple, 3) if budget_to_market_multiple is not None else None,
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
    ) -> Dict[str, Any]:
        """
        Paginated + filtered + sorted job post browse.
        Draft gate: drafts only appear when the requesting client owns them.
        """
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

            count_query = f"""
                SELECT COUNT(DISTINCT jp.job_post_id) AS total
                FROM job_post jp
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
    def get_job_posts_by_client_id(client_id: str) -> List[Dict]:
        """Fetch all job posts for a client with role_count, client_name, and live proposal_count."""
        try:
            db = get_db()
            query = _JOB_POST_SELECT + """
                WHERE jp.client_id = :client_id
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