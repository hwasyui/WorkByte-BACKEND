import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import MessageCreate, MessageUpdate, MessageResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_current_user_is_contract_party
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.messages.message_functions import MessageFunctions
from routes.contracts.contract_functions import ContractFunctions

message_router = APIRouter(prefix="/messages", tags=["Messages"])


@message_router.get("", response_model=List[MessageResponse])
async def get_all_messages(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all messages - Authenticated users only - JSON response"""
    try:
        sent = MessageFunctions.get_messages_by_sender_id(current_user.user_id)
        received = MessageFunctions.get_messages_by_receiver_id(current_user.user_id)
        messages = sent + received
        success_msg = f"Retrieved {len(messages)} messages for user {current_user.user_id}" + (f" (limit: {limit})" if limit else "")
        logger("MESSAGE", success_msg, "GET /messages", "INFO")
        return ResponseSchema.success(messages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch messages: {str(e)}"
        logger("MESSAGE", error_msg, "GET /messages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.get("/{message_id}", response_model=MessageResponse)
async def get_message(message_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single message by ID - Authenticated users only - JSON response"""
    try:
        message = MessageFunctions.get_message_by_id(message_id)
        if not message:
            error_msg = f"Message {message_id} not found"
            logger("MESSAGE", error_msg, "GET /messages/{message_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        if str(current_user.user_id) not in [str(message["sender_id"]), str(message["receiver_id"])]:
            return ResponseSchema.error("Cannot access another user's message", 403)
        success_msg = f"Retrieved message {message_id}"
        logger("MESSAGE", success_msg, "GET /messages/{message_id}", "INFO")
        return ResponseSchema.success(message, 200)
    except Exception as e:
        error_msg = f"Failed to fetch message {message_id}: {str(e)}"
        logger("MESSAGE", error_msg, "GET /messages/{message_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.get("/sender/{sender_id}", response_model=List[MessageResponse])
async def get_messages_by_sender(sender_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all messages sent by a specific user - Authenticated users only - JSON response"""
    try:
        if str(sender_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot fetch another user's sent messages", 403)
        messages = MessageFunctions.get_messages_by_sender_id(sender_id)
        success_msg = f"Retrieved {len(messages)} messages from sender {sender_id}"
        logger("MESSAGE", success_msg, "GET /messages/sender/{sender_id}", "INFO")
        return ResponseSchema.success(messages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch messages from sender {sender_id}: {str(e)}"
        logger("MESSAGE", error_msg, "GET /messages/sender/{sender_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.get("/receiver/{receiver_id}", response_model=List[MessageResponse])
async def get_messages_by_receiver(receiver_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all messages received by a specific user - Authenticated users only - JSON response"""
    try:
        if str(receiver_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot fetch another user's received messages", 403)
        messages = MessageFunctions.get_messages_by_receiver_id(receiver_id)
        success_msg = f"Retrieved {len(messages)} messages for receiver {receiver_id}"
        logger("MESSAGE", success_msg, "GET /messages/receiver/{receiver_id}", "INFO")
        return ResponseSchema.success(messages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch messages for receiver {receiver_id}: {str(e)}"
        logger("MESSAGE", error_msg, "GET /messages/receiver/{receiver_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.get("/contract/{contract_id}", response_model=List[MessageResponse])
async def get_messages_by_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all messages for a specific contract - Authenticated users only - JSON response"""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        assert_current_user_is_contract_party(current_user, contract)
        messages = MessageFunctions.get_messages_by_contract_id(contract_id)
        success_msg = f"Retrieved {len(messages)} messages for contract {contract_id}"
        logger("MESSAGE", success_msg, "GET /messages/contract/{contract_id}", "INFO")
        return ResponseSchema.success(messages, 200)
    except Exception as e:
        error_msg = f"Failed to fetch messages for contract {contract_id}: {str(e)}"
        logger("MESSAGE", error_msg, "GET /messages/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.post("", response_model=MessageResponse, status_code=201)
async def create_message(message: MessageCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new message - Authenticated users only - JSON body accepted"""
    try:
        message_id = message.message_id or str(uuid.uuid4())
        
        if str(message.sender_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot send messages on behalf of another user", 403)
        if getattr(message, 'contract_id', None):
            contract = ContractFunctions.get_contract_by_id(message.contract_id)
            assert_current_user_is_contract_party(current_user, contract)
        new_message = MessageFunctions.create_message(
            sender_id=message.sender_id,
            receiver_id=message.receiver_id,
            message_text=message.message_text,
            contract_id=getattr(message, 'contract_id', None)
        )
        
        success_msg = f"Created message {message_id} from {message.sender_id} to {message.receiver_id}"
        logger("MESSAGE", success_msg, "POST /messages", "INFO")
        return ResponseSchema.success(new_message, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("MESSAGE", error_msg, "POST /messages", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create message: {str(e)}"
        logger("MESSAGE", error_msg, "POST /messages", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.put("/{message_id}", response_model=MessageResponse)
async def update_message(message_id: str, message_update: MessageUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update message information - Authenticated users only"""
    try:
        existing_message = MessageFunctions.get_message_by_id(message_id)
        if not existing_message:
            error_msg = f"Message {message_id} not found"
            logger("MESSAGE", error_msg, "PUT /messages/{message_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        if str(existing_message["sender_id"]) != str(current_user.user_id):
            return ResponseSchema.error("Cannot update another user's message", 403)
        
        update_data = message_update.model_dump(exclude_unset=True)
        updated_message = MessageFunctions.update_message(message_id, update_data)
        
        success_msg = f"Updated message {message_id}"
        logger("MESSAGE", success_msg, "PUT /messages/{message_id}", "INFO")
        return ResponseSchema.success(updated_message, 200)
    except Exception as e:
        error_msg = f"Failed to update message {message_id}: {str(e)}"
        logger("MESSAGE", error_msg, "PUT /messages/{message_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@message_router.delete("/{message_id}", status_code=200)
async def delete_message(message_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a message - Authenticated users only"""
    try:
        existing_message = MessageFunctions.get_message_by_id(message_id)
        if not existing_message:
            error_msg = f"Message {message_id} not found"
            logger("MESSAGE", error_msg, "DELETE /messages/{message_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        if str(existing_message["sender_id"]) != str(current_user.user_id):
            return ResponseSchema.error("Cannot delete another user's message", 403)
        
        MessageFunctions.delete_message(message_id)
        
        success_msg = f"Deleted message {message_id}"
        logger("MESSAGE", success_msg, "DELETE /messages/{message_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete message {message_id}: {str(e)}"
        logger("MESSAGE", error_msg, "DELETE /messages/{message_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
