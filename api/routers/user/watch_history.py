"""
Watch History API endpoints for tracking user viewing progress.
"""

from datetime import datetime

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.crud.media import get_all_external_ids_batch
from db.database import get_async_session, get_read_session
from db.enums import WatchAction
from db.models import User, UserProfile, WatchHistory
from db.models import Episode, EpisodeImage, Season, SeriesMetadata

router = APIRouter(prefix="/api/v1/watch-history", tags=["Watch History"])


# ============================================
# Pydantic Schemas
# ============================================


class ExternalIds(BaseModel):
    """External IDs for a media item"""

    imdb: str | None = None
    tmdb: int | None = None
    tvdb: int | None = None
    mal: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalIds":
        return cls(
            imdb=data.get("imdb"),
            tmdb=int(data["tmdb"]) if data.get("tmdb") else None,
            tvdb=int(data["tvdb"]) if data.get("tvdb") else None,
            mal=int(data["mal"]) if data.get("mal") else None,
        )


class WatchHistoryCreate(BaseModel):
    """Request schema for creating a watch history entry."""

    profile_id: int
    media_id: int  # Internal media ID
    title: str
    media_type: str = Field(..., pattern="^(movie|series|tv)$")
    season: int | None = None
    episode: int | None = None
    duration: int | None = None  # Total duration in seconds
    progress: int = 0  # Progress in seconds


class StreamActionTrack(BaseModel):
    """Request schema for auto-tracking stream actions."""

    media_id: int  # Internal media ID
    title: str
    catalog_type: str = Field(..., pattern="^(movie|series|tv)$")
    season: int | None = None
    episode: int | None = None
    action: str = Field(..., pattern="^(download|queue|watch)$")  # Removed 'copy' - not worth tracking
    stream_info: dict | None = None  # Stream metadata (quality, size, source, etc.)


class WatchHistoryUpdate(BaseModel):
    """Request schema for updating watch progress."""

    progress: int
    duration: int | None = None


class StreamInfoResponse(BaseModel):
    """Stream info for downloaded/queued items."""

    resolution: str | None = None
    size: int | None = None
    source: str | None = None
    codec: str | None = None
    quality: str | None = None


class WatchHistoryResponse(BaseModel):
    """Response schema for a watch history entry."""

    id: int
    user_id: int
    profile_id: int
    media_id: int  # Internal FK
    external_ids: ExternalIds  # All external IDs for display
    title: str
    media_type: str
    season: int | None
    episode: int | None
    duration: int | None
    progress: int
    watched_at: datetime
    poster: str | None = None
    episode_poster: str | None = None  # Episode still/thumbnail if available
    action: str = "WATCHED"  # WATCHED, DOWNLOADED, QUEUED
    source: str = "mediafusion"  # mediafusion, trakt, simkl, manual
    stream_info: StreamInfoResponse | None = None

    class Config:
        from_attributes = True


class WatchHistoryListResponse(BaseModel):
    """Response schema for paginated watch history list."""

    items: list[WatchHistoryResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ContinueWatchingItem(BaseModel):
    """Schema for continue watching item."""

    id: int
    media_id: int
    external_ids: ExternalIds
    title: str
    media_type: str
    season: int | None
    episode: int | None
    progress: int
    duration: int | None
    progress_percent: float
    watched_at: datetime
    poster: str | None = None


# ============================================
# Helper Functions
# ============================================


async def get_episode_images_batch(
    session: AsyncSession,
    episode_keys: list[tuple[int, int, int]],  # List of (media_id, season, episode)
) -> dict[tuple[int, int, int], str]:
    """
    Batch fetch episode images for series items.
    Returns a dict mapping (media_id, season, episode) to image URL.
    """
    if not episode_keys:
        return {}

    result_map: dict[tuple[int, int, int], str] = {}

    # Get media IDs that need episode image lookup
    media_ids = list(set(k[0] for k in episode_keys))

    # Build a lookup of (media_id, season_num, episode_num) -> episode_id
    # First get series metadata for these media IDs
    series_query = select(SeriesMetadata).where(SeriesMetadata.media_id.in_(media_ids))
    series_result = await session.exec(series_query)
    series_list = series_result.all()

    if not series_list:
        return result_map

    series_ids = [s.id for s in series_list]
    media_to_series = {s.media_id: s.id for s in series_list}

    # Get all seasons for these series
    seasons_query = select(Season).where(Season.series_id.in_(series_ids))
    seasons_result = await session.exec(seasons_query)
    seasons_list = seasons_result.all()

    if not seasons_list:
        return result_map

    # Build season lookup: (series_id, season_number) -> season_id
    season_lookup = {(s.series_id, s.season_number): s.id for s in seasons_list}
    season_ids = [s.id for s in seasons_list]

    # Get all episodes for these seasons
    episodes_query = select(Episode).where(Episode.season_id.in_(season_ids))
    episodes_result = await session.exec(episodes_query)
    episodes_list = episodes_result.all()

    if not episodes_list:
        return result_map

    # Build episode lookup: (season_id, episode_number) -> episode_id
    episode_lookup = {(e.season_id, e.episode_number): e.id for e in episodes_list}
    episode_ids = [e.id for e in episodes_list]

    # Get primary images for these episodes
    images_query = select(EpisodeImage).where(
        EpisodeImage.episode_id.in_(episode_ids),
        EpisodeImage.is_primary.is_(True),
    )
    images_result = await session.exec(images_query)
    images_list = images_result.all()

    # Build image lookup: episode_id -> url
    image_lookup = {img.episode_id: img.url for img in images_list}

    # Now map back to our original keys
    for media_id, season_num, episode_num in episode_keys:
        series_id = media_to_series.get(media_id)
        if not series_id:
            continue

        season_id = season_lookup.get((series_id, season_num))
        if not season_id:
            continue

        episode_id = episode_lookup.get((season_id, episode_num))
        if not episode_id:
            continue

        image_url = image_lookup.get(episode_id)
        if image_url:
            result_map[(media_id, season_num, episode_num)] = image_url

    return result_map


async def verify_profile_ownership(session: AsyncSession, user: User, profile_id: int) -> UserProfile:
    """Verify that the profile belongs to the user."""
    profile = await session.get(UserProfile, profile_id)
    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    return profile


# ============================================
# API Endpoints
# ============================================


@router.get("", response_model=WatchHistoryListResponse)
async def list_watch_history(
    profile_id: int | None = Query(None, description="Filter by profile ID"),
    media_type: str | None = Query(None, pattern="^(movie|series|tv)$"),
    action: str | None = Query(None, pattern="^(WATCHED|DOWNLOADED|QUEUED)$", description="Filter by action type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    List user's watch history with pagination.
    Optionally filter by profile_id, media_type, or action.
    """
    # Build query (external_id lookup done after query)
    query = select(WatchHistory).where(WatchHistory.user_id == user.id)
    count_query = select(func.count(WatchHistory.id)).where(WatchHistory.user_id == user.id)

    if profile_id:
        # Verify profile ownership
        await verify_profile_ownership(session, user, profile_id)
        query = query.where(WatchHistory.profile_id == profile_id)
        count_query = count_query.where(WatchHistory.profile_id == profile_id)

    if media_type:
        query = query.where(WatchHistory.media_type == media_type)
        count_query = count_query.where(WatchHistory.media_type == media_type)

    if action:
        query = query.where(WatchHistory.action == WatchAction(action))
        count_query = count_query.where(WatchHistory.action == WatchAction(action))

    # Get total count
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(col(WatchHistory.watched_at).desc()).offset(offset).limit(page_size)
    result = await session.exec(query)
    items = result.all()

    if not items:
        return WatchHistoryListResponse(items=[], total=total, page=page, page_size=page_size, has_more=False)

    # Batch lookup all external_ids
    media_ids = [item.media_id for item in items]
    all_external_ids = await get_all_external_ids_batch(session, media_ids)

    # Batch lookup episode images for series items
    episode_keys = [
        (item.media_id, item.season, item.episode)
        for item in items
        if item.media_type == "series" and item.season and item.episode
    ]
    episode_images = await get_episode_images_batch(session, episode_keys)

    history_items = []
    for item in items:
        ext_ids_dict = all_external_ids.get(item.media_id, {})
        imdb_id = ext_ids_dict.get("imdb")
        poster_id = imdb_id or f"mf:{item.media_id}"

        # Get episode poster if available
        episode_poster = None
        if item.media_type == "series" and item.season and item.episode:
            episode_poster = episode_images.get((item.media_id, item.season, item.episode))

        # Build stream info if available
        stream_info = None
        if item.stream_info:
            stream_info = StreamInfoResponse(
                resolution=item.stream_info.get("resolution"),
                size=item.stream_info.get("size"),
                source=item.stream_info.get("source"),
                codec=item.stream_info.get("codec"),
                quality=item.stream_info.get("quality"),
            )

        history_items.append(
            WatchHistoryResponse(
                id=item.id,
                user_id=item.user_id,
                profile_id=item.profile_id,
                media_id=item.media_id,
                external_ids=ExternalIds.from_dict(ext_ids_dict),
                title=item.title,
                media_type=item.media_type,
                season=item.season,
                episode=item.episode,
                duration=item.duration,
                progress=item.progress,
                watched_at=item.watched_at,
                poster=f"/poster/{item.media_type}/{poster_id}.jpg",
                episode_poster=episode_poster,
                action=item.action.value if item.action else "WATCHED",
                source=item.source.value if item.source else "mediafusion",
                stream_info=stream_info,
            )
        )

    return WatchHistoryListResponse(
        items=history_items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.get("/continue-watching", response_model=list[ContinueWatchingItem])
async def get_continue_watching(
    profile_id: int | None = Query(None, description="Filter by profile ID"),
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get items the user can continue watching (progress > 0 and < 90%).
    Returns the most recently watched items first.
    """
    query = select(WatchHistory).where(
        WatchHistory.user_id == user.id,
        WatchHistory.progress > 0,
    )

    if profile_id:
        await verify_profile_ownership(session, user, profile_id)
        query = query.where(WatchHistory.profile_id == profile_id)

    query = query.order_by(col(WatchHistory.watched_at).desc()).limit(limit * 2)  # Get more to filter
    result = await session.exec(query)
    items = result.all()

    if not items:
        return []

    # Batch lookup all external_ids
    media_ids = [item.media_id for item in items]
    all_external_ids = await get_all_external_ids_batch(session, media_ids)

    continue_watching = []
    for item in items:
        if len(continue_watching) >= limit:
            break

        ext_ids_dict = all_external_ids.get(item.media_id, {})
        imdb_id = ext_ids_dict.get("imdb")
        poster_id = imdb_id or f"mf:{item.media_id}"

        # Calculate progress percentage
        if item.duration and item.duration > 0:
            progress_percent = (item.progress / item.duration) * 100
            # Only include if between 1% and 90%
            if 1 <= progress_percent < 90:
                continue_watching.append(
                    ContinueWatchingItem(
                        id=item.id,
                        media_id=item.media_id,
                        external_ids=ExternalIds.from_dict(ext_ids_dict),
                        title=item.title,
                        media_type=item.media_type,
                        season=item.season,
                        episode=item.episode,
                        progress=item.progress,
                        duration=item.duration,
                        progress_percent=round(progress_percent, 1),
                        watched_at=item.watched_at,
                        poster=f"/poster/{item.media_type}/{poster_id}.jpg",
                    )
                )

    return continue_watching


@router.post("", response_model=WatchHistoryResponse, status_code=status.HTTP_201_CREATED)
async def create_watch_history(
    data: WatchHistoryCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create or update a watch history entry.
    Uses media_id (internal ID) directly.
    If an entry exists for the same media (and episode for series), it will be updated.
    """
    from db.crud.media import get_all_external_ids_dict
    from db.models import Media

    # Verify profile ownership
    await verify_profile_ownership(session, user, data.profile_id)

    # Verify media exists
    media = await session.get(Media, data.media_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    # Check for existing entry using media_id
    query = select(WatchHistory).where(
        WatchHistory.user_id == user.id,
        WatchHistory.profile_id == data.profile_id,
        WatchHistory.media_id == data.media_id,
    )

    if data.media_type == "series" and data.season and data.episode:
        query = query.where(
            WatchHistory.season == data.season,
            WatchHistory.episode == data.episode,
        )

    result = await session.exec(query)
    existing = result.first()

    if existing:
        # Update existing entry
        existing.progress = data.progress
        existing.duration = data.duration or existing.duration
        existing.watched_at = datetime.now(pytz.UTC)
        existing.title = data.title
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        watch_entry = existing
    else:
        # Create new entry
        watch_entry = WatchHistory(
            user_id=user.id,
            profile_id=data.profile_id,
            media_id=data.media_id,
            title=data.title,
            media_type=data.media_type,
            season=data.season,
            episode=data.episode,
            duration=data.duration,
            progress=data.progress,
            watched_at=datetime.now(pytz.UTC),
        )
        session.add(watch_entry)
        await session.commit()
        await session.refresh(watch_entry)

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, data.media_id)
    imdb_id = ext_ids_dict.get("imdb")
    poster_id = imdb_id or f"mf:{data.media_id}"

    # Build stream info if available
    stream_info = None
    if watch_entry.stream_info:
        stream_info = StreamInfoResponse(
            resolution=watch_entry.stream_info.get("resolution"),
            size=watch_entry.stream_info.get("size"),
            source=watch_entry.stream_info.get("source"),
            codec=watch_entry.stream_info.get("codec"),
            quality=watch_entry.stream_info.get("quality"),
        )

    return WatchHistoryResponse(
        id=watch_entry.id,
        user_id=watch_entry.user_id,
        profile_id=watch_entry.profile_id,
        media_id=watch_entry.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        title=watch_entry.title,
        media_type=watch_entry.media_type,
        season=watch_entry.season,
        episode=watch_entry.episode,
        duration=watch_entry.duration,
        progress=watch_entry.progress,
        watched_at=watch_entry.watched_at,
        poster=f"/poster/{watch_entry.media_type}/{poster_id}.jpg",
        action=watch_entry.action.value if watch_entry.action else "WATCHED",
        source=watch_entry.source.value if watch_entry.source else "mediafusion",
        stream_info=stream_info,
    )


@router.patch("/{history_id}", response_model=WatchHistoryResponse)
async def update_watch_progress(
    history_id: int,
    data: WatchHistoryUpdate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Update watch progress for an existing entry."""
    from db.crud.media import get_all_external_ids_dict

    # Get watch entry
    query = select(WatchHistory).where(
        WatchHistory.id == history_id,
        WatchHistory.user_id == user.id,
    )
    result = await session.exec(query)
    watch_entry = result.first()

    if not watch_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watch history entry not found",
        )

    watch_entry.progress = data.progress
    if data.duration:
        watch_entry.duration = data.duration
    watch_entry.watched_at = datetime.now(pytz.UTC)

    session.add(watch_entry)
    await session.commit()
    await session.refresh(watch_entry)

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, watch_entry.media_id)
    imdb_id = ext_ids_dict.get("imdb")
    poster_id = imdb_id or f"mf:{watch_entry.media_id}"

    # Build stream info if available
    stream_info = None
    if watch_entry.stream_info:
        stream_info = StreamInfoResponse(
            resolution=watch_entry.stream_info.get("resolution"),
            size=watch_entry.stream_info.get("size"),
            source=watch_entry.stream_info.get("source"),
            codec=watch_entry.stream_info.get("codec"),
            quality=watch_entry.stream_info.get("quality"),
        )

    return WatchHistoryResponse(
        id=watch_entry.id,
        user_id=watch_entry.user_id,
        profile_id=watch_entry.profile_id,
        media_id=watch_entry.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        title=watch_entry.title,
        media_type=watch_entry.media_type,
        season=watch_entry.season,
        episode=watch_entry.episode,
        duration=watch_entry.duration,
        progress=watch_entry.progress,
        watched_at=watch_entry.watched_at,
        poster=f"/poster/{watch_entry.media_type}/{poster_id}.jpg",
        action=watch_entry.action.value if watch_entry.action else "WATCHED",
        source=watch_entry.source.value if watch_entry.source else "mediafusion",
        stream_info=stream_info,
    )


@router.delete("/{history_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watch_history(
    history_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a watch history entry."""
    watch_entry = await session.get(WatchHistory, history_id)

    if not watch_entry or watch_entry.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watch history entry not found",
        )

    await session.delete(watch_entry)
    await session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_watch_history(
    profile_id: int | None = Query(None, description="Clear only for this profile"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Clear all watch history for the user or a specific profile."""
    query = select(WatchHistory).where(WatchHistory.user_id == user.id)

    if profile_id:
        await verify_profile_ownership(session, user, profile_id)
        query = query.where(WatchHistory.profile_id == profile_id)

    result = await session.exec(query)
    entries = result.all()

    for entry in entries:
        await session.delete(entry)

    await session.commit()


@router.post("/track", response_model=WatchHistoryResponse, status_code=status.HTTP_201_CREATED)
async def track_stream_action(
    data: StreamActionTrack,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Auto-track when user performs an action on a stream (download, queue, watch).
    Uses media_id (internal ID) directly.
    Creates or updates a watch history entry with the action recorded.
    Uses the user's default profile.
    For 'watch' action, also scrobbles to external platforms (Trakt, Simkl).
    """
    from api.services.sync.manager import IntegrationManager
    from db.crud.media import get_all_external_ids_dict
    from db.models import Media

    # Verify media exists
    media = await session.get(Media, data.media_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )

    # Get user's default profile
    profile_query = select(UserProfile).where(
        UserProfile.user_id == user.id,
        UserProfile.is_default == True,
    )
    profile_result = await session.exec(profile_query)
    profile = profile_result.first()

    if not profile:
        # Get any profile or create default
        any_profile_query = select(UserProfile).where(UserProfile.user_id == user.id)
        any_result = await session.exec(any_profile_query)
        profile = any_result.first()

        if not profile:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No profile found. Please create a profile first.",
            )

    # Check for existing entry for this media using media_id
    existing_query = select(WatchHistory).where(
        WatchHistory.user_id == user.id,
        WatchHistory.profile_id == profile.id,
        WatchHistory.media_id == data.media_id,
    )

    if data.catalog_type == "series" and data.season and data.episode:
        existing_query = existing_query.where(
            WatchHistory.season == data.season,
            WatchHistory.episode == data.episode,
        )

    result = await session.exec(existing_query)
    existing = result.first()

    if existing:
        # Update existing entry - just update the timestamp
        existing.watched_at = datetime.now(pytz.UTC)
        existing.title = data.title
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        watch_entry = existing
    else:
        # Create new entry with initial progress
        watch_entry = WatchHistory(
            user_id=user.id,
            profile_id=profile.id,
            media_id=data.media_id,
            title=data.title,
            media_type=data.catalog_type,
            season=data.season,
            episode=data.episode,
            duration=None,
            progress=0,  # Will be updated when actual watching happens
            watched_at=datetime.now(pytz.UTC),
        )
        session.add(watch_entry)
        await session.commit()
        await session.refresh(watch_entry)

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, data.media_id)
    imdb_id = ext_ids_dict.get("imdb")
    tmdb_id = int(ext_ids_dict["tmdb"]) if ext_ids_dict.get("tmdb") else None
    poster_id = imdb_id or f"mf:{data.media_id}"

    # Scrobble to external platforms for 'watch' action
    # IntegrationManager looks up integrations from DB by profile_id
    if data.action == "watch" and (imdb_id or tmdb_id):
        try:
            await IntegrationManager.scrobble_playback_start(
                profile_id=profile.id,
                imdb_id=imdb_id,
                tmdb_id=tmdb_id,
                title=data.title,
                media_type=data.catalog_type,
                season=data.season,
                episode=data.episode,
            )
        except Exception as e:
            # Don't fail the request if scrobbling fails
            import logging

            logging.warning(f"Failed to scrobble to external platforms: {e}")

    # Build stream info if available
    stream_info = None
    if watch_entry.stream_info:
        stream_info = StreamInfoResponse(
            resolution=watch_entry.stream_info.get("resolution"),
            size=watch_entry.stream_info.get("size"),
            source=watch_entry.stream_info.get("source"),
            codec=watch_entry.stream_info.get("codec"),
            quality=watch_entry.stream_info.get("quality"),
        )

    return WatchHistoryResponse(
        id=watch_entry.id,
        user_id=watch_entry.user_id,
        profile_id=watch_entry.profile_id,
        media_id=watch_entry.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        title=watch_entry.title,
        media_type=watch_entry.media_type,
        season=watch_entry.season,
        episode=watch_entry.episode,
        duration=watch_entry.duration,
        progress=watch_entry.progress,
        watched_at=watch_entry.watched_at,
        poster=f"/poster/{watch_entry.media_type}/{poster_id}.jpg",
        action=watch_entry.action.value if watch_entry.action else "WATCHED",
        source=watch_entry.source.value if watch_entry.source else "mediafusion",
        stream_info=stream_info,
    )
