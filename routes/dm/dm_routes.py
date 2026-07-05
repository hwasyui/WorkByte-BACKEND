import asyncio
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
from functions.minio_client import upload_thread_attachment, guess_mime, resolve_file_url, BUCKET_MESSAGE_ATTACHMENTS
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from routes.admin.admin_moderation import scan_harmful_text_with_ml_fallback


dm_router = APIRouter(prefix="/dm", tags=["Direct Messages"])

# ~4-5 chunks of the 128-token scan window, keeps moderation latency predictable
_MAX_MESSAGE_LENGTH = 2500


def _resolve_msg_attachment_urls(msg: dict) -> dict:
    for att in msg.get("attachments", []):
        if att.get("file_url"):
            att["file_url"] = resolve_file_url(BUCKET_MESSAGE_ATTACHMENTS, att["file_url"])
    return msg


def _classify_file_type(mime_type: str, is_voice_note: bool = False) -> str:
    if is_voice_note:
        return "voice_note"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def _get_sender_name(current_user: UserInDB) -> str:
    """Resolve display name from client or freelancer table."""
    try:
        if current_user.freelancer_id:
            fl = FreelancerFunctions.get_freelancer_by_user_id(str(current_user.user_id))
            return fl.get("full_name", "Someone") if fl else "Someone"
        elif current_user.client_id:
            cl = ClientFunctions.get_client_by_user_id(str(current_user.user_id))
            return cl.get("full_name", "Someone") if cl else "Someone"
    except Exception:
        pass
    return "Someone"


# WebSocket connection manager


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


# Auth helpers


def _is_participant(thread: dict, user_id: str) -> bool:
    return str(user_id) in (str(thread.get("user_a_id", "")),
                            str(thread.get("user_b_id", "")))


def _is_receiver(thread: dict, user_id: str) -> bool:
    """The receiver is the non-initiator."""
    return str(user_id) != str(thread.get("initiator_id", ""))


# POST /dm/threads


@dm_router.post("/threads", status_code=201)
async def start_thread(
    payload: DMThreadCreate,
    current_user: UserInDB = Depends(get_current_user),
):
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
            {"thread_id": existing.get("thread_id", ""), "thread": existing, "already_exists": True}, 200
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
        return ResponseSchema.success(
            {"thread_id": result["thread"].get("thread_id", ""), **result}, 201
        )
    except ValueError as e:
        return ResponseSchema.error(str(e), 400)
    except Exception as e:
        logger("DM", f"Failed to start thread: {e}", "POST /dm/threads", "ERROR")
        return ResponseSchema.error(str(e), 500)


# GET /dm/threads


@dm_router.get("/threads")
async def list_threads(
    status: Optional[str] = Query(None, description="Filter by status: request | active | declined"),
    current_user: UserInDB = Depends(get_current_user),
):
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


# GET /dm/threads/requests


@dm_router.get("/threads/requests")
async def list_requests(
    current_user: UserInDB = Depends(get_current_user),
):
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


# GET /dm/threads/{thread_id}


@dm_router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
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


# PUT /dm/threads/{thread_id}/accept


@dm_router.put("/threads/{thread_id}/accept")
async def accept_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
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

        # Notify initiator that their request was accepted
        try:
            sender_name = _get_sender_name(current_user)
            initiator_id = str(thread.get("initiator_id", ""))
            if initiator_id:
                await NotificationFunctions.notify(
                    recipient_user_id=initiator_id,
                    notif_type="thread_accepted",
                    title=f"{sender_name} accepted your message request",
                    body="You can now chat freely",
                    data={"thread_id": thread_id},
                )
        except Exception as notif_err:
            logger("DM", f"Accept notification failed (non-fatal): {notif_err}", "PUT /dm/threads/{thread_id}/accept", "WARNING")

        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("DM", f"Failed to accept thread: {e}", "PUT /dm/threads/{thread_id}/accept", "ERROR")
        return ResponseSchema.error(str(e), 500)


# PUT /dm/threads/{thread_id}/decline


@dm_router.put("/threads/{thread_id}/decline")
async def decline_thread(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
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


# GET /dm/threads/{thread_id}/messages


@dm_router.get("/threads/{thread_id}/messages")
async def get_messages(
    thread_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[str] = Query(None, description="ISO datetime cursor for pagination"),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        messages, has_more, next_cursor = DMFunctions.get_messages(
            thread_id, limit=limit, before=before
        )
        messages = [_resolve_msg_attachment_urls(m) for m in messages]
        return ResponseSchema.success(
            {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}, 200
        )
    except Exception as e:
        logger("DM", f"Failed to fetch messages: {e}", "GET /dm/threads/{thread_id}/messages", "ERROR")
        return ResponseSchema.error(str(e), 500)


# POST /dm/threads/{thread_id}/messages


@dm_router.post("/threads/{thread_id}/messages", status_code=201)
async def send_message(
    thread_id: str,
    payload: DMMessageCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        if not payload.message_text.strip():
            return ResponseSchema.error("message_text cannot be empty", 400)
        if len(payload.message_text) > _MAX_MESSAGE_LENGTH:
            return ResponseSchema.error(
                f"Message is too long. Please keep it under {_MAX_MESSAGE_LENGTH:,} characters, or split it into shorter messages.",
                400,
            )

        harm_result = await asyncio.to_thread(scan_harmful_text_with_ml_fallback, payload.message_text)
        if harm_result["is_flagged"]:
            labels = harm_result.get("detected_labels", [])
            logger("DM", f"Blocked toxic message from {current_user.user_id} in thread {thread_id}, labels={labels}", "POST /dm/threads/{thread_id}/messages", "WARNING")
            return ResponseSchema.error("Message couldn't be sent.", 400)

        msg = DMFunctions.send_message(
            thread_id=thread_id,
            sender_id=str(current_user.user_id),
            message_text=payload.message_text,
        )
        logger("DM", f"Message sent in thread {thread_id} by {current_user.user_id}", "POST /dm/threads/{thread_id}/messages", "INFO")
        await _manager.broadcast(thread_id, msg)

        # Notify the other participant
        try:
            recipient_id = (
                str(thread["user_b_id"])
                if str(current_user.user_id) == str(thread["user_a_id"])
                else str(thread["user_a_id"])
            )
            sender_name = _get_sender_name(current_user)
            preview = payload.message_text[:60] + ("..." if len(payload.message_text) > 60 else "")
            await NotificationFunctions.notify(
                recipient_user_id=recipient_id,
                notif_type="new_message",
                title="New Message 💬",
                body=f"{sender_name}: {preview}",
                data={"thread_id": thread_id},
            )
        except Exception as notif_err:
            logger("DM", f"Message notification failed (non-fatal): {notif_err}", "POST /dm/threads/{thread_id}/messages", "WARNING")

        return ResponseSchema.success(msg, 201)
    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger("DM", f"Failed to send message: {e}", "POST /dm/threads/{thread_id}/messages", "ERROR")
        return ResponseSchema.error(str(e), 500)


# POST /dm/threads/{thread_id}/messages/upload

@dm_router.post("/threads/{thread_id}/messages/upload", status_code=201)
async def send_message_with_attachment(
    thread_id: str,
    message_text: Optional[str] = Form(None),
    is_voice_note: bool = Form(False),
    file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = DMFunctions.get_thread_by_id(thread_id)
        if not thread:
            return ResponseSchema.error("Thread not found", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        text = (message_text or "").strip()
        if not text and (not file or not file.filename):
            return ResponseSchema.error("Provide message_text, a file, or both", 400)
        if len(text) > _MAX_MESSAGE_LENGTH:
            return ResponseSchema.error(
                f"Message is too long. Please keep it under {_MAX_MESSAGE_LENGTH:,} characters, or split it into shorter messages.",
                400,
            )

        if text:
            harm_result = await asyncio.to_thread(scan_harmful_text_with_ml_fallback, text)
            if harm_result["is_flagged"]:
                labels = harm_result.get("detected_labels", [])
                logger(
                    "DM",
                    f"Blocked toxic attachment message from {current_user.user_id} in thread {thread_id}, labels={labels}",
                    "POST /dm/threads/{thread_id}/messages/upload",
                    "WARNING",
                )
                return ResponseSchema.error("Message couldn't be sent.", 400)

        msg = DMFunctions.send_message(
            thread_id=thread_id,
            sender_id=str(current_user.user_id),
            message_text=text or "",
        )

        if file and file.filename:
            file_bytes = await file.read()
            mime_type = file.content_type or guess_mime(file.filename)
            file_type = _classify_file_type(mime_type, is_voice_note=is_voice_note)
            file_url = upload_thread_attachment(
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
            msgs, _, _ = DMFunctions.get_messages(thread_id, limit=1)
            msg = next((m for m in msgs if m["dm_message_id"] == msg["dm_message_id"]), msg)
            msg = _resolve_msg_attachment_urls(msg)

        logger(
            "DM",
            f"Message+attachment sent in thread {thread_id} by {current_user.user_id}",
            "POST /dm/threads/{thread_id}/messages/upload",
            "INFO",
        )
        await _manager.broadcast(thread_id, msg)

        try:
            recipient_id = (
                str(thread["user_b_id"])
                if str(current_user.user_id) == str(thread["user_a_id"])
                else str(thread["user_a_id"])
            )
            sender_name = _get_sender_name(current_user)
            notif_body = text[:60] + ("..." if len(text) > 60 else "") if text else "Sent an attachment 📎"
            await NotificationFunctions.notify(
                recipient_user_id=recipient_id,
                notif_type="new_message",
                title=sender_name,
                body=notif_body,
                data={"thread_id": thread_id},
            )
        except Exception as notif_err:
            logger(
                "DM",
                f"Attachment message notification failed (non-fatal): {notif_err}",
                "POST /dm/threads/{thread_id}/messages/upload",
                "WARNING",
            )

        return ResponseSchema.success(msg, 201)

    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger(
            "DM",
            f"Failed to send message with attachment: {e}",
            "POST /dm/threads/{thread_id}/messages/upload",
            "ERROR",
        )
        return ResponseSchema.error(str(e), 500)

# PUT /dm/threads/{thread_id}/read


@dm_router.put("/threads/{thread_id}/read")
async def mark_read(
    thread_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
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


# WS /dm/ws/{thread_id}


@dm_router.websocket("/ws/{thread_id}")
async def ws_thread(
    websocket: WebSocket,
    thread_id: str,
    token: str = Query(...),
):
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
            await websocket.receive_text()
    except WebSocketDisconnect:
        _manager.disconnect(thread_id, websocket)
        logger("DM", f"WS disconnected: user {user.user_id} on thread {thread_id}", "WS /dm/ws/{thread_id}", "INFO")