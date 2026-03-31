import os
import sys
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
from functions.functions import db
from functions.schema_model import Token, TokenData, UserInDB

from dotenv import load_dotenv
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is not set")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Initialize Argon2 password hasher
pwd_hasher = PasswordHasher()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

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

def authenticate_user(email: str, password: str):
    """Authenticate user with email and password."""
    try:
        query = "SELECT user_id, email, password, type FROM users WHERE email = :email"
        result = db.execute_query(query, params={"email": email})
        if not result or len(result) == 0:
            return False
        user = result[0]
        if not verify_password(password, user['password']):
            return False
        return UserInDB(user_id=str(user['user_id']), email=user['email'], password=user['password'], type=user['type'])
    except Exception as e:
        logger("AUTH", f"Authentication error: {str(e)}", level="ERROR")
        return False

def get_user(email: str):
    """Get user from database by email."""
    try:
        query = "SELECT user_id, email, password, type FROM users WHERE email = :email"
        result = db.execute_query(query, params={"email": email})
        if result and len(result) > 0:
            user = result[0]
            return UserInDB(user_id=str(user['user_id']), email=user['email'], password=user['password'], type=user['type'])
    except Exception as e:
        logger("AUTH", f"Get user error: {str(e)}", level="ERROR")
    return None

def register_user(email: str, password: str, user_type: str = "freelancer", full_name: str = None, company_name: str = None):
    """Register new user and create corresponding profile (freelancer or client)."""
    try:
        # Check if email already exists
        if get_user(email):
            raise HTTPException(status_code=400, detail="Email already registered")

        # Validate user_type
        if user_type not in ["freelancer", "client"]:
            raise HTTPException(status_code=400, detail="Invalid user type. Must be 'freelancer' or 'client'")

        hashed_password = get_password_hash(password)

        # Create user record
        user_query = """
        INSERT INTO users (email, password, type)
        VALUES (:email, :password, :user_type)
        RETURNING user_id
        """
        user_result = db.execute_query(user_query, params={"email": email, "password": hashed_password, "user_type": user_type})
        
        if not user_result or len(user_result) == 0:
            raise HTTPException(status_code=500, detail="Failed to create user record")
        
        user_id = str(user_result[0]['user_id'])
        
        # Create profile based on user type (only if required fields provided)
        if user_type == "freelancer" and full_name:
            profile_query = """
            INSERT INTO freelancer (user_id, full_name)
            VALUES (:user_id, :full_name)
            RETURNING freelancer_id
            """
            profile_result = db.execute_query(profile_query, params={"user_id": user_id, "full_name": full_name})
            if not profile_result:
                raise HTTPException(status_code=500, detail="Failed to create freelancer profile")
        elif user_type == "client":
            # Company name is optional for client, so always create profile
            profile_query = """
            INSERT INTO client (user_id, company_name)
            VALUES (:user_id, :company_name)
            RETURNING client_id
            """
            profile_result = db.execute_query(profile_query, params={"user_id": user_id, "company_name": company_name})
            if not profile_result:
                raise HTTPException(status_code=500, detail="Failed to create client profile")
        
        logger("AUTH", f"User registered: {email} as {user_type}", level="INFO")
        return {"message": "User registered successfully", "user": {"user_id": user_id, "email": email, "type": user_type}}
    
    except HTTPException:
        raise
    except Exception as e:
        logger("AUTH", f"Registration error: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail="Registration failed")

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
    """Get current user if they are a freelancer."""
    if current_user.type != "freelancer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only freelancers can access this resource"
        )
    return current_user

async def get_client_user(current_user: UserInDB = Depends(get_current_user)):
    """Get current user if they are a client."""
    if current_user.type != "client":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clients can access this resource"
        )
    return current_user
