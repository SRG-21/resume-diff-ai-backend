"""
Authentication API Routes
Handles signup, login, logout, refresh token, and user info endpoints
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth import (
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
    RefreshTokenRequest,
    create_user,
    authenticate_user,
    create_tokens_for_user,
    refresh_access_token,
    blacklist_token,
    get_current_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

security = HTTPBearer()


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate):
    """
    Register a new user
    
    Args:
        user_data: User registration data (email, name, password)
    
    Returns:
        TokenResponse with access_token, refresh_token, and user info
    
    Raises:
        400: Email already registered or validation error
    """
    logger.info(f"Signup attempt for email: {user_data.email}")
    
    try:
        # Create user
        user = await create_user(user_data)
        
        # Generate tokens
        token_response = create_tokens_for_user(user)
        
        logger.info(f"User registered successfully: {user_data.email}")
        return token_response
    
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"Signup validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Signup error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        )


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin):
    """
    Login with email and password
    
    Args:
        credentials: User login credentials (email, password)
    
    Returns:
        TokenResponse with access_token, refresh_token, and user info
    
    Raises:
        401: Invalid credentials
    """
    logger.info(f"Login attempt for email: {credentials.email}")
    
    try:
        # Authenticate user
        user = await authenticate_user(credentials.email, credentials.password)
        
        # Generate tokens
        token_response = create_tokens_for_user(user)
        
        logger.info(f"User logged in successfully: {credentials.email}")
        return token_response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Logout current user (invalidate token)
    
    Returns:
        Success message
    """
    token = credentials.credentials
    
    try:
        # Blacklist the token
        await blacklist_token(token)
        
        logger.info("User logged out successfully")
        return {"message": "Logged out successfully"}
    
    except Exception as e:
        logger.error(f"Logout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed"
        )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token using refresh token
    
    Args:
        request: Refresh token request containing the refresh token
    
    Returns:
        New TokenResponse with fresh access_token and refresh_token
    
    Raises:
        401: Invalid or expired refresh token
    """
    logger.info("Token refresh attempt")
    
    try:
        token_response = await refresh_access_token(request.refresh_token)
        
        logger.info("Token refreshed successfully")
        return token_response
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current authenticated user info
    
    Returns:
        UserResponse with user details
    
    Raises:
        401: Not authenticated
    """
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        name=current_user["name"],
        created_at=current_user["created_at"]
    )


@router.get("/validate")
async def validate_token(current_user: dict = Depends(get_current_user)):
    """
    Validate current token
    
    Returns:
        Valid status and user info
    
    Raises:
        401: Invalid token
    """
    return {
        "valid": True,
        "user": UserResponse(
            id=current_user["id"],
            email=current_user["email"],
            name=current_user["name"],
            created_at=current_user["created_at"]
        )
    }
