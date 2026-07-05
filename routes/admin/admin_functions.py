import asyncio
import json
import math
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from fastapi import HTTPException

from functions.db_manager import get_db
from functions.logger import logger
from routes.admin.admin_moderation import (
    scan_harmful_text,
    scan_for_scam,
    scan_for_scam_with_ml_fallback,
    scan_harmful_text_with_ml_fallback,
    insert_harmful_text_queue_entry,
    SCAM_AUTO_REMOVE_THRESHOLD,
)
from routes.proposals.proposal_functions import ProposalFunctions
from routes.notifications.notification_functions import NotificationFunctions

AUTO_REMOVE_DAYS  = 30

# total_score = sum of all 5 label scores (each 0-1, max 5.0)
CONTENT_AUTO_CLOSE_THRESHOLD_PROFILE = 0.90
CONTENT_AUTO_CLOSE_THRESHOLD_JOB     = 0.85

REPORT_AUTO_ACTION_THRESHOLD = 10   # min reports to trigger auto-action
REPORT_AUTO_ACTION_DAYS      = 30   # min age (days) of oldest report

SCAM_SOFT_FLAG_THRESHOLD = 0.25  # suspicious but not auto-closed; goes to admin queue for manual review

# Default closure / ban messages (admin can override via admin_note / ban_message)
DEFAULT_CLOSURE_REASON_CONTENT = "content_violation"
DEFAULT_CLOSURE_NOTE_CONTENT   = (
    "This job post was removed due to a content policy violation. "
    "Submit an appeal if you believe this was a mistake."
)
DEFAULT_CLOSURE_REASON_SCAM    = "scam"
DEFAULT_CLOSURE_NOTE_SCAM      = (
    "This job post was removed due to suspected fraudulent activity. "
    "Submit an appeal if you believe this was a mistake."
)
DEFAULT_CLOSURE_REASON_REPORTS = "community_reports"
DEFAULT_CLOSURE_NOTE_REPORTS   = (
    "This item was removed after receiving multiple community reports. "
    "Submit an appeal if you believe this was a mistake."
)
DEFAULT_BAN_REASON_REPORTS     = "community_reports"
DEFAULT_BAN_MESSAGE_REPORTS    = (
    "Your account has been restricted due to multiple community reports. "
    "Submit an appeal if you believe this was a mistake."
)
DEFAULT_CLOSURE_REASON_ADMIN   = "admin_override"
DEFAULT_CLOSURE_NOTE_ADMIN     = (
    "This job post was closed by an administrator. "
    "Submit an appeal if you believe this was a mistake."
)
DEFAULT_BAN_REASON_ADMIN       = "admin_override"
DEFAULT_BAN_MESSAGE_ADMIN      = (
    "Your account has been restricted by an administrator. "
    "Submit an appeal if you believe this was a mistake."
)

# Sort-column whitelists (safe f-string interpolation; values are hardcoded)
_MOD_SORT_COLS = {
    "created_at":   "cmq.created_at",
    "total_score":  "(cmq.toxic_score + cmq.obscene_score + cmq.threat_score + cmq.insult_score + cmq.identity_hate_score)",
    "content_type": "cmq.content_type",
    "status":       "cmq.status",
}
_SCAM_SORT_COLS = {
    "created_at": "sf.created_at",
    "scam_score": "sf.scam_score",
}
_REPORT_SORT_COLS = {
    "created_at":    "ur.created_at",
    "reported_type": "ur.reported_type",
    "status":        "ur.status",
}
_REPORT_TARGET_SORT_COLS = {
    "report_count":  "report_count",
    "oldest_report": "oldest_report",
    "latest_report": "latest_report",
}

VALID_REPORT_REASONS = [
    "spam",
    "scam",
    "harassment",
    "inappropriate_content",
    "fake_profile",
    "impersonation",
    "other",
]


def _rows(result) -> List[Dict]:
    if not result:
        return []
    return [dict(r) for r in result]


def _row(result) -> Optional[Dict]:
    if not result:
        return None
    return dict(result[0])


def _fire_notification(coro) -> None:
    """schedule a notify() coroutine from sync code, whether this runs on the
    event loop thread (route handlers) or a plain worker thread (sweep loop)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(coro)
    else:
        asyncio.run(coro)


def _active_contracts_for_job_post(job_post_id: str) -> List[Dict]:
    """contracts still in progress for a job post - closing it would affect live, paid work."""
    return _rows(get_db().execute_query(
        """
        SELECT c.contract_id, f.user_id AS freelancer_user_id
        FROM contract c
        JOIN freelancer f ON f.freelancer_id = c.freelancer_id
        WHERE c.job_post_id = :jid AND c.status = 'active'
        """,
        params={"jid": job_post_id},
    ))


def _client_user_id_for_job_post(job_post_id: str) -> Optional[str]:
    row = _row(get_db().execute_query(
        """
        SELECT cl.user_id
        FROM job_post jp
        JOIN client cl ON cl.client_id = jp.client_id
        WHERE jp.job_post_id = :jid
        """,
        params={"jid": job_post_id},
    ))
    return str(row["user_id"]) if row else None


def queue_harmful_text_scan(
    content_type: str,
    content_id: str,
    user_id: str,
    text: str,
) -> Optional[Dict]:
    """
    Run harmful text scan and insert a pending moderation record if any label is triggered.
    content_type: 'job_post' | 'freelancer_profile' | 'client_profile'
    Returns the inserted row dict, or None if content is clean.
    """
    result = scan_harmful_text_with_ml_fallback(text)
    if not result["is_flagged"]:
        return None
    return insert_harmful_text_queue_entry(content_type, content_id, user_id, text, result)


def _auto_approve_expired():
    """
    Process pending moderation items whose 30-day window has closed.
    High cumulative label score → flag confirmed (status='approved') → auto-close job/ban user.
    Low score → flag dismissed as false positive (status='rejected').
    """
    expired = _rows(get_db().execute_query(
        """
        SELECT *,
               (toxic_score + obscene_score +
                threat_score + insult_score + identity_hate_score) AS total_score
        FROM harmful_text_queue
        WHERE status = 'pending' AND auto_approve_at <= NOW()
        """,
        params={},
    ))
    for item in expired:
        total      = float(item.get("total_score") or 0)
        ctype      = item.get("content_type", "")
        content_id = str(item.get("content_id", ""))
        user_id    = str(item.get("user_id", ""))
        mid        = str(item.get("moderation_id", ""))

        threshold = (
            CONTENT_AUTO_CLOSE_THRESHOLD_JOB
            if ctype == "job_post"
            else CONTENT_AUTO_CLOSE_THRESHOLD_PROFILE
        )

        if total >= threshold:
            new_status = "approved"  # flag confirmed: harmful content actioned
            if ctype == "job_post":
                active_contracts = _active_contracts_for_job_post(content_id)
                if active_contracts:
                    # a human has to decide what happens to live, paid work - don't auto-close under it
                    new_status = "held_active_contract"
                    logger(
                        "ADMIN",
                        f"Job post {content_id} flag confirmed (total_score={total:.2f} >= {threshold}) but NOT "
                        f"auto-closed: {len(active_contracts)} active contract(s) in progress, needs manual admin review",
                        level="WARNING",
                    )
                    client_user_id = _client_user_id_for_job_post(content_id)
                    if client_user_id:
                        _fire_notification(NotificationFunctions.notify(
                            recipient_user_id=client_user_id,
                            notif_type="job_post_flagged_held",
                            title="Job Post Under Review",
                            body="Your job post was flagged for a content policy violation. Since it has an active contract, an admin will review it manually instead of an automatic closure.",
                            data={"job_post_id": content_id},
                        ))
                    for contract in active_contracts:
                        _fire_notification(NotificationFunctions.notify(
                            recipient_user_id=str(contract["freelancer_user_id"]),
                            notif_type="job_post_flagged_held",
                            title="Job Post Under Review",
                            body="The job post tied to your active contract was flagged for a content policy violation and is under manual admin review. Your contract is not affected yet.",
                            data={"job_post_id": content_id, "contract_id": str(contract["contract_id"])},
                        ))
                else:
                    get_db().execute_query(
                        """
                        UPDATE job_post
                        SET status = 'closed',
                            closure_reason = :reason,
                            closure_note   = :note
                        WHERE job_post_id = :id
                        """,
                        params={
                            "id":     content_id,
                            "reason": DEFAULT_CLOSURE_REASON_CONTENT,
                            "note":   DEFAULT_CLOSURE_NOTE_CONTENT,
                        },
                    )
                    _fire_notification(ProposalFunctions.notify_proposal_owners_of_job_closure(
                        content_id, "flagged for a content policy violation"
                    ))
                    logger("ADMIN", f"Auto-closed {ctype} {content_id}, total_score={total:.2f} >= {threshold}", level="WARNING")
            else:
                logger("ADMIN", f"Auto-closed {ctype} {content_id}, total_score={total:.2f} >= {threshold}", level="WARNING")
        else:
            new_status = "rejected"  # flag dismissed: false positive
            logger("ADMIN", f"Auto-dismissed {ctype} {content_id}, total_score={total:.2f} < {threshold}", level="INFO")

        get_db().execute_query(
            """
            UPDATE harmful_text_queue
            SET status = :status, actioned_at = NOW()
            WHERE moderation_id = :mid
            """,
            params={"status": new_status, "mid": mid},
        )


def force_expire_moderation(moderation_ids: List[str]) -> None:
    """Backdate auto_approve_at for specific items then immediately run the sweep (testing utility)."""
    if not moderation_ids:
        return
    placeholders = ", ".join(f":id_{i}" for i in range(len(moderation_ids)))
    params = {f"id_{i}": mid for i, mid in enumerate(moderation_ids)}
    get_db().execute_query(
        f"""
        UPDATE harmful_text_queue
        SET auto_approve_at = NOW() - INTERVAL '1 minute'
        WHERE moderation_id IN ({placeholders}) AND status = 'pending'
        """,
        params=params,
    )
    _auto_approve_expired()


def force_expire_scam_flags(flag_ids: List[str]) -> None:
    """Backdate auto_remove_at for specific flags then immediately run the sweep (testing utility)."""
    if not flag_ids:
        return
    placeholders = ", ".join(f":id_{i}" for i in range(len(flag_ids)))
    params = {f"id_{i}": fid for i, fid in enumerate(flag_ids)}
    get_db().execute_query(
        f"""
        UPDATE scam_job_flags
        SET auto_remove_at = NOW() - INTERVAL '1 minute'
        WHERE flag_id IN ({placeholders}) AND status = 'pending'
        """,
        params=params,
    )
    _process_auto_remove()


def list_moderation_queue(
    status: str = "pending",
    content_type: str = "all",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    _auto_approve_expired()
    offset    = (page - 1) * page_size
    sort_col  = _MOD_SORT_COLS.get(sort_by, "cmq.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    # 'proposal' rows are an audit trail only (instant-blocked, no admin review step),
    # so the default 'all' view excludes them - an admin must ask for content_type='proposal'
    # explicitly to see them.
    return _rows(get_db().execute_query(
        f"""
        SELECT cmq.*,
               (cmq.toxic_score + cmq.obscene_score +
                cmq.threat_score + cmq.insult_score + cmq.identity_hate_score) AS total_score,
               u.email AS user_email
        FROM harmful_text_queue cmq
        JOIN users u ON u.user_id = cmq.user_id
        WHERE (:status = 'all' OR cmq.status = :status)
          AND (
                (:content_type = 'all' AND cmq.content_type != 'proposal')
                OR (:content_type != 'all' AND cmq.content_type = :content_type)
              )
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={
            "status":       status,
            "content_type": content_type,
            "limit":        page_size,
            "offset":       offset,
        },
    ))


def action_moderation_item(
    moderation_id: str,
    action: str,  # 'approve' | 'reject'
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """
    Approve or reject a pending moderation item.
    Rejected job posts are closed; rejected profiles are not deleted
    (admin may handle separately).
    """
    new_status = "approved" if action == "approve" else "rejected"
    updated = _row(get_db().execute_query(
        """
        UPDATE harmful_text_queue
        SET status = :status, admin_user_id = :admin_id,
            admin_note = :note, actioned_at = NOW()
        WHERE moderation_id = :mid AND status = 'pending'
        RETURNING *
        """,
        params={
            "status":   new_status,
            "admin_id": admin_user_id,
            "note":     admin_note,
            "mid":      moderation_id,
        },
    ))

    if updated and new_status == "approved":  # approved = flag confirmed → take action
        content_type = updated.get("content_type", "")
        content_id   = str(updated.get("content_id", ""))
        if content_type == "job_post":
            closure_note = admin_note or DEFAULT_CLOSURE_NOTE_CONTENT
            get_db().execute_query(
                """
                UPDATE job_post
                SET status = 'closed',
                    closure_reason = :reason,
                    closure_note   = :note
                WHERE job_post_id = :id
                """,
                params={
                    "id":     content_id,
                    "reason": DEFAULT_CLOSURE_REASON_CONTENT,
                    "note":   closure_note,
                },
            )
            logger("ADMIN", f"Job post {content_id} closed after moderation rejection", level="INFO")

            active_contracts = _active_contracts_for_job_post(content_id)
            if active_contracts:
                logger(
                    "ADMIN",
                    f"Job post {content_id} closed by admin {admin_user_id} while "
                    f"{len(active_contracts)} active contract(s) were in progress",
                    level="WARNING",
                )
                for contract in active_contracts:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(contract["freelancer_user_id"]),
                        notif_type="job_post_flagged_held",
                        title="Job Post Closed",
                        body="The job post tied to your active contract was closed by an administrator for a content policy violation. Your contract is not affected yet - contact support if you have questions.",
                        data={"job_post_id": content_id, "contract_id": str(contract["contract_id"])},
                    ))

            _fire_notification(ProposalFunctions.notify_proposal_owners_of_job_closure(
                content_id, "flagged for a content policy violation"
            ))

    return updated


def queue_scam_scan(
    job_post_id: str,
    client_id: str,
    text: str,
    title: str = "",
    description: str = "",
) -> Optional[Dict]:
    """
    Run ML-based scam scan (SBERT + RF, falls back to keyword) on a job post.

    If scam is detected:
      1. Immediately closes the job post (status → 'closed').
      2. Inserts a pending flag into scam_job_flags for admin review.
         Admin can mark it safe (job reopens) or confirm removal.

    Returns the flag row, or None if clean.
    """
    # Prefer explicit title/description for the ML model; fall back to combined text.
    if title or description:
        result = scan_for_scam_with_ml_fallback(title, description)
    else:
        # Legacy callers pass combined text; split heuristically on first 60 chars.
        result = scan_for_scam_with_ml_fallback("", text)

    scan_method = result.get("scan_method", "unknown")
    scam_score  = result["scam_score"]
    is_hard     = result["is_flagged"]                              # score >= 0.4 → auto-close
    is_soft     = not is_hard and scam_score >= SCAM_SOFT_FLAG_THRESHOLD  # 0.25–0.4 → review only

    if not is_hard and not is_soft:
        logger(
            "ADMIN",
            f"Scam scan ({scan_method}): job {job_post_id} is clean, score={scam_score:.3f}",
            level="INFO",
        )
        return None

    auto_remove_at = datetime.utcnow() + timedelta(days=AUTO_REMOVE_DAYS)
    try:
        if is_hard:
            # Close the job immediately; high-confidence scam.
            get_db().execute_query(
                """
                UPDATE job_post
                SET status         = 'closed',
                    closure_reason = :reason,
                    closure_note   = :note
                WHERE job_post_id = :jid
                  AND status NOT IN ('closed', 'filled')
                """,
                params={
                    "jid":    job_post_id,
                    "reason": DEFAULT_CLOSURE_REASON_SCAM,
                    "note":   DEFAULT_CLOSURE_NOTE_SCAM,
                },
            )

        row = _row(get_db().execute_query(
            """
            INSERT INTO scam_job_flags (
                job_post_id, client_id, scam_score,
                detected_keywords, flagged_text, auto_remove_at, auto_closed
            ) VALUES (
                :job_post_id, :client_id, :scam_score,
                CAST(:keywords AS JSONB), :text, :auto_remove_at, :auto_closed
            )
            RETURNING *
            """,
            params={
                "job_post_id":    job_post_id,
                "client_id":      client_id,
                "scam_score":     scam_score,
                "keywords":       json.dumps(result["detected_keywords"]),
                "text":           text[:500],
                "auto_remove_at": auto_remove_at,
                "auto_closed":    is_hard,
            },
        ))
        if is_hard:
            logger(
                "ADMIN",
                f"Scam detected ({scan_method}): job {job_post_id} auto-closed and flagged, score={scam_score:.3f}",
                level="WARNING",
            )
        else:
            logger(
                "ADMIN",
                f"Suspicious job ({scan_method}): job {job_post_id} soft-flagged for review "
                f", score={scam_score:.3f} (job still active)",
                level="WARNING",
            )
        return row
    except Exception as e:
        logger("ADMIN", f"Failed to queue scam scan: {e}", level="ERROR")
        return None


def _flag_client_for_scam(client_id: str):
    """Increment confirmed-scam count; ban client if total reaches 3."""
    get_db().execute_query(
        """
        INSERT INTO client_scam_record (client_id, total_scam_confirmed)
        VALUES (:cid, 1)
        ON CONFLICT (client_id) DO UPDATE
            SET total_scam_confirmed = client_scam_record.total_scam_confirmed + 1,
                updated_at = NOW()
        """,
        params={"cid": client_id},
    )
    record = _row(get_db().execute_query(
        "SELECT * FROM client_scam_record WHERE client_id = :cid",
        params={"cid": client_id},
    ))
    if record and record["total_scam_confirmed"] >= 3 and not record["is_banned"]:
        get_db().execute_query(
            """
            UPDATE client_scam_record
            SET is_banned = TRUE, banned_at = NOW()
            WHERE client_id = :cid
            """,
            params={"cid": client_id},
        )
        get_db().execute_query(
            """
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE client_id = :cid AND status = 'active'
            """,
            params={
                "cid":    client_id,
                "reason": DEFAULT_CLOSURE_REASON_SCAM,
                "note":   DEFAULT_CLOSURE_NOTE_SCAM,
            },
        )
        logger("ADMIN", f"Client {client_id} banned; 3+ confirmed scam jobs, active jobs closed", level="WARNING")


def _process_auto_remove():
    """Process auto-removal of expired scam flags after 30 days.

    High score (>=85%) confirms scam: closes job post and flags client.
    Low score (<85%): dismisses flag as false positive.
    """
    # high score: confirmed scam
    expired_high = _rows(get_db().execute_query(
        """
        UPDATE scam_job_flags
        SET status = 'removed', actioned_at = NOW()
        WHERE status = 'pending'
          AND auto_remove_at <= NOW()
          AND scam_score >= :threshold
        RETURNING *
        """,
        params={"threshold": SCAM_AUTO_REMOVE_THRESHOLD},
    ))
    for flag in expired_high:
        _flag_client_for_scam(str(flag["client_id"]))
        get_db().execute_query(
            """
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE job_post_id = :jid
            """,
            params={
                "jid":    str(flag["job_post_id"]),
                "reason": DEFAULT_CLOSURE_REASON_SCAM,
                "note":   DEFAULT_CLOSURE_NOTE_SCAM,
            },
        )
        logger("ADMIN", f"Auto-removed scam job {flag['job_post_id']}, score={flag['scam_score']:.2f}", level="WARNING")

    # low score: false positive, mark safe
    get_db().execute_query(
        """
        UPDATE scam_job_flags
        SET status = 'safe', actioned_at = NOW()
        WHERE status = 'pending'
          AND auto_remove_at <= NOW()
          AND scam_score < :threshold
        """,
        params={"threshold": SCAM_AUTO_REMOVE_THRESHOLD},
    )


def list_scam_flags(
    status: str = "pending",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    _process_auto_remove()
    offset    = (page - 1) * page_size
    sort_col  = _SCAM_SORT_COLS.get(sort_by, "sf.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    return _rows(get_db().execute_query(
        f"""
        SELECT sf.*,
               jp.job_title,
               c.full_name AS client_name,
               u.email     AS client_email,
               csr.total_scam_confirmed,
               csr.is_banned
        FROM scam_job_flags sf
        JOIN job_post jp ON jp.job_post_id = sf.job_post_id
        JOIN client   c  ON c.client_id    = sf.client_id
        JOIN users    u  ON u.user_id      = c.user_id
        LEFT JOIN client_scam_record csr ON csr.client_id = sf.client_id
        WHERE (:status = 'all' OR sf.status = :status)
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={"status": status, "limit": page_size, "offset": offset},
    ))


def action_scam_flag(
    flag_id: str,
    action: str,  # 'approve' (mark safe) | 'remove'
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """
    Approve (safe) or remove a scam-flagged job.
    Removing closes the job post and flags the client.
    """
    new_status = "safe" if action == "approve" else "removed"
    updated = _row(get_db().execute_query(
        """
        UPDATE scam_job_flags
        SET status = :status, admin_user_id = :admin_id,
            admin_note = :note, actioned_at = NOW()
        WHERE flag_id = :fid AND status = 'pending'
        RETURNING *
        """,
        params={
            "status":   new_status,
            "admin_id": admin_user_id,
            "note":     admin_note,
            "fid":      flag_id,
        },
    ))
    if updated and new_status == "safe":
        if updated.get("auto_closed"):
            # Hard flag false positive; job was auto-closed, reopen it.
            get_db().execute_query(
                """
                UPDATE job_post
                SET status         = 'active',
                    closure_reason = NULL,
                    closure_note   = NULL
                WHERE job_post_id = :jid
                  AND closure_reason = 'scam'
                """,
                params={"jid": str(updated["job_post_id"])},
            )
            logger("ADMIN", f"Scam flag {flag_id} cleared: job {updated['job_post_id']} reopened by {admin_user_id}", level="INFO")
        else:
            # Soft flag dismissed; job was never closed, nothing to reopen.
            logger("ADMIN", f"Soft scam flag {flag_id} dismissed as safe by {admin_user_id} (job was active)", level="INFO")

    if updated and new_status == "removed":
        _flag_client_for_scam(str(updated["client_id"]))
        closure_note = admin_note or DEFAULT_CLOSURE_NOTE_SCAM
        get_db().execute_query(
            """
            UPDATE job_post
            SET status         = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE job_post_id = :jid
            """,
            params={
                "jid":    str(updated["job_post_id"]),
                "reason": DEFAULT_CLOSURE_REASON_SCAM,
                "note":   closure_note,
            },
        )
        logger("ADMIN", f"Scam job {updated['job_post_id']} confirmed removed by admin {admin_user_id}", level="WARNING")
    return updated


def get_client_scam_record(client_id: str) -> Optional[Dict]:
    return _row(get_db().execute_query(
        "SELECT * FROM client_scam_record WHERE client_id = :cid",
        params={"cid": client_id},
    ))


def _process_report_auto_actions():
    """
    Auto-ban users / close job posts that have ≥10 reports
    with the oldest report ≥30 days old.
    Skips targets that already have a record in report_auto_actions.
    """
    # User targets
    user_targets = _rows(get_db().execute_query(
        """
        SELECT reported_user_id AS target_id, COUNT(*) AS report_count
        FROM user_reports
        WHERE reported_user_id IS NOT NULL
          AND status IN ('pending', 'accepted')
        GROUP BY reported_user_id
        HAVING COUNT(*) >= :threshold
           AND MIN(created_at) <= NOW() - (:days * INTERVAL '1 day')
        """,
        params={
            "threshold": REPORT_AUTO_ACTION_THRESHOLD,
            "days":      REPORT_AUTO_ACTION_DAYS,
        },
    ))
    for t in user_targets:
        tid = str(t["target_id"])
        existing = _row(get_db().execute_query(
            "SELECT 1 AS x FROM report_auto_actions WHERE target_type = 'user' AND target_id = :tid",
            params={"tid": tid},
        ))
        if existing:
            continue
        get_db().execute_query(
            """
            UPDATE users
            SET is_report_banned = TRUE,
                report_banned_at = NOW(),
                ban_reason  = :reason,
                ban_message = :msg
            WHERE user_id = :uid
            """,
            params={
                "uid":    tid,
                "reason": DEFAULT_BAN_REASON_REPORTS,
                "msg":    DEFAULT_BAN_MESSAGE_REPORTS,
            },
        )
        get_db().execute_query(
            """
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE client_id = (SELECT client_id FROM client WHERE user_id = :uid)
              AND status = 'active'
            """,
            params={
                "uid":    tid,
                "reason": DEFAULT_CLOSURE_REASON_REPORTS,
                "note":   DEFAULT_CLOSURE_NOTE_REPORTS,
            },
        )
        get_db().execute_query(
            """
            INSERT INTO report_auto_actions (target_type, target_id, report_count)
            VALUES ('user', :tid, :cnt)
            ON CONFLICT (target_type, target_id) DO NOTHING
            """,
            params={"tid": tid, "cnt": int(t["report_count"])},
        )
        logger("ADMIN", f"User {tid} report-banned ({t['report_count']} reports); active jobs closed", level="WARNING")

    # Job post targets
    job_targets = _rows(get_db().execute_query(
        """
        SELECT job_post_id AS target_id, COUNT(*) AS report_count
        FROM user_reports
        WHERE job_post_id IS NOT NULL
          AND status IN ('pending', 'accepted')
        GROUP BY job_post_id
        HAVING COUNT(*) >= :threshold
           AND MIN(created_at) <= NOW() - (:days * INTERVAL '1 day')
        """,
        params={
            "threshold": REPORT_AUTO_ACTION_THRESHOLD,
            "days":      REPORT_AUTO_ACTION_DAYS,
        },
    ))
    for t in job_targets:
        tid = str(t["target_id"])
        existing = _row(get_db().execute_query(
            "SELECT 1 AS x FROM report_auto_actions WHERE target_type = 'job_post' AND target_id = :tid",
            params={"tid": tid},
        ))
        if existing:
            continue
        get_db().execute_query(
            """
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE job_post_id = :jid
            """,
            params={
                "jid":    tid,
                "reason": DEFAULT_CLOSURE_REASON_REPORTS,
                "note":   DEFAULT_CLOSURE_NOTE_REPORTS,
            },
        )
        get_db().execute_query(
            """
            INSERT INTO report_auto_actions (target_type, target_id, report_count)
            VALUES ('job_post', :tid, :cnt)
            ON CONFLICT (target_type, target_id) DO NOTHING
            """,
            params={"tid": tid, "cnt": int(t["report_count"])},
        )
        logger("ADMIN", f"Job post {tid} closed via report threshold ({t['report_count']} reports)", level="WARNING")


def list_report_auto_actions(page: int = 1, page_size: int = 20) -> List[Dict]:
    offset = (page - 1) * page_size
    return _rows(get_db().execute_query(
        """
        SELECT raa.*,
               u.email           AS user_email,
               jp.job_title      AS job_title
        FROM report_auto_actions raa
        LEFT JOIN users    u  ON raa.target_type = 'user'     AND u.user_id       = raa.target_id
        LEFT JOIN job_post jp ON raa.target_type = 'job_post' AND jp.job_post_id  = raa.target_id
        ORDER BY raa.created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        params={"limit": page_size, "offset": offset},
    ))


def list_report_targets(
    target_type: str = "all",  # 'user' | 'job_post' | 'all'
    sort_by: str = "report_count",
    sort_dir: str = "desc",
    min_count: int = 1,
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    """
    Grouped view: one row per reported target (user or job post) with
    aggregate report count, oldest/latest report date, and whether the
    auto-action threshold has been met.
    """
    offset    = (page - 1) * page_size
    sort_col  = _REPORT_TARGET_SORT_COLS.get(sort_by, "report_count")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    return _rows(get_db().execute_query(
        f"""
        SELECT
            ur.reported_user_id,
            ur.job_post_id,
            CASE WHEN ur.reported_user_id IS NOT NULL THEN 'user' ELSE 'job_post' END AS target_type,
            ur.reported_type,
            COUNT(*)            AS report_count,
            MIN(ur.created_at)  AS oldest_report,
            MAX(ur.created_at)  AS latest_report,
            u.email             AS target_email,
            jp.job_title        AS target_job_title,
            (COUNT(*) >= :auto_threshold
             AND MIN(ur.created_at) <= NOW() - (:auto_days * INTERVAL '1 day')
            )                   AS threshold_met
        FROM user_reports ur
        LEFT JOIN users    u  ON u.user_id       = ur.reported_user_id
        LEFT JOIN job_post jp ON jp.job_post_id  = ur.job_post_id
        WHERE ur.status IN ('pending', 'accepted')
          AND (
            :target_type = 'all'
            OR (:target_type = 'user'     AND ur.reported_user_id IS NOT NULL)
            OR (:target_type = 'job_post' AND ur.job_post_id      IS NOT NULL)
          )
        GROUP BY ur.reported_user_id, ur.job_post_id, u.email, jp.job_title, ur.reported_type
        HAVING COUNT(*) >= :min_count
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={
            "target_type":    target_type,
            "auto_threshold": REPORT_AUTO_ACTION_THRESHOLD,
            "auto_days":      REPORT_AUTO_ACTION_DAYS,
            "min_count":      min_count,
            "limit":          page_size,
            "offset":         offset,
        },
    ))


def force_expire_reports(target_type: str, target_id: str) -> None:
    """Backdate report created_at to simulate 30-day threshold crossing (testing utility)."""
    if target_type == "user":
        get_db().execute_query(
            """
            UPDATE user_reports
            SET created_at = NOW() - INTERVAL '31 days'
            WHERE reported_user_id = :tid AND status IN ('pending', 'accepted')
            """,
            params={"tid": target_id},
        )
    else:
        get_db().execute_query(
            """
            UPDATE user_reports
            SET created_at = NOW() - INTERVAL '31 days'
            WHERE job_post_id = :tid AND status IN ('pending', 'accepted')
            """,
            params={"tid": target_id},
        )
    _process_report_auto_actions()


def run_moderation_sweeps() -> None:
    """
    Run all three time-based moderation sweeps together.

    Called lazily by admin dashboard/queue endpoints, and periodically by
    moderation_sweep_worker.moderation_sweep_loop() so expired items get
    actioned even when no admin is logged in.
    """
    _auto_approve_expired()
    _process_auto_remove()
    _process_report_auto_actions()


_MAX_APPEALS_PER_TARGET = 2

def submit_appeal(user_id: str, target_type: str, target_id: str, message: str) -> Optional[Dict]:
    """
    User submits an appeal against a ban or job-post closure.

    Rules:
      - Cannot appeal while a pending appeal already exists for the same target.
      - Cannot appeal if the target was already approved (resolved positively).
      - Maximum of _MAX_APPEALS_PER_TARGET (2) appeals per (user, target).
        The second appeal is the user's one retry after a rejection.
    """
    existing = _rows(get_db().execute_query(
        """
        SELECT status FROM appeals
        WHERE user_id = :uid AND target_type = :tt AND target_id = :tid
        ORDER BY created_at DESC
        """,
        params={"uid": user_id, "tt": target_type, "tid": target_id},
    ))

    if existing:
        statuses = [r["status"] for r in existing]

        if "pending" in statuses:
            raise HTTPException(
                status_code=400,
                detail="You already have a pending appeal for this item. Wait for it to be reviewed.",
            )
        if "approved" in statuses:
            raise HTTPException(
                status_code=400,
                detail="Your previous appeal was approved; no further appeal is needed.",
            )
        if len(existing) >= _MAX_APPEALS_PER_TARGET:
            raise HTTPException(
                status_code=400,
                detail="You have reached the maximum number of appeals for this item.",
            )

    try:
        row = _row(get_db().execute_query(
            """
            INSERT INTO appeals (user_id, target_type, target_id, message)
            VALUES (:user_id, :target_type, :target_id, :message)
            RETURNING *
            """,
            params={
                "user_id":     user_id,
                "target_type": target_type,
                "target_id":   target_id,
                "message":     message,
            },
        ))
        attempt = len(existing) + 1
        logger("ADMIN", f"Appeal #{attempt} submitted by user {user_id} for {target_type} {target_id}", level="INFO")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger("ADMIN", f"Failed to submit appeal: {e}", level="ERROR")
        return None


def get_appeal_status(user_id: str, target_type: str, target_id: str) -> Dict:
    """
    Return the current appeal eligibility and a frontend-ready message for a given target.

    States:
      never_appealed       → can appeal, 2 chances left
      pending              → cannot appeal, waiting for review
      rejected_can_retry   → can appeal, 1 chance left
      rejected_final       → cannot appeal, exhausted
      approved             → cannot appeal, already resolved positively.
    """
    existing = _rows(get_db().execute_query(
        """
        SELECT status, admin_note, actioned_at, created_at
        FROM appeals
        WHERE user_id = :uid AND target_type = :tt AND target_id = :tid
        ORDER BY created_at DESC
        """,
        params={"uid": user_id, "tt": target_type, "tid": target_id},
    ))

    rejection_count = sum(1 for r in existing if r["status"] == "rejected")
    has_pending     = any(r["status"] == "pending"  for r in existing)
    has_approved    = any(r["status"] == "approved" for r in existing)
    total           = len(existing)
    appeals_remaining = max(0, _MAX_APPEALS_PER_TARGET - total)

    # Fetch the restriction reason from the target itself
    restriction_reason = None
    if target_type == "user":
        row = _row(get_db().execute_query(
            "SELECT ban_message, ban_reason FROM users WHERE user_id = :tid",
            params={"tid": target_id},
        ))
        if row:
            restriction_reason = row.get("ban_message") or row.get("ban_reason")
    elif target_type == "job_post":
        row = _row(get_db().execute_query(
            "SELECT closure_reason, closure_note FROM job_post WHERE job_post_id = :tid",
            params={"tid": target_id},
        ))
        if row:
            restriction_reason = row.get("closure_note") or row.get("closure_reason")

    if has_approved:
        return {
            "can_appeal":        False,
            "appeals_remaining": 0,
            "state":             "approved",
            "message":           "Your previous appeal was approved. No further appeal is needed.",
            "restriction_reason": None,
        }

    if has_pending:
        return {
            "can_appeal":        False,
            "appeals_remaining": appeals_remaining,
            "state":             "pending",
            "message":           "You already have a pending appeal for this case. Please wait for the admin to review it.",
            "restriction_reason": restriction_reason,
        }

    if rejection_count == 0:
        return {
            "can_appeal":        True,
            "appeals_remaining": _MAX_APPEALS_PER_TARGET,
            "state":             "never_appealed",
            "message":           f"You can submit an appeal. Appeal chances: {_MAX_APPEALS_PER_TARGET}.",
            "restriction_reason": restriction_reason,
        }

    if rejection_count < _MAX_APPEALS_PER_TARGET:
        return {
            "can_appeal":        True,
            "appeals_remaining": appeals_remaining,
            "state":             "rejected_can_retry",
            "message":           f"Your previous appeal was rejected. You have {appeals_remaining} last chance(s) to re-appeal.",
            "restriction_reason": restriction_reason,
        }

    # rejection_count >= _MAX_APPEALS_PER_TARGET
    return {
        "can_appeal":        False,
        "appeals_remaining": 0,
        "state":             "rejected_final",
        "message":           "You have exhausted all appeals for this case. No further appeal is possible.",
        "restriction_reason": restriction_reason,
    }


def get_user_appeals(user_id: str) -> List[Dict]:
    """Return all appeals submitted by a user."""
    return _rows(get_db().execute_query(
        """
        SELECT a.*,
               jp.job_title AS job_title
        FROM appeals a
        LEFT JOIN job_post jp ON jp.job_post_id = a.target_id AND a.target_type = 'job_post'
        WHERE a.user_id = :uid
        ORDER BY a.created_at DESC
        """,
        params={"uid": user_id},
    ))


def list_appeals(
    status:         str = "pending",
    target_type:    Optional[str] = None,
    appeal_attempt: Optional[int] = None,
    search:         Optional[str] = None,
    page:           int = 1,
    page_size:      int = 20,
) -> List[Dict]:
    """
    Admin: list appeals with optional filters.

    appeal_attempt  1 = first appeal for that target, 2 = second/final attempt.
    target_type     'user' | 'job_post'
    search          partial match on submitter email.
    """
    offset = (page - 1) * page_size

    # ROW_NUMBER per (user, target) ordered by created_at gives the attempt number.
    rows = _rows(get_db().execute_query(
        """
        SELECT a.*,
               u.email      AS user_email,
               jp.job_title AS job_title,
               ROW_NUMBER() OVER (
                   PARTITION BY a.user_id, a.target_type, a.target_id
                   ORDER BY a.created_at
               ) AS appeal_attempt
        FROM appeals a
        JOIN users u        ON u.user_id          = a.user_id
        LEFT JOIN job_post jp ON jp.job_post_id   = a.target_id
                              AND a.target_type   = 'job_post'
        WHERE (:status      = 'all'  OR a.status      = :status)
          AND (:target_type IS NULL  OR a.target_type = :target_type)
          AND (:search       IS NULL OR u.email ILIKE :search_pat)
        ORDER BY a.created_at DESC
        LIMIT :limit OFFSET :offset
        """,
        params={
            "status":      status,
            "target_type": target_type,
            "search":      search,
            "search_pat":  f"%{search}%" if search else None,
            "limit":       page_size,
            "offset":      offset,
        },
    ))

    # Filter by appeal_attempt in Python (window func result not filterable in WHERE).
    if appeal_attempt is not None:
        rows = [r for r in rows if r.get("appeal_attempt") == appeal_attempt]

    return rows


def get_appeal(appeal_id: str) -> Optional[Dict]:
    """Fetch a single appeal by ID with submitter email and job title."""
    return _row(get_db().execute_query(
        """
        SELECT a.*,
               u.email      AS user_email,
               jp.job_title AS job_title
        FROM appeals a
        JOIN users u    ON u.user_id        = a.user_id
        LEFT JOIN job_post jp ON jp.job_post_id = a.target_id AND a.target_type = 'job_post'
        WHERE a.appeal_id = :aid
        """,
        params={"aid": appeal_id},
    ))


def resolve_appeal(
    appeal_id: str,
    action: str,  # 'approve' | 'reject'
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """
    Approve → restore the target (reopen job post or unban user).
    Reject  → keep the closure/ban, record decision.
    """
    new_status = "approved" if action == "approve" else "rejected"
    updated = _row(get_db().execute_query(
        """
        UPDATE appeals
        SET status = :status, admin_user_id = :admin_id,
            admin_note = :note, actioned_at = NOW()
        WHERE appeal_id = :aid AND status = 'pending'
        RETURNING *
        """,
        params={
            "status":   new_status,
            "admin_id": admin_user_id,
            "note":     admin_note,
            "aid":      appeal_id,
        },
    ))
    if updated and new_status == "approved":
        target_type = updated.get("target_type")
        target_id   = str(updated.get("target_id"))
        if target_type == "job_post":
            get_db().execute_query(
                """
                UPDATE job_post
                SET status = 'open', closure_reason = NULL, closure_note = NULL
                WHERE job_post_id = :jid
                """,
                params={"jid": target_id},
            )
            logger("ADMIN", f"Job post {target_id} restored via appeal {appeal_id}", level="INFO")
        elif target_type == "user":
            get_db().execute_query(
                """
                UPDATE users
                SET is_report_banned = FALSE, report_banned_at = NULL,
                    ban_reason = NULL, ban_message = NULL
                WHERE user_id = :uid
                """,
                params={"uid": target_id},
            )
            logger("ADMIN", f"User {target_id} restored via appeal {appeal_id}", level="INFO")
    return updated


def create_report(
    reporter_id: str,
    reported_type: str,
    reasons: List[str],
    custom_reason: Optional[str],
    reported_user_id: Optional[str] = None,
    job_post_id: Optional[str] = None,
) -> Optional[Dict]:
    try:
        row = _row(get_db().execute_query(
            """
            INSERT INTO user_reports
                (reporter_id, reported_user_id, job_post_id, reported_type, reasons, custom_reason)
            VALUES
                (:reporter_id, :reported_user_id, :job_post_id, :reported_type, CAST(:reasons AS JSONB), :custom_reason)
            RETURNING *
            """,
            params={
                "reporter_id":      reporter_id,
                "reported_user_id": reported_user_id,
                "job_post_id":      job_post_id,
                "reported_type":    reported_type,
                "reasons":          json.dumps(reasons),
                "custom_reason":    custom_reason,
            },
        ))
        return row
    except Exception as e:
        logger("ADMIN", f"Failed to create report: {e}", level="ERROR")
        return None


def list_reports(
    status: str = "pending",
    reported_type: str = "all",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    _process_report_auto_actions()
    offset    = (page - 1) * page_size
    sort_col  = _REPORT_SORT_COLS.get(sort_by, "ur.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    return _rows(get_db().execute_query(
        f"""
        SELECT ur.*,
               reporter.email          AS reporter_email,
               reported.email          AS reported_email,
               jp.job_title            AS job_post_title
        FROM user_reports ur
        JOIN users reporter ON reporter.user_id = ur.reporter_id
        LEFT JOIN users    reported ON reported.user_id  = ur.reported_user_id
        LEFT JOIN job_post jp       ON jp.job_post_id    = ur.job_post_id
        WHERE (:status = 'all' OR ur.status = :status)
          AND (:reported_type = 'all' OR ur.reported_type = :reported_type)
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={
            "status":        status,
            "reported_type": reported_type,
            "limit":         page_size,
            "offset":        offset,
        },
    ))


def get_report(report_id: str) -> Optional[Dict]:
    """Fetch a single report by ID with reporter and reported entity details."""
    return _row(get_db().execute_query(
        """
        SELECT ur.*,
               reporter.email          AS reporter_email,
               reported.email          AS reported_email,
               jp.job_title            AS job_post_title
        FROM user_reports ur
        JOIN users reporter ON reporter.user_id = ur.reporter_id
        LEFT JOIN users    reported ON reported.user_id  = ur.reported_user_id
        LEFT JOIN job_post jp       ON jp.job_post_id    = ur.job_post_id
        WHERE ur.report_id = :rid
        """,
        params={"rid": report_id},
    ))


def get_admin_user_detail(user_id: str) -> Optional[Dict]:
    """Fetch a single user with full profile details for admin review."""
    return _row(get_db().execute_query(
        """
        SELECT
            u.user_id, u.email, u.is_admin, u.email_verified, u.email_verified_at,
            u.is_report_banned, u.report_banned_at, u.ban_reason, u.ban_message,
            u.created_at, u.updated_at,
            f.freelancer_id,
            f.full_name             AS freelancer_name,
            f.profile_picture_url   AS freelancer_avatar,
            f.bio,
            c.client_id,
            c.full_name             AS client_name,
            c.profile_picture_url   AS client_avatar,
            c.total_jobs_posted,
            CASE
                WHEN u.is_admin              THEN 'admin'
                WHEN f.freelancer_id IS NOT NULL THEN 'freelancer'
                WHEN c.client_id     IS NOT NULL THEN 'client'
                ELSE 'unassigned'
            END AS role,
            csr.total_scam_confirmed,
            csr.is_banned AS is_scam_banned,
            (SELECT COUNT(*) FROM user_reports WHERE reported_user_id = u.user_id) AS total_reports_received,
            (SELECT COUNT(*) FROM appeals       WHERE user_id          = u.user_id) AS total_appeals_submitted
        FROM users u
        LEFT JOIN freelancer         f   ON f.user_id    = u.user_id
        LEFT JOIN client             c   ON c.user_id    = u.user_id
        LEFT JOIN client_scam_record csr ON csr.client_id = c.client_id
        WHERE u.user_id = :uid
        """,
        params={"uid": user_id},
    ))


def action_report(
    report_id: str,
    action: str,  # 'accept' | 'dismiss'
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    new_status = "accepted" if action == "accept" else "dismissed"
    return _row(get_db().execute_query(
        """
        UPDATE user_reports
        SET status = :status, admin_user_id = :admin_id,
            admin_note = :note, actioned_at = NOW()
        WHERE report_id = :rid AND status = 'pending'
        RETURNING *
        """,
        params={
            "status":   new_status,
            "admin_id": admin_user_id,
            "note":     admin_note,
            "rid":      report_id,
        },
    ))


def admin_close_job(
    job_post_id: str,
    admin_user_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict]:
    """Close any job post directly, bypassing reports and AI flags."""
    closure_note = reason or DEFAULT_CLOSURE_NOTE_ADMIN
    updated = _row(get_db().execute_query(
        """
        UPDATE job_post
        SET status         = 'closed',
            closure_reason = :reason,
            closure_note   = :note,
            closed_at      = NOW()
        WHERE job_post_id = :jid
        RETURNING *
        """,
        params={
            "jid":    job_post_id,
            "reason": DEFAULT_CLOSURE_REASON_ADMIN,
            "note":   closure_note,
        },
    ))
    if updated:
        logger("ADMIN", f"Job post {job_post_id} force-closed by admin {admin_user_id}", level="WARNING")

        active_contracts = _active_contracts_for_job_post(job_post_id)
        if active_contracts:
            logger(
                "ADMIN",
                f"Job post {job_post_id} force-closed while {len(active_contracts)} active contract(s) were in progress",
                level="WARNING",
            )
            for contract in active_contracts:
                _fire_notification(NotificationFunctions.notify(
                    recipient_user_id=str(contract["freelancer_user_id"]),
                    notif_type="job_post_flagged_held",
                    title="Job Post Closed",
                    body="The job post tied to your active contract was closed by an administrator. Your contract is not affected yet - contact support if you have questions.",
                    data={"job_post_id": job_post_id, "contract_id": str(contract["contract_id"])},
                ))

        _fire_notification(ProposalFunctions.notify_proposal_owners_of_job_closure(
            job_post_id, closure_note
        ))
    return updated


def admin_reopen_job(
    job_post_id: str,
    admin_user_id: str,
) -> Optional[Dict]:
    """Reopen a closed job post directly, without requiring a user appeal."""
    updated = _row(get_db().execute_query(
        """
        UPDATE job_post
        SET status         = 'active',
            closure_reason = NULL,
            closure_note   = NULL,
            closed_at      = NULL
        WHERE job_post_id = :jid
          AND status = 'closed'
        RETURNING *
        """,
        params={"jid": job_post_id},
    ))
    if updated:
        logger("ADMIN", f"Job post {job_post_id} reopened by admin {admin_user_id}", level="INFO")
    return updated


def admin_close_account(
    user_id: str,
    admin_user_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict]:
    """Restrict any user account directly, bypassing reports and AI flags."""
    ban_message = reason or DEFAULT_BAN_MESSAGE_ADMIN
    updated = _row(get_db().execute_query(
        """
        UPDATE users
        SET is_report_banned = TRUE,
            report_banned_at = NOW(),
            ban_reason       = :reason,
            ban_message      = :message
        WHERE user_id = :uid
        RETURNING user_id, email, is_report_banned, ban_reason, ban_message, report_banned_at
        """,
        params={
            "uid":     user_id,
            "reason":  DEFAULT_BAN_REASON_ADMIN,
            "message": ban_message,
        },
    ))
    if updated:
        get_db().execute_query(
            """
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note
            WHERE client_id = (SELECT client_id FROM client WHERE user_id = :uid)
              AND status = 'active'
            """,
            params={
                "uid":    user_id,
                "reason": DEFAULT_CLOSURE_REASON_ADMIN,
                "note":   DEFAULT_CLOSURE_NOTE_ADMIN,
            },
        )
        logger("ADMIN", f"Account {user_id} force-closed by admin {admin_user_id}; active jobs closed", level="WARNING")
    return updated


def admin_reopen_account(
    user_id: str,
    admin_user_id: str,
) -> Optional[Dict]:
    """Restore a restricted user account directly, without requiring a user appeal."""
    updated = _row(get_db().execute_query(
        """
        UPDATE users
        SET is_report_banned = FALSE,
            report_banned_at = NULL,
            ban_reason       = NULL,
            ban_message      = NULL
        WHERE user_id = :uid
          AND is_report_banned = TRUE
        RETURNING user_id, email, is_report_banned, ban_reason, ban_message, report_banned_at
        """,
        params={"uid": user_id},
    ))
    if updated:
        logger("ADMIN", f"Account {user_id} restored by admin {admin_user_id}", level="INFO")
    return updated


def get_admin_dashboard_stats() -> Dict:
    """Return aggregate counts for the admin overview panel."""
    run_moderation_sweeps()

    def _count(query: str, params: dict = {}) -> int:
        row = _row(get_db().execute_query(query, params=params))
        return int(row["cnt"]) if row else 0

    return {
        "pending_moderation_items": _count(
            "SELECT COUNT(*) AS cnt FROM harmful_text_queue WHERE status = 'pending' AND content_type != 'proposal'"
        ),
        "pending_scam_flags": _count(
            "SELECT COUNT(*) AS cnt FROM scam_job_flags WHERE status = 'pending'"
        ),
        "pending_reports": _count(
            "SELECT COUNT(*) AS cnt FROM user_reports WHERE status = 'pending'"
        ),
        "banned_clients": _count(
            "SELECT COUNT(*) AS cnt FROM client_scam_record WHERE is_banned = TRUE"
        ),
        "auto_approved_last_24h": _count(
            """
            SELECT COUNT(*) AS cnt FROM harmful_text_queue
            WHERE status = 'approved'
              AND admin_user_id IS NULL
              AND content_type != 'proposal'
              AND actioned_at >= NOW() - INTERVAL '24 hours'
            """
        ),
        "auto_removed_last_24h": _count(
            """
            SELECT COUNT(*) AS cnt FROM scam_job_flags
            WHERE status = 'removed'
              AND admin_user_id IS NULL
              AND actioned_at >= NOW() - INTERVAL '24 hours'
            """
        ),
        "total_reports_accepted": _count(
            "SELECT COUNT(*) AS cnt FROM user_reports WHERE status = 'accepted'"
        ),
        "report_auto_actions_total": _count(
            "SELECT COUNT(*) AS cnt FROM report_auto_actions"
        ),
    }


_JOB_ADMIN_SORT_COLS = {
    "created_at":    "jp.created_at",
    "closed_at":     "jp.closed_at",
    "updated_at":    "jp.updated_at",
    "job_title":     "jp.job_title",
    "status":        "jp.status",
    "proposal_count": "jp.proposal_count",
    "view_count":    "jp.view_count",
}

_USER_ADMIN_SORT_COLS = {
    "created_at":       "u.created_at",
    "updated_at":       "u.updated_at",
    "email":            "u.email",
    "report_banned_at": "u.report_banned_at",
    "ban_reason":       "u.ban_reason",
}


def _csv(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip()]


def _in_filter(
    col: str,
    values: List[str],
    prefix: str,
    where: List[str],
    params: dict,
    exclude: bool = False,
) -> None:
    if not values:
        return
    placeholders = ", ".join(f":{prefix}_{i}" for i in range(len(values)))
    op = "NOT IN" if exclude else "IN"
    where.append(f"{col} {op} ({placeholders})")
    for i, v in enumerate(values):
        params[f"{prefix}_{i}"] = v


def admin_list_jobs(
    status: Optional[str] = None,
    exclude_status: Optional[str] = None,
    closure_reason: Optional[str] = None,
    exclude_closure_reason: Optional[str] = None,
    project_type: Optional[str] = None,
    exclude_project_type: Optional[str] = None,
    project_scope: Optional[str] = None,
    exclude_project_scope: Optional[str] = None,
    experience_level: Optional[str] = None,
    exclude_experience_level: Optional[str] = None,
    project_category: Optional[str] = None,
    is_ai_generated: Optional[bool] = None,
    client_id: Optional[str] = None,
    search: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    closed_from: Optional[str] = None,
    closed_to: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    offset    = (page - 1) * page_size
    sort_col  = _JOB_ADMIN_SORT_COLS.get(sort_by, "jp.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    where: List[str] = []
    params: Dict     = {}

    _in_filter("jp.status",           _csv(status),                   "st",  where, params)
    _in_filter("jp.status",           _csv(exclude_status),           "xst", where, params, exclude=True)
    _in_filter("jp.closure_reason",   _csv(closure_reason),           "cr",  where, params)
    _in_filter("jp.closure_reason",   _csv(exclude_closure_reason),   "xcr", where, params, exclude=True)
    _in_filter("jp.project_type",     _csv(project_type),             "pt",  where, params)
    _in_filter("jp.project_type",     _csv(exclude_project_type),     "xpt", where, params, exclude=True)
    _in_filter("jp.project_scope",    _csv(project_scope),            "ps",  where, params)
    _in_filter("jp.project_scope",    _csv(exclude_project_scope),    "xps", where, params, exclude=True)
    _in_filter("jp.experience_level", _csv(experience_level),         "el",  where, params)
    _in_filter("jp.experience_level", _csv(exclude_experience_level), "xel", where, params, exclude=True)

    if project_category:
        where.append("jp.project_category ILIKE :proj_cat")
        params["proj_cat"] = f"%{project_category}%"
    if is_ai_generated is not None:
        where.append("jp.is_ai_generated = :is_ai")
        params["is_ai"] = is_ai_generated
    if client_id:
        where.append("jp.client_id = :client_id")
        params["client_id"] = client_id
    if search:
        where.append("jp.job_title ILIKE :search")
        params["search"] = f"%{search}%"
    if created_from:
        where.append("jp.created_at >= :created_from")
        params["created_from"] = created_from
    if created_to:
        where.append("jp.created_at <= :created_to")
        params["created_to"] = created_to
    if closed_from:
        where.append("jp.closed_at >= :closed_from")
        params["closed_from"] = closed_from
    if closed_to:
        where.append("jp.closed_at <= :closed_to")
        params["closed_to"] = closed_to

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = _rows(get_db().execute_query(
        f"""
        SELECT
            jp.job_post_id, jp.client_id, jp.job_title, jp.project_type,
            jp.project_scope, jp.experience_level, jp.status, jp.is_ai_generated,
            jp.view_count, jp.proposal_count, jp.project_category,
            jp.created_at, jp.updated_at, jp.posted_at, jp.closed_at,
            jp.closure_reason, jp.closure_note,
            c.full_name  AS client_name,
            u.email      AS client_email,
            COUNT(DISTINCT jr.job_role_id) AS role_count
        FROM job_post jp
        LEFT JOIN client   c  ON c.client_id   = jp.client_id
        LEFT JOIN users    u  ON u.user_id      = c.user_id
        LEFT JOIN job_role jr ON jr.job_post_id = jp.job_post_id
        {where_sql}
        GROUP BY jp.job_post_id, c.full_name, u.email
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={**params, "limit": page_size, "offset": offset},
    ))

    total_row = _row(get_db().execute_query(
        f"""
        SELECT COUNT(DISTINCT jp.job_post_id) AS cnt
        FROM job_post jp
        LEFT JOIN client c ON c.client_id = jp.client_id
        LEFT JOIN users  u ON u.user_id   = c.user_id
        {where_sql}
        """,
        params=params,
    ))
    total = int(total_row["cnt"]) if total_row else 0

    return {
        "jobs":        rows,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": math.ceil(total / page_size) if page_size > 0 else 0,
    }


def admin_list_users(
    role: Optional[str] = None,
    exclude_role: Optional[str] = None,
    is_banned: Optional[bool] = None,
    email_verified: Optional[bool] = None,
    ban_reason: Optional[str] = None,
    exclude_ban_reason: Optional[str] = None,
    search: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    banned_from: Optional[str] = None,
    banned_to: Optional[str] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    offset    = (page - 1) * page_size
    sort_col  = _USER_ADMIN_SORT_COLS.get(sort_by, "u.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    where: List[str] = []
    params: Dict     = {}

    # Role is derived from joined tables, so we build OR conditions manually.
    include_roles = _csv(role)
    if include_roles:
        role_conds = []
        for r in include_roles:
            if r == "freelancer":
                role_conds.append("f.freelancer_id IS NOT NULL")
            elif r == "client":
                role_conds.append("c.client_id IS NOT NULL AND u.is_admin = FALSE")
            elif r == "admin":
                role_conds.append("u.is_admin = TRUE")
        if role_conds:
            where.append(f"({' OR '.join(role_conds)})")

    exclude_roles = _csv(exclude_role)
    for r in exclude_roles:
        if r == "freelancer":
            where.append("f.freelancer_id IS NULL")
        elif r == "client":
            where.append("c.client_id IS NULL")
        elif r == "admin":
            where.append("u.is_admin = FALSE")

    if is_banned is not None:
        where.append("u.is_report_banned = :is_banned")
        params["is_banned"] = is_banned
    if email_verified is not None:
        where.append("u.email_verified = :email_verified")
        params["email_verified"] = email_verified

    _in_filter("u.ban_reason", _csv(ban_reason),         "br",  where, params)
    _in_filter("u.ban_reason", _csv(exclude_ban_reason),  "xbr", where, params, exclude=True)

    if search:
        where.append(
            "(u.email ILIKE :search OR COALESCE(f.full_name, c.full_name, '') ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    if created_from:
        where.append("u.created_at >= :created_from")
        params["created_from"] = created_from
    if created_to:
        where.append("u.created_at <= :created_to")
        params["created_to"] = created_to
    if banned_from:
        where.append("u.report_banned_at >= :banned_from")
        params["banned_from"] = banned_from
    if banned_to:
        where.append("u.report_banned_at <= :banned_to")
        params["banned_to"] = banned_to

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = _rows(get_db().execute_query(
        f"""
        SELECT
            u.user_id, u.email, u.is_admin, u.email_verified, u.email_verified_at,
            u.is_report_banned, u.report_banned_at, u.ban_reason, u.ban_message,
            u.created_at, u.updated_at,
            f.freelancer_id,
            f.full_name             AS freelancer_name,
            f.profile_picture_url   AS freelancer_avatar,
            c.client_id,
            c.full_name             AS client_name,
            c.profile_picture_url   AS client_avatar,
            c.total_jobs_posted,
            CASE
                WHEN u.is_admin              THEN 'admin'
                WHEN f.freelancer_id IS NOT NULL THEN 'freelancer'
                WHEN c.client_id     IS NOT NULL THEN 'client'
                ELSE 'unassigned'
            END AS role,
            csr.total_scam_confirmed,
            csr.is_banned AS is_scam_banned
        FROM users u
        LEFT JOIN freelancer         f   ON f.user_id   = u.user_id
        LEFT JOIN client             c   ON c.user_id   = u.user_id
        LEFT JOIN client_scam_record csr ON csr.client_id = c.client_id
        {where_sql}
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={**params, "limit": page_size, "offset": offset},
    ))

    total_row = _row(get_db().execute_query(
        f"""
        SELECT COUNT(*) AS cnt
        FROM users u
        LEFT JOIN freelancer f ON f.user_id = u.user_id
        LEFT JOIN client     c ON c.user_id = u.user_id
        {where_sql}
        """,
        params=params,
    ))
    total = int(total_row["cnt"]) if total_row else 0

    return {
        "users":       rows,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": math.ceil(total / page_size) if page_size > 0 else 0,
    }
