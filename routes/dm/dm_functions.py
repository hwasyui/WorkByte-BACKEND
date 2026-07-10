import os
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import uuid


def _job_pitch_default(job_title: str) -> str:
    return (
        f'Hi! I came across your profile and have a project, "{job_title}", '
        f"that I think would be a great match for your skills. "
        f"I'd love to chat about it. Let me know if you're interested!"
    )

def _contract_accepted_default(role_title: str, contract_title: str) -> str:
    return (
        f"Hi! I've reviewed your proposal for \"{contract_title}\" ({role_title}) "
        f"and I'm excited to move forward. Looking forward to collaborating with you!"
    )


def _canonical(uid_1: str, uid_2: str) -> Tuple[str, str]:
    """Return (user_a, user_b) with user_a < user_b (UUID string order)."""
    return (uid_1, uid_2) if str(uid_1) < str(uid_2) else (uid_2, uid_1)


def _to_str(val) -> Optional[str]:
    return str(val) if val is not None else None


def _parse_meta(raw) -> Optional[Dict]:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


def _get_user_profile(user_id: Optional[str]) -> Dict:
    """Return minimal display info for any user_id. A None user_id means that side of
    the thread/message has been anonymized (the account was deleted while the other
    party kept theirs) - shown as a "Deleted User" placeholder, not a raw null."""
    if user_id is None:
        return {
            "user_id": None, "full_name": "Deleted User", "profile_picture_url": None,
            "role": "user", "freelancer_id": None, "client_id": None,
        }
    db = get_db()
    rows = db.execute_query(
        """
        SELECT u.user_id,
               COALESCE(f.full_name, c.full_name, u.email)        AS full_name,
               COALESCE(f.profile_picture_url, c.profile_picture_url) AS profile_picture_url,
               CASE
                   WHEN f.user_id IS NOT NULL AND c.user_id IS NOT NULL THEN 'dual'
                   WHEN f.user_id IS NOT NULL THEN 'freelancer'
                   WHEN c.user_id IS NOT NULL THEN 'client'
                   ELSE 'user'
               END AS role,
               f.freelancer_id,
               c.client_id
        FROM users u
        LEFT JOIN freelancer f ON f.user_id = u.user_id
        LEFT JOIN client     c ON c.user_id = u.user_id
        WHERE u.user_id = :uid
        """,
        {"uid": user_id},
    )
    if not rows:
        return {"user_id": user_id, "full_name": None, "profile_picture_url": None, "role": "user"}
    r = dict(rows[0])
    return {
        "user_id":             _to_str(r["user_id"]),
        "full_name":           r["full_name"],
        "profile_picture_url": r["profile_picture_url"],
        "role":                r["role"],
        "freelancer_id":       _to_str(r["freelancer_id"]),
        "client_id":           _to_str(r["client_id"]),
    }


def _get_job_post_info(job_post_id: str) -> Optional[Dict]:
    db = get_db()
    rows = db.execute_query(
        "SELECT job_post_id, job_title FROM job_post WHERE job_post_id = :id",
        {"id": job_post_id},
    )
    if not rows:
        return None
    r = dict(rows[0])
    return {"job_post_id": _to_str(r["job_post_id"]), "job_title": r["job_title"]}


def _get_dm_attachments(dm_message_id: str) -> List[Dict]:
    db = get_db()
    rows = db.execute_query(
        """
        SELECT attachment_id, dm_message_id, file_name, file_url,
               file_type, mime_type, file_size_bytes, duration_seconds, created_at
        FROM dm_message_attachment
        WHERE dm_message_id = :id
        ORDER BY created_at ASC
        """,
        {"id": dm_message_id},
    )
    return [dict(r) for r in rows] if rows else []


def _enrich_message(row: dict) -> Dict:
    m = dict(row)
    m["dm_message_id"] = _to_str(m.get("dm_message_id"))
    m["thread_id"]     = _to_str(m.get("thread_id"))
    m["sender_id"]     = _to_str(m.get("sender_id"))
    m["metadata"]      = _parse_meta(m.get("metadata"))
    m["status"]        = "read" if m.get("is_read") else "sent"
    m["attachments"]   = _get_dm_attachments(m["dm_message_id"])
    return m


def _enrich_thread(row: dict, current_user_id: str) -> Dict:
    t = dict(row)
    t["thread_id"]    = _to_str(t.get("thread_id"))
    t["user_a_id"]    = _to_str(t.get("user_a_id"))
    t["user_b_id"]    = _to_str(t.get("user_b_id"))
    t["initiator_id"] = _to_str(t.get("initiator_id"))
    other_id = t["user_b_id"] if t["user_a_id"] == current_user_id else t["user_a_id"]
    t["other_user"]   = _get_user_profile(other_id)
    if t.get("job_post_id"):
        t["job_post"] = _get_job_post_info(_to_str(t["job_post_id"]))
    else:
        t["job_post"] = None
    return t


class DMFunctions:

    @staticmethod
    def get_thread_by_users(uid_1: str, uid_2: str) -> Optional[Dict]:
        a, b = _canonical(uid_1, uid_2)
        db   = get_db()
        rows = db.execute_query(
            "SELECT * FROM dm_thread WHERE user_a_id = :a AND user_b_id = :b",
            {"a": a, "b": b},
        )
        if not rows:
            return None
        return {k: _to_str(v) if hasattr(v, '__class__') and 'UUID' in type(v).__name__ else v
                for k, v in dict(rows[0]).items()}

    @staticmethod
    def get_thread_by_contract_id(contract_id: str) -> Optional[Dict]:
        db   = get_db()
        rows = db.execute_query(
            "SELECT * FROM dm_thread WHERE contract_id = :cid",
            {"cid": contract_id},
        )
        if not rows:
            return None
        return {k: _to_str(v) if hasattr(v, '__class__') and 'UUID' in type(v).__name__ else v
                for k, v in dict(rows[0]).items()}

    @staticmethod
    def get_thread_by_id(thread_id: str) -> Optional[Dict]:
        db   = get_db()
        rows = db.execute_query(
            "SELECT * FROM dm_thread WHERE thread_id = :id",
            {"id": thread_id},
        )
        if not rows:
            return None
        return {k: _to_str(v) if hasattr(v, '__class__') and 'UUID' in type(v).__name__ else v
                for k, v in dict(rows[0]).items()}

    @staticmethod
    def is_blocked_between(uid_1: str, uid_2: str) -> bool:
        """True if either user has blocked the other - blocking is one user's action
        but stops messaging in both directions."""
        rows = get_db().execute_query(
            """
            SELECT 1 FROM user_blocks
            WHERE (blocker_user_id = :a AND blocked_user_id = :b)
               OR (blocker_user_id = :b AND blocked_user_id = :a)
            LIMIT 1
            """,
            {"a": uid_1, "b": uid_2},
        )
        return bool(rows)

    @staticmethod
    def has_ongoing_contract_between(uid_1: str, uid_2: str) -> bool:
        """True if these two users share a contract that isn't finished yet
        (active/under_review/revision_requested/disputed). Used to stop block
        from being used to dodge a live business obligation - see
        POST /dm/block: blocking a current contract counterparty is refused,
        the appeal path (with optional proof) is the way to escalate instead."""
        rows = get_db().execute_query(
            """
            SELECT 1
            FROM contract c
            JOIN freelancer f ON f.freelancer_id = c.freelancer_id
            JOIN client cl    ON cl.client_id     = c.client_id
            WHERE c.status IN ('active', 'under_review', 'revision_requested', 'disputed')
              AND (
                (f.user_id = :a AND cl.user_id = :b)
                OR (f.user_id = :b AND cl.user_id = :a)
              )
            LIMIT 1
            """,
            {"a": uid_1, "b": uid_2},
        )
        return bool(rows)

    @staticmethod
    def block_user(blocker_user_id: str, blocked_user_id: str) -> Dict:
        block_id = str(uuid.uuid4())
        get_db().execute_query(
            """
            INSERT INTO user_blocks (block_id, blocker_user_id, blocked_user_id)
            VALUES (:id, :blocker, :blocked)
            ON CONFLICT (blocker_user_id, blocked_user_id) DO NOTHING
            """,
            {"id": block_id, "blocker": blocker_user_id, "blocked": blocked_user_id},
        )
        return {"blocker_user_id": blocker_user_id, "blocked_user_id": blocked_user_id}

    @staticmethod
    def unblock_user(blocker_user_id: str, blocked_user_id: str) -> bool:
        rows = get_db().execute_query(
            """
            DELETE FROM user_blocks
            WHERE blocker_user_id = :blocker AND blocked_user_id = :blocked
            RETURNING block_id
            """,
            {"blocker": blocker_user_id, "blocked": blocked_user_id},
        )
        return bool(rows)

    @staticmethod
    def get_blocked_users(blocker_user_id: str) -> List[Dict]:
        rows = get_db().execute_query(
            """
            SELECT ub.blocked_user_id, ub.created_at,
                   COALESCE(f.full_name, cl.full_name) AS full_name
            FROM user_blocks ub
            LEFT JOIN freelancer f  ON f.user_id  = ub.blocked_user_id
            LEFT JOIN client     cl ON cl.user_id = ub.blocked_user_id
            WHERE ub.blocker_user_id = :uid
            ORDER BY ub.created_at DESC
            """,
            {"uid": blocker_user_id},
        )
        return [dict(r) for r in (rows or [])]

    @staticmethod
    def create_thread(
        initiator_id: str,
        participant_id: str,
        job_post_id: Optional[str] = None,
        message_text: Optional[str] = None,
    ) -> Dict:
        """Create a thread and send the first message. Returns (thread, first_message)."""
        existing = DMFunctions.get_thread_by_users(initiator_id, participant_id)
        if existing:
            raise ValueError("A message thread already exists between these users.")

        a, b = _canonical(initiator_id, participant_id)
        db   = get_db()

        # Resolve job title for default message
        job_title = None
        if job_post_id:
            info = _get_job_post_info(job_post_id)
            if info:
                job_title = info["job_title"]

        if not message_text:
            message_text = _job_pitch_default(job_title) if job_title else "Hi! I'd love to connect."

        thread_id = str(uuid.uuid4())
        db.execute_query(
            """
            INSERT INTO dm_thread (thread_id, user_a_id, user_b_id, initiator_id, status, job_post_id)
            VALUES (:tid, :a, :b, :init, 'request', :jpid)
            """,
            {"tid": thread_id, "a": a, "b": b, "init": initiator_id, "jpid": job_post_id},
        )

        metadata = None
        if job_post_id and job_title:
            metadata = json.dumps({"type": "job_pitch", "job_post_id": job_post_id, "job_title": job_title})

        first_msg = DMFunctions._insert_message(thread_id, initiator_id, message_text, metadata)
        thread    = DMFunctions.get_thread_by_id(thread_id)
        return {"thread": thread, "first_message": first_msg}

    @staticmethod
    def _insert_message(thread_id: str, sender_id: str, message_text: str,
                        metadata_json: Optional[str] = None) -> Dict:
        db         = get_db()
        msg_id     = str(uuid.uuid4())
        rows       = db.execute_query(
            """
            INSERT INTO dm_message (dm_message_id, thread_id, sender_id, message_text, metadata)
            VALUES (:id, :tid, :sid, :txt, :meta)
            RETURNING dm_message_id, thread_id, sender_id, message_text, metadata,
                      is_read, read_at, sent_at
            """,
            {"id": msg_id, "tid": thread_id, "sid": sender_id,
             "txt": message_text.strip() if message_text else "",
             "meta": metadata_json},
        )
        # bump thread updated_at
        db.execute_query(
            "UPDATE dm_thread SET updated_at = NOW() WHERE thread_id = :tid",
            {"tid": thread_id},
        )
        return _enrich_message(dict(rows[0])) if rows else {}

    @staticmethod
    def send_message(thread_id: str, sender_id: str, message_text: str,
                     metadata: Optional[Dict] = None) -> Dict:
        """
        Send a message in a thread.
        Enforces 1-message cap when thread is in 'request' status and sender = initiator.
        """
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            raise ValueError("Thread not found.")

        if thread["status"] == "declined":
            raise PermissionError("This thread has been declined.")

        if thread["status"] == "request" and str(sender_id) == str(thread["initiator_id"]):
            db   = get_db()
            rows = db.execute_query(
                "SELECT COUNT(*) AS cnt FROM dm_message WHERE thread_id = :tid AND sender_id = :sid",
                {"tid": thread_id, "sid": sender_id},
            )
            count = int(rows[0]["cnt"]) if rows else 0
            if count >= 1:
                raise PermissionError(
                    "Your message request is pending. You can only send 1 message until the other person accepts."
                )

        meta_json = json.dumps(metadata) if metadata else None
        return DMFunctions._insert_message(thread_id, sender_id, message_text, meta_json)

    @staticmethod
    def get_messages(
        thread_id: str,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> Tuple[List[Dict], bool, Optional[str]]:
        db          = get_db()
        fetch_limit = limit + 1

        if before:
            rows = db.execute_query(
                """
                SELECT dm_message_id, thread_id, sender_id, message_text, metadata,
                       is_read, read_at, sent_at
                FROM dm_message
                WHERE thread_id = :tid AND sent_at < :before
                ORDER BY sent_at DESC LIMIT :lim
                """,
                {"tid": thread_id, "before": before, "lim": fetch_limit},
            )
        else:
            rows = db.execute_query(
                """
                SELECT dm_message_id, thread_id, sender_id, message_text, metadata,
                       is_read, read_at, sent_at
                FROM dm_message
                WHERE thread_id = :tid
                ORDER BY sent_at DESC LIMIT :lim
                """,
                {"tid": thread_id, "lim": fetch_limit},
            )

        rows     = list(rows) if rows else []
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        rows = list(reversed(rows))

        messages    = [_enrich_message(dict(r)) for r in rows]
        next_cursor = None
        if has_more and messages:
            oldest = messages[0].get("sent_at")
            next_cursor = oldest.isoformat() if isinstance(oldest, datetime) else str(oldest) if oldest else None

        return messages, has_more, next_cursor

    @staticmethod
    def get_threads_for_user(user_id: str, status_filter: Optional[str] = None) -> List[Dict]:
        db    = get_db()
        query = """
            SELECT t.*,
                   COALESCE(lm.message_text, '')  AS last_message_text,
                   lm.sent_at                      AS last_message_at,
                   lm.sender_id                    AS last_sender_id,
                   COALESCE(uc.cnt, 0)             AS unread_count
            FROM dm_thread t
            LEFT JOIN LATERAL (
                SELECT message_text, sent_at, sender_id
                FROM dm_message
                WHERE thread_id = t.thread_id
                ORDER BY sent_at DESC LIMIT 1
            ) lm ON TRUE
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM dm_message
                WHERE thread_id = t.thread_id
                  AND sender_id != :uid
                  AND is_read = FALSE
            ) uc ON TRUE
            WHERE (t.user_a_id = :uid OR t.user_b_id = :uid)
        """
        params = {"uid": user_id}
        if status_filter:
            query += " AND t.status = :status"
            params["status"] = status_filter
        query += " ORDER BY COALESCE(lm.sent_at, t.created_at) DESC"

        rows = db.execute_query(query, params)
        if not rows:
            return []

        result = []
        for row in rows:
            t               = _enrich_thread(dict(row), user_id)
            t["last_message"] = {
                "message_text": row["last_message_text"],
                "sent_at":      row["last_message_at"],
                "sender_id":    _to_str(row["last_sender_id"]),
            } if row.get("last_message_at") else None
            t["unread_count"] = int(row["unread_count"])
            result.append(t)
        return result

    @staticmethod
    def get_pending_requests_count(user_id: str) -> int:
        db   = get_db()
        rows = db.execute_query(
            """
            SELECT COUNT(*) AS cnt FROM dm_thread
            WHERE (user_a_id = :uid OR user_b_id = :uid)
              AND status = 'request'
              AND initiator_id != :uid
            """,
            {"uid": user_id},
        )
        return int(rows[0]["cnt"]) if rows else 0

    @staticmethod
    def accept_thread(thread_id: str) -> Dict:
        db = get_db()
        db.execute_query(
            "UPDATE dm_thread SET status = 'active', updated_at = NOW() WHERE thread_id = :tid",
            {"tid": thread_id},
        )
        return DMFunctions.get_thread_by_id(thread_id)

    @staticmethod
    def decline_thread(thread_id: str) -> Dict:
        db = get_db()
        db.execute_query(
            "UPDATE dm_thread SET status = 'declined', updated_at = NOW() WHERE thread_id = :tid",
            {"tid": thread_id},
        )
        return DMFunctions.get_thread_by_id(thread_id)

    @staticmethod
    def mark_read(thread_id: str, reader_id: str) -> int:
        db   = get_db()
        rows = db.execute_query(
            """
            UPDATE dm_message
            SET is_read = TRUE, read_at = NOW()
            WHERE thread_id = :tid
              AND sender_id != :rid
              AND is_read = FALSE
            RETURNING dm_message_id
            """,
            {"tid": thread_id, "rid": reader_id},
        )
        return len(rows) if rows else 0

    @staticmethod
    def create_attachment(
        dm_message_id: str,
        file_name: str,
        file_url: str,
        mime_type: str,
        file_type: str,
        file_size_bytes: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> Dict:
        db  = get_db()
        aid = str(uuid.uuid4())
        db.execute_query(
            """
            INSERT INTO dm_message_attachment
                (attachment_id, dm_message_id, file_name, file_url,
                 file_type, mime_type, file_size_bytes, duration_seconds)
            VALUES
                (:aid, :mid, :fn, :url, :ft, :mt, :fsb, :ds)
            """,
            {"aid": aid, "mid": dm_message_id, "fn": file_name, "url": file_url,
             "ft": file_type, "mt": mime_type, "fsb": file_size_bytes, "ds": duration_seconds},
        )
        rows = db.execute_query(
            "SELECT * FROM dm_message_attachment WHERE attachment_id = :id", {"id": aid}
        )
        return dict(rows[0]) if rows else {}

    @staticmethod
    def send_system_event(
        contract_id: str,
        actor_id: str,
        message_text: str,
        event_type: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Send a system-event message to the thread linked to a contract. No-op if no thread exists."""
        thread = DMFunctions.get_thread_by_contract_id(contract_id)
        if not thread:
            return None
        meta = {"type": event_type}
        if metadata:
            meta.update(metadata)
        return DMFunctions._insert_message(
            thread["thread_id"], actor_id, message_text, json.dumps(meta)
        )

    @staticmethod
    def activate_or_create_thread(
        client_user_id: str,
        freelancer_user_id: str,
        message_text: str,
        sender_id: str,
        job_post_id: Optional[str] = None,
        job_role_id: Optional[str] = None,
        contract_id: Optional[str] = None,
        role_title: Optional[str] = None,
        contract_title: Optional[str] = None,
    ) -> Dict:
        """
        Called when a contract is created.
        Finds or creates the DM thread, promotes it to 'active', and sends the auto-message.
        """
        thread = DMFunctions.get_thread_by_users(client_user_id, freelancer_user_id)

        if not thread:
            a, b      = _canonical(client_user_id, freelancer_user_id)
            thread_id = str(uuid.uuid4())
            db        = get_db()
            db.execute_query(
                """
                INSERT INTO dm_thread
                    (thread_id, user_a_id, user_b_id, initiator_id, status, job_post_id, contract_id)
                VALUES (:tid, :a, :b, :init, 'active', :jpid, :cid)
                """,
                {"tid": thread_id, "a": a, "b": b, "init": sender_id,
                 "jpid": job_post_id, "cid": contract_id},
            )
            thread = DMFunctions.get_thread_by_id(thread_id)
        else:
            db = get_db()
            db.execute_query(
                """
                UPDATE dm_thread
                SET status = 'active', updated_at = NOW(), contract_id = COALESCE(contract_id, :cid)
                WHERE thread_id = :tid
                """,
                {"tid": thread["thread_id"], "cid": contract_id},
            )
            thread = DMFunctions.get_thread_by_id(thread["thread_id"])

        # Build metadata card for the frontend to render
        metadata = {"type": "contract_accepted"}
        if contract_id:
            metadata["contract_id"]     = contract_id
        if job_post_id:
            metadata["job_post_id"]     = job_post_id
            info = _get_job_post_info(job_post_id)
            if info:
                metadata["job_title"]   = info["job_title"]
        if job_role_id:
            metadata["job_role_id"]     = job_role_id
        if role_title:
            metadata["role_title"]      = role_title
        if contract_title:
            metadata["contract_title"]  = contract_title

        msg = DMFunctions._insert_message(
            thread["thread_id"], sender_id, message_text, json.dumps(metadata)
        )
        return {"thread": thread, "auto_message": msg}
