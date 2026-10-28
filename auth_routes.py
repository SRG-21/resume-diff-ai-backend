"""
Authentication API Routes
Handles signup, login, logout, refresh token, and user info endpoints
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    get_user_by_email,
    user_to_response,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
    GoogleLoginRequest,
    create_verification_token,
    create_reset_token,
    verify_email_token,
    reset_password_with_token,
    verify_google_credential,
    login_or_create_google_user,
    delete_user_account,
)
from email_service import send_verification_email, send_password_reset_email
from usage import client_ip, enforce_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

security = HTTPBearer()


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(user_data: UserCreate, request: Request):
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
    await enforce_rate_limit(f"signup:{client_ip(request)}", 10, 3600, "Too many signups from this network. Try again later.")
    
    try:
        # Create user
        user = await create_user(user_data)
        await send_verification_email(user["email"], create_verification_token(user))
        
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
async def login(credentials: UserLogin, request: Request):
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
    ip = client_ip(request)
    await enforce_rate_limit(f"login-ip:{ip}", 30, 900, "Too many login attempts. Please wait a few minutes.")
    await enforce_rate_limit(f"login-email:{credentials.email}", 10, 900, "Too many login attempts. Please wait a few minutes.")
    
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
    return user_to_response(current_user)


@router.get("/validate")
async def validate_token(current_user: dict = Depends(get_current_user)):
    """
    Validate current token
    
    Returns:
        Valid status and user info
    
    Raises:
        401: Invalid token
    """
    return {"valid": True, "user": user_to_response(current_user)}


@router.post("/verify-email", response_model=UserResponse)
async def verify_email(body: VerifyEmailRequest):
    """Confirm an email address using the token from the verification email"""
    user = await verify_email_token(body.token)
    return user_to_response(user)


@router.post("/resend-verification")
async def resend_verification(request: Request, current_user: dict = Depends(get_current_user)):
    """Send another verification email to the signed-in user"""
    if current_user.get("email_verified"):
        return {"message": "Email already verified"}
    await enforce_rate_limit(f"resend:{current_user['id']}", 3, 3600, "Verification email already sent. Please check your inbox.")
    await send_verification_email(current_user["email"], create_verification_token(current_user))
    return {"message": "Verification email sent"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """
    Email a password reset link.

    Always returns the same response so the endpoint cannot be used to find out
    which email addresses have accounts.
    """
    await enforce_rate_limit(f"forgot-ip:{client_ip(request)}", 10, 3600, "Too many requests. Try again later.")
    await enforce_rate_limit(f"forgot-email:{body.email}", 3, 3600, "Too many requests. Try again later.")
    user = await get_user_by_email(body.email)
    if user and user.get("hashed_password"):
        await send_password_reset_email(user["email"], create_reset_token(user))
    return {"message": "If an account exists for that email, a reset link has been sent."}


@router.post("/reset-password", response_model=TokenResponse)
async def reset_password(body: ResetPasswordRequest):
    """Set a new password using a reset link token, then sign the user in"""
    user = await reset_password_with_token(body.token, body.password)
    return create_tokens_for_user(user)


@router.post("/google", response_model=TokenResponse)
async def google_login(body: GoogleLoginRequest, request: Request):
    """Sign in or sign up with a Google Identity Services ID token"""
    await enforce_rate_limit(f"google:{client_ip(request)}", 30, 900, "Too many attempts. Please wait a few minutes.")
    claims = await verify_google_credential(body.credential)
    user = await login_or_create_google_user(claims)
    return create_tokens_for_user(user)


@router.delete("/account", status_code=status.HTTP_200_OK)
async def delete_account(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: dict = Depends(get_current_user),
):
    """Permanently delete the account and all stored data, and cancel any subscription"""
    from billing import cancel_subscriptions_for_user  # local import avoids a circular import

    await cancel_subscriptions_for_user(current_user)
    await delete_user_account(current_user)
    await blacklist_token(credentials.credentials)
    return {"message": "Account deleted"}
