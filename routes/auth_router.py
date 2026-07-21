from fastapi import APIRouter, Depends, HTTPException, status
from datetime import timedelta
import sys, os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.authentication import (
    authenticate_user,
    change_password,
    create_access_token,
    create_refresh_token,
    use_refresh_token,
    revoke_refresh_token,
    register_user,
    add_role,
    verify_token,
    TokenData,
    UserInDB,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    get_user,
    get_current_user,
    get_password_hash,
    resend_email_verification,
    verify_email_otp,
    request_password_reset,
    reset_password,
    set_password,
)
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.schema_model import (
    AddRoleRequest,
    ChangePasswordRequest,
    EmailVerificationRequest,
    RefreshRequest,
    ResendVerificationRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    SetPasswordRequest,
    UserRegister,
    UserLogin,
    Token,
    UserResponse,
)

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


@auth_router.post("/register", response_model=None)
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


@auth_router.post("/verify-email", response_model=None)
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


@auth_router.post("/resend-verification", response_model=None)
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


@auth_router.post("/login", response_model=None)
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
        refresh_token = create_refresh_token(user.user_id)

        logger("AUTH", f"Login successful for {credentials.email}", "POST /auth/login", "INFO")
        return ResponseSchema.success({
            "access_token":  access_token,
            "token_type":    "bearer",
            "expires_in":    ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_token": refresh_token,
            "refresh_token_expires_in": REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        }, 200)

    except HTTPException as e:
        logger("AUTH", f"Login failed for {credentials.email}: {e.detail}", "POST /auth/login", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Login error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/login", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.get("/me", response_model=None)
async def get_me(current_user: UserInDB = Depends(get_current_user)):
    """Get current authenticated user info including active profile IDs."""
    try:
        response = UserResponse(
            user_id=current_user.user_id,
            email=current_user.email,
            password_login_enabled=current_user.password_login_enabled,
            email_verified=current_user.email_verified,
            is_admin=current_user.is_admin,
            freelancer_id=current_user.freelancer_id,
            client_id=current_user.client_id,
            is_report_banned=current_user.is_report_banned or False,
            ban_message=current_user.ban_message,
            report_banned_at=current_user.report_banned_at,
        )
        logger("AUTH", f"Retrieved user info for {current_user.email}", "GET /auth/me", "INFO")
        return ResponseSchema.success(response.model_dump(), 200)
    except Exception as e:
        error_msg = f"Failed to retrieve user info: {str(e)}"
        logger("AUTH", error_msg, "GET /auth/me", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@auth_router.post("/forgot-password", response_model=None)
async def forgot_password(request: ForgotPasswordRequest):
    """Request a password reset OTP sent to the given email."""
    try:
        result = request_password_reset(request.email)
        logger("AUTH", f"Password reset requested for {request.email}", "POST /auth/forgot-password", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Password reset request failed for {request.email}: {e.detail}", "POST /auth/forgot-password", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Password reset request error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/forgot-password", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/reset-password", response_model=None)
async def reset_password_route(request: ResetPasswordRequest):
    """Verify the OTP and set a new password."""
    try:
        result = reset_password(request.email, request.otp, request.new_password)
        logger("AUTH", f"Password reset successful for {request.email}", "POST /auth/reset-password", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Password reset failed for {request.email}: {e.detail}", "POST /auth/reset-password", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Password reset error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/reset-password", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/add-role", response_model=None)
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


@auth_router.post("/refresh", response_model=None)
async def refresh_token_endpoint(payload: RefreshRequest):
    """Exchange a valid refresh token for a new access token and rotated refresh token.

    The old refresh token is revoked immediately; each token can only be used once.
    """
    try:
        user, new_refresh = use_refresh_token(payload.refresh_token)
        access_token = create_access_token(
            data={"sub": user.email},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        )
        logger("AUTH", f"Token refreshed for {user.email}", "POST /auth/refresh", "INFO")
        return ResponseSchema.success({
            "access_token":  access_token,
            "token_type":    "bearer",
            "expires_in":    ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "refresh_token": new_refresh,
            "refresh_token_expires_in": REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        }, 200)
    except HTTPException as e:
        logger("AUTH", f"Token refresh failed: {e.detail}", "POST /auth/refresh", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Token refresh error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/refresh", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/change-password", response_model=None)
async def change_password_endpoint(
    payload: ChangePasswordRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Change password for the currently authenticated user.

    Requires the current password for verification. All existing refresh tokens
    are revoked on success, logging out other devices.
    """
    try:
        result = change_password(current_user, payload.old_password, payload.new_password)
        logger("AUTH", f"Password changed for {current_user.email}", "POST /auth/change-password", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Change password failed for {current_user.email}: {e.detail}", "POST /auth/change-password", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Change password error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/change-password", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/set-password", response_model=None)
async def set_password_endpoint(
    payload: SetPasswordRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Set the first real password for an authenticated OAuth-only account."""
    try:
        result = set_password(current_user, payload.new_password)
        logger("AUTH", f"Password login enabled for {current_user.email}", "POST /auth/set-password", "INFO")
        return ResponseSchema.success(result, 200)
    except HTTPException as e:
        logger("AUTH", f"Set password failed for {current_user.email}: {e.detail}", "POST /auth/set-password", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        error_msg = f"Set password error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/set-password", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@auth_router.post("/logout", response_model=None)
async def logout(payload: RefreshRequest):
    """Revoke the given refresh token so it can no longer be used to obtain new access tokens.

    The current access token remains valid until it naturally expires (max 30 min).
    """
    try:
        revoke_refresh_token(payload.refresh_token)
        logger("AUTH", "Refresh token revoked via logout", "POST /auth/logout", "INFO")
        return ResponseSchema.success({"message": "Logged out successfully"}, 200)
    except Exception as e:
        error_msg = f"Logout error: {str(e)}"
        logger("AUTH", error_msg, "POST /auth/logout", "ERROR")
        return ResponseSchema.error(error_msg, 500)
