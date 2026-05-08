"""
Role-Based Access Control (RBAC) utilities and decorators.
"""

from fastapi import HTTPException, status

from db.enums import UserRole
from db.models import User

# Role hierarchy (higher number = more permissions)
ROLE_HIERARCHY = {
    UserRole.USER: 1,
    UserRole.PAID_USER: 2,
    UserRole.MODERATOR: 3,
    UserRole.ADMIN: 4,
}


def has_minimum_role(user: User, required_role: UserRole) -> bool:
    """Check if user has at least the required role level."""
    user_level = ROLE_HIERARCHY.get(user.role, 0)
    required_level = ROLE_HIERARCHY.get(required_role, 999)
    return user_level >= required_level


def check_permission(user: User, required_role: UserRole) -> None:
    """
    Check if user has permission and raise HTTPException if not.

    Args:
        user: The authenticated user
        required_role: The minimum role required for the action

    Raises:
        HTTPException: 403 if user doesn't have sufficient permissions
    """
    if not has_minimum_role(user, required_role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions. Required role: {required_role.value}",
        )


# Convenience role check functions
def is_admin(user: User) -> bool:
    """Check if user is an admin."""
    return has_minimum_role(user, UserRole.ADMIN)


def is_moderator(user: User) -> bool:
    """Check if user is at least a moderator."""
    return has_minimum_role(user, UserRole.MODERATOR)


def is_paid_user(user: User) -> bool:
    """Check if user is at least a paid user."""
    return has_minimum_role(user, UserRole.PAID_USER)


# Permission-based access control
class Permission:
    """Permission constants for fine-grained access control."""

    # User permissions
    VIEW_DASHBOARD = "view_dashboard"
    MANAGE_PROFILES = "manage_profiles"
    VIEW_WATCH_HISTORY = "view_watch_history"
    VIEW_DOWNLOADS = "view_downloads"
    SUBMIT_CONTRIBUTION = "submit_contribution"
    IMPORT_CONTENT = "import_content"
    MANAGE_OWN_RSS = "manage_own_rss"

    # Moderator permissions
    VIEW_METRICS = "view_metrics"
    BLOCK_TORRENT = "block_torrent"
    DELETE_TORRENT = "delete_torrent"
    REVIEW_CONTRIBUTIONS = "review_contributions"
    RUN_SCRAPERS = "run_scrapers"

    # Admin permissions
    MANAGE_USERS = "manage_users"
    ASSIGN_ROLES = "assign_roles"
    VIEW_ALL_RSS = "view_all_rss"
    MANAGE_ALL_RSS = "manage_all_rss"
    SYSTEM_CONFIG = "system_config"
    MANAGE_METADATA = "manage_metadata"
    MANAGE_TORRENT_STREAMS = "manage_torrent_streams"
    MANAGE_TV_STREAMS = "manage_tv_streams"


# Permission to role mapping
PERMISSION_ROLES = {
    # User permissions
    Permission.VIEW_DASHBOARD: UserRole.USER,
    Permission.MANAGE_PROFILES: UserRole.USER,
    Permission.VIEW_WATCH_HISTORY: UserRole.USER,
    Permission.VIEW_DOWNLOADS: UserRole.USER,
    Permission.SUBMIT_CONTRIBUTION: UserRole.USER,
    Permission.IMPORT_CONTENT: UserRole.USER,
    Permission.MANAGE_OWN_RSS: UserRole.USER,
    # Moderator permissions
    Permission.VIEW_METRICS: UserRole.MODERATOR,
    Permission.BLOCK_TORRENT: UserRole.MODERATOR,
    Permission.DELETE_TORRENT: UserRole.MODERATOR,
    Permission.REVIEW_CONTRIBUTIONS: UserRole.MODERATOR,
    Permission.RUN_SCRAPERS: UserRole.MODERATOR,
    # Admin permissions
    Permission.MANAGE_USERS: UserRole.ADMIN,
    Permission.ASSIGN_ROLES: UserRole.ADMIN,
    Permission.VIEW_ALL_RSS: UserRole.ADMIN,
    Permission.MANAGE_ALL_RSS: UserRole.ADMIN,
    Permission.SYSTEM_CONFIG: UserRole.ADMIN,
    Permission.MANAGE_METADATA: UserRole.ADMIN,
    Permission.MANAGE_TORRENT_STREAMS: UserRole.ADMIN,
    Permission.MANAGE_TV_STREAMS: UserRole.ADMIN,
}


def has_permission(user: User, permission: str) -> bool:
    """Check if user has a specific permission."""
    required_role = PERMISSION_ROLES.get(permission)
    if not required_role:
        return False
    return has_minimum_role(user, required_role)


def check_permissions(user: User, *permissions: str) -> None:
    """
    Check if user has all specified permissions.

    Args:
        user: The authenticated user
        permissions: List of permission strings to check

    Raises:
        HTTPException: 403 if user doesn't have all required permissions
    """
    for permission in permissions:
        if not has_permission(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
