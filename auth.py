"""
Authentication module for Resume Diff AI
Handles user registration, login, logout, and JWT token management
Uses MongoDB Atlas for data persistence
"""
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator

from config import settings
from database import get_users_collection, get_blacklisted_tokens_collection

logger = logging.getLogger(__name__)

# HTTP Bearer token scheme
security = HTTPBearer()

# Email regex pattern for validation
EMAIL_REGEX = re.compile(
    r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
)


# ============ Pydantic Models ============

class UserBase(BaseModel):
    """Base user model"""
    email: str
    name: str
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        """Validate email with regex"""
        if not EMAIL_REGEX.match(v):
            raise ValueError('Invalid email format')
        return v.lower()
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate name is not empty"""
        if not v or len(v.strip()) < 2:
            raise ValueError('Name must be at least 2 characters')
        return v.strip()


class UserCreate(UserBase):
    """User registration model"""
    password: str
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        """Validate password strength"""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one digit')
        return v


class UserLogin(BaseModel):
    """User login model"""
    email: str
    password: str
    
    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        """Validate email with regex"""
        if not EMAIL_REGEX.match(v):
            raise ValueError('Invalid email format')
        return v.lower()


class UserResponse(BaseModel):
    """User response model (without password)"""
    id: str
    email: str
    name: str
    created_at: str


class TokenResponse(BaseModel):
    """Token response model"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshTokenRequest(BaseModel):
    """Refresh token request model"""
    refresh_token: str


class TokenData(BaseModel):
    """Token payload data"""
    user_id: str
    email: str
    token_type: str = "access"


# ============ User Database Functions (MongoDB) ============

async def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email from MongoDB"""
    users = get_users_collection()
    user = await users.find_one({"email": email.lower()})
    if user:
        user["id"] = str(user["_id"])
        return user
    return None


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by ID from MongoDB"""
    users = get_users_collection()
    user = await users.find_one({"_id": user_id})
    if user:
        user["id"] = str(user["_id"])
        return user
    return None


async def create_user(user_data: UserCreate) -> dict:
    """Create a new user in MongoDB"""
    users = get_users_collection()
    
    # Check if email already exists
    existing_user = await get_user_by_email(user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Generate user ID
    user_id = str(uuid.uuid4())
    
    # Hash password (truncated to 72 bytes for bcrypt)
    hashed_password = hash_password(user_data.password)
    
    # Create user document
    user_doc = {
        "_id": user_id,
        "email": user_data.email.lower(),
        "name": user_data.name,
        "hashed_password": hashed_password,
        "created_at": datetime.utcnow().isoformat()
    }
    
    try:
        await users.insert_one(user_doc)
        logger.info(f"Created new user: {user_data.email}")
        return {**user_doc, "id": user_id}
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )


# ============ Password Functions ============

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    # Truncate to 72 bytes (bcrypt limit)
    password_bytes = plain_password.encode('utf-8')[:72]
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (truncated to 72 bytes)"""
    # Truncate to 72 bytes (bcrypt limit)
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


# ============ JWT Token Functions ============

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access"
    })
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Create JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "refresh"
    })
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate JWT token"""
    try:
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET_KEY, 
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError as e:
        logger.warning(f"Token decode error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )


async def is_token_blacklisted(token: str) -> bool:
    """Check if token is blacklisted in MongoDB"""
    blacklisted = get_blacklisted_tokens_collection()
    result = await blacklisted.find_one({"token": token})
    return result is not None


async def blacklist_token(token: str) -> None:
    """Add token to blacklist in MongoDB (for logout)"""
    blacklisted = get_blacklisted_tokens_collection()
    try:
        await blacklisted.insert_one({
            "token": token,
            "created_at": datetime.utcnow()
        })
    except Exception as e:
        # Token might already be blacklisted, ignore duplicate key error
        logger.debug(f"Token blacklist insert: {e}")


# ============ Authentication Dependency ============

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Dependency to get current authenticated user
    Use this in protected routes
    """
    token = credentials.credentials
    
    # Check if token is blacklisted
    if await is_token_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Decode token
    payload = decode_token(token)
    
    # Validate token type
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Get user
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    return user


# ============ Auth Service Functions ============

async def authenticate_user(email: str, password: str) -> dict:
    """Authenticate user with email and password"""
    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    if not verify_password(password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    return user


def create_tokens_for_user(user: dict) -> TokenResponse:
    """Create access and refresh tokens for user"""
    token_data = {"sub": user["id"], "email": user["email"]}
    
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    
    user_response = UserResponse(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        created_at=user["created_at"]
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user_response
    )


async def refresh_access_token(refresh_token: str) -> TokenResponse:
    """Refresh access token using refresh token"""
    # Check if token is blacklisted
    if await is_token_blacklisted(refresh_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked"
        )
    
    # Decode refresh token
    payload = decode_token(refresh_token)
    
    # Validate token type
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type"
        )
    
    # Get user
    user_id = payload.get("sub")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Create new tokens
    return create_tokens_for_user(user)
