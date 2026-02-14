"""
Backward-compatible re-exports of auth utilities.

The actual implementation is in api/routers/user/auth.py.
This file provides backward-compatible imports for other modules.
"""

from api.routers.user.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    # Constants
    JWT_SECRET_KEY,
    REFRESH_TOKEN_EXPIRE_DAYS,
    LinkConfigRequest,
    RefreshRequest,
    TokenResponse,
    # Schemas
    UserCreate,
    UserLogin,
    UserResponse,
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
    get_current_user,
    hash_password,
    # Auth dependencies
    require_auth,
    require_role,
    # Router
    router,
    security,
    verify_password,
)

__all__ = [
    "require_auth",
    "require_role",
    "get_current_user",
    "verify_password",
    "hash_password",
    "create_access_token",
    "create_refresh_token",
    "create_token",
    "decode_token",
    "UserCreate",
    "UserLogin",
    "TokenResponse",
    "RefreshRequest",
    "UserResponse",
    "LinkConfigRequest",
    "router",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
    "security",
]
