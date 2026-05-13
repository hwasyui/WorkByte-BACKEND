import hashlib
import hmac
import os
import secrets
import sys
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger

# ── Config ────────────────────────────────────────────────────────────────────

_SECRET_KEY = os.getenv("SECRET_KEY", "")

GOOGLE_CLIENT_ID      = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET  = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_REDIRECT_URI   = os.getenv("GOOGLE_OAUTH_REDIRECT_URI",
                                   "http://localhost:8000/auth/oauth/google/callback")

LINKEDIN_CLIENT_ID     = os.getenv("LINKEDIN_OAUTH_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_OAUTH_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI  = os.getenv("LINKEDIN_OAUTH_REDIRECT_URI",
                                    "http://localhost:8000/auth/oauth/linkedin/callback")

FRONTEND_URL = os.getenv("FRONTEND_URL", "")

SUPPORTED_PROVIDERS = {"google", "linkedin"}


# ── CSRF state helpers ────────────────────────────────────────────────────────

def generate_state() -> str:
    """Return a self-verifying HMAC-signed state token (no server-side storage)."""
    token = secrets.token_urlsafe(32)
    sig = hmac.new(_SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{sig}"


def verify_state(state: str) -> bool:
    try:
        token, sig = state.rsplit(".", 1)
        expected = hmac.new(_SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


# ── Google ────────────────────────────────────────────────────────────────────

def get_google_auth_url(state: str) -> str:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured on this server")
    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_google_code(code: str) -> dict:
    """Return {sub, email, name} from a Google authorization code."""
    try:
        with httpx.Client(timeout=15) as client:
            token_resp = client.post("https://oauth2.googleapis.com/token", data={
                "code":          code,
                "client_id":     GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri":  GOOGLE_REDIRECT_URI,
                "grant_type":    "authorization_code",
            })
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            info_resp = client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            info_resp.raise_for_status()
            return info_resp.json()
    except httpx.HTTPStatusError as e:
        logger("OAUTH", f"Google token exchange failed: {e.response.text}", level="ERROR")
        raise HTTPException(status_code=502, detail="Google authentication failed")
    except Exception as e:
        logger("OAUTH", f"Google OAuth error: {str(e)}", level="ERROR")
        raise HTTPException(status_code=502, detail="Google authentication failed")


# ── LinkedIn ──────────────────────────────────────────────────────────────────

def get_linkedin_auth_url(state: str) -> str:
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=501, detail="LinkedIn OAuth is not configured on this server")
    params = {
        "response_type": "code",
        "client_id":     LINKEDIN_CLIENT_ID,
        "redirect_uri":  LINKEDIN_REDIRECT_URI,
        "scope":         "openid profile email",
        "state":         state,
    }
    return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"


def exchange_linkedin_code(code: str) -> dict:
    """Return {sub, email, name} from a LinkedIn authorization code."""
    try:
        with httpx.Client(timeout=15) as client:
            token_resp = client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "redirect_uri":  LINKEDIN_REDIRECT_URI,
                    "client_id":     LINKEDIN_CLIENT_ID,
                    "client_secret": LINKEDIN_CLIENT_SECRET,
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            info_resp = client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            info_resp.raise_for_status()
            return info_resp.json()
    except httpx.HTTPStatusError as e:
        logger("OAUTH", f"LinkedIn token exchange failed: {e.response.text}", level="ERROR")
        raise HTTPException(status_code=502, detail="LinkedIn authentication failed")
    except Exception as e:
        logger("OAUTH", f"LinkedIn OAuth error: {str(e)}", level="ERROR")
        raise HTTPException(status_code=502, detail="LinkedIn authentication failed")


# ── Unified user lookup / creation ────────────────────────────────────────────

def find_or_create_oauth_user(
    provider: str,
    provider_user_id: str,
    email: str,
    full_name: str,
) -> dict:
    """
    Resolve an OAuth identity to an app user and return a JWT.

    Priority:
      1. provider_user_id already linked  → return existing user's token
      2. email already in users           → link provider, return existing user's token
      3. new email                        → create user (email_verified=True), link provider
    """
    from functions.db_manager import get_db
    from functions.authentication import (
        _build_user_from_row,
        create_access_token,
        get_password_hash,
        ACCESS_TOKEN_EXPIRE_MINUTES,
    )

    # ── 1. Existing provider link ─────────────────────────────────────────────
    rows = get_db().execute_query(
        """
        SELECT u.user_id, u.email, u.password, u.is_admin, u.email_verified,
               u.is_report_banned, u.ban_message, u.report_banned_at,
               f.freelancer_id,
               c.client_id
        FROM user_oauth_providers p
        JOIN users u ON u.user_id = p.user_id
        LEFT JOIN freelancer f ON f.user_id = u.user_id
        LEFT JOIN client     c ON c.user_id = u.user_id
        WHERE p.provider = :provider AND p.provider_user_id = :provider_user_id
        """,
        params={"provider": provider, "provider_user_id": provider_user_id},
    )
    if rows:
        user = _build_user_from_row(rows[0])
        token = create_access_token({"sub": user.email}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        logger("OAUTH", f"Existing OAuth link used: {provider} → {user.email}", level="INFO")
        return {"access_token": token, "token_type": "bearer", "is_new_user": False}

    # ── 2. Email already registered (manual or other provider) ───────────────
    existing = get_db().execute_query(
        """
        SELECT u.user_id, u.email, u.password, u.is_admin, u.email_verified,
               u.is_report_banned, u.ban_message, u.report_banned_at,
               f.freelancer_id,
               c.client_id
        FROM users u
        LEFT JOIN freelancer f ON f.user_id = u.user_id
        LEFT JOIN client     c ON c.user_id = u.user_id
        WHERE u.email = :email
        """,
        params={"email": email},
    )
    if existing:
        user = _build_user_from_row(existing[0])
        get_db().execute_query(
            """
            INSERT INTO user_oauth_providers (user_id, provider, provider_user_id, provider_email)
            VALUES (:user_id, :provider, :provider_user_id, :provider_email)
            ON CONFLICT (provider, provider_user_id) DO NOTHING
            """,
            params={
                "user_id":          user.user_id,
                "provider":         provider,
                "provider_user_id": provider_user_id,
                "provider_email":   email,
            },
        )
        # OAuth confirms the email — mark verified if it wasn't already
        if not user.email_verified:
            get_db().execute_query(
                "UPDATE users SET email_verified = TRUE, email_verified_at = NOW() WHERE user_id = :uid",
                params={"uid": user.user_id},
            )
        token = create_access_token({"sub": user.email}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
        logger("OAUTH", f"OAuth linked to existing account: {provider} → {user.email}", level="INFO")
        return {"access_token": token, "token_type": "bearer", "is_new_user": False}

    # ── 3. Brand new user ─────────────────────────────────────────────────────
    # Store a random hash so the password column is never NULL while still
    # being unguessable. The user can set a real password via forgot-password.
    random_hash = get_password_hash(secrets.token_urlsafe(32))
    user_rows = get_db().execute_query(
        """
        INSERT INTO users (email, password, email_verified, email_verified_at)
        VALUES (:email, :password, TRUE, NOW())
        RETURNING user_id
        """,
        params={"email": email, "password": random_hash},
    )
    if not user_rows:
        raise HTTPException(status_code=500, detail="Failed to create user account")

    user_id = str(user_rows[0]["user_id"])

    get_db().execute_query(
        """
        INSERT INTO user_oauth_providers (user_id, provider, provider_user_id, provider_email)
        VALUES (:user_id, :provider, :provider_user_id, :provider_email)
        """,
        params={
            "user_id":          user_id,
            "provider":         provider,
            "provider_user_id": provider_user_id,
            "provider_email":   email,
        },
    )

    token = create_access_token({"sub": email}, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    logger("OAUTH", f"New user created via OAuth: {provider} → {email}", level="INFO")
    return {
        "access_token": token,
        "token_type":   "bearer",
        "is_new_user":  True,
        "user_id":      user_id,
        "email":        email,
        "note":         "No profile yet — call POST /auth/add-role to set up your freelancer or client profile.",
    }
