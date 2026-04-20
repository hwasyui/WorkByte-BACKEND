import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List
from functions.schema_model import MessageCreate, MessageResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.messages.message_functions import MessageFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions


message_router = APIRouter(prefix="/messages", tags=["Messages"])


def _check_participant(contract: dict, current_user: UserInDB) -> bool:
    if current_user.type == "freelancer":
        profile = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        return bool(profile and str(profile["freelancer_id"]) == str(contract["freelancer_id"]))
    elif current_user.type == "client":
        profile = ClientFunctions.get_client_by_user_id(current_user.user_id)
        return bool(profile and str(profile["client_id"]) == str(contract["client_id"]))
    return False


@message_router.get("/contract/{contract_id}", response_model=List[MessageResponse])
async def get_messages_by_contract(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = MessageFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if not _check_participant(contract, current_user):
            return ResponseSchema.error("You are not allowed to view messages for this contract", 403)

        messages = MessageFunctions.get_messages_by_contract_id(contract_id)
        logger("MESSAGE", f"Retrieved {len(messages)} messages for contract {contract_id}", "GET /messages/contract/{contract_id}", "INFO")
        return ResponseSchema.success(messages, 200)
    except Exception as e:
        logger("MESSAGE", f"Failed to fetch contract messages: {str(e)}", "GET /messages/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch messages: {str(e)}", 500)


@message_router.post("", response_model=MessageResponse, status_code=201)
async def create_message(
    payload: MessageCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = MessageFunctions.get_contract_by_id(payload.contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {payload.contract_id} not found", 404)

        if not _check_participant(contract, current_user):
            return ResponseSchema.error("You are not allowed to send messages in this contract", 403)

        if not payload.message_text or not payload.message_text.strip():
            return ResponseSchema.error("message_text cannot be empty", 400)

        if current_user.type == "client":
            receiver_profile = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            if not receiver_profile:
                return ResponseSchema.error("Freelancer not found", 404)
            receiver_id = str(receiver_profile["user_id"])
        else:
            receiver_profile = ClientFunctions.get_client_by_id(str(contract["client_id"]))
            if not receiver_profile:
                return ResponseSchema.error("Client not found", 404)
            receiver_id = str(receiver_profile["user_id"])

        created = MessageFunctions.create_message(
            sender_id=str(current_user.user_id),
            receiver_id=receiver_id,
            contract_id=payload.contract_id,
            message_text=payload.message_text,
            message_type="user",
        )

        logger("MESSAGE", f"Message sent in contract {payload.contract_id} by user {current_user.user_id}", "POST /messages", "INFO")
        return ResponseSchema.success(created, 201)
    except Exception as e:
        logger("MESSAGE", f"Failed to create message: {str(e)}", "POST /messages", "ERROR")
        return ResponseSchema.error(f"Failed to create message: {str(e)}", 500)


@message_router.put("/contract/{contract_id}/read")
async def mark_messages_as_read(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = MessageFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if not _check_participant(contract, current_user):
            return ResponseSchema.error("You are not allowed to update messages for this contract", 403)

        updated_count = MessageFunctions.mark_messages_as_read(
            contract_id=contract_id,
            user_id=str(current_user.user_id),
        )

        logger("MESSAGE", f"Marked messages as read for user {current_user.user_id} in contract {contract_id}", "PUT /messages/contract/{contract_id}/read", "INFO")
        return ResponseSchema.success({"updated_count": updated_count}, 200)
    except Exception as e:
        logger("MESSAGE", f"Failed to mark messages as read: {str(e)}", "PUT /messages/contract/{contract_id}/read", "ERROR")
        return ResponseSchema.error(f"Failed to mark messages as read: {str(e)}", 500)