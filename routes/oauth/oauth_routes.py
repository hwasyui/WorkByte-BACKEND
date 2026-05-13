import os
import sys

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from functions.logger import logger
from functions.oauth import (
    FRONTEND_URL,
    exchange_google_code,
    exchange_linkedin_code,
    find_or_create_oauth_user,
    generate_state,
    get_google_auth_url,
    get_linkedin_auth_url,
    verify_state,
)
from functions.response_utils import ResponseSchema

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
    return JSONResponse(ResponseSchema.success(token_data, 200))


def _error_response(message: str, status_code: int = 400):
    if FRONTEND_URL:
        url = f"{FRONTEND_URL.rstrip('/')}/auth/oauth/callback?error={message}"
        return RedirectResponse(url, status_code=302)
    return JSONResponse(ResponseSchema.error(message, status_code), status_code=status_code)


# ── Google ────────────────────────────────────────────────────────────────────

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
        return _error_response("Invalid state parameter — possible CSRF attempt", 400)

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


# ── LinkedIn ──────────────────────────────────────────────────────────────────

@oauth_router.get("/linkedin")
async def linkedin_login():
    """Redirect the browser to LinkedIn's OAuth consent screen."""
    try:
        state = generate_state()
        url = get_linkedin_auth_url(state)
        return RedirectResponse(url, status_code=302)
    except Exception as e:
        logger("OAUTH", f"LinkedIn login initiation failed: {str(e)}", "GET /auth/oauth/linkedin", "ERROR")
        return _error_response(str(e), 501)


@oauth_router.get("/linkedin/callback")
async def linkedin_callback(code: str = None, state: str = None, error: str = None):
    """Handle LinkedIn's redirect back with an authorization code."""
    if error:
        logger("OAUTH", f"LinkedIn OAuth denied: {error}", "GET /auth/oauth/linkedin/callback", "WARNING")
        return _error_response("LinkedIn login was cancelled or denied")

    if not code:
        return _error_response("Missing authorization code from LinkedIn")

    if not state or not verify_state(state):
        logger("OAUTH", "LinkedIn callback received invalid state", "GET /auth/oauth/linkedin/callback", "WARNING")
        return _error_response("Invalid state parameter — possible CSRF attempt", 400)

    try:
        user_info = exchange_linkedin_code(code)
        email     = user_info.get("email")
        sub       = user_info.get("sub")
        name      = user_info.get("name") or email

        if not email or not sub:
            return _error_response("LinkedIn did not return an email address")

        token_data = find_or_create_oauth_user(
            provider="linkedin",
            provider_user_id=sub,
            email=email,
            full_name=name,
        )
        logger("OAUTH", f"LinkedIn login success: {email}", "GET /auth/oauth/linkedin/callback", "INFO")
        return _success_response(token_data)

    except Exception as e:
        logger("OAUTH", f"LinkedIn callback error: {str(e)}", "GET /auth/oauth/linkedin/callback", "ERROR")
        return _error_response("LinkedIn authentication failed")
