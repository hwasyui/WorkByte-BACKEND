import os
import sys
import secrets
from datetime import datetime, timedelta
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.database import Database
from functions.logger import logger
from functions.db_manager import get_db
from functions.email_utils import send_otp_email, send_password_reset_email
from functions.schema_model import Token, TokenData, UserInDB

from dotenv import load_dotenv
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
OTP_EXPIRE_MINUTES = int(os.getenv("EMAIL_OTP_EXPIRE_MINUTES", "10"))
MAX_OTP_ATTEMPTS = int(os.getenv("EMAIL_OTP_MAX_ATTEMPTS", "5"))
APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
EMAIL_VERIFICATION_REQUIRED = os.getenv("EMAIL_VERIFICATION_REQUIRED", "true").lower() == "true"
SHOW_DEV_OTP = os.getenv("SHOW_DEV_OTP", "true").lower() == "true"
PRODUCTION_EMAIL_DELIVERY_REQUIRED = os.getenv("PRODUCTION_EMAIL_DELIVERY_REQUIRED", "true").lower() == "true"

# Initialize Argon2 password hasher
pwd_hasher = PasswordHasher()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def is_development_env() -> bool:
    return APP_ENV in {"dev", "development", "local", "test", "testing"}

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its Argon2 hash."""
    try:
        pwd_hasher.verify(hashed_password, plain_password)
        return True
    except VerifyMismatchError:
        return False
    except Exception as e:
        logger("AUTH", f"Password verification error: {str(e)}", level="ERROR")
        return False

def get_password_hash(password: str) -> str:
    """Hash a password using Argon2."""
    try:
        return pwd_hasher.hash(password)
    except Exception as e:
        logger("AUTH", f"Password hashing error: {str(e)}", level="ERROR")
        raise

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def generate_otp() -> str:
    """Generate a six-digit verification code."""
    return f"{secrets.randbelow(1_000_000):06d}"

def create_email_verification_otp(user_id: str, email: str) -> dict:
    """Create, store, and email a new verification OTP."""
    otp_code = generate_otp()
    otp_hash = get_password_hash(otp_code)
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)

    # Invalidate any previous active codes for the same user.
    get_db().execute_query(
        """
        UPDATE email_verification_otps
        SET consumed_at = NOW()
        WHERE user_id = :user_id AND consumed_at IS NULL
        """,
        params={"user_id": user_id}
    )

    get_db().execute_query(
        """
        INSERT INTO email_verification_otps (user_id, otp_hash, expires_at)
        VALUES (:user_id, :otp_hash, :expires_at)
        """,
        params={"user_id": user_id, "otp_hash": otp_hash, "expires_at": expires_at}
    )

    email_sent = send_otp_email(email, otp_code)
    if not email_sent and not is_development_env() and PRODUCTION_EMAIL_DELIVERY_REQUIRED:
        raise HTTPException(status_code=500, detail="Failed to send verification email")

    response = {
        "email_sent": email_sent,
        "expires_in_minutes": OTP_EXPIRE_MINUTES
    }
    if is_development_env() and SHOW_DEV_OTP:
        response["dev_verification_otp"] = otp_code
    return response

def verify_token(token: str, credentials_exception):
    """Verify JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    return token_data

def _build_user_from_row(row: dict) -> UserInDB:
    return UserInDB(
        user_id=str(row['user_id']),
        email=row['email'],
        password=row['password'],
        email_verified=bool(row['email_verified']),
        is_admin=bool(row.get('is_admin', False)),
        freelancer_id=str(row['freelancer_id']) if row.get('freelancer_id') else None,
        client_id=str(row['client_id']) if row.get('client_id') else None,
        is_report_banned=bool(row.get('is_report_banned', False)), 
        ban_message=row.get('ban_message'),                          
        report_banned_at=row.get('report_banned_at'),                
    )

_USER_QUERY = """
    SELECT u.user_id, u.email, u.password, u.is_admin, u.email_verified,
           u.is_report_banned, u.ban_message, u.report_banned_at,
           f.freelancer_id,
           c.client_id
    FROM users u
    LEFT JOIN freelancer f ON f.user_id = u.user_id
    LEFT JOIN client     c ON c.user_id = u.user_id
    WHERE u.email = :email
"""

def authenticate_user(email: str, password: str):
    """Authenticate user with email and password."""
    try:
        result = get_db().execute_query(_USER_QUERY, params={"email": email})
        if not result or len(result) == 0:
            return False
        user = result[0]
        if not verify_password(password, user['password']):
            return False
        if EMAIL_VERIFICATION_REQUIRED and not user['email_verified']:
            raise HTTPException(status_code=403, detail="Email is not verified")
        return _build_user_from_row(user)
    except HTTPException:
        raise
    except Exception as e:
        logger("AUTH", f"Authentication error: {str(e)}", level="ERROR")
        return False

def get_user(email: str):
    """Get user from database by email."""
    try:
        result = get_db().execute_query(_USER_QUERY, params={"email": email})
        if result and len(result) > 0:
            return _build_user_from_row(result[0])
    except Exception as e:
        logger("AUTH", f"Get user error: {str(e)}", level="ERROR")
    return None

def register_user(email: str, password: str, user_type: str = "freelancer", full_name: str = None, company_name: str = None):
    """Register new user and create the initial profile (freelancer or client)."""
    created_user_id = None
    try:
        if get_user(email):
            raise HTTPException(status_code=400, detail="Email already registered")

        if user_type not in ["freelancer", "client"]:
            raise HTTPException(status_code=400, detail="Initial role must be 'freelancer' or 'client'")

        hashed_password = get_password_hash(password)

        user_result = get_db().execute_query(
            "INSERT INTO users (email, password) VALUES (:email, :password) RETURNING user_id",
            params={"email": email, "password": hashed_password}
        )
        if not user_result:
            raise HTTPException(status_code=500, detail="Failed to create user record")

        user_id = str(user_result[0]['user_id'])
        created_user_id = user_id
        freelancer_id = None
        client_id = None

        if user_type == "freelancer" and full_name:
            profile_result = get_db().execute_query(
                "INSERT INTO freelancer (user_id, full_name) VALUES (:uid, :name) RETURNING freelancer_id",
                params={"uid": user_id, "name": full_name}
            )
            if not profile_result:
                raise HTTPException(status_code=500, detail="Failed to create freelancer profile")
            freelancer_id = str(profile_result[0]['freelancer_id'])
        elif user_type == "client":
            full_name_value = company_name or full_name or ""
            profile_result = get_db().execute_query(
                "INSERT INTO client (user_id, full_name) VALUES (:uid, :name) RETURNING client_id",
                params={"uid": user_id, "name": full_name_value}
            )
            if not profile_result:
                raise HTTPException(status_code=500, detail="Failed to create client profile")
            client_id = str(profile_result[0]['client_id'])

        verification = None
        if EMAIL_VERIFICATION_REQUIRED:
            verification = create_email_verification_otp(user_id, email)

        logger("AUTH", f"User registered: {email} as {user_type}", level="INFO")
        result = {
            "message": "User registered successfully. Please verify your email before logging in.",
            "user": {
                "user_id": user_id,
                "email": email,
                "email_verified": False,
                "is_admin": False,
                "freelancer_id": freelancer_id,
                "client_id": client_id,
            }
        }
        if verification:
            result["verification"] = verification
        return result

    except HTTPException as e:
        if created_user_id and e.status_code >= 500:
            try:
                get_db().execute_query("DELETE FROM users WHERE user_id = :uid", params={"uid": created_user_id})
            except Exception as cleanup_error:
                logger("AUTH", f"Registration cleanup error: {str(cleanup_error)}", level="ERROR")
        raise
    except Exception as e:
        if created_user_id:
            try:
                get_db().execute_query("DELETE FROM users WHERE user_id = :uid", params={"uid": created_user_id})
            except Exception as cleanup_error:
                logger("AUTH", f"Registration cleanup error: {str(cleanup_error)}", level="ERROR")
        logger("AUTH", f"Registration error: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail="Registration failed")

def add_role(current_user: UserInDB, role: str, full_name: str = None) -> dict:
    """Add a second profile (freelancer or client) to an existing user."""
    if role == "admin":
        raise HTTPException(status_code=403, detail="Admin role cannot be self-assigned")
    if role not in ("freelancer", "client"):
        raise HTTPException(status_code=400, detail="Role must be 'freelancer' or 'client'")

    if role == "freelancer":
        if current_user.freelancer_id:
            raise HTTPException(status_code=400, detail="You already have a freelancer profile")
        if not full_name:
            raise HTTPException(status_code=400, detail="full_name is required to create a freelancer profile")
        result = get_db().execute_query(
            "INSERT INTO freelancer (user_id, full_name) VALUES (:uid, :name) RETURNING freelancer_id",
            params={"uid": current_user.user_id, "name": full_name}
        )
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create freelancer profile")
        logger("AUTH", f"Freelancer profile added for user {current_user.user_id}", level="INFO")
        return {"role": "freelancer", "freelancer_id": str(result[0]["freelancer_id"])}

    else:  # client
        if current_user.client_id:
            raise HTTPException(status_code=400, detail="You already have a client profile")
        result = get_db().execute_query(
            "INSERT INTO client (user_id, full_name) VALUES (:uid, :name) RETURNING client_id",
            params={"uid": current_user.user_id, "name": full_name or ""}
        )
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create client profile")
        logger("AUTH", f"Client profile added for user {current_user.user_id}", level="INFO")
        return {"role": "client", "client_id": str(result[0]["client_id"])}

def verify_email_otp(email: str, otp_code: str) -> dict:
    """Verify a registration OTP and mark the user email as verified."""
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        return {"message": "Email already verified", "email": email, "email_verified": True}

    rows = get_db().execute_query(
        """
        SELECT otp_id, otp_hash, attempts, expires_at
        FROM email_verification_otps
        WHERE user_id = :user_id
          AND consumed_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        params={"user_id": user.user_id}
    )

    if not rows:
        raise HTTPException(status_code=400, detail="No active verification code found")

    otp = rows[0]
    otp_id = str(otp["otp_id"])
    if otp["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Verification code has expired")
    if otp["attempts"] >= MAX_OTP_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many verification attempts")

    if not verify_password(otp_code, otp["otp_hash"]):
        get_db().execute_query(
            """
            UPDATE email_verification_otps
            SET attempts = attempts + 1
            WHERE otp_id = :otp_id
            """,
            params={"otp_id": otp_id}
        )
        raise HTTPException(status_code=400, detail="Invalid verification code")

    get_db().execute_query(
        """
        UPDATE users
        SET email_verified = TRUE, email_verified_at = NOW()
        WHERE user_id = :user_id
        """,
        params={"user_id": user.user_id}
    )
    get_db().execute_query(
        """
        UPDATE email_verification_otps
        SET consumed_at = NOW()
        WHERE otp_id = :otp_id
        """,
        params={"otp_id": otp_id}
    )
    return {"message": "Email verified successfully", "email": email, "email_verified": True}

def request_password_reset(email: str) -> dict:
    """Generate and send a password reset OTP. Always returns success to prevent email enumeration."""
    user = get_user(email)
    if not user:
        return {"message": "If that email is registered, a reset code has been sent."}

    otp_code = generate_otp()
    otp_hash = get_password_hash(otp_code)
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)

    get_db().execute_query(
        """
        UPDATE password_reset_otps
        SET consumed_at = NOW()
        WHERE user_id = :user_id AND consumed_at IS NULL
        """,
        params={"user_id": user.user_id}
    )

    get_db().execute_query(
        """
        INSERT INTO password_reset_otps (user_id, otp_hash, expires_at)
        VALUES (:user_id, :otp_hash, :expires_at)
        """,
        params={"user_id": user.user_id, "otp_hash": otp_hash, "expires_at": expires_at}
    )

    email_sent = send_password_reset_email(email, otp_code)
    if not email_sent and not is_development_env() and PRODUCTION_EMAIL_DELIVERY_REQUIRED:
        raise HTTPException(status_code=500, detail="Failed to send password reset email")

    response = {"message": "If that email is registered, a reset code has been sent."}
    if is_development_env() and SHOW_DEV_OTP:
        response["dev_reset_otp"] = otp_code
    return response


def reset_password(email: str, otp_code: str, new_password: str) -> dict:
    """Verify a password reset OTP and update the user's password."""
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    rows = get_db().execute_query(
        """
        SELECT otp_id, otp_hash, attempts, expires_at
        FROM password_reset_otps
        WHERE user_id = :user_id
          AND consumed_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        params={"user_id": user.user_id}
    )

    if not rows:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    otp = rows[0]
    otp_id = str(otp["otp_id"])

    if otp["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Reset code has expired")
    if otp["attempts"] >= MAX_OTP_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many reset attempts")

    if not verify_password(otp_code, otp["otp_hash"]):
        get_db().execute_query(
            "UPDATE password_reset_otps SET attempts = attempts + 1 WHERE otp_id = :otp_id",
            params={"otp_id": otp_id}
        )
        raise HTTPException(status_code=400, detail="Invalid reset code")

    new_hash = get_password_hash(new_password)
    get_db().execute_query(
        "UPDATE users SET password = :password WHERE user_id = :user_id",
        params={"password": new_hash, "user_id": user.user_id}
    )
    get_db().execute_query(
        "UPDATE password_reset_otps SET consumed_at = NOW() WHERE otp_id = :otp_id",
        params={"otp_id": otp_id}
    )

    logger("AUTH", f"Password reset successful for {email}", level="INFO")
    return {"message": "Password reset successfully"}


def resend_email_verification(email: str) -> dict:
    """Send a fresh verification OTP for an unverified account."""
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        return {"message": "Email already verified", "email": email, "email_verified": True}

    verification = create_email_verification_otp(user.user_id, email)
    return {
        "message": "Verification code sent",
        "email": email,
        "email_verified": False,
        "verification": verification
    }

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Get current authenticated user from token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token_data = verify_token(token, credentials_exception)
        user = get_user(token_data.email)
        if user is None:
            raise credentials_exception
        return user
    except Exception as e:
        logger("AUTH", f"Token validation failed: {str(e)}", level="ERROR")
        raise credentials_exception

async def get_freelancer_user(current_user: UserInDB = Depends(get_current_user)):
    """Require the current user to have a freelancer profile."""
    if not current_user.freelancer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A freelancer profile is required to access this resource"
        )
    return current_user

async def get_client_user(current_user: UserInDB = Depends(get_current_user)):
    """Require the current user to have a client profile."""
    if not current_user.client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A client profile is required to access this resource"
        )
    return current_user

async def get_admin_user(current_user: UserInDB = Depends(get_current_user)):
    """Require the current user to have admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user
