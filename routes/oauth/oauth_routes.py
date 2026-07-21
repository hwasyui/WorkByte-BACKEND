import os
import sys

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from functions.logger import logger
from functions.oauth import (
    FRONTEND_URL,
    exchange_google_code,
    find_or_create_oauth_user,
    generate_state,
    get_google_auth_url,
    verify_google_id_token,
    verify_state,
)
from functions.response_utils import ResponseSchema
from functions.schema_model import GoogleMobileTokenRequest

oauth_router = APIRouter(prefix="/auth/oauth", tags=["OAuth"])


def _success_response(token_data: dict):
    """
    After a successful OAuth callback return either:
      - a redirect to FRONTEND_URL with the token in query params (production SPA), or
      - a JSON response (when FRONTEND_URL is not configured, useful for dev/testing).
    """
    if FRONTEND_URL:
        url = (
            f"{FRONTEND_URL.rstrip('/')}/auth/oauth/callback"
            f"?token={token_data['access_token']}"
            f"&is_new_user={'true' if token_data.get('is_new_user') else 'false'}"
        )
        return RedirectResponse(url, status_code=302)
    # ResponseSchema.success already returns a JSONResponse -- return it directly (wrapping it
    # in JSONResponse() again double-encodes and 500s on the dev/no-FRONTEND_URL path).
    return ResponseSchema.success(token_data, 200)


def _error_response(message: str, status_code: int = 400):
    if FRONTEND_URL:
        url = f"{FRONTEND_URL.rstrip('/')}/auth/oauth/callback?error={message}"
        return RedirectResponse(url, status_code=302)
    return ResponseSchema.error(message, status_code)

@oauth_router.get("/google")
async def google_login():
    """Redirect the browser to Google's OAuth consent screen."""
    try:
        state = generate_state()
        url = get_google_auth_url(state)
        return RedirectResponse(url, status_code=302)
    except Exception as e:
        logger("OAUTH", f"Google login initiation failed: {str(e)}", "GET /auth/oauth/google", "ERROR")
        return _error_response(str(e), 501)


@oauth_router.get("/google/callback")
async def google_callback(code: str = None, state: str = None, error: str = None):
    """Handle Google's redirect back with an authorization code."""
    if error:
        logger("OAUTH", f"Google OAuth denied by user: {error}", "GET /auth/oauth/google/callback", "WARNING")
        return _error_response("Google login was cancelled or denied")

    if not code:
        return _error_response("Missing authorization code from Google")

    if not state or not verify_state(state):
        logger("OAUTH", "Google callback received invalid state", "GET /auth/oauth/google/callback", "WARNING")
        return _error_response("Invalid state parameter, possible CSRF attempt", 400)

    try:
        user_info = exchange_google_code(code)
        email     = user_info.get("email")
        sub       = user_info.get("sub")
        name      = user_info.get("name") or user_info.get("given_name") or email

        if not email or not sub:
            return _error_response("Google did not return an email address")

        token_data = find_or_create_oauth_user(
            provider="google",
            provider_user_id=sub,
            email=email,
            full_name=name,
        )
        logger("OAUTH", f"Google login success: {email}", "GET /auth/oauth/google/callback", "INFO")
        return _success_response(token_data)

    except Exception as e:
        logger("OAUTH", f"Google callback error: {str(e)}", "GET /auth/oauth/google/callback", "ERROR")
        return _error_response("Google authentication failed")


@oauth_router.post("/google/mobile")
async def google_mobile_login(payload: GoogleMobileTokenRequest):
    """Verify a Google ID token from the Flutter google_sign_in SDK and return a WorkByte JWT."""
    try:
        user_info = verify_google_id_token(payload.id_token)
        token_data = find_or_create_oauth_user(
            provider="google",
            provider_user_id=user_info["sub"],
            email=user_info["email"],
            full_name=user_info["name"],
        )
        logger("OAUTH", f"Google mobile login: {user_info['email']}", "POST /auth/oauth/google/mobile", "INFO")
        return ResponseSchema.success(token_data, 200)
    except Exception as e:
        if isinstance(e, HTTPException):
            logger("OAUTH", f"Google mobile login failed: {e.detail}", "POST /auth/oauth/google/mobile", "WARNING")
            return ResponseSchema.error(e.detail, e.status_code)
        logger("OAUTH", f"Google mobile login error: {str(e)}", "POST /auth/oauth/google/mobile", "ERROR")
        return ResponseSchema.error("Google authentication failed", 500)


