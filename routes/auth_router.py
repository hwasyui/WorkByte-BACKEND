from fastapi import APIRouter, Depends, status
from datetime import timedelta
import sys, os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.authentication import (
    authenticate_user, 
    create_access_token, 
    register_user, 
    verify_token, 
    TokenData, 
    UserInDB, 
    ACCESS_TOKEN_EXPIRE_MINUTES, 
    get_user, 
    get_current_user,
    get_password_hash
)
from functions.database import Database
from functions.logger import logger
from functions.functions import db
from functions.response_utils import ResponseSchema
from functions.schema_model import UserRegister, UserLogin, Token, UserResponse
from typing import Dict

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

@auth_router.post("/register", response_model=dict)
async def register(user: UserRegister):
    """Register a new user (freelancer or client)."""
    try:
        result = register_user(
            email=user.email, 
            password=user.password, 
            user_type=user.user_type,
            full_name=user.full_name,
            company_name=user.company_name or user.full_name
        )
        success_msg = f"User {user.email} registered successfully as {user.user_type}"
        logger("AUTH", success_msg, "POST /auth/register", "INFO")
        return ResponseSchema.success(result, 201)
    except Exception as e:
        error_msg = f"Registration failed: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/register", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@auth_router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    """Login user with email and password, return JWT token."""
    try:
        user = authenticate_user(credentials.email, credentials.password)
        if not user:
            error_msg = f"Login failed for {credentials.email}: Invalid email or password"
            logger("AUTH", error_msg, "POST /auth/login", "WARNING")
            return ResponseSchema.error(error_msg, 401)
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        success_msg = f"Login successful for {user.email}"
        logger("AUTH", success_msg, "POST /auth/login", "INFO")
        return ResponseSchema.success({"access_token": access_token, "token_type": "bearer"}, 201)
    except Exception as e:
        error_msg = f"Login authentication error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/login", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@auth_router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserInDB = Depends(get_current_user)):
    """Get current authenticated user info."""
    try:
        response = UserResponse(
            user_id=current_user.user_id,
            email=current_user.email,
            type=current_user.type
        )
        logger("AUTH", f"Retrieved user info for {current_user.email} - type: {current_user.type}", "GET /auth/me", "INFO")
        return ResponseSchema.success(response.model_dump(), 200)
    except Exception as e:
        error_msg = f"Failed to retrieve user info: {str(e)}"
        logger("AUTH", error_msg, "GET /auth/me", "ERROR")
        return ResponseSchema.error(error_msg, 500)