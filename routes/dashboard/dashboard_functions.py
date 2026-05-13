import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Dict, List, Any, Optional
import math


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> Dict:
    d = dict(row)
    return {k: str(v) if hasattr(v, "__class__") and "UUID" in v.__class__.__name__ else v
            for k, v in d.items()}


def _sorted_none_last(items: List[Dict], field: str, reverse: bool) -> List[Dict]:
    """Sort items by field, always placing None values at the end."""
    has_val = [item for item in items if item.get(field) is not None]
    no_val  = [item for item in items if item.get(field) is None]
    try:
        has_val.sort(key=lambda x: x[field], reverse=reverse)
    except TypeError:
        has_val.sort(key=lambda x: str(x[field]), reverse=reverse)
    return has_val + no_val


def _paginate(items: List[Dict], page: int, page_size: int) -> Dict[str, Any]:
    total      = len(items)
    total_pages = math.ceil(total / page_size) if page_size else 1
    start      = (page - 1) * page_size
    return {
        "items":       items[start : start + page_size],
        "pagination": {
            "page":        page,
            "page_size":   page_size,
            "total":       total,
            "total_pages": total_pages,
        },
    }


def _filter_tracking_statuses(
    items: List[Dict],
    include_statuses: Optional[set] = None,
    exclude_statuses: Optional[set] = None,
) -> List[Dict]:
    """Apply include/exclude tracking status filters after summary calculation."""
    filtered = items
    if include_statuses:
        filtered = [i for i in filtered if i.get("tracking_status") in include_statuses]
    if exclude_statuses:
        filtered = [i for i in filtered if i.get("tracking_status") not in exclude_statuses]
    return filtered


# ── Tracking status helpers ───────────────────────────────────────────────────

def _freelancer_tracking_status(proposal_status: str, contract_status: Optional[str]) -> str:
    """Single unified status for a freelancer's journey on one job."""
    if contract_status == "completed":          return "completed"
    if contract_status == "cancelled":          return "cancelled"
    if contract_status == "disputed":           return "disputed"
    if contract_status == "revision_requested": return "revision_requested"
    if contract_status == "under_review":       return "work_submitted"
    if contract_status == "active":             return "in_progress"
    if proposal_status == "accepted":           return "hired"
    if proposal_status == "pending":            return "applied"
    if proposal_status == "rejected":           return "rejected"
    if proposal_status == "withdrawn":          return "withdrawn"
    return "unknown"


def _contract_tracking_status(contract_status: str) -> str:
    return {
        "active":             "in_progress",
        "under_review":       "work_submitted",
        "revision_requested": "revision_requested",
        "completed":          "completed",
        "cancelled":          "cancelled",
        "disputed":           "disputed",
    }.get(contract_status, contract_status)


def _role_tracking_status(positions_available: int, positions_filled: int, role_contracts: List[Dict]) -> str:
    """
    Per-role status. 'hiring' means positions still open while work is ongoing.
    """
    contract_statuses = {c["status"] for c in role_contracts}
    if "disputed" in contract_statuses:            return "disputed"
    if "revision_requested" in contract_statuses:  return "revision_requested"
    if "under_review" in contract_statuses:        return "work_submitted"
    if "active" in contract_statuses:
        if positions_filled < positions_available:  return "hiring"
        return "in_progress"
    if contract_statuses <= {"completed", "cancelled"}:
        if positions_filled < positions_available:  return "open"
        return "completed"
    return "open"


def _job_tracking_status(job_status: str, role_statuses: List[str]) -> str:
    """
    Job-level status rolled up from all role statuses.
    Priority (highest first):
    disputed > revision_requested > work_submitted > hiring > in_progress > open > completed > draft
    """
    if not role_statuses:
        return "draft" if job_status == "draft" else "open"
    s = set(role_statuses)
    if "disputed" in s:            return "disputed"
    if "revision_requested" in s:  return "revision_requested"
    if "work_submitted" in s:      return "work_submitted"
    if "hiring" in s:              return "hiring"
    if "in_progress" in s:         return "in_progress"
    if "open" in s:                return "open"
    if s <= {"completed"}:         return "completed"
    return job_status


# ── Valid sort fields ─────────────────────────────────────────────────────────

_FREELANCER_SORT_FIELDS = {
    "submitted_at",         # when the proposal was sent
    "start_date",           # contract start
    "end_date",             # contract expected end
    "actual_completion_date",
    "last_activity_date",   # computed: most recent date in the lifecycle
    "job_title",
    "proposed_budget",
    "agreed_budget",
}

_CLIENT_SORT_FIELDS = {
    "created_at",           # when the job post was created
    "posted_at",            # when the job went live
    "deadline",             # application deadline
    "last_activity_date",   # computed: most recent contract start across all roles
    "job_title",
}


# ── Dashboard functions ───────────────────────────────────────────────────────

class DashboardFunctions:

    # ── Freelancer ────────────────────────────────────────────────────────────

    @staticmethod
    def get_freelancer_dashboard(
        freelancer_id: str,
        tracking_status: Optional[str] = None,
        tracking_statuses: Optional[set] = None,
        exclude_tracking_statuses: Optional[set] = None,
        order_by: str = "last_activity_date",
        order_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Returns every job the freelancer applied to, enriched with contract data.
        Dates on every item:
          submitted_at            — when they applied
          start_date              — contract start (null if no contract)
          end_date                — expected contract end
          actual_completion_date  — when work was accepted
          last_activity_date      — most recent lifecycle date (used for default sort)
        """
        try:
            db = get_db()

            rows = db.execute_query(
                """
                SELECT
                    p.proposal_id,
                    p.job_post_id,
                    p.job_role_id,
                    p.status            AS proposal_status,
                    p.proposed_budget,
                    p.proposed_duration,
                    p.submitted_at,
                    jp.job_title,
                    jp.project_type,
                    jp.project_scope,
                    jp.status           AS job_status,
                    jp.deadline         AS job_deadline,
                    jr.role_title,
                    jr.budget_currency,
                    c.contract_id,
                    c.contract_title,
                    c.agreed_budget,
                    c.status            AS contract_status,
                    c.start_date,
                    c.end_date,
                    c.actual_completion_date,
                    c.total_paid,
                    c.created_at        AS contract_created_at
                FROM proposal p
                JOIN      job_post jp ON jp.job_post_id = p.job_post_id
                LEFT JOIN job_role jr ON jr.job_role_id = p.job_role_id
                LEFT JOIN contract  c ON c.proposal_id  = p.proposal_id
                WHERE p.freelancer_id = :fid
                """,
                {"fid": freelancer_id},
            )

            all_items: List[Dict] = []
            for row in (rows or []):
                item = _row_to_dict(row)
                item["tracking_status"] = _freelancer_tracking_status(
                    item["proposal_status"],
                    item.get("contract_status"),
                )
                # last_activity_date: most recent meaningful date in the lifecycle
                item["last_activity_date"] = (
                    item.get("actual_completion_date")
                    or item.get("end_date")
                    or item.get("start_date")
                    or item.get("submitted_at")
                )
                all_items.append(item)

            # Full summary always based on all items (not filtered)
            status_counts: Dict[str, int] = {}
            for i in all_items:
                s = i["tracking_status"]
                status_counts[s] = status_counts.get(s, 0) + 1

            total_earned = sum(
                float(i["total_paid"] or 0)
                for i in all_items
                if i.get("total_paid") is not None
            )

            summary = {
                "total_applied":      len(all_items),
                "applied":            status_counts.get("applied", 0),
                "hired":              status_counts.get("hired", 0),
                "in_progress":        status_counts.get("in_progress", 0),
                "work_submitted":     status_counts.get("work_submitted", 0),
                "revision_requested": status_counts.get("revision_requested", 0),
                "completed":          status_counts.get("completed", 0),
                "rejected":           status_counts.get("rejected", 0),
                "withdrawn":          status_counts.get("withdrawn", 0),
                "cancelled":          status_counts.get("cancelled", 0),
                "total_earned":       round(total_earned, 2),
            }

            include_statuses = set(tracking_statuses or set())
            if tracking_status:
                include_statuses.add(tracking_status)

            filtered = _filter_tracking_statuses(
                all_items,
                include_statuses=include_statuses,
                exclude_statuses=exclude_tracking_statuses,
            )

            # Sort
            sort_field = order_by if order_by in _FREELANCER_SORT_FIELDS else "last_activity_date"
            sorted_items = _sorted_none_last(filtered, sort_field, order_dir.lower() == "desc")

            # Paginate
            result = _paginate(sorted_items, page, page_size)

            return {
                "freelancer_id": freelancer_id,
                "summary":       summary,
                "pagination":    result["pagination"],
                "items":         result["items"],
            }

        except Exception as e:
            logger("DASHBOARD_FUNCTIONS", f"Error building freelancer dashboard: {str(e)}", level="ERROR")
            raise

    # ── Client ────────────────────────────────────────────────────────────────

    @staticmethod
    def get_client_dashboard(
        client_id: str,
        tracking_status: Optional[str] = None,
        tracking_statuses: Optional[set] = None,
        exclude_tracking_statuses: Optional[set] = None,
        order_by: str = "last_activity_date",
        order_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """
        Returns every job post the client created.
        Structure: job → roles → contracts (all with tracking_status + dates).
        Dates on every job item:
          created_at        — when the job post was created
          posted_at         — when it went live
          deadline          — application deadline
          last_activity_date — most recent contract start across all roles
        """
        try:
            db = get_db()

            job_rows = db.execute_query(
                """
                SELECT
                    job_post_id,
                    job_title,
                    project_type,
                    project_scope,
                    experience_level,
                    status,
                    proposal_count,
                    deadline,
                    created_at,
                    posted_at,
                    closed_at
                FROM job_post
                WHERE client_id = :cid
                """,
                {"cid": client_id},
            )

            role_rows = db.execute_query(
                """
                SELECT
                    jr.job_role_id,
                    jr.job_post_id,
                    jr.role_title,
                    jr.role_budget,
                    jr.budget_currency,
                    jr.budget_type,
                    jr.positions_available,
                    jr.positions_filled
                FROM job_role jr
                JOIN job_post jp ON jp.job_post_id = jr.job_post_id
                WHERE jp.client_id = :cid
                ORDER BY jr.display_order ASC, jr.created_at ASC
                """,
                {"cid": client_id},
            )

            contract_rows = db.execute_query(
                """
                SELECT
                    contract_id,
                    job_post_id,
                    job_role_id,
                    freelancer_id,
                    contract_title,
                    role_title,
                    agreed_budget,
                    budget_currency,
                    payment_structure,
                    status,
                    start_date,
                    end_date,
                    actual_completion_date,
                    total_paid,
                    created_at          AS contract_created_at
                FROM contract
                WHERE client_id = :cid
                ORDER BY created_at DESC
                """,
                {"cid": client_id},
            )

            # Index contracts by job_role_id
            contracts_by_role: Dict[str, List[Dict]] = {}
            all_contracts: List[Dict] = []
            for row in (contract_rows or []):
                c = _row_to_dict(row)
                c["tracking_status"] = _contract_tracking_status(c["status"])
                all_contracts.append(c)
                contracts_by_role.setdefault(c["job_role_id"], []).append(c)

            # Build roles indexed by job_post_id
            roles_by_job: Dict[str, List[Dict]] = {}
            for row in (role_rows or []):
                role = _row_to_dict(row)
                role_contracts = contracts_by_role.get(role["job_role_id"], [])
                role["contracts"] = role_contracts
                role["tracking_status"] = _role_tracking_status(
                    role.get("positions_available") or 1,
                    role.get("positions_filled") or 0,
                    role_contracts,
                )
                roles_by_job.setdefault(role["job_post_id"], []).append(role)

            # Build job list
            all_jobs: List[Dict] = []
            for row in (job_rows or []):
                job = _row_to_dict(row)
                job_roles = roles_by_job.get(job["job_post_id"], [])
                job["roles"] = job_roles
                job["tracking_status"] = _job_tracking_status(
                    job["status"],
                    [r["tracking_status"] for r in job_roles],
                )
                # last_activity_date: latest contract start across all roles,
                # falling back to posted_at then created_at
                contract_starts = [
                    c["start_date"]
                    for r in job_roles
                    for c in r["contracts"]
                    if c.get("start_date") is not None
                ]
                job["last_activity_date"] = (
                    max(contract_starts) if contract_starts
                    else job.get("posted_at") or job.get("created_at")
                )
                all_jobs.append(job)

            # Full summary (unfiltered)
            tracking_counts: Dict[str, int] = {}
            for job in all_jobs:
                s = job["tracking_status"]
                tracking_counts[s] = tracking_counts.get(s, 0) + 1

            total_spent = sum(float(c["total_paid"] or 0) for c in all_contracts)

            summary = {
                "total_jobs_posted":  len(all_jobs),
                "draft":              tracking_counts.get("draft", 0),
                "open":               tracking_counts.get("open", 0),
                "hiring":             tracking_counts.get("hiring", 0),
                "in_progress":        tracking_counts.get("in_progress", 0),
                "work_submitted":     tracking_counts.get("work_submitted", 0),
                "revision_requested": tracking_counts.get("revision_requested", 0),
                "completed":          tracking_counts.get("completed", 0),
                "disputed":           tracking_counts.get("disputed", 0),
                "total_spent":        round(total_spent, 2),
            }

            include_statuses = set(tracking_statuses or set())
            if tracking_status:
                include_statuses.add(tracking_status)

            filtered = _filter_tracking_statuses(
                all_jobs,
                include_statuses=include_statuses,
                exclude_statuses=exclude_tracking_statuses,
            )

            # Sort
            sort_field = order_by if order_by in _CLIENT_SORT_FIELDS else "last_activity_date"
            sorted_jobs = _sorted_none_last(filtered, sort_field, order_dir.lower() == "desc")

            # Paginate
            result = _paginate(sorted_jobs, page, page_size)

            return {
                "client_id":  client_id,
                "summary":    summary,
                "pagination": result["pagination"],
                "items":      result["items"],
            }

        except Exception as e:
            logger("DASHBOARD_FUNCTIONS", f"Error building client dashboard: {str(e)}", level="ERROR")
            raise
