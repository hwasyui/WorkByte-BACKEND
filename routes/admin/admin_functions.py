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
from functions.profile_ids import user_id_for_client, user_id_for_freelancer
from routes.admin.admin_moderation import (
    scan_harmful_text,
    scan_for_scam,
    scan_for_scam_with_ml_fallback,
    scan_harmful_text_with_ml_fallback,
    scan_harmful_text_fields,
)
from routes.notifications.notification_functions import NotificationFunctions

AUTO_APPROVE_DAYS = 30
AUTO_REMOVE_DAYS  = 30

CONTENT_AUTO_CLOSE_THRESHOLD_JOB     = 0.88

REPORT_AUTO_ACTION_THRESHOLD = 10
REPORT_AUTO_ACTION_DAYS      = 30

DEFAULT_CLOSURE_REASON_CONTENT = "harmful_text"
DEFAULT_CLOSURE_NOTE_CONTENT   = (
    "This job post was closed by Harmful Text Detection. "
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

SYSTEM_CLOSURE_REASONS = frozenset({
    DEFAULT_CLOSURE_REASON_CONTENT,
    DEFAULT_CLOSURE_REASON_SCAM,
    DEFAULT_CLOSURE_REASON_REPORTS,
    DEFAULT_CLOSURE_REASON_ADMIN,
})

DEFAULT_BAN_REASON_ADMIN       = "admin_override"
DEFAULT_BAN_MESSAGE_ADMIN      = (
    "Your account has been restricted by an administrator. "
    "Submit an appeal if you believe this was a mistake."
)

LIVE_CONTRACT_STATUSES     = ("active", "revision_requested", "under_review", "disputed")
_LIVE_CONTRACT_STATUS_SQL  = ", ".join(f"'{s}'" for s in LIVE_CONTRACT_STATUSES)

def _is_engaged_sql(job_post_id_expr: str) -> str:
    return (
        f"(EXISTS (SELECT 1 FROM job_role jr "
        f"WHERE jr.job_post_id = {job_post_id_expr} AND jr.positions_filled > 0) "
        f"OR EXISTS (SELECT 1 FROM contract c "
        f"WHERE c.job_post_id = {job_post_id_expr} "
        f"AND c.status IN ({_LIVE_CONTRACT_STATUS_SQL})))"
    )

_ACTIVE_NO_ENGAGEMENT = f"\n      AND NOT {_is_engaged_sql('job_post.job_post_id')}"

def _job_is_engaged(job_post_id: str) -> bool:
    row = _row(get_db().execute_query(
        f"SELECT {_is_engaged_sql(':jid')} AS engaged",
        params={"jid": job_post_id},
    ))
    return bool(row and row["engaged"])

_MOD_SORT_COLS = {
    "created_at":   "cmq.created_at",
    "total_score":  "(cmq.toxic_score + cmq.obscene_score + cmq.threat_score + cmq.insult_score + cmq.identity_hate_score)",
    "max_score":    "GREATEST(cmq.toxic_score, cmq.obscene_score, cmq.threat_score, cmq.insult_score, cmq.identity_hate_score)",
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

def _schedule_notification(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        logger("ADMIN", "No running event loop, cannot send job-closed notification", level="WARNING")
        coro.close()

def _notify_engaged_freelancers(job_post_id: str) -> None:
    rows = _rows(get_db().execute_query(
        f"""
        SELECT c.contract_id, c.contract_title, f.user_id, jp.job_title
        FROM contract c
        JOIN freelancer f ON f.freelancer_id = c.freelancer_id
        JOIN job_post   jp ON jp.job_post_id = c.job_post_id
        WHERE c.job_post_id = :jid
          AND c.status IN ({_LIVE_CONTRACT_STATUS_SQL})
        """,
        params={"jid": job_post_id},
    ))
    for row in rows:
        _schedule_notification(NotificationFunctions.notify(
            recipient_user_id=str(row["user_id"]),
            notif_type="job_closed_admin_contract",
            title="Job Closed by Admin",
            body=(
                f"The job post \"{row['job_title']}\" was closed by an administrator. "
                f"Your contract \"{row['contract_title']}\" is still active - check with the "
                f"client before continuing work."
            ),
            data={"job_post_id": job_post_id, "contract_id": str(row["contract_id"])},
        ))

def _notify_job_post_closed(job_post_id: str, notif_type: str, title: str, body: str) -> None:
    _notify_engaged_freelancers(job_post_id)
    row = _row(get_db().execute_query(
        """
        SELECT c.user_id FROM job_post jp
        JOIN client c ON c.client_id = jp.client_id
        WHERE jp.job_post_id = :jid
        """,
        params={"jid": job_post_id},
    ))
    if not row:
        return
    _schedule_notification(NotificationFunctions.notify(
        recipient_user_id=str(row["user_id"]),
        notif_type=notif_type,
        title=title,
        body=body,
        data={"job_post_id": job_post_id},
    ))

def queue_harmful_text_scan(content_type: str,
    content_id: str,
    user_id: str,
    text: str,
    *fields: str,
) -> Optional[Dict]:
    # Callers with several fields (a job post's title and description) pass them separately
    # so each is scored on its own. text stays the snapshot stored on the row.
    # The whole body sits in the try because this runs as a fire-and-forget task: anything
    # raised outside it would die with the task and leave the content unmoderated silently.
    try:
        result = scan_harmful_text_fields(*fields) if fields else scan_harmful_text_with_ml_fallback(text)
        if not result["is_flagged"]:
            return None

        scan_method = result.get("scan_method", "unknown")
        # One pending row per content. A re-scan overwrites it only when the new text scores
        # higher, so the admin and the 30-day sweep judge the worst version that went live.
        # auto_approve_at is left alone, so the deadline still runs from the first offence.
        auto_approve_at = datetime.utcnow() + timedelta(days=AUTO_APPROVE_DAYS)
        row = _row(get_db().execute_query(
            """
            INSERT INTO harmful_text_queue (
                content_type, content_id, user_id,
                toxic_score, obscene_score,
                threat_score, insult_score, identity_hate_score,
                detected_labels, flagged_text, auto_approve_at
            ) VALUES (
                :content_type, :content_id, :user_id,
                :toxic_score, :obscene_score,
                :threat_score, :insult_score, :identity_hate_score,
                CAST(:detected_labels AS JSONB), :flagged_text, :auto_approve_at
            )
            ON CONFLICT (content_type, content_id) WHERE status = 'pending'
            DO UPDATE SET
                toxic_score         = EXCLUDED.toxic_score,
                obscene_score       = EXCLUDED.obscene_score,
                threat_score        = EXCLUDED.threat_score,
                insult_score        = EXCLUDED.insult_score,
                identity_hate_score = EXCLUDED.identity_hate_score,
                detected_labels     = EXCLUDED.detected_labels,
                flagged_text        = EXCLUDED.flagged_text
            WHERE GREATEST(EXCLUDED.toxic_score, EXCLUDED.obscene_score, EXCLUDED.threat_score,
                           EXCLUDED.insult_score, EXCLUDED.identity_hate_score)
                > GREATEST(harmful_text_queue.toxic_score, harmful_text_queue.obscene_score,
                           harmful_text_queue.threat_score, harmful_text_queue.insult_score,
                           harmful_text_queue.identity_hate_score)
            RETURNING *
            """,
            params={
                "content_type":         content_type,
                "content_id":           content_id,
                "user_id":              user_id,
                "toxic_score":          result["toxic_score"],
                "obscene_score":        result["obscene_score"],
                "threat_score":         result["threat_score"],
                "insult_score":         result["insult_score"],
                "identity_hate_score":  result["identity_hate_score"],
                "detected_labels":      json.dumps(result["detected_labels"]),
                "flagged_text":         text[:500],
                "auto_approve_at":      auto_approve_at,
            },
        ))
        if row is None:
            logger(
                "ADMIN",
                f"Content already has a pending scan that is at least as severe, kept: {content_type} {content_id}",
                level="INFO",
            )
        else:
            logger(
                "ADMIN",
                f"Content flagged via {scan_method} scan: {content_type} {content_id} labels={result['detected_labels']}",
                level="INFO",
            )
        return row
    except Exception as e:
        logger("ADMIN", f"Harmful scan failed, content left unmoderated: {content_type} {content_id} | {e}",
               level="ERROR")
        return None

def _auto_approve_expired():
    expired = _rows(get_db().execute_query(
        """
        SELECT *
        FROM harmful_text_queue
        WHERE status = 'pending' AND auto_approve_at <= NOW()
        """,
        params={},
    ))
    for item in expired:
        max_score = max(
            float(item.get("toxic_score") or 0),
            float(item.get("obscene_score") or 0),
            float(item.get("threat_score") or 0),
            float(item.get("insult_score") or 0),
            float(item.get("identity_hate_score") or 0),
        )
        ctype      = item.get("content_type", "")
        content_id = str(item.get("content_id", ""))
        mid        = str(item.get("moderation_id", ""))

        new_status = "approved" if max_score >= CONTENT_AUTO_CLOSE_THRESHOLD_JOB else "rejected"

        if new_status == "approved":
            engaged = _row(get_db().execute_query(
                f"""
                SELECT 1 AS x
                FROM job_post
                WHERE job_post_id = :id AND status IN ('active', 'filled')
                  AND {_is_engaged_sql('job_post.job_post_id')}
                """,
                params={"id": content_id},
            ))
            if engaged:
                logger(
                    "ADMIN",
                    f"Harmful flag on {ctype} {content_id} left pending: job has ongoing engagement, needs manual admin action",
                    level="INFO",
                )
                continue

        claimed = _rows(get_db().execute_query(
            """
            UPDATE harmful_text_queue
            SET status = :status, actioned_at = NOW()
            WHERE moderation_id = :mid AND status = 'pending'
            RETURNING moderation_id
            """,
            params={"status": new_status, "mid": mid},
        ))
        if not claimed:
            continue

        if new_status == "approved":
            note = DEFAULT_CLOSURE_NOTE_CONTENT
            closed = _rows(get_db().execute_query(
                f"""
                UPDATE job_post
                SET status = 'closed',
                    closure_reason = :reason,
                    closure_note   = :note,
                    closed_at      = NOW()
                WHERE job_post_id = :id AND status = 'active'{_ACTIVE_NO_ENGAGEMENT}
                RETURNING job_post_id
                """,
                params={
                    "id":     content_id,
                    "reason": DEFAULT_CLOSURE_REASON_CONTENT,
                    "note":   note,
                },
            ))
            if closed:
                logger("ADMIN", f"Auto-closed {ctype} {content_id}, max_label_score={max_score:.2f} >= {CONTENT_AUTO_CLOSE_THRESHOLD_JOB}", level="WARNING")
                _notify_job_post_closed(
                    content_id,
                    "job_closed_harmful_text",
                    "Job Post Closed",
                    note,
                )
            else:
                logger("ADMIN", f"Flag confirmed but {ctype} {content_id} not active or has ongoing engagement (skipped close)", level="INFO")
        else:
            logger("ADMIN", f"Auto-dismissed {ctype} {content_id}, max_label_score={max_score:.2f}", level="INFO")

MODERATION_SWEEP_INTERVAL_SECONDS = int(os.getenv("MODERATION_SWEEP_INTERVAL_SECONDS", "3600"))

async def moderation_sweep_loop() -> None:
    """
    Expire both moderation queues on a timer.

    Scam flags used to be swept only when an admin opened the flag list or the dashboard,
    which made auto_remove_at mean "30 days and someone happened to look" rather than a
    deadline - a flagged job stayed live indefinitely on a quiet week. The lazy calls stay
    where they are; this just stops them from being the only thing that runs.

    Each sweep gets its own try so a failure in one cannot skip the other, and neither is
    moved onto a worker thread: closure notifications go through _schedule_notification,
    which drops them unless it is called with a running event loop.
    """
    logger("ADMIN", f"Moderation sweep loop started | interval={MODERATION_SWEEP_INTERVAL_SECONDS}s", level="INFO")
    while True:
        await asyncio.sleep(MODERATION_SWEEP_INTERVAL_SECONDS)
        try:
            _auto_approve_expired()
        except Exception as e:
            logger("ADMIN", f"Moderation sweep loop unhandled error: {e}", level="ERROR")
        try:
            _process_auto_remove()
        except Exception as e:
            logger("ADMIN", f"Scam expiry sweep unhandled error: {e}", level="ERROR")

def force_expire_moderation(moderation_ids: List[str]) -> None:
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
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    min_severity: Optional[float] = None,
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    _auto_approve_expired()
    offset    = (page - 1) * page_size
    sort_col  = _MOD_SORT_COLS.get(sort_by, "cmq.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    return _rows(get_db().execute_query(
        f"""
        SELECT cmq.*,
               (cmq.toxic_score + cmq.obscene_score +
                cmq.threat_score + cmq.insult_score + cmq.identity_hate_score) AS total_score,
               GREATEST(cmq.toxic_score, cmq.obscene_score, cmq.threat_score,
                        cmq.insult_score, cmq.identity_hate_score) AS max_score,
               u.email AS user_email,
               c.client_id,
               c.full_name AS client_name,
               CASE WHEN cmq.content_type = 'job_post'
                    THEN {_is_engaged_sql('cmq.content_id')}
                    ELSE FALSE
               END AS is_engaged,
               jp.job_title AS job_title
        FROM harmful_text_queue cmq
        JOIN users u ON u.user_id = cmq.user_id
        JOIN client c ON c.user_id = cmq.user_id
        LEFT JOIN job_post jp
               ON cmq.content_type = 'job_post'
              AND jp.job_post_id = cmq.content_id
        WHERE (:status = 'all' OR cmq.status = :status)
          AND (
                :min_severity IS NULL
                OR GREATEST(cmq.toxic_score, cmq.obscene_score, cmq.threat_score,
                            cmq.insult_score, cmq.identity_hate_score) >= :min_severity
              )
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={
            "status":       status,
            "min_severity": min_severity,
            "limit":        page_size,
            "offset":       offset,
        },
    ))

def action_moderation_item(
    moderation_id: str,
    action: str,
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """
    Record an admin verdict on a harmful-text flag.

    action is 'uphold' (the flag was right - the content comes down) or 'dismiss' (the
    flag was wrong - the content stays). The legacy spelling approve/reject is still
    accepted: here 'approve' meant approving the FLAG, so it maps to uphold. Scam flags
    used the opposite word for the same verdict, which is why both now speak
    uphold/dismiss - see action_scam_flag.
    """
    upheld     = action in ("uphold", "approve")
    new_status = "approved" if upheld else "rejected"
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

    if updated and new_status == "approved":
        content_type = updated.get("content_type", "")
        content_id   = str(updated.get("content_id", ""))
        if content_type == "job_post":
            closure_note = admin_note or DEFAULT_CLOSURE_NOTE_CONTENT
            closed = _rows(get_db().execute_query(
                """
                UPDATE job_post
                SET status = 'closed',
                    closure_reason = :reason,
                    closure_note   = :note,
                    closed_at      = NOW()
                WHERE job_post_id = :id AND status <> 'closed'
                RETURNING job_post_id
                """,
                params={
                    "id":     content_id,
                    "reason": DEFAULT_CLOSURE_REASON_CONTENT,
                    "note":   closure_note,
                },
            ))
            if closed:
                logger("ADMIN", f"Job post {content_id} closed after moderation rejection", level="INFO")
                _notify_job_post_closed(
                    content_id,
                    "job_closed_harmful_text",
                    "Job Post Closed",
                    closure_note,
                )
            else:
                logger("ADMIN", f"Job post {content_id} flag confirmed but not active or has ongoing engagement (skipped close)", level="INFO")

    return updated

def _notify_scam_closure(job_post_id: str) -> None:
    _notify_engaged_freelancers(job_post_id)
    row = _row(get_db().execute_query(
        """
        SELECT c.user_id FROM job_post jp
        JOIN client c ON c.client_id = jp.client_id
        WHERE jp.job_post_id = :jid
        """,
        params={"jid": job_post_id},
    ))
    if not row:
        return

    coro = NotificationFunctions.notify(
        recipient_user_id=str(row["user_id"]),
        notif_type="job_closed_scam",
        title="Job Post Closed",
        body=DEFAULT_CLOSURE_NOTE_SCAM,
        data={"job_post_id": job_post_id},
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception as e:
            logger("ADMIN", f"Scam closure notification failed: {e}", level="WARNING")
        return
    loop.create_task(coro)

def queue_scam_scan(
    job_post_id: str,
    client_id: str,
    text: str,
    title: str = "",
    description: str = "",
) -> Optional[Dict]:
    # The whole body sits in the try because this runs as a fire-and-forget task: anything
    # raised outside it would die with the task and leave the job unscanned silently.
    # Same reason queue_harmful_text_scan is shaped this way.
    try:
        if title or description:
            result = scan_for_scam_with_ml_fallback(title, description)
        else:
            result = scan_for_scam_with_ml_fallback("", text)

        scan_method = result.get("scan_method", "unknown")
        scam_score  = result["scam_score"]
        is_hard     = result["is_flagged"]
        is_soft     = not is_hard and result.get("needs_review", False)

        if not is_hard and not is_soft:
            logger(
                "ADMIN",
                f"Scam scan ({scan_method}): job {job_post_id} is clean, score={scam_score:.3f}",
                level="INFO",
            )
            return None

        auto_remove_at = datetime.utcnow() + timedelta(days=AUTO_REMOVE_DAYS)
        closed = []
        if is_hard:
            closed = _rows(get_db().execute_query(
                f"""
                UPDATE job_post
                SET status         = 'closed',
                    closure_reason = :reason,
                    closure_note   = :note,
                    closed_at      = NOW()
                WHERE job_post_id = :jid
                  AND status = 'active'{_ACTIVE_NO_ENGAGEMENT}
                RETURNING job_post_id
                """,
                params={
                    "jid":    job_post_id,
                    "reason": DEFAULT_CLOSURE_REASON_SCAM,
                    "note":   DEFAULT_CLOSURE_NOTE_SCAM,
                },
            ))

        # One pending flag per job. A re-scan (an edit, or an admin re-running the scan)
        # overwrites it only when the new text scores higher, so the admin and the 30-day
        # sweep judge the worst version that went live - editing the scam out after the
        # applicants have already seen it does not clear the record. auto_remove_at is left
        # alone so the deadline still runs from the first offence, and auto_closed is only
        # ever raised, never dropped back to FALSE by a later scan.
        row = _row(get_db().execute_query(
            """
            INSERT INTO scam_job_flags (
                job_post_id, client_id, scam_score,
                detected_keywords, flagged_text, auto_remove_at, auto_closed
            ) VALUES (
                :job_post_id, :client_id, :scam_score,
                CAST(:keywords AS JSONB), :text, :auto_remove_at, :auto_closed
            )
            ON CONFLICT (job_post_id) WHERE status = 'pending'
            DO UPDATE SET
                scam_score        = EXCLUDED.scam_score,
                detected_keywords = EXCLUDED.detected_keywords,
                flagged_text      = EXCLUDED.flagged_text,
                auto_closed       = scam_job_flags.auto_closed OR EXCLUDED.auto_closed
            WHERE EXCLUDED.scam_score > scam_job_flags.scam_score
            RETURNING *
            """,
            params={
                "job_post_id":    job_post_id,
                "client_id":      client_id,
                "scam_score":     scam_score,
                "keywords":       json.dumps(result["detected_keywords"]),
                "text":           text[:500],
                "auto_remove_at": auto_remove_at,
                "auto_closed":    bool(closed),
            },
        ))
        if is_hard and closed:
            logger(
                "ADMIN",
                f"Scam detected ({scan_method}): job {job_post_id} auto-closed and flagged, score={scam_score:.3f}",
                level="WARNING",
            )
            _notify_scam_closure(job_post_id)
        elif is_hard:
            logger(
                "ADMIN",
                f"Scam detected ({scan_method}): job {job_post_id} flagged but not closed "
                f"(not active or has ongoing engagement), score={scam_score:.3f}",
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
        logger("ADMIN", f"Scam scan failed, job left unmoderated: job_post {job_post_id} | {e}",
               level="ERROR")
        return None

def _flag_client_for_scam(client_id: str):
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
                closure_note   = :note,
                closed_at      = NOW()
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
    from ai_related.job_scam_detection.scam_detector import get_thresholds

    try:
        expire_close = get_thresholds()["expire_close"]
    except Exception as e:
        logger("ADMIN", f"Scam expiry sweep skipped, thresholds unavailable: {e}", level="ERROR")
        return

    expired = _rows(get_db().execute_query(
        f"""
        UPDATE scam_job_flags
        SET status = 'removed', actioned_at = NOW()
        WHERE status = 'pending'
          AND auto_remove_at <= NOW()
          AND scam_score >= :threshold
          AND NOT EXISTS (
                SELECT 1 FROM job_post jp
                WHERE jp.job_post_id = scam_job_flags.job_post_id
                  AND jp.status IN ('active', 'filled')
                  AND {_is_engaged_sql('jp.job_post_id')}
              )
        RETURNING *
        """,
        params={"threshold": expire_close},
    ))
    parked = _rows(get_db().execute_query(
        f"""
        SELECT sf.flag_id, sf.job_post_id
        FROM scam_job_flags sf
        JOIN job_post jp ON jp.job_post_id = sf.job_post_id
        WHERE sf.status = 'pending'
          AND sf.auto_remove_at <= NOW()
          AND sf.scam_score >= :threshold
          AND jp.status IN ('active', 'filled')
          AND {_is_engaged_sql('jp.job_post_id')}
        """,
        params={"threshold": expire_close},
    ))
    for flag in parked:
        logger(
            "ADMIN",
            f"Scam flag {flag['flag_id']} left pending: job {flag['job_post_id']} has ongoing "
            f"engagement, needs manual admin action",
            level="INFO",
        )
    for flag in expired:
        job_post_id = str(flag["job_post_id"])
        closed = _rows(get_db().execute_query(
            f"""
            UPDATE job_post
            SET status         = 'closed',
                closure_reason = :reason,
                closure_note   = :note,
                closed_at      = NOW()
            WHERE job_post_id = :jid
              AND status = 'active'{_ACTIVE_NO_ENGAGEMENT}
            RETURNING job_post_id
            """,
            params={
                "jid":    job_post_id,
                "reason": DEFAULT_CLOSURE_REASON_SCAM,
                "note":   DEFAULT_CLOSURE_NOTE_SCAM,
            },
        ))
        if closed:
            logger(
                "ADMIN",
                f"Scam flag {flag['flag_id']} expired unreviewed: job {job_post_id} closed, "
                f"score={flag['scam_score']:.3f} (no strike applied)",
                level="WARNING",
            )
            _notify_scam_closure(job_post_id)
        else:
            logger(
                "ADMIN",
                f"Scam flag {flag['flag_id']} expired unreviewed but job {job_post_id} was "
                f"not active (engaged jobs never get this far - they stay pending)",
                level="INFO",
            )

    dismissed = _rows(get_db().execute_query(
        """
        UPDATE scam_job_flags
        SET status = 'safe', actioned_at = NOW()
        WHERE status = 'pending'
          AND auto_remove_at <= NOW()
          AND scam_score < :threshold
          AND auto_closed = FALSE
        RETURNING flag_id
        """,
        params={"threshold": expire_close},
    ))
    if dismissed:
        logger(
            "ADMIN",
            f"{len(dismissed)} scam flag(s) expired below the {expire_close:.4f} cutoff, dismissed",
            level="INFO",
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
               csr.is_banned,
               -- always a job post, so no content_type branch like the moderation queue has
               {_is_engaged_sql('sf.job_post_id')} AS is_engaged
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
    action: str,
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """
    Record an admin verdict on a scam flag.

    action is 'uphold' (the flag was right - the job is closed and the client takes a
    strike) or 'dismiss' (the flag was wrong - the job stays, and is reopened if the
    scan had auto-closed it). 'remove' is the old spelling of uphold.

    'approve' means uphold, matching action_moderation_item. It used to mean the exact
    opposite here - approving the JOB rather than the flag - and nothing in the request
    distinguishes a caller on the old meaning from one on the new, so an unmigrated
    client closes the job it meant to clear. The HTTP layer logs every legacy /approve
    as a WARNING for exactly this reason; that log is the only signal, not a guardrail.
    Callers that mean "this job is fine" must say dismiss.
    """
    upheld     = action in ("uphold", "approve", "remove")
    new_status = "removed" if upheld else "safe"
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
            job_post_id = str(updated["job_post_id"])
            get_db().execute_query(
                """
                UPDATE job_post
                SET status         = :restore,
                    closure_reason = NULL,
                    closure_note   = NULL,
                    closed_at      = NULL
                WHERE job_post_id = :jid
                  AND closure_reason = 'scam'
                """,
                params={"jid": job_post_id, "restore": _restore_status_for(job_post_id)},
            )
            logger("ADMIN", f"Scam flag {flag_id} cleared: job {updated['job_post_id']} reopened by {admin_user_id}", level="INFO")
        else:
            logger("ADMIN", f"Soft scam flag {flag_id} dismissed as safe by {admin_user_id} (job was active)", level="INFO")

    if updated and new_status == "removed":
        job_post_id = str(updated["job_post_id"])
        _flag_client_for_scam(str(updated["client_id"]))
        closure_note = admin_note or DEFAULT_CLOSURE_NOTE_SCAM
        closed = _rows(get_db().execute_query(
            """
            UPDATE job_post
            SET status         = 'closed',
                closure_reason = :reason,
                closure_note   = :note,
                closed_at      = NOW()
            WHERE job_post_id = :jid AND status <> 'closed'
            RETURNING job_post_id
            """,
            params={
                "jid":    job_post_id,
                "reason": DEFAULT_CLOSURE_REASON_SCAM,
                "note":   closure_note,
            },
        ))
        if closed:
            logger("ADMIN", f"Scam job {job_post_id} confirmed removed by admin {admin_user_id}", level="WARNING")
            _notify_scam_closure(job_post_id)
        else:
            logger(
                "ADMIN",
                f"Scam flag {flag_id} confirmed by {admin_user_id} but job {job_post_id} was "
                f"already closed; strike recorded, closure reason left as-is",
                level="INFO",
            )
    return updated

def get_client_scam_record(client_id: str) -> Optional[Dict]:
    return _row(get_db().execute_query(
        "SELECT * FROM client_scam_record WHERE client_id = :cid",
        params={"cid": client_id},
    ))

def _process_report_auto_actions():
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
            f"""
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note,
                closed_at      = NOW()
            WHERE client_id = (SELECT client_id FROM client WHERE user_id = :uid)
              AND status = 'active'{_ACTIVE_NO_ENGAGEMENT}
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
        closed = _rows(get_db().execute_query(
            f"""
            UPDATE job_post
            SET status = 'closed',
                closure_reason = :reason,
                closure_note   = :note,
                closed_at      = NOW()
            WHERE job_post_id = :jid AND status = 'active'{_ACTIVE_NO_ENGAGEMENT}
            RETURNING job_post_id
            """,
            params={
                "jid":    tid,
                "reason": DEFAULT_CLOSURE_REASON_REPORTS,
                "note":   DEFAULT_CLOSURE_NOTE_REPORTS,
            },
        ))
        if not closed:
            logger("ADMIN", f"Job post {tid} hit report threshold but not active or has ongoing engagement (skipped close)", level="INFO")
            continue
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
    target_type: str = "all",
    sort_by: str = "report_count",
    sort_dir: str = "desc",
    min_count: int = 1,
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
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
            CASE WHEN ur.job_post_id IS NOT NULL
                 THEN {_is_engaged_sql('ur.job_post_id')}
                 ELSE FALSE
            END                 AS is_engaged,
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

_MAX_APPEALS_PER_TARGET = 2

def _validate_appeal_target(user_id: str, target_type: str, target_id: str) -> None:
    if target_type == "job_post":
        row = _row(get_db().execute_query(
            """
            SELECT jp.status, c.user_id AS owner_user_id
            FROM job_post jp
            JOIN client c ON c.client_id = jp.client_id
            WHERE jp.job_post_id = :tid
            """,
            params={"tid": target_id},
        ))
        if not row:
            raise HTTPException(status_code=404, detail="Job post not found")
        if str(row["owner_user_id"]) != str(user_id):
            raise HTTPException(status_code=403, detail="You can only appeal your own job posts")
        if row["status"] != "closed":
            raise HTTPException(status_code=400, detail="This job post is not closed, there is nothing to appeal")
    elif target_type == "user":
        if str(target_id) != str(user_id):
            raise HTTPException(status_code=403, detail="You can only appeal your own account restriction")
        row = _row(get_db().execute_query(
            "SELECT is_report_banned FROM users WHERE user_id = :uid",
            params={"uid": user_id},
        ))
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if not row["is_report_banned"]:
            raise HTTPException(status_code=400, detail="Your account is not currently restricted, there is nothing to appeal")

def submit_appeal(user_id: str, target_type: str, target_id: str, message: str) -> Optional[Dict]:
    _validate_appeal_target(user_id, target_type, target_id)

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

    return {
        "can_appeal":        False,
        "appeals_remaining": 0,
        "state":             "rejected_final",
        "message":           "You have exhausted all appeals for this case. No further appeal is possible.",
        "restriction_reason": restriction_reason,
    }

def get_user_appeals(user_id: str) -> List[Dict]:
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
    offset = (page - 1) * page_size

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

    if appeal_attempt is not None:
        rows = [r for r in rows if r.get("appeal_attempt") == appeal_attempt]

    return rows

def get_appeal(appeal_id: str) -> Optional[Dict]:
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
    action: str,
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
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
            restored = _row(get_db().execute_query(
                """
                UPDATE job_post
                SET status         = :new_status,
                    closure_reason = NULL,
                    closure_note   = NULL,
                    closed_at      = NULL
                WHERE job_post_id = :jid
                  AND status = 'closed'
                RETURNING job_post_id
                """,
                params={"jid": target_id, "new_status": _restore_status_for(target_id)},
            ))
            if restored:
                logger("ADMIN", f"Job post {target_id} restored via appeal {appeal_id}", level="INFO")
            else:
                logger(
                    "ADMIN",
                    f"Appeal {appeal_id} approved but job post {target_id} was not 'closed' - "
                    "nothing restored",
                    level="WARNING",
                )
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
               jp.job_title            AS job_post_title,
               CASE WHEN ur.job_post_id IS NOT NULL
                    THEN {_is_engaged_sql('ur.job_post_id')}
                    ELSE FALSE
               END                      AS is_engaged
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
    return _row(get_db().execute_query(
        f"""
        SELECT ur.*,
               reporter.email          AS reporter_email,
               reported.email          AS reported_email,
               jp.job_title            AS job_post_title,
               CASE WHEN ur.job_post_id IS NOT NULL
                    THEN {_is_engaged_sql('ur.job_post_id')}
                    ELSE FALSE
               END                      AS is_engaged
        FROM user_reports ur
        JOIN users reporter ON reporter.user_id = ur.reporter_id
        LEFT JOIN users    reported ON reported.user_id  = ur.reported_user_id
        LEFT JOIN job_post jp       ON jp.job_post_id    = ur.job_post_id
        WHERE ur.report_id = :rid
        """,
        params={"rid": report_id},
    ))

def get_admin_user_detail(user_id: str) -> Optional[Dict]:
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
    action: str,
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    new_status = "accepted" if action == "accept" else "dismissed"
    updated = _row(get_db().execute_query(
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

    if updated and new_status == "accepted" and updated.get("job_post_id"):
        job_post_id  = str(updated["job_post_id"])
        closure_note = admin_note or DEFAULT_CLOSURE_NOTE_REPORTS
        closed = _row(get_db().execute_query(
            """
            UPDATE job_post
            SET status         = 'closed',
                closure_reason = :reason,
                closure_note   = :note,
                closed_at      = NOW()
            WHERE job_post_id = :jid AND status <> 'closed'
            RETURNING job_post_id
            """,
            params={
                "jid":    job_post_id,
                "reason": DEFAULT_CLOSURE_REASON_REPORTS,
                "note":   closure_note,
            },
        ))
        if closed:
            logger("ADMIN", f"Job post {job_post_id} closed after report {report_id} accepted by admin {admin_user_id}", level="WARNING")
            _notify_job_post_closed(job_post_id, "job_closed_reports", "Job Post Closed", closure_note)
        else:
            logger("ADMIN", f"Report {report_id} accepted; job {job_post_id} was already closed (skipped re-close)", level="INFO")

    return updated

def admin_close_job(
    job_post_id: str,
    admin_user_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict]:
    if _job_is_engaged(job_post_id):
        raise HTTPException(
            status_code=409,
            detail="This job post has an active contract or an engaged freelancer and cannot be closed.",
        )
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
        _notify_job_post_closed(
            job_post_id,
            "job_closed_admin",
            "Job Post Closed",
            closure_note,
        )
    return updated

def _restore_status_for(job_post_id: str) -> str:
    row = _row(get_db().execute_query(
        """
        SELECT
            EXISTS (SELECT 1 FROM job_role WHERE job_post_id = :jpid) AS has_roles,
            NOT EXISTS (
                SELECT 1 FROM job_role
                WHERE job_post_id = :jpid AND positions_filled < positions_available
            ) AS all_filled
        """,
        params={"jpid": job_post_id},
    ))
    return "filled" if row and row.get("has_roles") and row.get("all_filled") else "active"

def admin_reopen_job(
    job_post_id: str,
    admin_user_id: str,
) -> Optional[Dict]:
    updated = _row(get_db().execute_query(
        """
        UPDATE job_post
        SET status         = :new_status,
            closure_reason = NULL,
            closure_note   = NULL,
            closed_at      = NULL
        WHERE job_post_id = :jid
          AND status = 'closed'
        RETURNING *
        """,
        params={"jid": job_post_id, "new_status": _restore_status_for(job_post_id)},
    ))
    if updated:
        logger("ADMIN", f"Job post {job_post_id} reopened by admin {admin_user_id}", level="INFO")
    return updated

def admin_close_account(
    user_id: str,
    admin_user_id: str,
    reason: Optional[str] = None,
) -> Optional[Dict]:
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
                closure_note   = :note,
                closed_at      = NOW()
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
    _auto_approve_expired()
    _process_auto_remove()
    _process_report_auto_actions()

    def _count(query: str, params: dict = {}) -> int:
        row = _row(get_db().execute_query(query, params=params))
        return int(row["cnt"]) if row else 0

    return {
        "pending_moderation_items": _count(
            "SELECT COUNT(*) AS cnt FROM harmful_text_queue WHERE status = 'pending'"
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

_RED_FLAG_SORT_COLS = {
    "triggered_at": "rfa.triggered_at",
    "severity":     "rfa.severity",
}
_FLAGGED_REVIEW_SORT_COLS = {
    "created_at": "r.created_at",
    "status":     "r.status",
}

def list_red_flag_alerts(
    is_resolved: Optional[bool] = None,
    subject_type: str = "all",  
    sort_by: str = "triggered_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    """Admin-wide (not per-subject) red flag alert listing, mirroring list_scam_flags.
    Exactly one of rfa.freelancer_id / rfa.client_id is set (enforced by
    red_flag_alerts_one_subject_check), so both sides can be LEFT JOINed
    unconditionally and coalesced - no subject_type predicate in the join."""
    offset    = (page - 1) * page_size
    sort_col  = _RED_FLAG_SORT_COLS.get(sort_by, "rfa.triggered_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    return _rows(get_db().execute_query(
        f"""
        SELECT rfa.*,
               COALESCE(f.full_name, c.full_name) AS subject_name,
               COALESCE(fu.email, cu.email)       AS subject_email
        FROM red_flag_alerts rfa
        LEFT JOIN freelancer f  ON f.freelancer_id = rfa.freelancer_id
        LEFT JOIN users      fu ON fu.user_id      = f.user_id
        LEFT JOIN client     c  ON c.client_id     = rfa.client_id
        LEFT JOIN users      cu ON cu.user_id      = c.user_id
        WHERE (:is_resolved IS NULL OR rfa.is_resolved = :is_resolved)
          AND (:subject_type = 'all' OR rfa.subject_type = :subject_type)
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={"is_resolved": is_resolved, "subject_type": subject_type, "limit": page_size, "offset": offset},
    ))

def resolve_red_flag_alert(alert_id: str, admin_user_id: str) -> Optional[Dict]:
    updated = _row(get_db().execute_query(
        """
        UPDATE red_flag_alerts
        SET is_resolved = TRUE
        WHERE id = :aid AND is_resolved = FALSE
        RETURNING *
        """,
        params={"aid": alert_id},
    ))
    if updated:
        logger("ADMIN", f"Red flag {alert_id} resolved by {admin_user_id}", level="INFO")
    return updated

def list_flagged_reviews(
    status: str = "all",  
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    """Reviews held back from publishing (overall_pass=false), with the AI
    analysis that caused the hold, for manual admin review."""
    offset       = (page - 1) * page_size
    sort_col     = _FLAGGED_REVIEW_SORT_COLS.get(sort_by, "r.created_at")
    direction    = "ASC" if sort_dir.lower() == "asc" else "DESC"
    status_filter = "r.status IN ('flagged', 'suppressed')" if status == "all" else "r.status = :status"
    return _rows(get_db().execute_query(
        f"""
        SELECT r.id, r.contract_id, r.freelancer_id, r.reviewer_id, r.status,
               r.inferred_category, r.created_at,
               f.full_name AS freelancer_name,
               wc.overall_comment,
               ra.sentiment_score, ra.sentiment_label, ra.sentiment_mismatch, ra.mismatch_severity,
               ra.authenticity_score, ra.is_flagged_fake, ra.is_flagged_coerced, ra.flag_reasons,
               ra.overall_pass
        FROM reviews r
        JOIN freelancer f ON f.freelancer_id = r.freelancer_id
        LEFT JOIN review_written_content wc ON wc.review_id = r.id
        LEFT JOIN review_ai_analysis     ra ON ra.review_id = r.id
        WHERE {status_filter}
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={"status": status, "limit": page_size, "offset": offset},
    ))

async def override_publish_review(review_id: str, admin_user_id: str) -> Optional[Dict]:
    updated = _row(get_db().execute_query(
        """
        UPDATE reviews
        SET status = 'published', published_at = NOW()
        WHERE id = :rid AND status IN ('flagged', 'suppressed')
        RETURNING *
        """,
        params={"rid": review_id},
    ))
    if not updated:
        return None

    logger("ADMIN", f"Review {review_id} override-published by {admin_user_id}", level="INFO")

    freelancer_name = "the freelancer"
    freelancer_rows = get_db().execute_query(
        "SELECT full_name FROM freelancer WHERE freelancer_id = :fid", {"fid": updated["freelancer_id"]}
    )
    if freelancer_rows and freelancer_rows[0].get("full_name"):
        freelancer_name = freelancer_rows[0]["full_name"]
    # reviews.reviewer_id is a client.client_id; notifications address users.
    _schedule_notification(NotificationFunctions.notify(
        recipient_user_id=user_id_for_client(str(updated["reviewer_id"])),
        notif_type="review_publish_confirmed",
        title="Your Review Was Published",
        body=f"After manual review, your review for {freelancer_name} has been approved and is now live.",
        data={"contract_id": str(updated["contract_id"]), "review_id": review_id},
    ))

    from ai_related.review_analysis.review_pipeline import recalculate_and_persist_trust_score
    await recalculate_and_persist_trust_score(
        freelancer_id=str(updated["freelancer_id"]),
        category=updated.get("inferred_category"),
    )
    return updated

_FLAGGED_CLIENT_REVIEW_SORT_COLS = {
    "created_at": "cr.created_at",
    "status":     "cr.status",
}

def list_flagged_client_reviews(
    status: str = "all", 
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> List[Dict]:
    """Client reviews (written by freelancers) held back from publishing -
    counterpart to list_flagged_reviews for the freelancer-reviews-client system."""
    offset        = (page - 1) * page_size
    sort_col      = _FLAGGED_CLIENT_REVIEW_SORT_COLS.get(sort_by, "cr.created_at")
    direction     = "ASC" if sort_dir.lower() == "asc" else "DESC"
    status_filter = "cr.status IN ('flagged', 'suppressed')" if status == "all" else "cr.status = :status"
    return _rows(get_db().execute_query(
        f"""
        SELECT cr.id, cr.contract_id, cr.reviewer_id, cr.client_id, cr.status, cr.created_at,
               c.full_name AS client_name,
               wc.overall_comment,
               cra.sentiment_score, cra.sentiment_label, cra.sentiment_mismatch, cra.mismatch_severity,
               cra.authenticity_score, cra.is_flagged_fake, cra.is_flagged_coerced, cra.flag_reasons,
               cra.overall_pass
        FROM client_reviews cr
        JOIN client c ON c.client_id = cr.client_id
        LEFT JOIN client_review_written_content wc ON wc.client_review_id = cr.id
        LEFT JOIN client_review_ai_analysis     cra ON cra.client_review_id = cr.id
        WHERE {status_filter}
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={"status": status, "limit": page_size, "offset": offset},
    ))

async def override_publish_client_review(client_review_id: str, admin_user_id: str) -> Optional[Dict]:
    updated = _row(get_db().execute_query(
        """
        UPDATE client_reviews
        SET status = 'published', published_at = NOW()
        WHERE id = :rid AND status IN ('flagged', 'suppressed')
        RETURNING *
        """,
        params={"rid": client_review_id},
    ))
    if not updated:
        return None

    logger("ADMIN", f"Client review {client_review_id} override-published by {admin_user_id}", level="INFO")

    client_name = "the client"
    client_rows = get_db().execute_query(
        "SELECT full_name FROM client WHERE client_id = :cid", {"cid": updated["client_id"]}
    )
    if client_rows and client_rows[0].get("full_name"):
        client_name = client_rows[0]["full_name"]
    # client_reviews.reviewer_id is a freelancer.freelancer_id; notifications address users.
    _schedule_notification(NotificationFunctions.notify(
        recipient_user_id=user_id_for_freelancer(str(updated["reviewer_id"])),
        notif_type="review_publish_confirmed",
        title="Your Review Was Published",
        body=f"After manual review, your review for {client_name} has been approved and is now live.",
        data={"contract_id": str(updated["contract_id"]), "client_review_id": client_review_id},
    ))

    from ai_related.review_analysis.client_review_pipeline import recalculate_and_persist_client_trust_score
    await recalculate_and_persist_client_trust_score(client_id=str(updated["client_id"]))
    return updated
