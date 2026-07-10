import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import UserCreate, UserUpdate, UserResponseDetail
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_user_owns
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.users.users_functions import UserFunctions
from routes.contracts.contract_functions import ContractFunctions
from routes.notifications.notification_functions import NotificationFunctions

users_router = APIRouter(prefix="/users", tags=["Users"])

# Statuses that mean a contract is still unresolved - completed/cancelled are the
# only terminal states, everything else means real work or money is still pending.
NON_TERMINAL_CONTRACT_STATUSES = {"active", "under_review", "revision_requested", "disputed"}


@users_router.get("", response_model=List[UserResponseDetail])
async def get_all_users(limit: Optional[int] = None, offset: int = 0, current_user: UserInDB = Depends(get_current_user)):
    """Fetch current user only - Authenticated users only - JSON response."""
    try:
        user = UserFunctions.get_user_by_id(current_user.user_id)
        success_msg = f"Retrieved current user {current_user.user_id}"
        logger("USER", success_msg, "GET /users", "INFO")
        return ResponseSchema.success([user], 200)
    except Exception as e:
        error_msg = f"Failed to fetch users: {str(e)}"
        logger("USER", error_msg, "GET /users", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@users_router.get("/search", response_model=Dict)
async def search_users(
    name: str = Query(..., untu="User email or name to search for"),
    current_user: UserInDB = Depends(get_current_user),
):
    """Search users by email - Authenticated users only - JSON response."""
    try:
        users = UserFunctions.search_users(name)
        logger("USER", f"Searched users for '{name}', found {len(users)} results", "GET /users/search", "INFO")
        return ResponseSchema.success({"results": users, "count": len(users)}, 200)
    except Exception as e:
        error_msg = f"Failed to search users with term '{name}': {str(e)}"
        logger("USER", error_msg, "GET /users/search", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@users_router.get("/{user_id}", response_model=UserResponseDetail)
async def get_user(user_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single user by ID - Authenticated users only - JSON response."""
    try:
        assert_user_owns(current_user, user_id)
        user = UserFunctions.get_user_by_id(user_id)
        if not user:
            error_msg = f"User {user_id} not found"
            logger("USER", error_msg, "GET /users/{user_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved user {user_id}"
        logger("USER", success_msg, "GET /users/{user_id}", "INFO")
        return ResponseSchema.success(user, 200)
    except HTTPException as e:
        logger("USER", f"HTTP {e.status_code}: {e.detail}", "GET /users/{user_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to fetch user {user_id}: {str(e)}"
        logger("USER", error_msg, "GET /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@users_router.post("", response_model=UserResponseDetail, status_code=201)
async def create_user(user: UserCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new user - Authenticated users only - JSON body accepted."""
    try:
        # Generate UUID if not provided
        user_id = user.user_id or str(uuid.uuid4())
        
        # Check if user already exists
        existing_user = UserFunctions.get_user_by_email(user.email)
        if existing_user:
            error_msg = f"Email {user.email} already registered"
            logger("USER", error_msg, "POST /users", "WARNING")
            return ResponseSchema.error(error_msg, 400)
        
        new_user = UserFunctions.create_user(
            user_id=user_id,
            email=user.email,
            password=user.password,
        )
        success_msg = f"Created user {user_id} with email {user.email}"
        logger("USER", success_msg, "POST /users", "INFO")
        return ResponseSchema.success(new_user, 201)
    except Exception as e:
        error_msg = f"Failed to create user: {str(e)}"
        logger("USER", error_msg, "POST /users", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@users_router.put("/{user_id}", response_model=UserResponseDetail)
async def update_user(user_id: str, user_update: UserUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update user information - Authenticated users only."""
    try:
        assert_user_owns(current_user, user_id)
        # Check if user exists
        existing_user = UserFunctions.get_user_by_id(user_id)
        if not existing_user:
            error_msg = f"User {user_id} not found for update"
            logger("USER", error_msg, "PUT /users/{user_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        # If email is being updated, check if it's already in use
        if user_update.email:
            email_user = UserFunctions.get_user_by_email(user_update.email)
            if email_user and email_user['user_id'] != user_id:
                error_msg = f"Email {user_update.email} already registered"
                logger("USER", error_msg, "PUT /users/{user_id}", "WARNING")
                return ResponseSchema.error(error_msg, 400)
        
        update_data = {k: v for k, v in user_update.dict().items() if v is not None}
        updated_user = UserFunctions.update_user(user_id, update_data)
        
        success_msg = f"Updated user {user_id} with fields: {', '.join(update_data.keys())}"
        logger("USER", success_msg, "PUT /users/{user_id}", "INFO")
        return ResponseSchema.success(updated_user, 200)
    except HTTPException as e:
        logger("USER", f"HTTP {e.status_code}: {e.detail}", "PUT /users/{user_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to update user {user_id}: {str(e)}"
        logger("USER", error_msg, "PUT /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@users_router.delete("/{user_id}", status_code=200)
async def delete_user(user_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a user - Authenticated users only."""
    try:
        assert_user_owns(current_user, user_id)
        # Check if user exists
        existing_user = UserFunctions.get_user_by_id(user_id)
        if not existing_user:
            error_msg = f"User {user_id} not found for deletion"
            logger("USER", error_msg, "DELETE /users/{user_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)

        # contract.freelancer_id/client_id are ON DELETE RESTRICT - without this
        # pre-check, deleting a user with any contract raises a raw DB integrity
        # error that surfaces as an ugly 500 further down.
        contracts = ContractFunctions.get_contracts_by_user_id(user_id)
        in_progress = [c for c in contracts if c["status"] in NON_TERMINAL_CONTRACT_STATUSES]
        if in_progress:
            error_msg = (
                f"Cannot delete this account - {len(in_progress)} contract(s) are still in "
                "progress (active, under review, revision requested, or disputed). "
                "You can delete your account once they're completed or cancelled."
            )
            logger("USER", error_msg, "DELETE /users/{user_id}", "WARNING")
            await NotificationFunctions.notify(
                recipient_user_id=user_id,
                notif_type="account_deletion_blocked",
                title="Account Deletion Blocked",
                body="You tried to delete your account, but you still have contract(s) in "
                     "progress. Finish or cancel them first, then you can delete your account.",
                data={"contract_ids": [c["contract_id"] for c in in_progress]},
            )
            return ResponseSchema.error(error_msg, 409)

        if contracts:
            # Only completed/cancelled contracts remain. contract.freelancer_id/client_id
            # are ON DELETE RESTRICT with no exception for terminal statuses, so the
            # database itself still refuses this delete - lifting that would mean
            # anonymizing the account instead of hard-deleting it (schema change), not
            # something to silently attempt here.
            error_msg = (
                "Cannot delete this account - it has completed contract history that the "
                "platform retains permanently. Contact support if you need this handled."
            )
            logger("USER", error_msg, "DELETE /users/{user_id}", "WARNING")
            return ResponseSchema.error(error_msg, 409)

        UserFunctions.delete_user(user_id)
        success_msg = f"User {user_id} deleted successfully"
        logger("USER", success_msg, "DELETE /users/{user_id}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except HTTPException as e:
        logger("USER", f"HTTP {e.status_code}: {e.detail}", "DELETE /users/{user_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Failed to delete user {user_id}: {str(e)}"
        logger("USER", error_msg, "DELETE /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
