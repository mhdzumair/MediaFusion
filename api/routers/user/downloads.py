"""
Download History API endpoints for tracking user downloads.

Downloads are now stored in the unified WatchHistory table with action='downloaded'.
This provides a consistent view of all user activity while maintaining the
downloads-specific API for backward compatibility.
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

router = APIRouter(prefix="/api/v1/downloads", tags=["Downloads"])


# ============================================
# Pydantic Schemas
# ============================================


class StreamInfo(BaseModel):
    """Stream information for the download."""

    info_hash: str | None = None
    source: str | None = None
    resolution: str | None = None
    quality: str | None = None
    size: int | None = None
    file_name: str | None = None


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


class DownloadCreate(BaseModel):
    """Request schema for logging a download."""

    profile_id: int
    media_id: int  # Internal media ID
    title: str
    media_type: str = Field(..., pattern="^(movie|series)$")
    season: int | None = None
    episode: int | None = None
    stream_info: StreamInfo | None = None


class DownloadResponse(BaseModel):
    """Response schema for a download entry."""

    id: int
    user_id: int
    profile_id: int
    media_id: int  # Internal FK
    external_ids: ExternalIds  # All external IDs for display
    title: str
    media_type: str
    season: int | None
    episode: int | None
    stream_info: dict
    downloaded_at: datetime
    poster: str | None = None

    class Config:
        from_attributes = True


class DownloadListResponse(BaseModel):
    """Response schema for paginated download list."""

    items: list[DownloadResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class DownloadStats(BaseModel):
    """Download statistics response."""

    total_downloads: int
    movies_downloaded: int
    series_downloaded: int
    this_month: int


# ============================================
# Helper Functions
# ============================================


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


@router.get("", response_model=DownloadListResponse)
async def list_downloads(
    profile_id: int | None = Query(None, description="Filter by profile ID"),
    media_type: str | None = Query(None, pattern="^(movie|series)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    List user's download history with pagination.
    Optionally filter by profile_id or media_type.
    """
    # Build query - filter by action=downloaded
    query = select(WatchHistory).where(
        WatchHistory.user_id == user.id,
        WatchHistory.action == WatchAction.DOWNLOADED,
    )
    count_query = select(func.count(WatchHistory.id)).where(
        WatchHistory.user_id == user.id,
        WatchHistory.action == WatchAction.DOWNLOADED,
    )

    if profile_id:
        await verify_profile_ownership(session, user, profile_id)
        query = query.where(WatchHistory.profile_id == profile_id)
        count_query = count_query.where(WatchHistory.profile_id == profile_id)

    if media_type:
        query = query.where(WatchHistory.media_type == media_type)
        count_query = count_query.where(WatchHistory.media_type == media_type)

    # Get total count
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(col(WatchHistory.watched_at).desc()).offset(offset).limit(page_size)
    result = await session.exec(query)
    items = result.all()

    if not items:
        return DownloadListResponse(items=[], total=total, page=page, page_size=page_size, has_more=False)

    # Batch lookup all external_ids
    media_ids = [item.media_id for item in items]
    all_external_ids = await get_all_external_ids_batch(session, media_ids)

    download_items = []
    for item in items:
        ext_ids_dict = all_external_ids.get(item.media_id, {})
        imdb_id = ext_ids_dict.get("imdb")
        poster_id = imdb_id or f"mf:{item.media_id}"
        download_items.append(
            DownloadResponse(
                id=item.id,
                user_id=item.user_id,
                profile_id=item.profile_id,
                media_id=item.media_id,
                external_ids=ExternalIds.from_dict(ext_ids_dict),
                title=item.title,
                media_type=item.media_type,
                season=item.season,
                episode=item.episode,
                stream_info=item.stream_info or {},
                downloaded_at=item.watched_at,
                poster=f"/poster/{item.media_type}/{poster_id}.jpg",
            )
        )

    return DownloadListResponse(
        items=download_items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.get("/stats", response_model=DownloadStats)
async def get_download_stats(
    profile_id: int | None = Query(None, description="Filter by profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get download statistics for the user."""
    base_filters = [
        WatchHistory.user_id == user.id,
        WatchHistory.action == WatchAction.DOWNLOADED,
    ]

    if profile_id:
        await verify_profile_ownership(session, user, profile_id)
        base_filters.append(WatchHistory.profile_id == profile_id)

    # Total downloads
    total_result = await session.exec(select(func.count(WatchHistory.id)).where(*base_filters))
    total_downloads = total_result.one()

    # By media type
    movies_result = await session.exec(
        select(func.count(WatchHistory.id)).where(
            *base_filters,
            WatchHistory.media_type == "movie",
        )
    )
    movies_downloaded = movies_result.one()

    series_result = await session.exec(
        select(func.count(WatchHistory.id)).where(
            *base_filters,
            WatchHistory.media_type == "series",
        )
    )
    series_downloaded = series_result.one()

    # This month
    now = datetime.now(pytz.UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_result = await session.exec(
        select(func.count(WatchHistory.id)).where(
            *base_filters,
            WatchHistory.watched_at >= month_start,
        )
    )
    this_month = month_result.one()

    return DownloadStats(
        total_downloads=total_downloads,
        movies_downloaded=movies_downloaded,
        series_downloaded=series_downloaded,
        this_month=this_month,
    )


@router.post("", response_model=DownloadResponse, status_code=status.HTTP_201_CREATED)
async def log_download(
    data: DownloadCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Log a new download. Uses media_id (internal ID) directly."""
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

    # Create WatchHistory entry with action=downloaded
    download = WatchHistory(
        user_id=user.id,
        profile_id=data.profile_id,
        media_id=data.media_id,
        title=data.title,
        media_type=data.media_type,
        season=data.season,
        episode=data.episode,
        stream_info=data.stream_info.model_dump() if data.stream_info else {},
        action=WatchAction.DOWNLOADED,
        progress=0,
        watched_at=datetime.now(pytz.UTC),
    )

    session.add(download)
    await session.commit()
    await session.refresh(download)

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, data.media_id)
    imdb_id = ext_ids_dict.get("imdb")
    poster_id = imdb_id or f"mf:{data.media_id}"

    return DownloadResponse(
        id=download.id,
        user_id=download.user_id,
        profile_id=download.profile_id,
        media_id=download.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        title=download.title,
        media_type=download.media_type,
        season=download.season,
        episode=download.episode,
        stream_info=download.stream_info,
        downloaded_at=download.watched_at,
        poster=f"/poster/{download.media_type}/{poster_id}.jpg",
    )


@router.delete("/{download_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_download(
    download_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a download history entry."""
    download = await session.get(WatchHistory, download_id)

    if not download or download.user_id != user.id or download.action != WatchAction.DOWNLOADED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Download not found",
        )

    await session.delete(download)
    await session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_downloads(
    profile_id: int | None = Query(None, description="Clear only for this profile"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Clear all download history for the user or a specific profile."""
    query = select(WatchHistory).where(
        WatchHistory.user_id == user.id,
        WatchHistory.action == WatchAction.DOWNLOADED,
    )

    if profile_id:
        await verify_profile_ownership(session, user, profile_id)
        query = query.where(WatchHistory.profile_id == profile_id)

    result = await session.exec(query)
    entries = result.all()

    for entry in entries:
        await session.delete(entry)

    await session.commit()
