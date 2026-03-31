from fastapi import APIRouter, HTTPException, Depends, status
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
            company_name=user.company_name
        )
        success_msg = f"User {user.email} registered successfully as {user.user_type}"
        logger("AUTH", success_msg, "POST /auth/register", "INFO")
        return {"status": "success", "reason": success_msg, "data": result}
    except HTTPException as e:
        error_msg = f"Registration failed: {str(e.detail)}"
        logger("AUTH", error_msg, "POST /auth/register", "ERROR")
        raise HTTPException(status_code=e.status_code, detail={"status": "error", "reason": error_msg})
    except Exception as e:
        error_msg = f"Registration error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/register", "ERROR")
        raise HTTPException(status_code=500, detail={"status": "error", "reason": error_msg})

@auth_router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    """Login user with email and password, return JWT token."""
    try:
        user = authenticate_user(credentials.email, credentials.password)
        if not user:
            error_msg = f"Login failed for {credentials.email}: Invalid email or password"
            logger("AUTH", error_msg, "POST /auth/login", "WARNING")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"status": "error", "reason": "Incorrect email or password"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        success_msg = f"Login successful for {user.email}"
        logger("AUTH", success_msg, "POST /auth/login", "INFO")
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException as e:
        if e.detail and isinstance(e.detail, dict):
            logger("AUTH", f"Login error: {e.detail.get('reason', str(e.detail))}", "POST /auth/login", "WARNING")
        raise e
    except Exception as e:
        error_msg = f"Login authentication error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/login", "ERROR")
        raise HTTPException(status_code=500, detail={"status": "error", "reason": error_msg})

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
        return response
    except Exception as e:
        error_msg = f"Failed to retrieve user info: {str(e)}"
        logger("AUTH", error_msg, "GET /auth/me", "ERROR")
        raise HTTPException(status_code=500, detail={"status": "error", "reason": error_msg})