import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from fastapi import (
    APIRouter, Depends, File, Form, Query,
    UploadFile, WebSocket, WebSocketDisconnect,
)
from typing import Optional

from functions.schema_model import (
    DMThreadCreate, DMMessageCreate, UserInDB,
)
from functions.authentication import get_current_user, verify_token, get_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_thread_attachment, guess_mime
from routes.dm.dm_functions import DMFunctions
from routes.messages.message_functions import _classify_file_type


dm_router = APIRouter(prefix="/dm", tags=["Direct Messages"])


# ── WebSocket connection manager ──────────────────────────────────────────────

class _DMConnectionManager:
    def __init__(self):
        self._rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, thread_id: str, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(thread_id, []).append(ws)

    def disconnect(self, thread_id: str, ws: WebSocket):
        room = self._rooms.get(thread_id, [])
        if ws in room:
            room.remove(ws)

    async def broadcast(self, thread_id: str, data: dict):
        for ws in list(self._rooms.get(thread_id, [])):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(thread_id, ws)


_manager = _DMConnectionManager()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _is_participant(thread: dict, user_id: str) -> bool:
    return str(user_id) in (str(thread.get("user_a_id", "")),
                            str(thread.get("user_b_id", "")))


def _is_receiver(thread: dict, user_id: str) -> bool:
    """The receiver is the non-initiator."""
    return str(user_id) != str(thread.get("initiator_id", ""))


# ── POST /dm/threads ──────────────────────────────────────────────────────────

@dm_router.post("/threads", status_code=201)
async def start_thread(
    payload: DMThreadCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Client opens a new DM thread with a freelancer.
    Only users with a client profile can initiate threads.

    - If `job_post_id` is provided the first message defaults to the job-pitch
      template (overrideable via `message_text`).
    - Thread starts in **request** status — freelancer gets a notification.
    - Once the freelancer accepts, both parties can chat freely.
    - A contract creation auto-promotes the thread to **active** (see POST /contracts).
    """
    if not current_user.client_id:
        return ResponseSchema.error(
            "Only clients can start a conversation. "
            "Freelancers can reply once a thread is opened by a client or created via a contract.",
            403,
        )

    existing = DMFunctions.get_thread_by_users(
        str(current_user.user_id), str(payload.participant_id)
    )
    if existing:
        return ResponseSchema.success(
            {"thread": existing, "already_exists": True}, 200
        )

    try:
        result = DMFunctions.create_thread(
            initiator_id=str(current_user.user_id),
            participant_id=str(payload.participant_id),
            job_post_id=payload.job_post_id,
            message_text=payload.message_text,
        )
        logger("DM", f"Thread created by client {current_user.user_id}", "POST /dm/threads", "INFO")
        await _manager.broadcast(
            result["thread"]["thread_id"], result["first_message"]
        )
        return ResponseSchema.success(result, 201)
    except ValueError as e:
        return ResponseSchema.error(str(e), 400)
    except Exception as e:
        logger("DM", f"Failed to start thread: {e}", "POST /dm/threads", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── GET /dm/threads ───────────────────────────────────────────────────────────

@dm_router.get("/threads")
async def list_threads(
    status: Optional[str] = Query(None, description="Filter by status: request | active | declined"),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    All DM threads for the current user (active + requests + declined).
    Each thread includes: other user info, last message, unread count.
    Pass `?status=request` to get only pending requests (for the notification badge).
    """
    try:
        threads = DMFunctions.get_threads_for_user(
            str(current_user.user_id), status_filter=status
        )
        pending = DMFunctions.get_pending_requests_count(str(current_user.user_id))
        logger("DM", f"Listed {len(threads)} threads for user {current_user.user_id}", "GET /dm/threads", "INFO")
        return ResponseSchema.success(
            {"threads": threads, "pending_requests_count": pending}, 200
        )
    except Exception as e:
        logger("DM", f"Failed to list threads: {e}", "GET /dm/threads", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── GET /dm/threads/requests ──────────────────────────────────────────────────

@dm_router.get("/threads/requests")
async def list_requests(
    current_user: UserInDB = Depends(get_current_user),
):
    """Pending message requests for the current user (shortcut for ?status=request)."""
    try:
        threads = DMFunctions.get_threads_for_user(
            str(current_user.user_id), status_filter="request"
        )
        return ResponseSchema.success(
            {"requests": threads, "count": len(threads)}, 200
        )
    except Exception as e:
        logger("DM", f"Failed to list requests: {e}", "GET /dm/threads/requests", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── GET /dm/threads/{thread_id} ───────────────────────────────────────────────

@dm_router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Full thread detail with other-user profile and job post info."""
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        return ResponseSchema.success(thread, 200)
    except Exception as e:
        logger("DM", f"Failed to get thread: {e}", "GET /dm/threads/{thread_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── PUT /dm/threads/{thread_id}/accept ───────────────────────────────────────

@dm_router.put("/threads/{thread_id}/accept")
async def accept_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Freelancer accepts a message request → thread becomes active.
    Only the non-initiating party (the receiver) can accept.
    """
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        if not _is_receiver(thread, str(current_user.user_id)):
            return ResponseSchema.error("Only the recipient can accept a request", 403)
        if thread["status"] == "active":
            return ResponseSchema.success(thread, 200)
        if thread["status"] == "declined":
            return ResponseSchema.error("This thread has already been declined", 400)

        updated = DMFunctions.accept_thread(thread_id)
        logger("DM", f"Thread {thread_id} accepted by {current_user.user_id}", "PUT /dm/threads/{thread_id}/accept", "INFO")
        await _manager.broadcast(thread_id, {"event": "thread_accepted", "thread_id": thread_id})
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("DM", f"Failed to accept thread: {e}", "PUT /dm/threads/{thread_id}/accept", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── PUT /dm/threads/{thread_id}/decline ──────────────────────────────────────

@dm_router.put("/threads/{thread_id}/decline")
async def decline_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer declines a message request. Initiator cannot send further messages."""
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        if not _is_receiver(thread, str(current_user.user_id)):
            return ResponseSchema.error("Only the recipient can decline a request", 403)

        updated = DMFunctions.decline_thread(thread_id)
        logger("DM", f"Thread {thread_id} declined by {current_user.user_id}", "PUT /dm/threads/{thread_id}/decline", "INFO")
        await _manager.broadcast(thread_id, {"event": "thread_declined", "thread_id": thread_id})
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("DM", f"Failed to decline thread: {e}", "PUT /dm/threads/{thread_id}/decline", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── GET /dm/threads/{thread_id}/messages ─────────────────────────────────────

@dm_router.get("/threads/{thread_id}/messages")
async def get_messages(
    thread_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[str] = Query(None, description="ISO datetime cursor for pagination"),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Paginated message history for a DM thread.
    Returns most recent `limit` messages (or messages older than `before`).
    Scroll up → pass `next_cursor` as `before` to load older messages.
    """
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        messages, has_more, next_cursor = DMFunctions.get_messages(
            thread_id, limit=limit, before=before
        )
        return ResponseSchema.success(
            {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}, 200
        )
    except Exception as e:
        logger("DM", f"Failed to fetch messages: {e}", "GET /dm/threads/{thread_id}/messages", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── POST /dm/threads/{thread_id}/messages ────────────────────────────────────

@dm_router.post("/threads/{thread_id}/messages", status_code=201)
async def send_message(
    thread_id: str,
    payload: DMMessageCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Send a text message in an existing DM thread.
    Enforces 1-message cap if thread is still in **request** status and sender is the initiator.
    """
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        if not payload.message_text.strip():
            return ResponseSchema.error("message_text cannot be empty", 400)

        msg = DMFunctions.send_message(
            thread_id=thread_id,
            sender_id=str(current_user.user_id),
            message_text=payload.message_text,
        )
        logger("DM", f"Message sent in thread {thread_id} by {current_user.user_id}", "POST /dm/threads/{thread_id}/messages", "INFO")
        await _manager.broadcast(thread_id, msg)
        return ResponseSchema.success(msg, 201)
    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger("DM", f"Failed to send message: {e}", "POST /dm/threads/{thread_id}/messages", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── POST /dm/threads/{thread_id}/messages/upload ─────────────────────────────

@dm_router.post("/threads/{thread_id}/messages/upload", status_code=201)
async def send_message_with_attachment(
    thread_id: str,
    message_text: Optional[str] = Form(None),
    is_voice_note: bool = Form(False),
    file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Send a message with an optional file attachment (image, video, audio, PDF, voice note).
    At least one of `message_text` or `file` must be provided.
    Set `is_voice_note=true` to flag an audio recording as a voice note.
    Same 1-message cap applies as the text endpoint.
    """
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        text = (message_text or "").strip()
        if not text and (not file or not file.filename):
            return ResponseSchema.error("Provide message_text, a file, or both", 400)

        msg = DMFunctions.send_message(
            thread_id=thread_id,
            sender_id=str(current_user.user_id),
            message_text=text or "",
        )

        if file and file.filename:
            file_bytes = await file.read()
            mime_type  = file.content_type or guess_mime(file.filename)
            file_type  = _classify_file_type(mime_type, is_voice_note=is_voice_note)
            file_url   = upload_thread_attachment(
                thread_id=thread_id,
                message_id=msg["dm_message_id"],
                file_name=file.filename,
                file_bytes=file_bytes,
                content_type=mime_type,
            )
            DMFunctions.create_attachment(
                dm_message_id=msg["dm_message_id"],
                file_name=file.filename,
                file_url=file_url,
                mime_type=mime_type,
                file_type=file_type,
                file_size_bytes=len(file_bytes),
            )
            # Reload with attachment included
            msgs, _, _ = DMFunctions.get_messages(thread_id, limit=1)
            msg = next((m for m in msgs if m["dm_message_id"] == msg["dm_message_id"]), msg)

        logger("DM", f"Message+attachment sent in thread {thread_id} by {current_user.user_id}", "POST /dm/threads/{thread_id}/messages/upload", "INFO")
        await _manager.broadcast(thread_id, msg)
        return ResponseSchema.success(msg, 201)
    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger("DM", f"Failed to send message with attachment: {e}", "POST /dm/threads/{thread_id}/messages/upload", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── PUT /dm/threads/{thread_id}/read ─────────────────────────────────────────

@dm_router.put("/threads/{thread_id}/read")
async def mark_read(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Mark all unread messages in this thread as read for the current user."""
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        count = DMFunctions.mark_read(thread_id, str(current_user.user_id))
        logger("DM", f"Marked {count} messages read in thread {thread_id}", "PUT /dm/threads/{thread_id}/read", "INFO")
        return ResponseSchema.success({"updated_count": count}, 200)
    except Exception as e:
        logger("DM", f"Failed to mark read: {e}", "PUT /dm/threads/{thread_id}/read", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── WS /dm/ws/{thread_id} ─────────────────────────────────────────────────────

@dm_router.websocket("/ws/{thread_id}")
async def ws_thread(
    websocket: WebSocket,
    thread_id: str,
    token: str = Query(...),
):
    """
    Real-time DM stream for a thread.

    Connect: `ws://<host>/dm/ws/<thread_id>?token=<jwt>`

    The server pushes every new message as JSON the instant it is created via
    POST /dm/threads/{id}/messages (or /upload).
    Thread-lifecycle events (accepted, declined) are also broadcast here as:
        {"event": "thread_accepted" | "thread_declined", "thread_id": "..."}

    Send any text frame to keep the connection alive (ping/pong).
    The connection is rejected with code 1008 for invalid token or non-participants.
    """
    credentials_exception = Exception("Invalid token")
    try:
        token_data = verify_token(token, credentials_exception)
        user       = get_user(token_data.email)
        if not user:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    thread = DMFunctions.get_thread_by_id(thread_id)
    if not thread or not _is_participant(thread, str(user.user_id)):
        await websocket.close(code=1008)
        return

    await _manager.connect(thread_id, websocket)
    logger("DM", f"WS connected: user {user.user_id} on thread {thread_id}", "WS /dm/ws/{thread_id}", "INFO")
    try:
        while True:
            await websocket.receive_text()   # keep-alive; real messages come via REST
    except WebSocketDisconnect:
        _manager.disconnect(thread_id, websocket)
        logger("DM", f"WS disconnected: user {user.user_id} on thread {thread_id}", "WS /dm/ws/{thread_id}", "INFO")
