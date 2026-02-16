"""
Authentication API endpoints for user registration, login, and token management.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.database import get_async_session
from db.enums import UserRole
from db.models import User, UserProfile
from utils.config import settings

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
# API Endpoints
# ============================================


@router.post("/register", response_model=TokenResponse)
async def register(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_async_session),
):
    """Register a new user account."""
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

    # Create new user
    user = User(
        email=user_data.email,
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        role=UserRole.USER,
        is_verified=False,
        is_active=True,
        last_login=datetime.now(pytz.UTC),
    )
    session.add(user)
    await session.flush()  # Get user.id before creating profile

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


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    session: AsyncSession = Depends(get_async_session),
):
    """Login with email and password.

    The bootstrap admin account cannot use this endpoint.
    Use /api/v1/instance/setup/login during initial setup instead.
    """
    from utils.bootstrap import is_bootstrap_user

    result = await session.exec(select(User).where(User.email == credentials.email))
    user = result.first()

    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Block bootstrap admin from normal login
    if is_bootstrap_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account can only be used through the initial setup wizard.",
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
