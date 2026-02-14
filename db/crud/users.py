"""
User CRUD operations.

Handles User, UserProfile, WatchHistory, PlaybackTracking.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

import pytz
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import UserRole, WatchAction
from db.models import (
    PlaybackTracking,
    User,
    UserProfile,
    WatchHistory,
)

logger = logging.getLogger(__name__)


# =============================================================================
# USER CRUD
# =============================================================================


async def get_user_by_id(
    session: AsyncSession,
    user_id: int,
    *,
    load_profiles: bool = False,
) -> User | None:
    """Get user by internal ID."""
    query = select(User).where(User.id == user_id)

    if load_profiles:
        query = query.options(selectinload(User.profiles))

    result = await session.exec(query)
    return result.first()


async def get_user_by_uuid(
    session: AsyncSession,
    uuid: str,
    *,
    load_profiles: bool = False,
) -> User | None:
    """Get user by UUID (for external API access)."""
    query = select(User).where(User.uuid == uuid)

    if load_profiles:
        query = query.options(selectinload(User.profiles))

    result = await session.exec(query)
    return result.first()


async def get_user_by_email(
    session: AsyncSession,
    email: str,
) -> User | None:
    """Get user by email address."""
    query = select(User).where(func.lower(User.email) == func.lower(email))
    result = await session.exec(query)
    return result.first()


async def get_user_by_username(
    session: AsyncSession,
    username: str,
) -> User | None:
    """Get user by username."""
    query = select(User).where(func.lower(User.username) == func.lower(username))
    result = await session.exec(query)
    return result.first()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    username: str | None = None,
    password_hash: str | None = None,
    role: UserRole = UserRole.USER,
    is_verified: bool = False,
) -> User:
    """Create a new user."""
    user = User(
        email=email,
        username=username,
        password_hash=password_hash,
        role=role,
        is_verified=is_verified,
    )
    session.add(user)
    await session.flush()
    return user


async def update_user(
    session: AsyncSession,
    user_id: int,
    **updates,
) -> User | None:
    """Update user fields."""
    if not updates:
        return await get_user_by_id(session, user_id)

    updates["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(sa_update(User).where(User.id == user_id).values(**updates))
    await session.flush()

    return await get_user_by_id(session, user_id)


async def update_last_login(
    session: AsyncSession,
    user_id: int,
) -> None:
    """Update user's last login timestamp."""
    await session.exec(sa_update(User).where(User.id == user_id).values(last_login=datetime.now(pytz.UTC)))
    await session.flush()


async def delete_user(
    session: AsyncSession,
    user_id: int,
) -> bool:
    """Delete user and all related data."""
    result = await session.exec(sa_delete(User).where(User.id == user_id))
    await session.flush()
    return result.rowcount > 0


async def increment_contribution_points(
    session: AsyncSession,
    user_id: int,
    points: int,
) -> None:
    """Add contribution points to user."""
    await session.exec(
        sa_update(User).where(User.id == user_id).values(contribution_points=User.contribution_points + points)
    )
    await session.flush()


# =============================================================================
# USER PROFILE CRUD
# =============================================================================


async def get_profile_by_id(
    session: AsyncSession,
    profile_id: int,
) -> UserProfile | None:
    """Get profile by internal ID."""
    query = select(UserProfile).where(UserProfile.id == profile_id)
    result = await session.exec(query)
    return result.first()


async def get_profile_by_uuid(
    session: AsyncSession,
    uuid: str,
) -> UserProfile | None:
    """Get profile by UUID."""
    query = select(UserProfile).where(UserProfile.uuid == uuid)
    result = await session.exec(query)
    return result.first()


async def get_profiles_for_user(
    session: AsyncSession,
    user_id: int,
) -> Sequence[UserProfile]:
    """Get all profiles for a user."""
    query = (
        select(UserProfile)
        .where(UserProfile.user_id == user_id)
        .order_by(UserProfile.is_default.desc(), UserProfile.created_at)
    )
    result = await session.exec(query)
    return result.all()


async def get_default_profile(
    session: AsyncSession,
    user_id: int,
) -> UserProfile | None:
    """Get user's default profile."""
    query = select(UserProfile).where(
        UserProfile.user_id == user_id,
        UserProfile.is_default == True,
    )
    result = await session.exec(query)
    return result.first()


async def create_profile(
    session: AsyncSession,
    user_id: int,
    name: str,
    *,
    config: dict | None = None,
    is_default: bool = False,
) -> UserProfile:
    """Create a new profile for user."""
    profile = UserProfile(
        user_id=user_id,
        name=name,
        config=config or {},
        is_default=is_default,
    )
    session.add(profile)
    await session.flush()
    return profile


async def update_profile(
    session: AsyncSession,
    profile_id: int,
    **updates,
) -> UserProfile | None:
    """Update profile fields."""
    if not updates:
        return await get_profile_by_id(session, profile_id)

    updates["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(sa_update(UserProfile).where(UserProfile.id == profile_id).values(**updates))
    await session.flush()

    return await get_profile_by_id(session, profile_id)


async def delete_profile(
    session: AsyncSession,
    profile_id: int,
) -> bool:
    """Delete a profile."""
    result = await session.exec(sa_delete(UserProfile).where(UserProfile.id == profile_id))
    await session.flush()
    return result.rowcount > 0


# =============================================================================
# WATCH HISTORY CRUD
# =============================================================================


async def add_watch_history(
    session: AsyncSession,
    user_id: int,
    profile_id: int,
    media_id: int,
    media_type: str,
    title: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    progress: int = 0,
    duration: int | None = None,
    action: WatchAction = WatchAction.WATCHED,
    stream_info: dict | None = None,
) -> WatchHistory:
    """Add or update watch history entry."""
    entry = WatchHistory(
        user_id=user_id,
        profile_id=profile_id,
        media_id=media_id,
        media_type=media_type,
        title=title,
        season=season,
        episode=episode,
        progress=progress,
        duration=duration,
        action=action,
        stream_info=stream_info or {},
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_watch_history(
    session: AsyncSession,
    user_id: int,
    profile_id: int | None = None,
    *,
    action: WatchAction | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[WatchHistory]:
    """Get watch history for user/profile, optionally filtered by action."""
    query = select(WatchHistory).where(WatchHistory.user_id == user_id)

    if profile_id:
        query = query.where(WatchHistory.profile_id == profile_id)

    if action:
        query = query.where(WatchHistory.action == action)

    query = query.order_by(WatchHistory.watched_at.desc())
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


async def clear_watch_history(
    session: AsyncSession,
    user_id: int,
    profile_id: int | None = None,
) -> int:
    """Clear watch history for user/profile."""
    query = sa_delete(WatchHistory).where(WatchHistory.user_id == user_id)

    if profile_id:
        query = query.where(WatchHistory.profile_id == profile_id)

    result = await session.exec(query)
    await session.flush()
    return result.rowcount


# =============================================================================
# DOWNLOAD HISTORY (via WatchHistory with action=downloaded)
# =============================================================================


async def add_download(
    session: AsyncSession,
    user_id: int,
    profile_id: int,
    media_id: int,
    media_type: str,
    title: str,
    *,
    season: int | None = None,
    episode: int | None = None,
    stream_info: dict | None = None,
) -> WatchHistory:
    """Add download entry (uses WatchHistory with action=downloaded)."""
    return await add_watch_history(
        session,
        user_id=user_id,
        profile_id=profile_id,
        media_id=media_id,
        media_type=media_type,
        title=title,
        season=season,
        episode=episode,
        action=WatchAction.DOWNLOADED,
        stream_info=stream_info,
    )


async def get_downloads(
    session: AsyncSession,
    user_id: int,
    profile_id: int | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[WatchHistory]:
    """Get download history for user/profile."""
    return await get_watch_history(
        session,
        user_id=user_id,
        profile_id=profile_id,
        action=WatchAction.DOWNLOADED,
        limit=limit,
        offset=offset,
    )


# =============================================================================
# PLAYBACK TRACKING CRUD
# =============================================================================


async def track_playback(
    session: AsyncSession,
    stream_id: int,
    media_id: int,
    *,
    user_id: int | None = None,
    profile_id: int | None = None,
    season: int | None = None,
    episode: int | None = None,
    provider_name: str | None = None,
    provider_service: str | None = None,
) -> PlaybackTracking:
    """Track a playback event."""
    now = datetime.now(pytz.UTC)

    # Check for existing tracking record
    if user_id:
        query = select(PlaybackTracking).where(
            PlaybackTracking.user_id == user_id,
            PlaybackTracking.stream_id == stream_id,
            PlaybackTracking.media_id == media_id,
        )
        if season is not None:
            query = query.where(PlaybackTracking.season == season)
        if episode is not None:
            query = query.where(PlaybackTracking.episode == episode)

        result = await session.exec(query)
        existing = result.first()

        if existing:
            # Update existing record
            await session.exec(
                sa_update(PlaybackTracking)
                .where(PlaybackTracking.id == existing.id)
                .values(
                    last_played_at=now,
                    play_count=PlaybackTracking.play_count + 1,
                )
            )
            await session.flush()
            return existing

    # Create new tracking record
    tracking = PlaybackTracking(
        user_id=user_id,
        profile_id=profile_id,
        stream_id=stream_id,
        media_id=media_id,
        season=season,
        episode=episode,
        provider_name=provider_name,
        provider_service=provider_service,
        first_played_at=now,
        last_played_at=now,
    )
    session.add(tracking)
    await session.flush()
    return tracking


async def get_playback_stats(
    session: AsyncSession,
    stream_id: int,
) -> dict:
    """Get playback statistics for a stream."""
    query = select(
        func.count(PlaybackTracking.id).label("total_plays"),
        func.sum(PlaybackTracking.play_count).label("total_play_count"),
        func.count(func.distinct(PlaybackTracking.user_id)).label("unique_users"),
    ).where(PlaybackTracking.stream_id == stream_id)

    result = await session.exec(query)
    row = result.first()

    return {
        "total_plays": row.total_plays if row else 0,
        "total_play_count": row.total_play_count or 0 if row else 0,
        "unique_users": row.unique_users if row else 0,
    }
