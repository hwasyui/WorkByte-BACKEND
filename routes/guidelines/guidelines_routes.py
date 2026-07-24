import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException
from functions.schema_model import GuidelineAckRequest, UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_user_owns
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.guidelines.guidelines_functions import GuidelineFunctions

guidelines_router = APIRouter(prefix="/users", tags=["Guidelines"])


@guidelines_router.get("/{user_id}/guidelines-ack", response_model=None)
async def get_guidelines_ack(user_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch which guideline sections the user has acknowledged - own account only."""
    try:
        assert_user_owns(current_user, user_id)
        ack_status = GuidelineFunctions.get_ack_status(user_id)
        success_msg = f"Retrieved guideline ack status for user {user_id}"
        logger("GUIDELINES", success_msg, "GET /users/{user_id}/guidelines-ack", "INFO")
        return ResponseSchema.success(ack_status, 200)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to fetch guideline ack status for user {user_id}: {str(e)}"
        logger("GUIDELINES", error_msg, "GET /users/{user_id}/guidelines-ack", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@guidelines_router.post("/{user_id}/guidelines-ack", response_model=None)
async def acknowledge_guideline_section(
    user_id: str,
    payload: GuidelineAckRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Mark one guideline section as read - own account only."""
    try:
        assert_user_owns(current_user, user_id)
        ack_status = GuidelineFunctions.ack_section(user_id, payload.section)
        success_msg = f"User {user_id} acknowledged guideline section '{payload.section}'"
        logger("GUIDELINES", success_msg, "POST /users/{user_id}/guidelines-ack", "INFO")
        return ResponseSchema.success(ack_status, 200)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to acknowledge guideline section for user {user_id}: {str(e)}"
        logger("GUIDELINES", error_msg, "POST /users/{user_id}/guidelines-ack", "ERROR")
        return ResponseSchema.error(error_msg, 500)
