import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import UserCreate, UserUpdate, UserResponseDetail
from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_admin_user
from functions.access_control import assert_user_owns
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.users.users_functions import UserFunctions

users_router = APIRouter(prefix="/users", tags=["Users"])


# dev/admin only - not called by the Flutter app
@users_router.get("", response_model=None)
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


# dev/admin only - not called by the Flutter app
@users_router.get("/search", response_model=None)
async def search_users(
    name: str = Query(..., description="User email or name to search for"),
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


# dev/admin only - not called by the Flutter app
@users_router.get("/{user_id}", response_model=None)
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
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to fetch user {user_id}: {str(e)}"
        logger("USER", error_msg, "GET /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# dev/admin only - the app signs people up through POST /auth/register, which sends the
# verification OTP. This one writes a user straight to the table, so it stays admin-gated:
# left open it would mint accounts that skip email verification entirely.
@users_router.post("", response_model=None, status_code=201)
async def create_user(user: UserCreate, current_user: UserInDB = Depends(get_admin_user)):
    """Create a new user directly - admin only - JSON body accepted."""
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


# dev/admin only - not called by the Flutter app
@users_router.put("/{user_id}", response_model=None)
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
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to update user {user_id}: {str(e)}"
        logger("USER", error_msg, "PUT /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# dev/admin only - not called by the Flutter app
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
        
        UserFunctions.delete_user(user_id)
        success_msg = f"User {user_id} deleted successfully"
        logger("USER", success_msg, "DELETE /users/{user_id}", "INFO")
        return ResponseSchema.success(success_msg, 200)
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to delete user {user_id}: {str(e)}"
        logger("USER", error_msg, "DELETE /users/{user_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
