"""
Admin API routes.

admin_router  — /admin/...  (requires is_admin)
reports_router — /reports/...  (any authenticated user)
"""
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from pydantic import BaseModel

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_admin_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.admin.admin_functions import (
    VALID_REPORT_REASONS,
    action_moderation_item,
    action_report,
    action_scam_flag,
    admin_close_account,
    admin_close_job,
    admin_reopen_account,
    admin_reopen_job,
    create_report,
    force_expire_moderation,
    force_expire_reports,
    force_expire_scam_flags,
    get_admin_dashboard_stats,
    get_client_scam_record,
    get_user_appeals,
    list_appeals,
    list_moderation_queue,
    list_report_auto_actions,
    list_report_targets,
    list_reports,
    list_scam_flags,
    queue_toxicity_scan,
    queue_scam_scan,
    resolve_appeal,
    submit_appeal,
)

admin_router   = APIRouter(prefix="/admin",   tags=["Admin"])
reports_router = APIRouter(prefix="/reports", tags=["Reports"])
appeals_router = APIRouter(prefix="/appeals", tags=["Appeals"])


# ─── request bodies ───────────────────────────────────────────────────────────

class AdminActionBody(BaseModel):
    admin_note: Optional[str] = None


class ContentScanBody(BaseModel):
    content_type: str   # 'job_post' | 'freelancer_profile' | 'client_profile'
    content_id:   str
    user_id:      str
    text:         str


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


# ─── dashboard ────────────────────────────────────────────────────────────────

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


# ─── toxicity detection ───────────────────────────────────────────────────────

@admin_router.get("/moderation")
async def list_moderation(
    status:       str = Query(default="pending",     description="pending | approved | rejected | all"),
    content_type: str = Query(default="all",         description="job_post | freelancer_profile | client_profile | all"),
    sort_by:      str = Query(default="created_at",  description="created_at | total_score | content_type | status"),
    sort_dir:     str = Query(default="desc",        description="asc | desc"),
    page:         int = Query(default=1, ge=1),
    page_size:    int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List toxicity detection queue items. Supports filtering by status and content_type, and sorting."""
    try:
        if status not in ("pending", "approved", "rejected", "all"):
            return ResponseSchema.error("status must be pending, approved, rejected, or all", 400)
        if content_type not in ("job_post", "freelancer_profile", "client_profile", "all"):
            return ResponseSchema.error("content_type must be job_post, freelancer_profile, client_profile, or all", 400)
        if sort_by not in ("created_at", "total_score", "content_type", "status"):
            return ResponseSchema.error("sort_by must be created_at, total_score, content_type, or status", 400)
        if sort_dir not in ("asc", "desc"):
            return ResponseSchema.error("sort_dir must be asc or desc", 400)
        items = list_moderation_queue(
            status=status, content_type=content_type,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
        logger("ADMIN", f"Moderation queue fetched: status={status} content_type={content_type} sort={sort_by} {sort_dir}", "GET /admin/moderation", "INFO")
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
    """Approve a flagged content item (content is allowed to stay)."""
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
    """Reject a flagged content item. Job posts are closed; profile flags are recorded."""
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


@admin_router.post("/moderation/scan")
async def trigger_content_scan(
    body: ContentScanBody,
    current_user: UserInDB = Depends(get_admin_user),
):
    """Manually trigger a toxicity detection scan (admin utility)."""
    try:
        if body.content_type not in ("job_post", "freelancer_profile", "client_profile"):
            return ResponseSchema.error("content_type must be job_post, freelancer_profile, or client_profile", 400)
        result = queue_toxicity_scan(
            content_type=body.content_type,
            content_id=body.content_id,
            user_id=body.user_id,
            text=body.text,
        )
        if result is None:
            return ResponseSchema.success({"flagged": False, "message": "Content passed moderation"}, 200)
        return ResponseSchema.success({"flagged": True, "moderation_record": result}, 200)
    except Exception as e:
        logger("ADMIN", f"Content scan error: {e}", "POST /admin/moderation/scan", "ERROR")
        return ResponseSchema.error(f"Content scan failed: {e}", 500)


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


# ─── scam detection ───────────────────────────────────────────────────────────

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


# ─── reports (admin view) ─────────────────────────────────────────────────────

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


# ─── appeals (admin view) ─────────────────────────────────────────────────────

@admin_router.get("/appeals")
async def admin_list_appeals(
    status:    str = Query(default="pending", description="pending | approved | rejected | all"),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_admin_user),
):
    """List all user appeals."""
    try:
        if status not in ("pending", "approved", "rejected", "all"):
            return ResponseSchema.error("status must be pending, approved, rejected, or all", 400)
        items = list_appeals(status=status, page=page, page_size=page_size)
        logger("ADMIN", f"Appeals fetched: status={status} page={page}", "GET /admin/appeals", "INFO")
        return ResponseSchema.success(items, 200)
    except Exception as e:
        logger("ADMIN", f"Appeals list error: {e}", "GET /admin/appeals", "ERROR")
        return ResponseSchema.error(f"Failed to fetch appeals: {e}", 500)


@admin_router.post("/appeals/{appeal_id}/approve")
async def approve_appeal(
    appeal_id: str,
    body: AdminActionBody = AdminActionBody(),
    current_user: UserInDB = Depends(get_admin_user),
):
    """Approve an appeal — restores the job post or removes the ban."""
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
    """Reject an appeal — closure/ban remains in effect."""
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


# ─── direct admin overrides (no report / no AI flag required) ────────────────

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


# ─── reports (user-facing) ────────────────────────────────────────────────────

@reports_router.get("/reasons")
async def get_report_reasons(current_user: UserInDB = Depends(get_current_user)):
    """Return the list of predefined report reasons."""
    return ResponseSchema.success({"reasons": VALID_REPORT_REASONS}, 200)


@appeals_router.post("")
async def user_submit_appeal(
    body: AppealSubmitBody,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Submit an appeal against a job-post closure or account restriction.
    target_type: 'user' (account ban) | 'job_post' (post closure).
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


# ─── reports (user-facing) ────────────────────────────────────────────────────

@reports_router.post("")
async def submit_report(
    body: ReportCreateBody,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Submit a report against a freelancer or client profile.
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
