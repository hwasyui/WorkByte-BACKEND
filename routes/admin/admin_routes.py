import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, List, Optional
from pydantic import BaseModel

from functions.schema_model import UserInDB, ArbitrateDisputeRequest
from functions.authentication import get_current_user, get_admin_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.db_manager import get_db
from routes.contracts.contract_functions import ContractFunctions
from routes.clients.client_functions import ClientFunctions
from routes.admin.admin_functions import (
    VALID_REPORT_REASONS,
    action_moderation_item,
    action_report,
    action_scam_flag,
    admin_close_account,
    admin_close_job,
    admin_list_jobs,
    admin_list_users,
    admin_reopen_account,
    admin_reopen_job,
    create_report,
    force_expire_moderation,
    force_expire_reports,
    force_expire_scam_flags,
    get_admin_dashboard_stats,
    get_admin_user_detail,
    get_client_scam_record,
    get_appeal,
    get_appeal_status,
    get_user_appeals,
    get_report,
    list_appeals,
    list_flagged_client_reviews,
    list_flagged_reviews,
    list_moderation_queue,
    list_red_flag_alerts,
    list_report_auto_actions,
    list_report_targets,
    list_reports,
    list_scam_flags,
    override_publish_client_review,
    override_publish_review,
    queue_scam_scan,
    resolve_appeal,
    resolve_red_flag_alert,
    submit_appeal,
)

admin_router   = APIRouter(prefix="/admin",   tags=["Admin"])
reports_router = APIRouter(prefix="/reports", tags=["Reports"])
appeals_router = APIRouter(prefix="/appeals", tags=["Appeals"])


class AdminActionBody(BaseModel):
    admin_note: Optional[str] = None


class ScamScanBody(BaseModel):
    job_post_id: str
    client_id:   str
    text:        str


class ForceExpireBody(BaseModel):
    ids: List[str]


class ReportCreateBody(BaseModel):
    reported_type:    str                    # 'freelancer' | 'client' | 'job_post'
    reported_user_id: Optional[str] = None  # required for freelancer / client reports
    job_post_id:      Optional[str] = None  # required for job_post reports
    reasons:          List[str] = []         # subset of VALID_REPORT_REASONS
    custom_reason:    Optional[str] = None


class AppealSubmitBody(BaseModel):
    target_type: str   # 'user' | 'job_post'
    target_id:   str
    message:     str


class ForceExpireReportBody(BaseModel):
    target_type: str   # 'user' | 'job_post'
    target_id:   str


class AdminOverrideBody(BaseModel):
    reason: Optional[str] = None  # shown to the affected user as closure_note / ban_message


@admin_router.get("/dashboard")
async def admin_dashboard(current_user: UserInDB = Depends(get_admin_user)):
    """Summary counts for moderation, scam flags, reports, and bans."""
    try:
        stats = get_admin_dashboard_stats()
        logger("ADMIN", "Dashboard stats fetched", "GET /admin/dashboard", "INFO")
        return ResponseSchema.success(stats, 200)
    except Exception as e:
        logger("ADMIN", f"Dashboard error: {e}", "GET /admin/dashboard", "ERROR")
        return ResponseSchema.error(f"Failed to fetch dashboard stats: {e}", 500)


@admin_router.get("/moderation")
async def list_moderation(
    status:       str = Query(default="pending",     description="pending | approved | rejected | all"),
    sort_by:      str = Query(default="created_at",  description="created_at | total_score | max_score | content_type | status"),
    sort_dir:     str = Query(default="desc",        description="asc | desc"),
    min_severity: Optional[float] = Query(default=None, ge=0, le=1,
                                           description="only return items where max(all 5 label scores) >= this value - "
                                                        "use this instead of judging severity off a single label's score, "
                                                        "since one label (e.g. threat) can be suppressed when another "
                                                        "(e.g. insult) dominates the same text"),
    page:         int = Query(default=1, ge=1),
    page_size:    int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List harmful text detection queue items. Supports filtering by status/severity and sorting."""
    try:
        if status not in ("pending", "approved", "rejected", "all"):
            return ResponseSchema.error("status must be pending, approved, rejected, or all", 400)
        if sort_by not in ("created_at", "total_score", "max_score", "content_type", "status"):
            return ResponseSchema.error("sort_by must be created_at, total_score, max_score, content_type, or status", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        items = list_moderation_queue(
            status=status,
            sort_by=sort_by, sort_dir=sort_dir,
            min_severity=min_severity,
            page=page, page_size=page_size,
        )
        logger("ADMIN", f"Moderation queue fetched: status={status} sort={sort_by} {sort_dir} min_severity={min_severity}", "GET /admin/moderation", "INFO")
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Moderation list error: {e}", "GET /admin/moderation", "ERROR")
        return ResponseSchema.error(f"Failed to fetch moderation queue: {e}", 500)


@admin_router.post("/moderation/{moderation_id}/approve")
async def approve_moderation(
    moderation_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Approve a flagged content item: confirms the AI flag and actions harmful content (job closed, etc.)."""
    try:
        updated = action_moderation_item(
            moderation_id=moderation_id,
            action="approve",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Item not found or already actioned", 404)
        logger("ADMIN", f"Moderation {moderation_id} approved by {current_user.user_id}", "POST /admin/moderation/approve", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Approve moderation error: {e}", "POST /admin/moderation/approve", "ERROR")
        return ResponseSchema.error(f"Failed to approve item: {e}", 500)


@admin_router.post("/moderation/{moderation_id}/reject")
async def reject_moderation(
    moderation_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Reject a flagged content item: dismisses the AI flag as a false positive; content is allowed to stay."""
    try:
        updated = action_moderation_item(
            moderation_id=moderation_id,
            action="reject",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Item not found or already actioned", 404)
        logger("ADMIN", f"Moderation {moderation_id} rejected by {current_user.user_id}", "POST /admin/moderation/reject", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Reject moderation error: {e}", "POST /admin/moderation/reject", "ERROR")
        return ResponseSchema.error(f"Failed to reject item: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.post("/moderation/force-expire")
async def force_expire_mod_items(
    body: ForceExpireBody,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Backdate auto_approve_at and immediately trigger the auto-action sweep (testing utility)."""
    try:
        force_expire_moderation(body.ids)
        return ResponseSchema.success({"processed": len(body.ids)}, 200)
    except Exception as e:
        logger("ADMIN", f"Force expire mod error: {e}", "POST /admin/moderation/force-expire", "ERROR")
        return ResponseSchema.error(f"Force expire failed: {e}", 500)


@admin_router.get("/scam-flags")
async def list_scam(
    status:    str = Query(default="pending",    description="pending | safe | removed | all"),
    sort_by:   str = Query(default="created_at", description="created_at | scam_score"),
    sort_dir:  str = Query(default="desc",       description="asc | desc"),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List scam-flagged job posts. Supports sorting by date or score."""
    try:
        if status not in ("pending", "safe", "removed", "all"):
            return ResponseSchema.error("status must be pending, safe, removed, or all", 400)
        if sort_by not in ("created_at", "scam_score"):
            return ResponseSchema.error("sort_by must be created_at or scam_score", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        flags = list_scam_flags(status=status, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
        logger("ADMIN", f"Scam flags fetched: status={status} sort={sort_by} {sort_dir}", "GET /admin/scam-flags", "INFO")
        return ResponseSchema.success(flags, 200)
    except Exception as e:
        logger("ADMIN", f"Scam flags list error: {e}", "GET /admin/scam-flags", "ERROR")
        return ResponseSchema.error(f"Failed to fetch scam flags: {e}", 500)


@admin_router.post("/scam-flags/{flag_id}/approve")
async def approve_scam_flag(
    flag_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Mark a scam-flagged job as safe (false positive)."""
    try:
        updated = action_scam_flag(
            flag_id=flag_id,
            action="approve",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Flag not found or already actioned", 404)
        logger("ADMIN", f"Scam flag {flag_id} marked safe by {current_user.user_id}", "POST /admin/scam-flags/approve", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Approve scam flag error: {e}", "POST /admin/scam-flags/approve", "ERROR")
        return ResponseSchema.error(f"Failed to approve flag: {e}", 500)


@admin_router.post("/scam-flags/{flag_id}/remove")
async def remove_scam_job(
    flag_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """
    Confirm scam and remove the job post. Closes the job and records a strike
    against the client (3 strikes = banned).
    """
    try:
        updated = action_scam_flag(
            flag_id=flag_id,
            action="remove",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Flag not found or already actioned", 404)
        logger("ADMIN", f"Scam flag {flag_id} removed by {current_user.user_id}", "POST /admin/scam-flags/remove", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Remove scam job error: {e}", "POST /admin/scam-flags/remove", "ERROR")
        return ResponseSchema.error(f"Failed to remove scam job: {e}", 500)


@admin_router.post("/scam-flags/force-expire")
async def force_expire_scam_flag_items(
    body: ForceExpireBody,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Backdate auto_remove_at and immediately trigger the auto-action sweep (testing utility)."""
    try:
        force_expire_scam_flags(body.ids)
        return ResponseSchema.success({"processed": len(body.ids)}, 200)
    except Exception as e:
        logger("ADMIN", f"Force expire scam error: {e}", "POST /admin/scam-flags/force-expire", "ERROR")
        return ResponseSchema.error(f"Force expire failed: {e}", 500)


@admin_router.post("/scam-flags/scan")
async def trigger_scam_scan(
    body: ScamScanBody,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Manually trigger a scam scan on a job post (admin utility)."""
    try:
        result = queue_scam_scan(
            job_post_id=body.job_post_id,
            client_id=body.client_id,
            text=body.text,
        )
        if result is None:
            return ResponseSchema.success({"flagged": False, "message": "Job post passed scam check"}, 200)
        return ResponseSchema.success({"flagged": True, "scam_flag": result}, 200)
    except Exception as e:
        logger("ADMIN", f"Scam scan error: {e}", "POST /admin/scam-flags/scan", "ERROR")
        return ResponseSchema.error(f"Scam scan failed: {e}", 500)


@admin_router.get("/scam-flags/client/{client_id}")
async def get_client_scam_info(
    client_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Retrieve a client's scam record (confirmed count, ban status)."""
    try:
        record = get_client_scam_record(client_id)
        return ResponseSchema.success(record or {"client_id": client_id, "total_scam_confirmed": 0, "is_banned": False}, 200)
    except Exception as e:
        logger("ADMIN", f"Client scam record error: {e}", "GET /admin/scam-flags/client", "ERROR")
        return ResponseSchema.error(f"Failed to fetch client scam record: {e}", 500)


@admin_router.get("/reports")
async def admin_list_reports(
    status:        str = Query(default="pending",    description="pending | accepted | dismissed | all"),
    reported_type: str = Query(default="all",        description="freelancer | client | job_post | all"),
    sort_by:       str = Query(default="created_at", description="created_at | reported_type | status"),
    sort_dir:      str = Query(default="desc",       description="asc | desc"),
    page:          int = Query(default=1, ge=1),
    page_size:     int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List individual user reports. Filter by status and reported_type; sort by date or type."""
    try:
        if status not in ("pending", "accepted", "dismissed", "all"):
            return ResponseSchema.error("status must be pending, accepted, dismissed, or all", 400)
        if reported_type not in ("freelancer", "client", "job_post", "all"):
            return ResponseSchema.error("reported_type must be freelancer, client, job_post, or all", 400)
        if sort_by not in ("created_at", "reported_type", "status"):
            return ResponseSchema.error("sort_by must be created_at, reported_type, or status", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        items = list_reports(
            status=status, reported_type=reported_type,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
        logger("ADMIN", f"Reports fetched: status={status} type={reported_type} sort={sort_by} {sort_dir}", "GET /admin/reports", "INFO")
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Reports list error: {e}", "GET /admin/reports", "ERROR")
        return ResponseSchema.error(f"Failed to fetch reports: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.get("/reports/targets")
async def admin_list_report_targets(
    target_type: str = Query(default="all",          description="user | job_post | all"),
    sort_by:     str = Query(default="report_count", description="report_count | oldest_report | latest_report"),
    sort_dir:    str = Query(default="desc",         description="asc | desc"),
    min_count:   int = Query(default=1, ge=1,        description="Only show targets with at least this many reports"),
    page:        int = Query(default=1, ge=1),
    page_size:   int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """
    Grouped report view: one row per target (user or job post) with report count,
    oldest/latest report date, and whether the auto-action threshold is met.
    Use min_count=10 to see all targets at or above the auto-ban threshold.
    """
    try:
        if target_type not in ("user", "job_post", "all"):
            return ResponseSchema.error("target_type must be user, job_post, or all", 400)
        if sort_by not in ("report_count", "oldest_report", "latest_report"):
            return ResponseSchema.error("sort_by must be report_count, oldest_report, or latest_report", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        items = list_report_targets(
            target_type=target_type, sort_by=sort_by, sort_dir=sort_dir,
            min_count=min_count, page=page, page_size=page_size,
        )
        logger("ADMIN", f"Report targets fetched: type={target_type} min={min_count} sort={sort_by} {sort_dir}", "GET /admin/reports/targets", "INFO")
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Report targets error: {e}", "GET /admin/reports/targets", "ERROR")
        return ResponseSchema.error(f"Failed to fetch report targets: {e}", 500)


@admin_router.post("/reports/{report_id}/accept")
async def accept_report(
    report_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Accept a user report (confirms the violation)."""
    try:
        updated = action_report(
            report_id=report_id,
            action="accept",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Report not found or already actioned", 404)
        logger("ADMIN", f"Report {report_id} accepted by {current_user.user_id}", "POST /admin/reports/accept", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Accept report error: {e}", "POST /admin/reports/accept", "ERROR")
        return ResponseSchema.error(f"Failed to accept report: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.get("/reports/auto-actions")
async def list_auto_actions(
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List all auto-ban / auto-close actions triggered by the report threshold."""
    try:
        items = list_report_auto_actions(page=page, page_size=page_size)
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Auto-actions list error: {e}", "GET /admin/reports/auto-actions", "ERROR")
        return ResponseSchema.error(f"Failed to fetch auto-actions: {e}", 500)


@admin_router.post("/reports/{report_id}/dismiss")
async def dismiss_report(
    report_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Dismiss a user report (no violation found)."""
    try:
        updated = action_report(
            report_id=report_id,
            action="dismiss",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Report not found or already actioned", 404)
        logger("ADMIN", f"Report {report_id} dismissed by {current_user.user_id}", "POST /admin/reports/dismiss", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Dismiss report error: {e}", "POST /admin/reports/dismiss", "ERROR")
        return ResponseSchema.error(f"Failed to dismiss report: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.post("/reports/force-expire-target")
async def force_expire_report_target(
    body: ForceExpireReportBody,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Backdate report created_at for a target and immediately trigger auto-action sweep (testing utility)."""
    try:
        if body.target_type not in ("user", "job_post"):
            return ResponseSchema.error("target_type must be 'user' or 'job_post'", 400)
        force_expire_reports(body.target_type, body.target_id)
        return ResponseSchema.success({"target_type": body.target_type, "target_id": body.target_id}, 200)
    except Exception as e:
        logger("ADMIN", f"Force expire reports error: {e}", "POST /admin/reports/force-expire-target", "ERROR")
        return ResponseSchema.error(f"Force expire failed: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.get("/reports/{report_id}")
async def admin_get_report(
    report_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Fetch a single report by ID with full reporter and target details."""
    try:
        item = get_report(report_id)
        if not item:
            return ResponseSchema.error("Report not found", 404)
        logger("ADMIN", f"Report {report_id} fetched by {current_user.user_id}", "GET /admin/reports/{report_id}", "INFO")
        return ResponseSchema.success(item, 200)
    except Exception as e:
        logger("ADMIN", f"Get report error: {e}", "GET /admin/reports/{report_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch report: {e}", 500)


@admin_router.get("/appeals")
async def admin_list_appeals(
    status:         str           = Query(default="pending", description="pending | approved | rejected | all"),
    target_type:    Optional[str] = Query(default=None,      description="user | job_post"),
    appeal_attempt: Optional[int] = Query(default=None,      description="1 = first appeal, 2 = final attempt", ge=1, le=2),
    search:         Optional[str] = Query(default=None,      description="Partial match on submitter email"),
    page:           int           = Query(default=1, ge=1),
    page_size:      int           = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List all user appeals with optional filters by status, target type, attempt number, and submitter email."""
    try:
        if status not in ("pending", "approved", "rejected", "all"):
            return ResponseSchema.error("status must be pending, approved, rejected, or all", 400)
        if target_type and target_type not in ("user", "job_post"):
            return ResponseSchema.error("target_type must be 'user' or 'job_post'", 400)
        items = list_appeals(
            status=status,
            target_type=target_type,
            appeal_attempt=appeal_attempt,
            search=search,
            page=page,
            page_size=page_size,
        )
        logger("ADMIN", f"Appeals fetched: status={status} target_type={target_type} attempt={appeal_attempt} page={page}", "GET /admin/appeals", "INFO")
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Appeals list error: {e}", "GET /admin/appeals", "ERROR")
        return ResponseSchema.error(f"Failed to fetch appeals: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.get("/appeals/{appeal_id}")
async def admin_get_appeal(
    appeal_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Fetch a single appeal by ID."""
    try:
        item = get_appeal(appeal_id)
        if not item:
            return ResponseSchema.error("Appeal not found", 404)
        logger("ADMIN", f"Appeal {appeal_id} fetched by {current_user.user_id}", "GET /admin/appeals/{appeal_id}", "INFO")
        return ResponseSchema.success(item, 200)
    except Exception as e:
        logger("ADMIN", f"Get appeal error: {e}", "GET /admin/appeals/{appeal_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch appeal: {e}", 500)


@admin_router.post("/appeals/{appeal_id}/approve")
async def approve_appeal(
    appeal_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Approve an appeal: restores the job post or removes the ban."""
    try:
        updated = resolve_appeal(
            appeal_id=appeal_id,
            action="approve",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Appeal not found or already resolved", 404)
        logger("ADMIN", f"Appeal {appeal_id} approved by {current_user.user_id}", "POST /admin/appeals/approve", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Approve appeal error: {e}", "POST /admin/appeals/approve", "ERROR")
        return ResponseSchema.error(f"Failed to approve appeal: {e}", 500)


@admin_router.post("/appeals/{appeal_id}/reject")
async def reject_appeal(
    appeal_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Reject an appeal: closure/ban remains in effect."""
    try:
        updated = resolve_appeal(
            appeal_id=appeal_id,
            action="reject",
            admin_user_id=current_user.user_id,
            admin_note=body.admin_note,
        )
        if not updated:
            return ResponseSchema.error("Appeal not found or already resolved", 404)
        logger("ADMIN", f"Appeal {appeal_id} rejected by {current_user.user_id}", "POST /admin/appeals/reject", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Reject appeal error: {e}", "POST /admin/appeals/reject", "ERROR")
        return ResponseSchema.error(f"Failed to reject appeal: {e}", 500)


@admin_router.post("/jobs/{job_post_id}/close")
async def force_close_job(
    job_post_id: str,
    body: AdminOverrideBody = AdminOverrideBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Force-close any job post regardless of report or AI flag status."""
    try:
        updated = admin_close_job(
            job_post_id=job_post_id,
            admin_user_id=current_user.user_id,
            reason=body.reason,
        )
        if not updated:
            return ResponseSchema.error("Job post not found", 404)
        logger("ADMIN", f"Job {job_post_id} force-closed by {current_user.user_id}", "POST /admin/jobs/close", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Force close job error: {e}", "POST /admin/jobs/close", "ERROR")
        return ResponseSchema.error(f"Failed to close job post: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.post("/jobs/{job_post_id}/reopen")
async def force_reopen_job(
    job_post_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Reopen a closed job post directly, without requiring a user appeal."""
    try:
        updated = admin_reopen_job(
            job_post_id=job_post_id,
            admin_user_id=current_user.user_id,
        )
        if not updated:
            return ResponseSchema.error("Job post not found or is not currently closed", 404)
        logger("ADMIN", f"Job {job_post_id} reopened by {current_user.user_id}", "POST /admin/jobs/reopen", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Force reopen job error: {e}", "POST /admin/jobs/reopen", "ERROR")
        return ResponseSchema.error(f"Failed to reopen job post: {e}", 500)


@admin_router.post("/accounts/{user_id}/close")
async def force_close_account(
    user_id: str,
    body: AdminOverrideBody = AdminOverrideBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Restrict any user account regardless of report or AI flag status."""
    try:
        updated = admin_close_account(
            user_id=user_id,
            admin_user_id=current_user.user_id,
            reason=body.reason,
        )
        if not updated:
            return ResponseSchema.error("User not found", 404)
        logger("ADMIN", f"Account {user_id} force-closed by {current_user.user_id}", "POST /admin/accounts/close", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Force close account error: {e}", "POST /admin/accounts/close", "ERROR")
        return ResponseSchema.error(f"Failed to close account: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.post("/accounts/{user_id}/reopen")
async def force_reopen_account(
    user_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Restore a restricted user account directly, without requiring a user appeal."""
    try:
        updated = admin_reopen_account(
            user_id=user_id,
            admin_user_id=current_user.user_id,
        )
        if not updated:
            return ResponseSchema.error("User not found or account is not currently restricted", 404)
        logger("ADMIN", f"Account {user_id} restored by {current_user.user_id}", "POST /admin/accounts/reopen", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Force reopen account error: {e}", "POST /admin/accounts/reopen", "ERROR")
        return ResponseSchema.error(f"Failed to restore account: {e}", 500)


@admin_router.get("/jobs")
async def admin_browse_jobs(
    status:                   Optional[str]  = Query(None, description="Include statuses (comma-sep): draft,active,closed,filled"),
    exclude_status:           Optional[str]  = Query(None, description="Exclude statuses (comma-sep)"),
    closure_reason:           Optional[str]  = Query(None, description="Include closure reasons (comma-sep): scam,content_violation,admin_override,community_reports"),
    exclude_closure_reason:   Optional[str]  = Query(None, description="Exclude closure reasons (comma-sep)"),
    project_type:             Optional[str]  = Query(None, description="Include project types (comma-sep): individual,team"),
    exclude_project_type:     Optional[str]  = Query(None, description="Exclude project types (comma-sep)"),
    project_scope:            Optional[str]  = Query(None, description="Include scopes (comma-sep): small,medium,large"),
    exclude_project_scope:    Optional[str]  = Query(None, description="Exclude scopes (comma-sep)"),
    experience_level:         Optional[str]  = Query(None, description="Include experience levels (comma-sep): entry,intermediate,expert"),
    exclude_experience_level: Optional[str]  = Query(None, description="Exclude experience levels (comma-sep)"),
    project_category:         Optional[str]  = Query(None, description="Partial match on project_category"),
    is_ai_generated:          Optional[bool] = Query(None, description="Filter by AI-generated flag"),
    client_id:                Optional[str]  = Query(None, description="Filter by specific client UUID"),
    search:                   Optional[str]  = Query(None, description="Partial match on job_title"),
    created_from:             Optional[str]  = Query(None, description="ISO date, created_at >="),
    created_to:               Optional[str]  = Query(None, description="ISO date, created_at <="),
    closed_from:              Optional[str]  = Query(None, description="ISO date, closed_at >="),
    closed_to:                Optional[str]  = Query(None, description="ISO date, closed_at <="),
    sort_by:   str = Query(default="created_at", description="created_at | closed_at | updated_at | job_title | status | proposal_count | view_count"),
    sort_dir:  str = Query(default="desc",        description="asc | desc"),
    page:      int = Query(default=1,  ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Browse all job posts with flexible include/exclude filters and sorting."""
    try:
        if sort_by not in ("created_at", "closed_at", "updated_at", "job_title", "status", "proposal_count", "view_count"):
            return ResponseSchema.error("Invalid sort_by value", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        result = admin_list_jobs(
            status=status,                       exclude_status=exclude_status,
            closure_reason=closure_reason,       exclude_closure_reason=exclude_closure_reason,
            project_type=project_type,           exclude_project_type=exclude_project_type,
            project_scope=project_scope,         exclude_project_scope=exclude_project_scope,
            experience_level=experience_level,   exclude_experience_level=exclude_experience_level,
            project_category=project_category,   is_ai_generated=is_ai_generated,
            client_id=client_id,                 search=search,
            created_from=created_from,           created_to=created_to,
            closed_from=closed_from,             closed_to=closed_to,
            sort_by=sort_by,                     sort_dir=sort_dir,
            page=page,                           page_size=page_size,
        )
        logger("ADMIN", f"Jobs browse: page={page} status={status!r} search={search!r}", "GET /admin/jobs", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("ADMIN", f"Jobs browse error: {e}", "GET /admin/jobs", "ERROR")
        return ResponseSchema.error(f"Failed to list jobs: {e}", 500)


@admin_router.get("/users")
async def admin_browse_users(
    role:                 Optional[str]  = Query(None, description="Include roles (comma-sep): freelancer,client,admin"),
    exclude_role:         Optional[str]  = Query(None, description="Exclude roles (comma-sep)"),
    is_banned:            Optional[bool] = Query(None, description="Filter by report-ban status"),
    email_verified:       Optional[bool] = Query(None, description="Filter by email verification status"),
    ban_reason:           Optional[str]  = Query(None, description="Include ban reasons (comma-sep): admin_override,community_reports"),
    exclude_ban_reason:   Optional[str]  = Query(None, description="Exclude ban reasons (comma-sep)"),
    search:               Optional[str]  = Query(None, description="Partial match on email or display name"),
    created_from:         Optional[str]  = Query(None, description="ISO date, created_at >="),
    created_to:           Optional[str]  = Query(None, description="ISO date, created_at <="),
    banned_from:          Optional[str]  = Query(None, description="ISO date, report_banned_at >="),
    banned_to:            Optional[str]  = Query(None, description="ISO date, report_banned_at <="),
    sort_by:   str = Query(default="created_at",  description="created_at | updated_at | email | report_banned_at | ban_reason"),
    sort_dir:  str = Query(default="desc",         description="asc | desc"),
    page:      int = Query(default=1,  ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Browse all user accounts with flexible include/exclude filters and sorting."""
    try:
        if sort_by not in ("created_at", "updated_at", "email", "report_banned_at", "ban_reason"):
            return ResponseSchema.error("Invalid sort_by value", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        result = admin_list_users(
            role=role,               exclude_role=exclude_role,
            is_banned=is_banned,     email_verified=email_verified,
            ban_reason=ban_reason,   exclude_ban_reason=exclude_ban_reason,
            search=search,
            created_from=created_from,  created_to=created_to,
            banned_from=banned_from,    banned_to=banned_to,
            sort_by=sort_by,            sort_dir=sort_dir,
            page=page,                  page_size=page_size,
        )
        logger("ADMIN", f"Users browse: page={page} role={role!r} is_banned={is_banned} search={search!r}", "GET /admin/users", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("ADMIN", f"Users browse error: {e}", "GET /admin/users", "ERROR")
        return ResponseSchema.error(f"Failed to list users: {e}", 500)


# dev/admin only - not called by the Flutter app
@admin_router.get("/users/{user_id}")
async def admin_get_user(
    user_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Fetch full details for a single user account."""
    try:
        item = get_admin_user_detail(user_id)
        if not item:
            return ResponseSchema.error("User not found", 404)
        logger("ADMIN", f"User {user_id} fetched by {current_user.user_id}", "GET /admin/users/{user_id}", "INFO")
        return ResponseSchema.success(item, 200)
    except Exception as e:
        logger("ADMIN", f"Get user detail error: {e}", "GET /admin/users/{user_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch user: {e}", 500)


@admin_router.get("/contracts/disputed")
async def admin_list_disputed_contracts(
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    current_user: UserInDB = Depends(get_admin_user),
):
    """
    List contracts currently in 'disputed' status, for admin arbitration.

    raise_dispute() never got a dedicated reason/raised_at column (see
    ContractFunctions.raise_dispute's docstring) - the reason only exists as a
    DM system-event message on the contract's thread. This pulls the most
    recent 'dispute_raised' event per contract (DISTINCT ON, in case a
    contract has been disputed more than once) so the admin UI can show the
    reason without a separate round trip per card.
    """
    try:
        offset = (page - 1) * page_size
        where = ["c.status = 'disputed'"]
        params: Dict = {}
        if search:
            where.append(
                "(c.contract_title ILIKE :search OR cl_u.email ILIKE :search "
                "OR fl_u.email ILIKE :search OR cl.full_name ILIKE :search "
                "OR fl.full_name ILIKE :search)"
            )
            params["search"] = f"%{search}%"
        where_sql = "WHERE " + " AND ".join(where)

        rows = get_db().execute_query(
            f"""
            WITH latest_dispute AS (
                SELECT DISTINCT ON (dt.contract_id)
                    dt.contract_id, dm.message_text, dm.metadata, dm.sent_at
                FROM dm_message dm
                JOIN dm_thread dt ON dt.thread_id = dm.thread_id
                WHERE dm.metadata->>'type' = 'dispute_raised'
                ORDER BY dt.contract_id, dm.sent_at DESC
            )
            SELECT
                c.contract_id, c.contract_title, c.agreed_budget, c.budget_currency,
                c.client_id, c.freelancer_id,
                cl.full_name AS client_name, cl_u.email AS client_email,
                fl.full_name AS freelancer_name, fl_u.email AS freelancer_email,
                ld.metadata->>'reason' AS dispute_reason,
                ld.sent_at AS dispute_raised_at
            FROM contract c
            LEFT JOIN client     cl   ON cl.client_id     = c.client_id
            LEFT JOIN users      cl_u ON cl_u.user_id      = cl.user_id
            LEFT JOIN freelancer fl   ON fl.freelancer_id  = c.freelancer_id
            LEFT JOIN users      fl_u ON fl_u.user_id      = fl.user_id
            LEFT JOIN latest_dispute ld ON ld.contract_id  = c.contract_id
            {where_sql}
            ORDER BY ld.sent_at DESC NULLS LAST, c.updated_at DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": page_size, "offset": offset},
        )

        total_row = get_db().execute_query(
            f"""
            SELECT COUNT(*) AS cnt
            FROM contract c
            LEFT JOIN client     cl   ON cl.client_id     = c.client_id
            LEFT JOIN users      cl_u ON cl_u.user_id      = cl.user_id
            LEFT JOIN freelancer fl   ON fl.freelancer_id  = c.freelancer_id
            LEFT JOIN users      fl_u ON fl_u.user_id      = fl.user_id
            {where_sql}
            """,
            params,
        )
        total = int(total_row[0]["cnt"]) if total_row else 0

        items = [dict(row) for row in rows or []]
        result = {
            "items": items,
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
            },
        }
        logger("ADMIN", f"Retrieved {len(items)} disputed contracts (page {page})", "GET /admin/contracts/disputed", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("ADMIN", f"Failed to list disputed contracts: {e}", "GET /admin/contracts/disputed", "ERROR")
        return ResponseSchema.error(f"Failed to list disputed contracts: {e}", 500)


@admin_router.put("/contracts/{contract_id}/arbitrate")
async def admin_arbitrate_contract_dispute(
    contract_id: str,
    payload: ArbitrateDisputeRequest,
    current_user: UserInDB = Depends(get_admin_user),
):
    """
    Resolve a disputed contract (status must be 'disputed', set via
    PUT /contracts/{contract_id}/dispute). Three outcomes:
      - approve: force-complete, reusing the same completion path as a manual approve.
      - cancel:  force-cancel, reusing the same path as a manual cancel.
      - revise:  send back for another revision round with a new deadline.
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if contract["status"] != "disputed":
            return ResponseSchema.error(
                f"Cannot arbitrate a contract with status '{contract['status']}' - must be 'disputed'", 400,
            )

        if payload.outcome == "revise" and not payload.new_deadline:
            return ResponseSchema.error("new_deadline is required when outcome is 'revise'", 400)

        updated_contract = ContractFunctions.arbitrate_dispute(
            contract_id=contract_id,
            outcome=payload.outcome,
            admin_user_id=str(current_user.user_id),
            note=payload.note,
            new_deadline=payload.new_deadline,
        )

        try:
            from routes.freelancers.freelancer_functions import FreelancerFunctions
            from routes.notifications.notification_functions import NotificationFunctions

            fl = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            cl = ClientFunctions.get_client_by_id(str(contract["client_id"]))
            body = f"Admin resolved the dispute on \"{contract.get('contract_title')}\": {payload.outcome}."
            for party in (fl, cl):
                if party:
                    await NotificationFunctions.notify(
                        recipient_user_id=str(party["user_id"]),
                        notif_type="dispute_resolved",
                        title="Dispute Resolved",
                        body=body,
                        data={"contract_id": contract_id, "outcome": payload.outcome},
                    )
        except Exception as notif_err:
            logger("ADMIN", f"Dispute-resolved notification failed (non-fatal): {notif_err}", "PUT /admin/contracts/{contract_id}/arbitrate", "WARNING")

        logger("ADMIN", f"Contract {contract_id} dispute arbitrated by admin {current_user.user_id}: {payload.outcome}", "PUT /admin/contracts/{contract_id}/arbitrate", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except ValueError as e:
        logger("ADMIN", f"Validation error: {e}", "PUT /admin/contracts/{contract_id}/arbitrate", "WARNING")
        return ResponseSchema.error(str(e), 400)
    except Exception as e:
        logger("ADMIN", f"Failed to arbitrate dispute for contract {contract_id}: {e}", "PUT /admin/contracts/{contract_id}/arbitrate", "ERROR")
        return ResponseSchema.error(f"Failed to arbitrate dispute: {e}", 500)


@admin_router.get("/clients/{client_id}/autoapprove-history")
async def admin_get_client_autoapprove_history(
    client_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """
    Read-only audit trail for the 3-strike auto-approve penalty (monitoring only -
    no action taken here; the ban itself already happens automatically at strike 3
    without any admin input). Meant for reviewing a client's pattern before deciding
    an appeal: which contracts triggered a strike, when, and the account's current
    ban/reliability state - all derived from existing data, nothing new stored.
    """
    try:
        client = ClientFunctions.get_client_by_id_or_user_id(client_id)
        if not client:
            return ResponseSchema.error(f"Client {client_id} not found", 404)

        user_id = str(client["user_id"])
        user_detail = get_admin_user_detail(user_id) or {}
        history = ContractFunctions.get_client_autoapprove_history(user_id)

        result = {
            "client_id": str(client["client_id"]),
            "email": user_detail.get("email"),
            "strike_count": len(history),
            "reliability_label": ContractFunctions.get_client_reliability_label(user_id),
            "is_banned": user_detail.get("is_report_banned", False),
            "ban_reason": user_detail.get("ban_reason"),
            "banned_at": user_detail.get("report_banned_at"),
            "history": history,
        }
        logger("ADMIN", f"Retrieved autoapprove history for client {client_id}", "GET /admin/clients/{client_id}/autoapprove-history", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("ADMIN", f"Failed to fetch autoapprove history for client {client_id}: {e}", "GET /admin/clients/{client_id}/autoapprove-history", "ERROR")
        return ResponseSchema.error(f"Failed to fetch autoapprove history: {e}", 500)


@reports_router.get("/reasons")
async def get_report_reasons(current_user: UserInDB = Depends(get_current_user)):
    """Return the list of predefined report reasons."""
    return ResponseSchema.success({"reasons": VALID_REPORT_REASONS}, 200)


@appeals_router.get("/status")
async def user_get_appeal_status(
    target_type: str,
    target_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Check whether the current user can appeal a specific ban or job-post closure.

    Args:
        target_type: 'user' or 'job_post'.
        target_id: UUID of the banned user or closed job post.

    Returns:
        can_appeal, appeals_remaining, state, message, and restriction_reason.
    """
    try:
        if target_type not in ("user", "job_post"):
            return ResponseSchema.error("target_type must be 'user' or 'job_post'", 400)
        result = get_appeal_status(current_user.user_id, target_type, target_id)
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("APPEAL", f"Appeal status check error: {e}", "GET /appeals/status", "ERROR")
        return ResponseSchema.error(f"Failed to check appeal status: {e}", 500)


@appeals_router.post("")
async def user_submit_appeal(
    body: AppealSubmitBody,
    current_user: UserInDB = Depends(get_current_user),
):
    """Submit an appeal against a job-post closure or account restriction.

    Args:
        body.target_type: 'user' (account ban) or 'job_post' (post closure).
        body.target_id: UUID of the target being appealed.
        body.message: Appeal message text.
    """
    try:
        if body.target_type not in ("user", "job_post"):
            return ResponseSchema.error("target_type must be 'user' or 'job_post'", 400)
        if not body.message.strip():
            return ResponseSchema.error("Appeal message cannot be empty", 400)
        appeal = submit_appeal(
            user_id=current_user.user_id,
            target_type=body.target_type,
            target_id=body.target_id,
            message=body.message,
        )
        if not appeal:
            return ResponseSchema.error("Failed to submit appeal", 500)
        logger("APPEAL", f"User {current_user.user_id} appealed {body.target_type} {body.target_id}", "POST /appeals", "INFO")
        return ResponseSchema.success({"message": "Appeal submitted successfully", "appeal_id": str(appeal["appeal_id"])}, 201)
    except HTTPException as e:
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("APPEAL", f"Submit appeal error: {e}", "POST /appeals", "ERROR")
        return ResponseSchema.error(f"Failed to submit appeal: {e}", 500)


@appeals_router.get("/mine")
async def user_list_appeals(current_user: UserInDB = Depends(get_current_user)):
    """Return the current user's appeals and their statuses."""
    try:
        items = get_user_appeals(current_user.user_id)
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("APPEAL", f"List appeals error: {e}", "GET /appeals/mine", "ERROR")
        return ResponseSchema.error(f"Failed to fetch appeals: {e}", 500)


@reports_router.post("")
async def submit_report(
    body: ReportCreateBody,
    current_user: UserInDB = Depends(get_current_user),
):
    """Submit a report against a freelancer, client profile, or job post.

    At least one reason (predefined or custom) is required.
    """
    try:
        if body.reported_type not in ("freelancer", "client", "job_post"):
            return ResponseSchema.error("reported_type must be 'freelancer', 'client', or 'job_post'", 400)

        if body.reported_type in ("freelancer", "client"):
            if not body.reported_user_id:
                return ResponseSchema.error("reported_user_id is required for freelancer/client reports", 400)
            if body.reported_user_id == current_user.user_id:
                return ResponseSchema.error("You cannot report yourself", 400)

        if body.reported_type == "job_post":
            if not body.job_post_id:
                return ResponseSchema.error("job_post_id is required for job post reports", 400)

        invalid = [r for r in body.reasons if r not in VALID_REPORT_REASONS]
        if invalid:
            return ResponseSchema.error(
                f"Invalid reasons: {invalid}. Valid: {VALID_REPORT_REASONS}", 400
            )

        if not body.reasons and not body.custom_reason:
            return ResponseSchema.error("Provide at least one reason or a custom_reason", 400)

        report = create_report(
            reporter_id=current_user.user_id,
            reported_type=body.reported_type,
            reasons=body.reasons,
            custom_reason=body.custom_reason,
            reported_user_id=body.reported_user_id,
            job_post_id=body.job_post_id,
        )
        if not report:
            return ResponseSchema.error("Failed to submit report", 500)

        target = body.reported_user_id or body.job_post_id
        logger("REPORT", f"User {current_user.user_id} reported {body.reported_type} {target}", "POST /reports", "INFO")
        return ResponseSchema.success({"message": "Report submitted successfully", "report_id": str(report["report_id"])}, 201)
    except Exception as e:
        logger("REPORT", f"Submit report error: {e}", "POST /reports", "ERROR")
        return ResponseSchema.error(f"Failed to submit report: {e}", 500)

@admin_router.get("/reviews/red-flags")
async def list_review_red_flags(
    is_resolved:  Optional[bool] = Query(default=None, description="filter by resolution status; omit for both"),
    subject_type: str = Query(default="all", description="freelancer | client | all"),
    sort_by:      str = Query(default="triggered_at", description="triggered_at | severity"),
    sort_dir:     str = Query(default="desc",         description="asc | desc"),
    page:         int = Query(default=1, ge=1),
    page_size:    int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Admin-wide red flag alert listing (trust score drops), across freelancers and/or clients."""
    try:
        if subject_type not in ("freelancer", "client", "all"):
            return ResponseSchema.error("subject_type must be freelancer, client, or all", 400)
        if sort_by not in ("triggered_at", "severity"):
            return ResponseSchema.error("sort_by must be triggered_at or severity", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        flags = list_red_flag_alerts(is_resolved=is_resolved, subject_type=subject_type, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
        logger("ADMIN", f"Review red flags fetched: is_resolved={is_resolved} subject_type={subject_type} sort={sort_by} {sort_dir}", "GET /admin/reviews/red-flags", "INFO")
        return ResponseSchema.success(flags, 200)
    except Exception as e:
        logger("ADMIN", f"Review red flags list error: {e}", "GET /admin/reviews/red-flags", "ERROR")
        return ResponseSchema.error(f"Failed to fetch red flags: {e}", 500)


@admin_router.post("/reviews/red-flags/{alert_id}/resolve")
async def resolve_review_red_flag(
    alert_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Mark a red flag alert as resolved."""
    try:
        updated = resolve_red_flag_alert(alert_id=alert_id, admin_user_id=current_user.user_id)
        if not updated:
            return ResponseSchema.error("Alert not found or already resolved", 404)
        logger("ADMIN", f"Red flag {alert_id} resolved by {current_user.user_id}", "POST /admin/reviews/red-flags/resolve", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Resolve red flag error: {e}", "POST /admin/reviews/red-flags/resolve", "ERROR")
        return ResponseSchema.error(f"Failed to resolve red flag: {e}", 500)


@admin_router.get("/reviews/flagged")
async def list_review_flagged(
    status:    str = Query(default="all",        description="flagged | suppressed | all"),
    sort_by:   str = Query(default="created_at", description="created_at | status"),
    sort_dir:  str = Query(default="desc",       description="asc | desc"),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List reviews held back from publishing (overall_pass=false), with the AI analysis that caused the hold."""
    try:
        if status not in ("flagged", "suppressed", "all"):
            return ResponseSchema.error("status must be flagged, suppressed, or all", 400)
        if sort_by not in ("created_at", "status"):
            return ResponseSchema.error("sort_by must be created_at or status", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        reviews = list_flagged_reviews(status=status, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
        logger("ADMIN", f"Flagged reviews fetched: status={status} sort={sort_by} {sort_dir}", "GET /admin/reviews/flagged", "INFO")
        return ResponseSchema.success(reviews, 200)
    except Exception as e:
        logger("ADMIN", f"Flagged reviews list error: {e}", "GET /admin/reviews/flagged", "ERROR")
        return ResponseSchema.error(f"Failed to fetch flagged reviews: {e}", 500)


@admin_router.post("/reviews/{review_id}/override-publish")
async def override_publish_review_route(
    review_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Manually publish a held-back (flagged/suppressed) review after human review."""
    try:
        updated = await override_publish_review(review_id=review_id, admin_user_id=current_user.user_id)
        if not updated:
            return ResponseSchema.error("Review not found or not currently flagged/suppressed", 404)
        logger("ADMIN", f"Review {review_id} override-published by {current_user.user_id}", "POST /admin/reviews/override-publish", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Override publish review error: {e}", "POST /admin/reviews/override-publish", "ERROR")
        return ResponseSchema.error(f"Failed to override-publish review: {e}", 500)


@admin_router.get("/client-reviews/flagged")
async def list_client_review_flagged(
    status:    str = Query(default="all",        description="flagged | suppressed | all"),
    sort_by:   str = Query(default="created_at", description="created_at | status"),
    sort_dir:  str = Query(default="desc",       description="asc | desc"),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List client reviews (written by freelancers) held back from publishing."""
    try:
        if status not in ("flagged", "suppressed", "all"):
            return ResponseSchema.error("status must be flagged, suppressed, or all", 400)
        if sort_by not in ("created_at", "status"):
            return ResponseSchema.error("sort_by must be created_at or status", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        reviews = list_flagged_client_reviews(status=status, sort_by=sort_by, sort_dir=sort_dir, page=page, page_size=page_size)
        logger("ADMIN", f"Flagged client reviews fetched: status={status} sort={sort_by} {sort_dir}", "GET /admin/client-reviews/flagged", "INFO")
        return ResponseSchema.success(reviews, 200)
    except Exception as e:
        logger("ADMIN", f"Flagged client reviews list error: {e}", "GET /admin/client-reviews/flagged", "ERROR")
        return ResponseSchema.error(f"Failed to fetch flagged client reviews: {e}", 500)


@admin_router.post("/client-reviews/{client_review_id}/override-publish")
async def override_publish_client_review_route(
    client_review_id: str,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Manually publish a held-back client review after human review."""
    try:
        updated = await override_publish_client_review(client_review_id=client_review_id, admin_user_id=current_user.user_id)
        if not updated:
            return ResponseSchema.error("Client review not found or not currently flagged/suppressed", 404)
        logger("ADMIN", f"Client review {client_review_id} override-published by {current_user.user_id}", "POST /admin/client-reviews/override-publish", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("ADMIN", f"Override publish client review error: {e}", "POST /admin/client-reviews/override-publish", "ERROR")
        return ResponseSchema.error(f"Failed to override-publish client review: {e}", 500)

