import asyncio
import json
import math
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, List, Optional
from fastapi import HTTPException

from functions.db_manager import get_db
from functions.logger import logger
from ai_related.review_analysis.judgment_log import log_admin_override, read_latest_judgment
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
    "created_at":   "htq.created_at",
    "total_score":  "(htq.toxic_score + htq.obscene_score + htq.threat_score + htq.insult_score + htq.identity_hate_score)",
    "max_score":    "GREATEST(htq.toxic_score, htq.obscene_score, htq.threat_score, htq.insult_score, htq.identity_hate_score)",
    "content_type": "htq.content_type",
    "status":       "htq.status",
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
    result: Optional[Dict] = None,
) -> Optional[Dict]:
    try:
        if result is None:
            result = scan_harmful_text_fields(*fields) if fields else scan_harmful_text_with_ml_fallback(text)
        if not result["is_flagged"]:
            return None

        scan_method = result.get("scan_method", "unknown")
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
                "flagged_text":         text,
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

FLAGGED_BY_PREFIX = "[FLAGGED BY] "

def flagged_source(flagged_text: Optional[str]) -> Optional[str]:
    """The field label that scored highest, e.g. '[ROLE] Frontend Developer'. None for rows
    written before the marker existed, or for content that has no field breakdown."""
    if not flagged_text or not flagged_text.startswith(FLAGGED_BY_PREFIX):
        return None
    marker = flagged_text.split("\n", 1)[0][len(FLAGGED_BY_PREFIX):].strip()
    # Trailing dash trimmed so it reads as a label; it stays a prefix of the body line, so
    # the client can still match the section with a plain startsWith.
    return marker.rstrip(" —-") or None

def _body_without_marker(flagged_text: Optional[str]) -> Optional[str]:
    """The snapshot as the admin should read it. The marker line stays in the column - it is
    the only place the offending field is recorded - but it is not text anyone wants to see."""
    if not flagged_text or not flagged_text.startswith(FLAGGED_BY_PREFIX):
        return flagged_text
    return flagged_text.partition("\n")[2]

def queue_job_post_harmful_scan(job_post_id: str, user_id: str) -> Optional[Dict]:
    """Scan a job post's title, description and every one of its roles, and queue the
    result as the single harmful_text_queue entry for that post."""
    try:
        post = _row(get_db().execute_query(
            "SELECT job_title, job_description, status FROM job_post WHERE job_post_id = :jid",
            params={"jid": job_post_id},
        ))
        if not post or post.get("status") != "active":
            return None
        
        labelled = [
            ("[TITLE]", post.get("job_title") or ""),
            ("[DESC]",  post.get("job_description") or ""),
        ]

        roles = _rows(get_db().execute_query(
            """
            SELECT role_title, role_description
            FROM job_role
            WHERE job_post_id = :jid
            ORDER BY display_order, job_role_id
            """,
            params={"jid": job_post_id},
        ))
        for role in roles:
            role_title = (role.get("role_title") or "").strip()
            role_desc  = (role.get("role_description") or "").strip()
            if not role_title and not role_desc:
                continue
           
            labelled.append((
                f"[ROLE] {role_title} —" if role_title else "[ROLE] —",
                f"Role: {role_title}. {role_desc}".strip(),
            ))

        labelled = [(prefix, text) for prefix, text in labelled if text.strip()]
        fields = [text for _, text in labelled]
        result = scan_harmful_text_fields(*fields)
        if not result["is_flagged"]:
            return None

        worst = result.get("worst_field", "")
        worst_label = next((p for p, t in labelled if t == worst), "")
        body = "\n".join(f"{prefix} {text}".strip() for prefix, text in labelled)
        snapshot = f"{FLAGGED_BY_PREFIX}{worst_label}\n{body}" if worst_label else body

        return queue_harmful_text_scan(
            "job_post", job_post_id, user_id, snapshot, *fields, result=result,
        )
    except Exception as e:
        logger("ADMIN", f"Job post harmful scan failed, content left unmoderated: {job_post_id} | {e}",
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
    sort_col  = _MOD_SORT_COLS.get(sort_by, "htq.created_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    rows = _rows(get_db().execute_query(
        f"""
        SELECT htq.*,
               (htq.toxic_score + htq.obscene_score +
                htq.threat_score + htq.insult_score + htq.identity_hate_score) AS total_score,
               GREATEST(htq.toxic_score, htq.obscene_score, htq.threat_score,
                        htq.insult_score, htq.identity_hate_score) AS max_score,
               u.email AS user_email,
               c.client_id,
               c.full_name AS client_name,
               CASE WHEN htq.content_type = 'job_post'
                    THEN {_is_engaged_sql('htq.content_id')}
                    ELSE FALSE
               END AS is_engaged,
               jp.job_title AS job_title
        FROM harmful_text_queue htq
        JOIN users u ON u.user_id = htq.user_id
        JOIN client c ON c.user_id = htq.user_id
        LEFT JOIN job_post jp
               ON htq.content_type = 'job_post'
              AND jp.job_post_id = htq.content_id
        WHERE (:status = 'all' OR htq.status = :status)
          AND (
                :min_severity IS NULL
                OR GREATEST(htq.toxic_score, htq.obscene_score, htq.threat_score,
                            htq.insult_score, htq.identity_hate_score) >= :min_severity
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
    for row in rows:
        row["flagged_source"] = flagged_source(row.get("flagged_text"))
        row["flagged_text"]   = _body_without_marker(row.get("flagged_text"))
    return rows

def action_moderation_item(
    moderation_id: str,
    action: str,
    admin_user_id: str,
    admin_note: Optional[str] = None,
) -> Optional[Dict]:
    """Record an admin verdict on a harmful-text flag."""
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
) -> Dict:
    """Admin-wide (not per-subject) red flag alert listing, mirroring list_scam_flags.
    Exactly one of rfa.freelancer_id / rfa.client_id is set (enforced by
    red_flag_alerts_one_subject_check), so both sides can be LEFT JOINed
    unconditionally and coalesced - no subject_type predicate in the join.

    Triage payload; the diagnosis for one alert comes from get_red_flag_detail().
    Each row does carry `open_held_reviews` - how many of that subject's reviews
    are currently held for moderation - because an alert on a subject with a held
    review usually needs that review ruled on first, and the queue should be able
    to show that without opening every alert.

    Returns a _paged envelope, NOT a bare list.
    """
    offset    = (page - 1) * page_size
    sort_col  = _RED_FLAG_SORT_COLS.get(sort_by, "rfa.triggered_at")
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    filters = """
        WHERE (:is_resolved IS NULL OR rfa.is_resolved = :is_resolved)
          AND (:subject_type = 'all' OR rfa.subject_type = :subject_type)
    """
    base_params = {"is_resolved": is_resolved, "subject_type": subject_type}

    total_row = _row(get_db().execute_query(
        f"SELECT COUNT(*) AS total FROM red_flag_alerts rfa {filters}",
        params=base_params,
    )) or {"total": 0}

    items = _rows(get_db().execute_query(
        f"""
        SELECT rfa.*,
               COALESCE(f.full_name, c.full_name)   AS subject_name,
               COALESCE(fu.email, cu.email)         AS subject_email,
               COALESCE(fts.overall_score, cts.trust_score) AS current_trust_score,
               COALESCE(fts.total_reviews, cts.total_reviews_received) AS subject_total_reviews,
               COALESCE(hr.held, hcr.held, 0)       AS open_held_reviews
        FROM red_flag_alerts rfa
        LEFT JOIN freelancer f  ON f.freelancer_id = rfa.freelancer_id
        LEFT JOIN users      fu ON fu.user_id      = f.user_id
        LEFT JOIN client     c  ON c.client_id     = rfa.client_id
        LEFT JOIN users      cu ON cu.user_id      = c.user_id
        LEFT JOIN freelancer_trust_scores fts ON fts.freelancer_id = rfa.freelancer_id
        LEFT JOIN client_trust_score      cts ON cts.client_id     = rfa.client_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS held FROM reviews r
            WHERE r.freelancer_id = rfa.freelancer_id
              AND r.status IN ('flagged', 'suppressed')
        ) hr ON rfa.freelancer_id IS NOT NULL
        -- Client subjects are held in a different table entirely; without this the
        -- triage badge read 0 for every client alert while the detail view found
        -- held reviews, which is worse than showing nothing.
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS held FROM client_reviews cr2
            WHERE cr2.client_id = rfa.client_id
              AND cr2.status IN ('flagged', 'suppressed')
        ) hcr ON rfa.client_id IS NOT NULL
        {filters}
        ORDER BY {sort_col} {direction}
        LIMIT :limit OFFSET :offset
        """,
        params={**base_params, "limit": page_size, "offset": offset},
    ))

    now = datetime.now(timezone.utc)
    for item in items:
        triggered = item.get("triggered_at")
        # Age matters for triage: an unresolved reputation alert sitting for days
        # is a different priority from one raised minutes ago.
        item["age_hours"] = (
            round((now - triggered).total_seconds() / 3600, 1)
            if isinstance(triggered, datetime) and triggered.tzinfo else None
        )

    return _paged(items, int(total_row["total"]), page, page_size)

@lru_cache(maxsize=32)
def _has_column(table: str, column: str) -> bool:
    """Whether a column exists, cached for the process lifetime.

    The schema lives in a separate DATABASE repo, so a checkout of this backend
    can legitimately be newer than the database it is pointed at. red_flag_alerts
    gained resolved_by/resolution_note after this code shipped; without this guard
    a teammate who has not run the migration gets a 500 on every resolve instead
    of a working endpoint that simply does not record the note.
    """
    row = _row(get_db().execute_query(
        """
        SELECT 1 AS present FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        """,
        params={"t": table, "c": column},
    ))
    return bool(row)


def resolve_red_flag_alert(alert_id: str, admin_user_id: str,
                           note: Optional[str] = None) -> Optional[Dict]:
    """Close an alert, recording who closed it and why.

    A red flag is a claim that something went wrong with a person's reputation.
    Closing one without a stated reason leaves no way to tell "investigated, the
    drop is legitimate" apart from "clicked to clear the badge", which are
    opposite conclusions about the same subject.
    """
    records_resolution = (_has_column("red_flag_alerts", "resolved_by")
                          and _has_column("red_flag_alerts", "resolution_note"))

    if records_resolution:
        sql = """
            UPDATE red_flag_alerts
            SET is_resolved = TRUE, resolved_at = NOW(),
                resolved_by = :admin, resolution_note = :note
            WHERE id = :aid AND is_resolved = FALSE
            RETURNING *
        """
        params = {"aid": alert_id, "admin": admin_user_id, "note": note}
    else:
        # Pre-migration database: still resolve, but say so rather than pretending
        # the note was stored.
        logger("ADMIN", "red_flag_alerts is missing resolved_by/resolution_note - "
                        "resolution recorded without attribution. Run the migration.",
               level="WARNING")
        sql = """
            UPDATE red_flag_alerts
            SET is_resolved = TRUE, resolved_at = NOW()
            WHERE id = :aid AND is_resolved = FALSE
            RETURNING *
        """
        params = {"aid": alert_id}

    updated = _row(get_db().execute_query(sql, params=params))
    if updated:
        updated["resolution_recorded"] = records_resolution
        logger("ADMIN", f"Red flag {alert_id} resolved by {admin_user_id}", level="INFO")
    return updated


def get_red_flag_detail(alert_id: str) -> Optional[Dict]:
    """Everything needed to act on one red flag alert.

    The alert row itself only says a trust score fell - "dropped by 12.7 points
    (from 82.2 to 69.5)". That is a symptom with no diagnosis attached: it names
    no component, no cause, and no reviews. An admin reading only the message
    cannot tell a genuine decline from a single retaliatory review, which are the
    two cases the alert exists to separate.

    So this assembles the diagnosis:
      * the trust-score trajectory, not just the two endpoints
      * the CURRENT component breakdown, so the admin can see which input fell
      * the reviews that landed in the drop window - the actual cause
      * whether any of those reviews are themselves held for moderation, which is
        the case that matters most: a trust drop driven by a review the pipeline
        already distrusts should usually be resolved by ruling on that review
        first, not by clearing the flag.
    """
    alert = _row(get_db().execute_query(
        """
        SELECT rfa.*,
               COALESCE(f.full_name, c.full_name) AS subject_name,
               COALESCE(fu.email, cu.email)       AS subject_email,
               COALESCE(fu.user_id, cu.user_id)   AS subject_user_id,
               au.email                           AS resolved_by_email
        FROM red_flag_alerts rfa
        LEFT JOIN freelancer f  ON f.freelancer_id = rfa.freelancer_id
        LEFT JOIN users      fu ON fu.user_id      = f.user_id
        LEFT JOIN client     c  ON c.client_id     = rfa.client_id
        LEFT JOIN users      cu ON cu.user_id      = c.user_id
        LEFT JOIN users      au ON au.user_id      = rfa.resolved_by
        WHERE rfa.id = :aid
        """
        if _has_column("red_flag_alerts", "resolved_by") else
        """
        SELECT rfa.*,
               COALESCE(f.full_name, c.full_name) AS subject_name,
               COALESCE(fu.email, cu.email)       AS subject_email,
               COALESCE(fu.user_id, cu.user_id)   AS subject_user_id
        FROM red_flag_alerts rfa
        LEFT JOIN freelancer f  ON f.freelancer_id = rfa.freelancer_id
        LEFT JOIN users      fu ON fu.user_id      = f.user_id
        LEFT JOIN client     c  ON c.client_id     = rfa.client_id
        LEFT JOIN users      cu ON cu.user_id      = c.user_id
        WHERE rfa.id = :aid
        """,
        params={"aid": alert_id},
    ))
    if not alert:
        return None

    is_freelancer = alert.get("subject_type") == "freelancer"
    subject_id = str(alert["freelancer_id"] if is_freelancer else alert["client_id"])
    triggered_at = alert["triggered_at"]

    if is_freelancer:
        components = _row(get_db().execute_query(
            """
            SELECT overall_score, weighted_review_avg, effective_review_avg, display_star_avg,
                   on_time_score, revision_rate_score, responsiveness_score,
                   communication_sentiment, authenticity_confidence, consistency_score,
                   total_reviews, category, category_rank_pct, last_updated
            FROM freelancer_trust_scores WHERE freelancer_id = :sid
            """,
            params={"sid": subject_id},
        ))
        history = _rows(get_db().execute_query(
            """
            SELECT overall_score AS score, snapshot_reason, recorded_at
            FROM trust_score_history WHERE freelancer_id = :sid
            ORDER BY recorded_at DESC LIMIT 12
            """,
            params={"sid": subject_id},
        ))[::-1]
        # Reviews ABOUT this freelancer that landed in the drop window. The window
        # opens at the previous snapshot, because that is the interval the alert
        # compared - anything older was already priced into the earlier score.
        window = _rows(get_db().execute_query(
            """
            SELECT r.id, r.status, r.created_at, r.published_at,
                   cl.full_name AS reviewer_name,
                   wc.overall_comment,
                   ra.authenticity_score, ra.sentiment_label, ra.sentiment_mismatch,
                   ra.disagreement_probability, ra.is_flagged_fake, ra.is_flagged_coerced,
                   ra.overall_pass,
                   rt.avg_stars
            FROM reviews r
            LEFT JOIN client cl ON cl.client_id = r.reviewer_id
            LEFT JOIN review_written_content wc ON wc.review_id = r.id
            LEFT JOIN review_ai_analysis     ra ON ra.review_id = r.id
            LEFT JOIN LATERAL (
                SELECT ROUND(AVG(score), 3) AS avg_stars
                FROM review_ratings WHERE review_id = r.id
            ) rt ON TRUE
            WHERE r.freelancer_id = :sid
              AND r.created_at <= :triggered
            ORDER BY r.created_at DESC
            LIMIT 10
            """,
            params={"sid": subject_id, "triggered": triggered_at},
        ))
    else:
        components = _row(get_db().execute_query(
            """
            SELECT trust_score AS overall_score, weighted_review_avg_received,
                   effective_review_avg_received, responsiveness_score,
                   communication_sentiment, authenticity_confidence, consistency_score,
                   dispute_fairness_score, total_reviews_received, updated_at AS last_updated
            FROM client_trust_score WHERE client_id = :sid
            """,
            params={"sid": subject_id},
        ))
        history = _rows(get_db().execute_query(
            """
            SELECT trust_score AS score, snapshot_reason, recorded_at
            FROM client_trust_score_history WHERE client_id = :sid
            ORDER BY recorded_at DESC LIMIT 12
            """,
            params={"sid": subject_id},
        ))[::-1]
        window = _rows(get_db().execute_query(
            """
            SELECT cr.id, cr.status, cr.created_at, cr.published_at,
                   fr.full_name AS reviewer_name,
                   wc.overall_comment,
                   cra.authenticity_score, cra.sentiment_label, cra.sentiment_mismatch,
                   cra.disagreement_probability, cra.is_flagged_fake, cra.is_flagged_coerced,
                   cra.overall_pass,
                   rt.avg_stars
            FROM client_reviews cr
            LEFT JOIN freelancer fr ON fr.freelancer_id = cr.reviewer_id
            LEFT JOIN client_review_written_content wc ON wc.client_review_id = cr.id
            LEFT JOIN client_review_ai_analysis     cra ON cra.client_review_id = cr.id
            LEFT JOIN LATERAL (
                SELECT ROUND(AVG(score), 3) AS avg_stars
                FROM client_review_ratings WHERE client_review_id = cr.id
            ) rt ON TRUE
            WHERE cr.client_id = :sid
              AND cr.created_at <= :triggered
            ORDER BY cr.created_at DESC
            LIMIT 10
            """,
            params={"sid": subject_id, "triggered": triggered_at},
        ))

    held = [r for r in window if r.get("status") in ("flagged", "suppressed")]
    # Published despite failing the gate: the signature of an admin override, since
    # the pipeline never publishes overall_pass=false on its own. These are the most
    # likely cause of a trust drop that looks inexplicable from the score alone -
    # a human let a review through and it moved the subject's reputation.
    overridden = [r for r in window
                  if r.get("status") == "published" and r.get("overall_pass") is False]

    # The two endpoints the alert message quotes, recovered from the history so the
    # UI can plot the drop rather than re-parsing prose out of `message`.
    drop = None
    if len(history) >= 2:
        previous, latest = history[-2], history[-1]
        try:
            drop = {
                "from": float(previous["score"]),
                "to": float(latest["score"]),
                "delta": round(float(latest["score"]) - float(previous["score"]), 2),
                "from_recorded_at": previous["recorded_at"],
                "to_recorded_at": latest["recorded_at"],
            }
        except (TypeError, ValueError):
            drop = None

    return {
        "alert": alert,
        "subject": {
            "subject_type": alert.get("subject_type"),
            "subject_id": subject_id,
            "name": alert.get("subject_name"),
            "email": alert.get("subject_email"),
            "user_id": alert.get("subject_user_id"),
        },
        "current_components": components,
        "score_history": history,
        "drop": drop,
        "recent_reviews": window,
        # Surfaced separately because it changes the recommended action: rule on
        # the held review before deciding whether the trust drop is real.
        "held_reviews_in_window": held,
        "held_review_count": len(held),
        "overridden_reviews_in_window": overridden,
        "overridden_review_count": len(overridden),
        "other_open_alerts": _rows(get_db().execute_query(
            """
            SELECT id, alert_type, severity, message, triggered_at
            FROM red_flag_alerts
            WHERE is_resolved = FALSE AND id <> :aid
              AND ((:is_fl AND freelancer_id = :sid) OR (NOT :is_fl AND client_id = :sid))
            ORDER BY triggered_at DESC
            """,
            params={"aid": alert_id, "sid": subject_id, "is_fl": is_freelancer},
        )),
    }

_MODERATION_SORT_COLS = {
    "created_at":   "r.created_at",
    "status":       "r.status",
    "authenticity": "ra.authenticity_score",
    "disagreement": "ra.disagreement_probability",
}

# Held reviews carrying this reason were never actually judged - the LLM was
# unreachable and analyse failed closed. The admin view has to say so, because a
# scorecard of nulls otherwise reads as "every model scored this badly".
_ANALYSIS_UNAVAILABLE_MARKER = "Automated analysis unavailable"


def _paged(items: List[Dict], total: int, page: int, page_size: int) -> Dict:
    """Envelope for admin lists. A bare array cannot express how many rows the
    filter matched, so the UI could never render 'page 1 of n' or an accurate
    queue badge."""
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total / page_size)) if total else 0,
    }


def _analysis_unavailable(flag_reasons) -> bool:
    reasons = flag_reasons or []
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except json.JSONDecodeError:
            return _ANALYSIS_UNAVAILABLE_MARKER.lower() in reasons.lower()
    return any(_ANALYSIS_UNAVAILABLE_MARKER.lower() in str(r).lower() for r in reasons)


def _ratings_for(table: str, id_column: str, record_id: str) -> Dict:
    """Per-category stars plus their average.

    The star rating is the single most decision-relevant fact for a held review -
    most holds ARE a rating-vs-text contradiction - and it lived in a separate
    table that the admin queue never joined. The flag reason would say "star
    rating of 5 contradicts clearly negative review text" while the payload
    carried no rating at all, leaving the admin to take the model's word for the
    one thing they were meant to check.
    """
    rows = _rows(get_db().execute_query(
        f"SELECT category, score FROM {table} WHERE {id_column} = :rid ORDER BY category",
        params={"rid": record_id},
    ))
    scores = [float(r["score"]) for r in rows if r.get("score") is not None]
    return {
        "categories": [{"category": r["category"], "score": float(r["score"])} for r in rows],
        "average": round(sum(scores) / len(scores), 3) if scores else None,
        "count": len(scores),
    }


def _component_breakdown(review_id: str) -> Optional[Dict]:
    """Per-model verdicts from the judgment log.

    review_ai_analysis persists the BLENDED authenticity score (0.4 LLM + 0.4
    classifier + 0.2 answer groundedness) and nothing about its inputs, so from
    the database alone an admin cannot tell which component objected - or whether
    the two disagreed, which is exactly the adjudication being asked of them.
    Optional by construction: returns None when no record exists.
    """
    record = read_latest_judgment(review_id)
    if not record:
        return None

    llm = record.get("llm") or {}
    ml = record.get("ml") or {}
    authenticity = ml.get("authenticity") or {}
    sentiment = ml.get("sentiment") or {}
    mismatch = ml.get("mismatch") or {}

    llm_fake = llm.get("is_flagged_fake")
    ml_fake = authenticity.get("is_likely_fake")
    llm_mismatch = llm.get("sentiment_mismatch")
    ml_mismatch = mismatch.get("is_mismatched")

    return {
        "llm": {
            "authenticity_score": llm.get("authenticity_score"),
            "is_flagged_fake": llm_fake,
            "is_flagged_coerced": llm.get("is_flagged_coerced"),
            "sentiment_mismatch": llm_mismatch,
            "answer_groundedness": llm.get("answer_groundedness"),
            "communication_quality_score": llm.get("communication_quality_score"),
            "analysis_unavailable": llm.get("analysis_unavailable"),
        },
        "sentiment_model": {
            "label": sentiment.get("sentiment_label"),
            "score": sentiment.get("sentiment_score"),
            # "cardiff_roberta" is the pretrained primary; an "sbert_" prefix means
            # it fell back to the weaker retired model and the score is less trustworthy.
            "model_used": sentiment.get("model_used"),
        },
        "authenticity_model": {
            "fake_probability": authenticity.get("fake_probability"),
            # Length-neutral. The raw score penalises short reviews ~7x more often,
            # so the calibrated figure is the fair one to read.
            "fake_probability_calibrated": authenticity.get("fake_probability_calibrated"),
            "is_likely_fake": ml_fake,
            "threshold": 0.75,
            "model_used": authenticity.get("model_used"),
        },
        "disagreement_model": {
            "disagreement_probability": mismatch.get("disagreement_probability"),
            "is_mismatched": ml_mismatch,
            "threshold": 0.5,
            "model_used": mismatch.get("model_used"),
        },
        # Surfaced as structured fields rather than left buried in flag_reasons
        # prose, because "which of the two objected" is the whole question.
        "disagreements": {
            "fake": (llm_fake is not None and ml_fake is not None and llm_fake != ml_fake),
            "mismatch": (llm_mismatch is not None and ml_mismatch is not None
                         and llm_mismatch != ml_mismatch),
        },
        "logged_at": record.get("logged_at"),
    }


def _contract_telemetry(contract_id: str) -> Optional[Dict]:
    """Objective contract record for the engagement under review.

    Half the LLM's flag reasons cite these numbers ("platform metrics show
    on-time delivery but reviewer describes missed deadlines"), and without them
    in the payload the admin cannot check whether the claim is true - a real
    failure mode, since the LLM has been observed asserting prompt communication
    on a contract whose measured responsiveness was 0.062.

    None-valued fields mean the data was never recorded, NOT zero. calculate_trust_score
    drops those components and renormalises; the UI must render them as
    "not measured" rather than as an empty bar.
    """
    row = _row(get_db().execute_query(
        """
        SELECT fps.on_time_score, fps.revision_count, fps.revision_rate_score,
               fps.responsiveness_score, fps.communication_sentiment_score,
               fps.conflict_score, fps.communication_summary,
               c.start_date, c.end_date, c.original_end_date, c.actual_completion_date,
               c.contract_title, c.status AS contract_status
        FROM contract c
        LEFT JOIN freelancer_performance_scores fps ON fps.contract_id = c.contract_id
        WHERE c.contract_id = :cid
        """,
        params={"cid": contract_id},
    ))
    if not row:
        return None
    row["on_time_measurable"] = bool(
        row.get("actual_completion_date") and (row.get("original_end_date") or row.get("end_date"))
    )
    return row


def _dm_excerpt(contract_id: str, limit: int = 20) -> List[Dict]:
    """Tail of the contract's DM thread.

    The LLM reads this thread to score communication quality and cites it in flag
    reasons; the admin had no way to see it. Returns [] when no thread is bound -
    which happens legitimately for a repeat client/freelancer pair, since
    dm_thread is UNIQUE per user pair and stays bound to the first contract.
    """
    return _rows(get_db().execute_query(
        """
        SELECT m.sender_id, m.message_text, m.sent_at
        FROM dm_message m
        JOIN dm_thread t ON t.thread_id = m.thread_id
        WHERE t.contract_id = :cid
        ORDER BY m.sent_at DESC
        LIMIT :limit
        """,
        params={"cid": contract_id, "limit": limit},
    ))[::-1]


def _client_reviewer_context(client_id: str, exclude_review_id: str) -> Dict:
    """History of the client writing this review.

    Coercion and retaliation are patterns across a reviewer's history, not
    properties of a single review, so one review in isolation cannot show them.
    """
    counts = _row(get_db().execute_query(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status IN ('flagged', 'suppressed')) AS held,
               COUNT(*) FILTER (WHERE status = 'published') AS published
        FROM reviews WHERE reviewer_id = :cid AND id <> :rid
        """,
        params={"cid": client_id, "rid": exclude_review_id},
    )) or {}
    profile = _row(get_db().execute_query(
        """
        SELECT c.full_name, u.email, cts.trust_score, cts.total_reviews_received
        FROM client c
        LEFT JOIN users u ON u.user_id = c.user_id
        LEFT JOIN client_trust_score cts ON cts.client_id = c.client_id
        WHERE c.client_id = :cid
        """,
        params={"cid": client_id},
    )) or {}
    return {**profile, "prior_reviews_written": counts}


def _freelancer_reviewer_context(freelancer_id: str, exclude_review_id: str) -> Dict:
    """History of the freelancer writing this client review."""
    counts = _row(get_db().execute_query(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status IN ('flagged', 'suppressed')) AS held,
               COUNT(*) FILTER (WHERE status = 'published') AS published
        FROM client_reviews WHERE reviewer_id = :fid AND id <> :rid
        """,
        params={"fid": freelancer_id, "rid": exclude_review_id},
    )) or {}
    profile = _row(get_db().execute_query(
        """
        SELECT f.full_name, u.email, fts.overall_score AS trust_score, fts.total_reviews
        FROM freelancer f
        LEFT JOIN users u ON u.user_id = f.user_id
        LEFT JOIN freelancer_trust_scores fts ON fts.freelancer_id = f.freelancer_id
        WHERE f.freelancer_id = :fid
        """,
        params={"fid": freelancer_id},
    )) or {}
    return {**profile, "prior_reviews_written": counts}


def list_flagged_reviews(
    status: str = "all",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    """Reviews held back from publishing (overall_pass=false), with the AI
    analysis that caused the hold, for manual admin review.

    Triage payload only - enough to sort and prioritise the queue. The full
    moderation record for a single review comes from
    get_review_moderation_detail(); loading telemetry, DM threads and per-model
    breakdowns for every row would make the queue expensive to no purpose.

    Returns a _paged envelope, NOT a bare list.
    """
    offset       = (page - 1) * page_size
    sort_col     = _MODERATION_SORT_COLS.get(sort_by, "r.created_at")
    direction    = "ASC" if sort_dir.lower() == "asc" else "DESC"
    status_filter = "r.status IN ('flagged', 'suppressed')" if status == "all" else "r.status = :status"

    total_row = _row(get_db().execute_query(
        f"SELECT COUNT(*) AS total FROM reviews r WHERE {status_filter}",
        params={"status": status},
    )) or {"total": 0}

    items = _rows(get_db().execute_query(
        f"""
        SELECT r.id, r.contract_id, r.freelancer_id, r.reviewer_id, r.status,
               r.inferred_category, r.created_at,
               f.full_name AS freelancer_name,
               cl.full_name AS reviewer_name,
               wc.overall_comment,
               ra.sentiment_score, ra.sentiment_label, ra.sentiment_mismatch, ra.disagreement_probability,
               ra.authenticity_score, ra.is_flagged_fake, ra.is_flagged_coerced, ra.flag_reasons,
               ra.overall_pass, ra.analyzed_at,
               rt.avg_stars, rt.rating_count
        FROM reviews r
        JOIN freelancer f ON f.freelancer_id = r.freelancer_id
        LEFT JOIN client cl ON cl.client_id = r.reviewer_id
        LEFT JOIN review_written_content wc ON wc.review_id = r.id
        LEFT JOIN review_ai_analysis     ra ON ra.review_id = r.id
        LEFT JOIN LATERAL (
            SELECT ROUND(AVG(score), 3) AS avg_stars, COUNT(*) AS rating_count
            FROM review_ratings WHERE review_id = r.id
        ) rt ON TRUE
        WHERE {status_filter}
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
        """,
        params={"status": status, "limit": page_size, "offset": offset},
    ))

    for item in items:
        reasons = item.get("flag_reasons") or []
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except json.JSONDecodeError:
                reasons = [reasons]
        item["flag_reason_count"] = len(reasons)
        item["analysis_unavailable"] = _analysis_unavailable(reasons)
        # 'suppressed' is the pipeline's high-confidence verdict, 'flagged' means
        # it wanted a human. Different severities, so the queue must not mix them.
        item["hold_level"] = item.get("status")

    return _paged(items, int(total_row["total"]), page, page_size)


def get_review_moderation_detail(review_id: str) -> Optional[Dict]:
    """Everything an admin needs to rule on one held freelancer review.

    Assembles what the queue deliberately leaves out: the star ratings the hold
    usually turns on, the targeted question with its answer (answer_groundedness
    is 20% of the blended authenticity score and unreadable without both), the
    objective contract record the LLM's reasons cite, the per-model breakdown,
    the reviewer's history, and the DM thread.
    """
    review = _row(get_db().execute_query(
        """
        SELECT r.id, r.contract_id, r.reviewer_id, r.freelancer_id, r.status,
               r.inferred_category, r.is_anonymous, r.created_at, r.published_at,
               f.full_name AS freelancer_name,
               wc.ai_question, wc.client_answer, wc.overall_comment,
               ra.sentiment_score, ra.sentiment_label, ra.sentiment_mismatch,
               ra.disagreement_probability, ra.authenticity_score, ra.is_flagged_fake,
               ra.is_flagged_coerced, ra.flag_reasons, ra.overall_pass, ra.analyzed_at
        FROM reviews r
        JOIN freelancer f ON f.freelancer_id = r.freelancer_id
        LEFT JOIN review_written_content wc ON wc.review_id = r.id
        LEFT JOIN review_ai_analysis     ra ON ra.review_id = r.id
        WHERE r.id = :rid
        """,
        params={"rid": review_id},
    ))
    if not review:
        return None

    return {
        "review_kind": "freelancer_review",
        "review": review,
        "hold_level": review.get("status"),
        "analysis_unavailable": _analysis_unavailable(review.get("flag_reasons")),
        "ratings": _ratings_for("review_ratings", "review_id", review_id),
        "components": _component_breakdown(review_id),
        "blend_weights": {"llm": 0.4, "authenticity_model": 0.4, "answer_groundedness": 0.2},
        "telemetry": _contract_telemetry(str(review["contract_id"])),
        "reviewer": _client_reviewer_context(str(review["reviewer_id"]), review_id),
        "dm_thread": _dm_excerpt(str(review["contract_id"])),
        "skill_tags": _rows(get_db().execute_query(
            "SELECT skill_tag, is_ai_suggested FROM review_skill_tags WHERE review_id = :rid",
            params={"rid": review_id},
        )),
    }

def _prior_review_snapshot(review_id: str) -> Optional[Dict]:
    return _row(get_db().execute_query(
        """
        SELECT r.status, ra.sentiment_score, ra.sentiment_label, ra.sentiment_mismatch,
               ra.disagreement_probability, ra.authenticity_score, ra.is_flagged_fake,
               ra.is_flagged_coerced, ra.flag_reasons, ra.overall_pass
        FROM reviews r
        LEFT JOIN review_ai_analysis ra ON ra.review_id = r.id
        WHERE r.id = :rid
        """,
        params={"rid": review_id},
    ))


async def uphold_review(review_id: str, admin_user_id: str,
                        reason: Optional[str] = None) -> Optional[Dict]:
    """Confirm the pipeline was right to hold this review.

    Moves 'flagged' to 'suppressed': the hold stops being a request for a human
    and becomes a final decision, which also clears it out of the pending queue
    without needing a new column. An already-suppressed review stays suppressed -
    the status does not change, but the ruling is still logged, because the label
    is the point.

    Deliberately symmetric with override_publish_review. An admin agreeing with
    the pipeline is as useful a training label as one reversing it, and capturing
    only reversals would build a dataset consisting entirely of pipeline errors.
    """
    prior = _prior_review_snapshot(review_id)
    if not prior:
        return None

    updated = _row(get_db().execute_query(
        """
        UPDATE reviews
        SET status = 'suppressed'
        WHERE id = :rid AND status IN ('flagged', 'suppressed')
        RETURNING *
        """,
        params={"rid": review_id},
    ))
    if not updated:
        return None

    logger("ADMIN", f"Review {review_id} hold upheld by {admin_user_id}", level="INFO")
    log_admin_override(
        review_id=review_id,
        review_kind="freelancer_review",
        admin_user_id=admin_user_id,
        action="uphold",
        prior_status=prior.get("status"),
        prior_analysis={k: v for k, v in prior.items() if k != "status"},
        reason=reason,
    )
    # No trust-score recalculation and no reviewer notification: nothing was
    # published, so no reputation input changed, and the reviewer was already told
    # the review was held when the pipeline held it.
    return updated


async def override_publish_review(review_id: str, admin_user_id: str,
                                  reason: Optional[str] = None) -> Optional[Dict]:
    # Captured BEFORE the update: an override is a human saying the pipeline got this
    # wrong, and the label only means something alongside the judgment being
    # reversed. These are the only true labels available for the publish decision
    # itself - every model in review_ml/ is otherwise trained on Amazon product
    # reviews. See ai_related/review_analysis/judgment_log.py.
    prior = _row(get_db().execute_query(
        """
        SELECT r.status, ra.sentiment_score, ra.sentiment_label, ra.sentiment_mismatch,
               ra.disagreement_probability, ra.authenticity_score, ra.is_flagged_fake,
               ra.is_flagged_coerced, ra.flag_reasons, ra.overall_pass
        FROM reviews r
        LEFT JOIN review_ai_analysis ra ON ra.review_id = r.id
        WHERE r.id = :rid
        """,
        params={"rid": review_id},
    ))

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

    log_admin_override(
        review_id=review_id,
        review_kind="freelancer_review",
        admin_user_id=admin_user_id,
        action="override_publish",
        prior_status=(prior or {}).get("status"),
        prior_analysis={k: v for k, v in (prior or {}).items() if k != "status"},
        reason=reason,
    )

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

_CLIENT_MODERATION_SORT_COLS = {
    "created_at":   "cr.created_at",
    "status":       "cr.status",
    "authenticity": "cra.authenticity_score",
    "disagreement": "cra.disagreement_probability",
}


def list_flagged_client_reviews(
    status: str = "all",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    """Client reviews (written by freelancers) held back from publishing -
    counterpart to list_flagged_reviews for the freelancer-reviews-client system.

    Same triage-only contract and same _paged envelope; full record comes from
    get_client_review_moderation_detail().
    """
    offset        = (page - 1) * page_size
    sort_col      = _CLIENT_MODERATION_SORT_COLS.get(sort_by, "cr.created_at")
    direction     = "ASC" if sort_dir.lower() == "asc" else "DESC"
    status_filter = "cr.status IN ('flagged', 'suppressed')" if status == "all" else "cr.status = :status"

    total_row = _row(get_db().execute_query(
        f"SELECT COUNT(*) AS total FROM client_reviews cr WHERE {status_filter}",
        params={"status": status},
    )) or {"total": 0}

    items = _rows(get_db().execute_query(
        f"""
        SELECT cr.id, cr.contract_id, cr.reviewer_id, cr.client_id, cr.status, cr.created_at,
               c.full_name AS client_name,
               fr.full_name AS reviewer_name,
               wc.overall_comment,
               cra.sentiment_score, cra.sentiment_label, cra.sentiment_mismatch, cra.disagreement_probability,
               cra.authenticity_score, cra.is_flagged_fake, cra.is_flagged_coerced, cra.flag_reasons,
               cra.overall_pass, cra.analyzed_at,
               rt.avg_stars, rt.rating_count
        FROM client_reviews cr
        JOIN client c ON c.client_id = cr.client_id
        LEFT JOIN freelancer fr ON fr.freelancer_id = cr.reviewer_id
        LEFT JOIN client_review_written_content wc ON wc.client_review_id = cr.id
        LEFT JOIN client_review_ai_analysis     cra ON cra.client_review_id = cr.id
        LEFT JOIN LATERAL (
            SELECT ROUND(AVG(score), 3) AS avg_stars, COUNT(*) AS rating_count
            FROM client_review_ratings WHERE client_review_id = cr.id
        ) rt ON TRUE
        WHERE {status_filter}
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
        """,
        params={"status": status, "limit": page_size, "offset": offset},
    ))

    for item in items:
        reasons = item.get("flag_reasons") or []
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except json.JSONDecodeError:
                reasons = [reasons]
        item["flag_reason_count"] = len(reasons)
        item["analysis_unavailable"] = _analysis_unavailable(reasons)
        item["hold_level"] = item.get("status")

    return _paged(items, int(total_row["total"]), page, page_size)


def get_client_review_moderation_detail(client_review_id: str) -> Optional[Dict]:
    """Everything an admin needs to rule on one held client review.

    Differs from the freelancer side in two ways the UI has to respect:
      * four rating categories, not five - a client review does not rate
        `timeliness`, because that is the freelancer's own delivery.
      * the objective counterpart is the CLIENT's lifetime trust components, not
        this contract's telemetry, because compute_client_responsiveness_score
        aggregates across all of that client's contracts. Both are returned, and
        `subject_lifetime_scores` is the one the ratings should be read against;
        `telemetry` is engagement context only.
    """
    review = _row(get_db().execute_query(
        """
        SELECT cr.id, cr.contract_id, cr.reviewer_id, cr.client_id, cr.status,
               cr.is_anonymous, cr.created_at, cr.published_at,
               c.full_name AS client_name,
               wc.ai_question, wc.freelancer_answer, wc.overall_comment,
               cra.sentiment_score, cra.sentiment_label, cra.sentiment_mismatch,
               cra.disagreement_probability, cra.authenticity_score, cra.is_flagged_fake,
               cra.is_flagged_coerced, cra.flag_reasons, cra.overall_pass, cra.analyzed_at
        FROM client_reviews cr
        JOIN client c ON c.client_id = cr.client_id
        LEFT JOIN client_review_written_content wc ON wc.client_review_id = cr.id
        LEFT JOIN client_review_ai_analysis     cra ON cra.client_review_id = cr.id
        WHERE cr.id = :rid
        """,
        params={"rid": client_review_id},
    ))
    if not review:
        return None

    subject = _row(get_db().execute_query(
        """
        SELECT trust_score, responsiveness_score, communication_sentiment,
               authenticity_confidence, consistency_score, dispute_fairness_score,
               total_reviews_received
        FROM client_trust_score WHERE client_id = :cid
        """,
        params={"cid": str(review["client_id"])},
    ))

    return {
        "review_kind": "client_review",
        "review": review,
        "hold_level": review.get("status"),
        "analysis_unavailable": _analysis_unavailable(review.get("flag_reasons")),
        "ratings": _ratings_for("client_review_ratings", "client_review_id", client_review_id),
        "components": _component_breakdown(client_review_id),
        "blend_weights": {"llm": 0.4, "authenticity_model": 0.4, "answer_groundedness": 0.2},
        "telemetry": _contract_telemetry(str(review["contract_id"])),
        "subject_lifetime_scores": subject,
        "reviewer": _freelancer_reviewer_context(str(review["reviewer_id"]), client_review_id),
        "dm_thread": _dm_excerpt(str(review["contract_id"])),
    }


async def uphold_client_review(client_review_id: str, admin_user_id: str,
                               reason: Optional[str] = None) -> Optional[Dict]:
    """Confirm the pipeline was right to hold this client review - see uphold_review."""
    prior = _row(get_db().execute_query(
        """
        SELECT cr.status, cra.sentiment_score, cra.sentiment_label, cra.sentiment_mismatch,
               cra.disagreement_probability, cra.authenticity_score, cra.is_flagged_fake,
               cra.is_flagged_coerced, cra.flag_reasons, cra.overall_pass
        FROM client_reviews cr
        LEFT JOIN client_review_ai_analysis cra ON cra.client_review_id = cr.id
        WHERE cr.id = :rid
        """,
        params={"rid": client_review_id},
    ))
    if not prior:
        return None

    updated = _row(get_db().execute_query(
        """
        UPDATE client_reviews
        SET status = 'suppressed'
        WHERE id = :rid AND status IN ('flagged', 'suppressed')
        RETURNING *
        """,
        params={"rid": client_review_id},
    ))
    if not updated:
        return None

    logger("ADMIN", f"Client review {client_review_id} hold upheld by {admin_user_id}", level="INFO")
    log_admin_override(
        review_id=client_review_id,
        review_kind="client_review",
        admin_user_id=admin_user_id,
        action="uphold",
        prior_status=prior.get("status"),
        prior_analysis={k: v for k, v in prior.items() if k != "status"},
        reason=reason,
    )
    return updated


async def override_publish_client_review(client_review_id: str, admin_user_id: str,
                                         reason: Optional[str] = None) -> Optional[Dict]:
    # Captured before the update - see override_publish_review.
    prior = _row(get_db().execute_query(
        """
        SELECT cr.status, cra.sentiment_score, cra.sentiment_label, cra.sentiment_mismatch,
               cra.disagreement_probability, cra.authenticity_score, cra.is_flagged_fake,
               cra.is_flagged_coerced, cra.flag_reasons, cra.overall_pass
        FROM client_reviews cr
        LEFT JOIN client_review_ai_analysis cra ON cra.client_review_id = cr.id
        WHERE cr.id = :rid
        """,
        params={"rid": client_review_id},
    ))

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

    log_admin_override(
        review_id=client_review_id,
        review_kind="client_review",
        admin_user_id=admin_user_id,
        action="override_publish",
        prior_status=(prior or {}).get("status"),
        prior_analysis={k: v for k, v in (prior or {}).items() if k != "status"},
        reason=reason,
    )

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
