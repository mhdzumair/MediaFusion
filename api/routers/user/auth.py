"""
Authentication API endpoints for user registration, login, and token management.
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta

import httpx
import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from db.database import get_async_session
from db.enums import UserRole
from db.models import User, UserProfile
from db.models.links import StreamMediaLink
from db.models.media import Media
from db.models.streams import Stream
from utils.config import settings
from utils.email.service import get_email_service

logger = logging.getLogger(__name__)

# In-memory rate limit for resend-verification (email -> timestamp)
_resend_cooldowns: dict[str, float] = {}
RESEND_COOLDOWN_SECONDS = 60

# JWT Configuration
JWT_SECRET_KEY = settings.secret_key
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 30

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])
security = HTTPBearer(auto_error=False)


# ============================================
# Pydantic Schemas
# ============================================


class UserCreate(BaseModel):
    email: EmailStr
    username: str | None = Field(None, min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    newsletter_opt_in: bool = False


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    uuid: str
    email: str
    username: str | None
    role: str
    is_verified: bool
    is_active: bool
    created_at: datetime
    last_login: datetime | None
    # Contribution stats
    contribution_points: int = 0
    contribution_level: str = "new"
    # Contribution preferences
    contribute_anonymously: bool = False


class LinkConfigRequest(BaseModel):
    secret_str: str


class UserUpdateRequest(BaseModel):
    """Request to update user account settings."""

    username: str | None = Field(None, min_length=3, max_length=100)
    contribute_anonymously: bool | None = None


class ChangePasswordRequest(BaseModel):
    """Request to change user password."""

    current_password: str
    new_password: str = Field(..., min_length=8)


class DeleteAccountRequest(BaseModel):
    """Request to delete user account. Requires password confirmation."""

    password: str


class RegisterResponse(BaseModel):
    """Response for registration when email verification is required."""

    message: str
    email: str
    requires_verification: bool


class VerifyEmailRequest(BaseModel):
    """Request to verify email address."""

    token: str


class ResendVerificationRequest(BaseModel):
    """Request to resend verification email."""

    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    """Request to initiate password reset."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request to reset password with token."""

    token: str
    new_password: str = Field(..., min_length=8)


# ============================================
# Password Hashing (using hashlib for simplicity)
# ============================================


def hash_password(password: str) -> str:
    """Hash password using SHA256 with salt."""
    salt = secrets.token_hex(16)
    hash_obj = hashlib.sha256((password + salt).encode())
    return f"{salt}${hash_obj.hexdigest()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash."""
    try:
        salt, hash_value = hashed.split("$")
        hash_obj = hashlib.sha256((password + salt).encode())
        return hash_obj.hexdigest() == hash_value
    except (ValueError, AttributeError):
        return False


# ============================================
# Simple JWT Implementation
# ============================================


def create_token(data: dict, expires_delta: timedelta) -> str:
    """Create a simple token (base64 encoded JSON with signature)."""
    import base64
    import hmac
    import json

    expire = datetime.now(pytz.UTC) + expires_delta
    to_encode = {**data, "exp": expire.timestamp()}
    payload = base64.urlsafe_b64encode(json.dumps(to_encode).encode()).decode()
    signature = hmac.new(JWT_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def decode_token(token: str) -> dict | None:
    """Decode and verify token."""
    import base64
    import hmac
    import json

    try:
        payload, signature = token.rsplit(".", 1)
        expected_signature = hmac.new(JWT_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data.get("exp", 0) < datetime.now(pytz.UTC).timestamp():
            return None
        return data
    except Exception:
        return None


def create_access_token(user_id: int, role: str) -> str:
    """Create access token for user."""
    return create_token(
        {"sub": str(user_id), "role": role, "type": "access"},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int) -> str:
    """Create refresh token for user."""
    return create_token(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )


def create_email_verify_token(user_id: int) -> str:
    """Create email verification token (24h expiry)."""
    return create_token(
        {"sub": str(user_id), "type": "email_verify"},
        timedelta(hours=24),
    )


def create_password_reset_token(user_id: int, password_hash: str) -> str:
    """Create password reset token (1h expiry).

    Embeds a prefix of the current password hash so the token is
    automatically invalidated when the password changes.
    """
    return create_token(
        {"sub": str(user_id), "type": "password_reset", "pwd_hash": password_hash[:16]},
        timedelta(hours=1),
    )


def is_email_configured() -> bool:
    """Check whether SMTP email sending is configured."""
    return settings.smtp_host is not None


# ============================================
# Dependencies
# ============================================


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    session: AsyncSession = Depends(get_async_session),
) -> User | None:
    """Get current authenticated user from JWT token."""
    if not credentials:
        return None

    token_data = decode_token(credentials.credentials)
    if not token_data or token_data.get("type") != "access":
        return None

    user_id_str = token_data.get("sub")
    if not user_id_str:
        return None

    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        return None

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        return None

    return user


async def require_auth(
    user: User | None = Depends(get_current_user),
) -> User:
    """Require authentication - raises 401 if not authenticated."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def optional_auth(
    user: User | None = Depends(get_current_user),
) -> User | None:
    """Optional authentication - returns None if not authenticated (no error)."""
    return user


def require_role(minimum_role: UserRole):
    """Dependency factory for role-based access control."""
    role_hierarchy = {
        UserRole.USER: 1,
        UserRole.PAID_USER: 2,
        UserRole.MODERATOR: 3,
        UserRole.ADMIN: 4,
    }

    async def check_role(user: User = Depends(require_auth)) -> User:
        user_level = role_hierarchy.get(user.role, 0)
        required_level = role_hierarchy.get(minimum_role, 999)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {minimum_role}",
            )
        return user

    return check_role


# ============================================
# ConvertKit Newsletter Integration
# ============================================


async def _subscribe_to_convertkit(email: str) -> None:
    """Subscribe an email to the configured ConvertKit form (v4 API).

    Creates the subscriber first, then adds them to the form.
    Both calls are idempotent (200 if already exists).
    """
    headers = {"X-Kit-Api-Key": settings.convertkit_api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Step 1: Create subscriber
        response = await client.post(
            "https://api.kit.com/v4/subscribers",
            headers=headers,
            json={"email_address": email},
        )
        response.raise_for_status()
        # Step 2: Add subscriber to form
        response = await client.post(
            f"https://api.kit.com/v4/forms/{settings.convertkit_form_id}/subscribers",
            headers=headers,
            json={"email_address": email},
        )
        response.raise_for_status()


# ============================================
# API Endpoints
# ============================================


@router.post("/register", response_model=TokenResponse | RegisterResponse)
async def register(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_async_session),
):
    """Register a new user account.

    When SMTP is configured, sends a verification email and returns a
    RegisterResponse (no tokens). When SMTP is not configured, auto-verifies
    and returns a TokenResponse immediately.
    """
    # Check if email already exists
    result = await session.exec(select(User).where(User.email == user_data.email))
    existing_user = result.first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Check if username already exists (if provided)
    if user_data.username:
        result = await session.exec(select(User).where(User.username == user_data.username))
        existing_username = result.first()
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken",
            )

    email_service = get_email_service()
    auto_verify = email_service is None

    # Create new user
    user = User(
        email=user_data.email,
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        role=UserRole.USER,
        is_verified=auto_verify,
        is_active=True,
        last_login=datetime.now(pytz.UTC) if auto_verify else None,
    )
    session.add(user)
    await session.flush()

    # Create default profile
    profile = UserProfile(
        user_id=user.id,
        name="Default",
        config={},
        is_default=True,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(user)

    # Subscribe to ConvertKit newsletter if opted in
    if user_data.newsletter_opt_in and settings.convertkit_api_key and settings.convertkit_form_id:
        try:
            await _subscribe_to_convertkit(user.email)
        except Exception:
            logger.exception("Failed to subscribe %s to ConvertKit newsletter", user.email)

    if not auto_verify:
        # Send verification email
        token = create_email_verify_token(user.id)
        try:
            await email_service.send_verification_email(user.email, token, user.username)
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)

        return RegisterResponse(
            message="Registration successful. Please check your email to verify your account.",
            email=user.email,
            requires_verification=True,
        )

    # No SMTP configured -- auto-verified, return tokens directly
    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(
            id=user.id,
            uuid=user.uuid,
            email=user.email,
            username=user.username,
            role=user.role.value,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login=user.last_login,
            contribution_points=user.contribution_points,
            contribution_level=user.contribution_level,
            contribute_anonymously=user.contribute_anonymously,
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    session: AsyncSession = Depends(get_async_session),
):
    """Login with email and password."""
    result = await session.exec(select(User).where(User.email == credentials.email))
    user = result.first()

    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    # Check email verification (only when SMTP is configured)
    if not user.is_verified and is_email_configured():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your inbox for a verification link.",
            headers={"X-Error-Code": "EMAIL_NOT_VERIFIED"},
        )

    # Update last login
    user.last_login = datetime.now(pytz.UTC)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # Generate tokens
    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(
            id=user.id,
            uuid=user.uuid,
            email=user.email,
            username=user.username,
            role=user.role.value,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login=user.last_login,
            contribution_points=user.contribution_points,
            contribution_level=user.contribution_level,
            contribute_anonymously=user.contribute_anonymously,
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Refresh access token using refresh token."""
    token_data = decode_token(request.refresh_token)

    if not token_data or token_data.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user_id = token_data.get("sub")
    # Convert user_id to int (JWT stores as string)
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        )
    user = await session.get(User, user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Generate new tokens
    access_token = create_access_token(user.id, user.role.value)
    new_refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        user=UserResponse(
            id=user.id,
            uuid=user.uuid,
            email=user.email,
            username=user.username,
            role=user.role.value,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login=user.last_login,
            contribution_points=user.contribution_points,
            contribution_level=user.contribution_level,
            contribute_anonymously=user.contribute_anonymously,
        ),
    )


@router.post("/logout")
async def logout(user: User = Depends(require_auth)):
    """Logout current user (invalidate tokens client-side)."""
    # In a stateless JWT system, logout is handled client-side
    # For enhanced security, you could implement a token blacklist with Redis
    return {"message": "Successfully logged out"}


# ============================================
# Email Verification & Password Reset
# ============================================


@router.post("/verify-email")
async def verify_email(
    request: VerifyEmailRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Verify a user's email address using the token from their verification link."""
    token_data = decode_token(request.token)
    if not token_data or token_data.get("type") != "email_verify":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link.",
        )

    try:
        user_id = int(token_data["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification link.",
        )

    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User not found.",
        )

    if user.is_verified:
        return {"message": "Email already verified. You can log in."}

    user.is_verified = True
    session.add(user)
    await session.commit()

    logger.info("Email verified for user %s (id=%d)", user.email, user.id)
    return {"message": "Email verified successfully. You can now log in."}


@router.post("/resend-verification")
async def resend_verification(
    request: ResendVerificationRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Resend the verification email. Rate-limited to one request per 60 seconds per email."""
    email_service = get_email_service()
    if not email_service:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email service is not configured on this instance.",
        )

    # Rate limiting
    now = time.time()
    last_sent = _resend_cooldowns.get(request.email, 0)
    if now - last_sent < RESEND_COOLDOWN_SECONDS:
        remaining = int(RESEND_COOLDOWN_SECONDS - (now - last_sent))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {remaining} seconds before requesting another email.",
        )

    result = await session.exec(select(User).where(User.email == request.email))
    user = result.first()

    # Always return success to avoid leaking whether the email exists
    if not user or user.is_verified:
        return {"message": "If the email is registered and unverified, a new verification link has been sent."}

    token = create_email_verify_token(user.id)
    try:
        await email_service.send_verification_email(user.email, token, user.username)
        _resend_cooldowns[request.email] = now
    except Exception:
        logger.exception("Failed to resend verification email to %s", request.email)

    return {"message": "If the email is registered and unverified, a new verification link has been sent."}


@router.post("/forgot-password")
async def forgot_password(
    request: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Request a password reset link. Always returns success to prevent email enumeration."""
    email_service = get_email_service()
    if not email_service:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email service is not configured on this instance.",
        )

    result = await session.exec(select(User).where(User.email == request.email))
    user = result.first()

    if user and user.password_hash and user.is_active:
        token = create_password_reset_token(user.id, user.password_hash)
        try:
            await email_service.send_password_reset_email(user.email, token, user.username)
        except Exception:
            logger.exception("Failed to send password reset email to %s", request.email)

    return {"message": "If an account with that email exists, a password reset link has been sent."}


@router.post("/reset-password")
async def reset_password(
    request: ResetPasswordRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Reset the user's password using the token from the reset email."""
    token_data = decode_token(request.token)
    if not token_data or token_data.get("type") != "password_reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link.",
        )

    try:
        user_id = int(token_data["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset link.",
        )

    user = await session.get(User, user_id)
    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset link.",
        )

    # Verify the token was generated against the current password hash
    # (ensures single-use: once password changes, old tokens are invalid)
    if token_data.get("pwd_hash") != user.password_hash[:16]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link has already been used or is no longer valid.",
        )

    user.password_hash = hash_password(request.new_password)
    # Also verify the user's email since they proved ownership via the reset link
    if not user.is_verified:
        user.is_verified = True
    session.add(user)
    await session.commit()

    logger.info("Password reset for user %s (id=%d)", user.email, user.id)
    return {"message": "Password reset successfully. You can now log in with your new password."}


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(require_auth)):
    """Get current authenticated user's information."""
    return UserResponse(
        id=user.id,
        uuid=user.uuid,
        email=user.email,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        contribution_points=user.contribution_points,
        contribution_level=user.contribution_level,
        contribute_anonymously=user.contribute_anonymously,
    )


@router.patch("/me", response_model=UserResponse)
async def update_me(
    update_data: UserUpdateRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Update current user's account settings."""
    # Update username if provided
    if update_data.username is not None:
        # Check if username is taken by another user
        if update_data.username != user.username:
            result = await session.exec(
                select(User).where(
                    User.username == update_data.username,
                    User.id != user.id,
                )
            )
            existing = result.first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username already taken",
                )
        user.username = update_data.username

    # Update contribution preferences if provided
    if update_data.contribute_anonymously is not None:
        user.contribute_anonymously = update_data.contribute_anonymously

    session.add(user)
    await session.commit()
    await session.refresh(user)

    return UserResponse(
        id=user.id,
        uuid=user.uuid,
        email=user.email,
        username=user.username,
        role=user.role.value,
        is_verified=user.is_verified,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login=user.last_login,
        contribution_points=user.contribution_points,
        contribution_level=user.contribution_level,
        contribute_anonymously=user.contribute_anonymously,
    )


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Change the current user's password."""
    # Verify current password
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change password for OAuth users",
        )

    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # Update password
    user.password_hash = hash_password(request.new_password)
    session.add(user)
    await session.commit()

    return {"message": "Password changed successfully"}


@router.delete("/me")
async def delete_account(
    request: DeleteAccountRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete the current user's account and all associated data."""
    if not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete account for OAuth users via this endpoint",
        )

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password is incorrect",
        )

    # SET NULL for nullable FK references that would block deletion
    await session.exec(update(Stream).where(Stream.uploader_user_id == user.id).values(uploader_user_id=None))
    await session.exec(
        update(StreamMediaLink).where(StreamMediaLink.linked_by_user_id == user.id).values(linked_by_user_id=None)
    )
    await session.exec(update(Media).where(Media.created_by_user_id == user.id).values(created_by_user_id=None))

    logger.info("Deleting account for user %s (id=%d)", user.email, user.id)

    # Delete user -- cascades handle profiles, watch history, library, etc.
    await session.delete(user)
    await session.commit()

    return {"message": "Account deleted successfully"}
