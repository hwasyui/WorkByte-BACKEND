from fastapi import APIRouter, Depends, HTTPException, status
from datetime import timedelta
import sys, os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.authentication import (
    authenticate_user,
    create_access_token,
    register_user,
    add_role,
    verify_token,
    TokenData,
    UserInDB,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    get_user,
    get_current_user,
    get_password_hash,
    resend_email_verification,
    verify_email_otp,
)
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.schema_model import (
    AddRoleRequest,
    EmailVerificationRequest,
    ResendVerificationRequest,
    UserRegister,
    UserLogin,
    Token,
    UserResponse,
)

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
    except HTTPException as e:
        logger("AUTH", f"Registration failed: {e.detail}", "POST /auth/register", "ERROR")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Registration failed: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/register", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/verify-email", response_model=dict)
async def verify_email(request: EmailVerificationRequest):
    """Verify a newly registered user's email address with an OTP."""
    try:
        result = verify_email_otp(request.email, request.otp)
        logger("AUTH", f"Email verified for {request.email}", "POST /auth/verify-email", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Email verification failed for {request.email}: {e.detail}", "POST /auth/verify-email", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Email verification error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/verify-email", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/resend-verification", response_model=dict)
async def resend_verification(request: ResendVerificationRequest):
    """Send a fresh email verification OTP."""
    try:
        result = resend_email_verification(request.email)
        logger("AUTH", f"Verification email resent to {request.email}", "POST /auth/resend-verification", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Resend verification failed for {request.email}: {e.detail}", "POST /auth/resend-verification", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Resend verification error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/resend-verification", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/login", response_model=dict)
async def login(credentials: UserLogin):
    """Validate credentials and return JWT access token directly."""
    try:
        user = authenticate_user(credentials.email, credentials.password)
        if not user:
            logger("AUTH", f"Login failed for {credentials.email}: invalid credentials", "POST /auth/login", "WARNING")
            return ResponseSchema.error("Invalid email or password", 401)

        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.email}, expires_delta=access_token_expires
        )
        
        logger("AUTH", f"Login successful for {credentials.email}", "POST /auth/login", "INFO")
        return ResponseSchema.success({
            "access_token": access_token,
            "token_type": "bearer"
        }, 200)

    except HTTPException as e:
        logger("AUTH", f"Login failed for {credentials.email}: {e.detail}", "POST /auth/login", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Login error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/login", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.get("/me", response_model=UserResponse)
async def get_me(current_user: UserInDB = Depends(get_current_user)):
    """Get current authenticated user info including active profile IDs."""
    try:
        response = UserResponse(
            user_id=current_user.user_id,
            email=current_user.email,
            email_verified=current_user.email_verified,
            is_admin=current_user.is_admin,
            freelancer_id=current_user.freelancer_id,
            client_id=current_user.client_id,
        )
        logger("AUTH", f"Retrieved user info for {current_user.email}", "GET /auth/me", "INFO")
        return ResponseSchema.success(response.model_dump(), 200)
    except Exception as e:
        error_msg = f"Failed to retrieve user info: {str(e)}"
        logger("AUTH", error_msg, "GET /auth/me", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/add-role", response_model=dict)
async def add_second_role(
    payload: AddRoleRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Add a freelancer or client profile to an existing account."""
    try:
        result = add_role(current_user, payload.role, payload.full_name)
        logger("AUTH", f"Role '{payload.role}' added for user {current_user.user_id}", "POST /auth/add-role", "INFO")
        return ResponseSchema.success(result, 201)
    except HTTPException as e:
        logger("AUTH", f"Add role failed for {current_user.user_id}: {e.detail}", "POST /auth/add-role", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Add role failed: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/add-role", "ERROR")
        return ResponseSchema.error(error_msg, 500)
