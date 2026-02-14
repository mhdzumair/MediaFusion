"""
FastAPI Dependencies for API endpoints.

This module provides reusable dependencies for authenticated endpoints,
including profile context resolution for the new UI flow.
"""

from typing import Callable

from fastapi import Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.database import get_read_session
from db.models import User
from utils.profile_context import ProfileContext, ProfileDataProvider


async def get_profile_context(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
) -> ProfileContext:
    """
    FastAPI dependency to get current user's profile context.

    This dependency:
    1. Authenticates the user via JWT (require_auth)
    2. Loads their active profile from Redis cache (or DB on miss)
    3. Decrypts secrets per-request
    4. Returns a ProfileContext with resolved UserData

    Usage:
        @router.get("/endpoint")
        async def my_endpoint(
            profile_ctx: ProfileContext = Depends(get_profile_context),
        ):
            user_data = profile_ctx.user_data
            rpdb_key = profile_ctx.rpdb_api_key
            ...
    """
    return await ProfileDataProvider.get_context(current_user.id, session)


async def get_optional_profile_context(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
) -> ProfileContext:
    """
    Same as get_profile_context but returns empty context on error.

    Use this for endpoints where profile data is optional but helpful.
    """
    try:
        return await ProfileDataProvider.get_context(current_user.id, session)
    except Exception:
        return ProfileContext.empty(current_user.id)


def get_profile_context_with_id(
    profile_id_param: str = "profile_id",
) -> Callable[..., ProfileContext]:
    """
    Factory to create a profile context dependency that accepts a profile_id query param.

    Args:
        profile_id_param: Name of the query parameter for profile ID

    Usage:
        @router.get("/endpoint")
        async def my_endpoint(
            profile_ctx: ProfileContext = Depends(
                get_profile_context_with_id("profile_id")
            ),
        ):
            ...
    """

    async def dependency(
        current_user: User = Depends(require_auth),
        session: AsyncSession = Depends(get_read_session),
        profile_id: int | None = Query(None, description="Specific profile ID to use (defaults to user's default)"),
    ) -> ProfileContext:
        return await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    return dependency


# Pre-built dependency for common use case
get_profile_context_optional_id = get_profile_context_with_id("profile_id")
