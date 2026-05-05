import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect
from typing import Optional
from functions.schema_model import MessageCreate, UserInDB
from functions.authentication import get_current_user, verify_token, get_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_thread_attachment, guess_mime
from routes.messages.message_functions import _classify_file_type, get_contract_by_id
from routes.dm.dm_functions import DMFunctions, _get_dm_attachments
from routes.dm.dm_routes import _manager as _dm_manager


message_router = APIRouter(prefix="/messages", tags=["Messages"])


def _thread_for_contract(contract_id: str):
    """Look up the DM thread linked to this contract."""
    return DMFunctions.get_thread_by_contract_id(contract_id)


def _is_participant(thread: dict, user_id: str) -> bool:
    return str(user_id) in (str(thread.get("user_a_id", "")),
                            str(thread.get("user_b_id", "")))


# ── GET /messages/contract/{contract_id} ──────────────────────────────────────

@message_router.get("/contract/{contract_id}")
async def get_messages_by_contract(
    contract_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    before: Optional[str] = Query(default=None),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = _thread_for_contract(contract_id)
        if not thread:
            return ResponseSchema.error("No message thread found for this contract", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        messages, has_more, next_cursor = DMFunctions.get_messages(
            thread["thread_id"], limit=limit, before=before
        )
        return ResponseSchema.success(
            {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}, 200
        )
    except Exception as e:
        logger("MESSAGE", f"Failed to fetch contract messages: {e}", "GET /messages/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── POST /messages (text) ─────────────────────────────────────────────────────

@message_router.post("", status_code=201)
async def create_message(
    payload: MessageCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = _thread_for_contract(payload.contract_id)
        if not thread:
            return ResponseSchema.error("No message thread found for this contract", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)
        if not (payload.message_text or "").strip():
            return ResponseSchema.error("message_text cannot be empty", 400)

        msg = DMFunctions.send_message(
            thread_id=thread["thread_id"],
            sender_id=str(current_user.user_id),
            message_text=payload.message_text,
        )
        await _dm_manager.broadcast(thread["thread_id"], msg)
        return ResponseSchema.success(msg, 201)
    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger("MESSAGE", f"Failed to create message: {e}", "POST /messages", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── POST /messages/upload (text + optional file) ──────────────────────────────

@message_router.post("/upload", status_code=201)
async def create_message_with_attachment(
    contract_id: str = Form(...),
    message_text: Optional[str] = Form(None),
    is_voice_note: bool = Form(False),
    file: Optional[UploadFile] = File(None),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = _thread_for_contract(contract_id)
        if not thread:
            return ResponseSchema.error("No message thread found for this contract", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        text = (message_text or "").strip()
        if not text and (not file or not file.filename):
            return ResponseSchema.error("Provide message_text, a file, or both", 400)

        msg = DMFunctions.send_message(
            thread_id=thread["thread_id"],
            sender_id=str(current_user.user_id),
            message_text=text or "",
        )

        if file and file.filename:
            file_bytes = await file.read()
            mime_type  = file.content_type or guess_mime(file.filename)
            file_type  = _classify_file_type(mime_type, is_voice_note=is_voice_note)
            file_url   = upload_thread_attachment(
                thread_id=thread["thread_id"],
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
            msgs, _, _ = DMFunctions.get_messages(thread["thread_id"], limit=1)
            msg = next((m for m in msgs if m["dm_message_id"] == msg["dm_message_id"]), msg)

        await _dm_manager.broadcast(thread["thread_id"], msg)
        return ResponseSchema.success(msg, 201)
    except PermissionError as e:
        return ResponseSchema.error(str(e), 403)
    except Exception as e:
        logger("MESSAGE", f"Failed to send message with attachment: {e}", "POST /messages/upload", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── PUT /messages/contract/{contract_id}/read ─────────────────────────────────

@message_router.put("/contract/{contract_id}/read")
async def mark_messages_as_read(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        thread = _thread_for_contract(contract_id)
        if not thread:
            return ResponseSchema.error("No message thread found for this contract", 404)
        if not _is_participant(thread, str(current_user.user_id)):
            return ResponseSchema.error("Access denied", 403)

        count = DMFunctions.mark_read(thread["thread_id"], str(current_user.user_id))
        return ResponseSchema.success({"updated_count": count}, 200)
    except Exception as e:
        logger("MESSAGE", f"Failed to mark messages as read: {e}", "PUT /messages/contract/{contract_id}/read", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── GET /messages/{message_id}/attachments ────────────────────────────────────

@message_router.get("/{message_id}/attachments")
async def get_message_attachments(
    message_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        attachments = _get_dm_attachments(message_id)
        return ResponseSchema.success(attachments, 200)
    except Exception as e:
        logger("MESSAGE", f"Failed to fetch attachments: {e}", "GET /messages/{message_id}/attachments", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ── WS /messages/ws/contract/{contract_id} ────────────────────────────────────

@message_router.websocket("/ws/contract/{contract_id}")
async def ws_messages(
    websocket: WebSocket,
    contract_id: str,
    token: str = Query(...),
):
    """Real-time stream for a contract chat. Shares the same room as /dm/ws/{thread_id}."""
    try:
        token_data = verify_token(token, Exception("Invalid token"))
        user       = get_user(token_data.email)
        if not user:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    thread = DMFunctions.get_thread_by_contract_id(contract_id)
    if not thread or not _is_participant(thread, str(user.user_id)):
        await websocket.close(code=1008)
        return

    # Join the same DM manager room so all listeners receive the same broadcast
    await _dm_manager.connect(thread["thread_id"], websocket)
    logger("MESSAGE", f"WS connected: user {user.user_id} on contract {contract_id}", "WS /messages/ws/contract/{contract_id}", "INFO")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _dm_manager.disconnect(thread["thread_id"], websocket)
        logger("MESSAGE", f"WS disconnected: user {user.user_id} on contract {contract_id}", "WS /messages/ws/contract/{contract_id}", "INFO")
