"""
M3U Playlist Import API endpoints.
"""

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.torrent_import import ImportResponse
from db.config import settings
from api.routers.user.auth import require_auth
from db import schemas
from db.crud import create_http_stream, get_media_by_external_id
from db.crud.scraper_helpers import (
    get_or_create_metadata,
    save_tv_channel_metadata,
)
from db.crud.streams import get_http_stream_by_url_for_media
from db.database import get_async_session
from db.enums import IPTVSourceType, MediaType
from db.models import FileMediaLink, HTTPStream, IPTVSource, Stream, StreamFile, User
from db.models.streams import FileType, LinkSource, StreamType
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.import_tasks import (
    create_import_job,
    get_import_job_status as get_job_status,
    run_m3u_import,
)
from scrapers.scraper_tasks import meta_fetcher
from utils.m3u_parser import parse_m3u_playlist_for_preview

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# M3U Import Schemas
# ============================================


class M3UContentType(str, Enum):
    """Content type detected from M3U entry."""

    TV = "tv"
    MOVIE = "movie"
    SERIES = "series"
    UNKNOWN = "unknown"


class M3UMatchedMedia(BaseModel):
    """Matched media metadata from IMDb/TMDB search."""

    id: str  # External ID (e.g., tt1234567)
    title: str
    year: int | None = None
    poster: str | None = None
    type: str  # movie or series


class M3UChannelPreview(BaseModel):
    """Preview of a single M3U entry with auto-detected info."""

    index: int
    name: str
    url: str
    logo: str | None = None
    genres: list[str] = Field(default_factory=list)
    country: str | None = None
    # Auto-detected content type
    detected_type: M3UContentType = M3UContentType.UNKNOWN
    # For movies/series - matched metadata if found
    matched_media: M3UMatchedMedia | None = None
    # Parsed info for series
    season: int | None = None
    episode: int | None = None
    parsed_title: str | None = None
    parsed_year: int | None = None


class M3UAnalyzeResponse(BaseModel):
    """Response from M3U playlist analysis."""

    status: str
    redis_key: str  # For subsequent import
    total_count: int
    channels: list[M3UChannelPreview]  # First 50 entries
    summary: dict[str, int]  # {tv: 10, movie: 30, series: 8, unknown: 2}
    error: str | None = None


class M3UImportOverride(BaseModel):
    """User override for a single M3U entry."""

    index: int
    type: M3UContentType
    media_id: str | None = None  # External ID if user selected different match


# ============================================
# M3U Import Helper Functions
# ============================================


async def _import_tv_entry(
    session: AsyncSession,
    entry: dict[str, Any],
    source: str,
    user_id: int,
    is_public: bool,
) -> dict:
    """Import a single TV channel entry.

    Handles deduplication:
    - Channels are matched by title (case-insensitive)
    - Stream URLs are deduplicated per channel

    Returns dict with:
        - channel_created: bool - True if new channel was created
        - stream_created: bool - True if new stream was added
        - stream_existed: bool - True if stream URL already existed
    """
    result = {
        "channel_created": False,
        "stream_created": False,
        "stream_existed": False,
    }

    # Create TV metadata
    tv_metadata = schemas.TVMetaData(
        title=entry["name"],
        poster=entry.get("logo"),
        country=entry.get("country"),
        tv_language=entry.get("language"),
        genres=entry.get("genres", []),
        streams=[],  # We'll create streams separately
    )

    # Save TV channel metadata (finds existing or creates new)
    # This now uses title-based matching for deduplication
    media_id = await save_tv_channel_metadata(
        session=session,
        metadata=tv_metadata,
        user_id=user_id,
        is_public=is_public,
    )

    # Check if this exact stream URL already exists for this channel
    stream_url = entry["url"]
    existing_stream = await get_http_stream_by_url_for_media(
        session=session,
        url=stream_url,
        media_id=media_id,
    )

    if existing_stream:
        # Stream URL already exists - skip to avoid duplicates
        result["stream_existed"] = True
    else:
        # Create new HTTP stream
        await create_http_stream(
            session=session,
            url=stream_url,
            name=entry.get("tvg_name", entry["name"]),
            media_id=media_id,
            source=source,
            uploader_user_id=user_id,
            is_public=is_public,
        )
        result["stream_created"] = True

    return result


async def _import_movie_entry(
    session: AsyncSession,
    entry: dict[str, Any],
    source: str,
    user_id: int,
    is_public: bool,
):
    """Import a single movie entry."""
    media_id = None

    # Try to find/create metadata
    matched_media_id = entry.get("matched_media_id")
    if matched_media_id:
        # User or auto-matched to specific media
        media = await get_media_by_external_id(session, matched_media_id, MediaType.MOVIE)
        if media:
            media_id = media.id

    if not media_id:
        # Try to search for matching media
        try:
            matches = await meta_fetcher.search_multiple_results(
                title=entry.get("parsed_title", entry["name"]),
                year=entry.get("parsed_year"),
                media_type="movie",
            )
            if matches:
                external_id = matches[0].get("id")
                if external_id:
                    media = await get_media_by_external_id(session, external_id, MediaType.MOVIE)
                    if media:
                        media_id = media.id
        except Exception as e:
            logger.warning(f"Failed to search for movie metadata: {e}")

    if not media_id:
        # Create user-owned metadata
        media = await get_or_create_metadata(
            session=session,
            metadata_data={
                "id": f"mf:user:{user_id}:{uuid4().hex[:8]}",
                "title": entry.get("parsed_title", entry["name"]),
                "year": entry.get("parsed_year"),
            },
            media_type="movie",
        )
        if media:
            # Update user ownership fields
            media.created_by_user_id = user_id
            media.is_user_created = True
            media.is_public = is_public
            media_id = media.id

    if media_id:
        # Create HTTP stream
        await create_http_stream(
            session=session,
            url=entry["url"],
            name=entry["name"],
            media_id=media_id,
            source=source,
            uploader_user_id=user_id,
            is_public=is_public,
        )


async def _import_series_entry(
    session: AsyncSession,
    entry: dict[str, Any],
    source: str,
    user_id: int,
    is_public: bool,
):
    """Import a single series episode entry."""
    media_id = None
    season = entry.get("season", 1)
    episode = entry.get("episode", 1)

    # Try to find/create metadata
    matched_media_id = entry.get("matched_media_id")
    if matched_media_id:
        media = await get_media_by_external_id(session, matched_media_id, MediaType.SERIES)
        if media:
            media_id = media.id

    if not media_id:
        # Try to search for matching media
        try:
            matches = await meta_fetcher.search_multiple_results(
                title=entry.get("parsed_title", entry["name"]),
                year=entry.get("parsed_year"),
                media_type="series",
            )
            if matches:
                external_id = matches[0].get("id")
                if external_id:
                    media = await get_media_by_external_id(session, external_id, MediaType.SERIES)
                    if media:
                        media_id = media.id
        except Exception as e:
            logger.warning(f"Failed to search for series metadata: {e}")

    if not media_id:
        # Create user-owned metadata
        media = await get_or_create_metadata(
            session=session,
            metadata_data={
                "id": f"mf:user:{user_id}:{uuid4().hex[:8]}",
                "title": entry.get("parsed_title", entry["name"]),
                "year": entry.get("parsed_year"),
            },
            media_type="series",
        )
        if media:
            # Update user ownership fields
            media.created_by_user_id = user_id
            media.is_user_created = True
            media.is_public = is_public
            media_id = media.id

    if not media_id:
        logger.warning(f"Could not create metadata for series entry: {entry['name']}")
        return

    # Create HTTP stream with file-level linking for series
    stream = Stream(
        stream_type=StreamType.HTTP,
        name=entry["name"],
        source=source,
        uploader_user_id=user_id,
        is_public=is_public,
    )
    session.add(stream)
    await session.flush()

    http_stream = HTTPStream(
        stream_id=stream.id,
        url=entry["url"],
    )
    session.add(http_stream)

    # Create a virtual file for the stream
    stream_file = StreamFile(
        stream_id=stream.id,
        file_index=0,
        filename=entry["name"],
        file_type=FileType.VIDEO,
    )
    session.add(stream_file)
    await session.flush()

    # Link file to media with season/episode info
    file_link = FileMediaLink(
        file_id=stream_file.id,
        media_id=media_id,
        season_number=season,
        episode_number=episode,
        is_primary=True,
        link_source=LinkSource.USER,
        confidence=1.0,
    )
    session.add(file_link)


# ============================================
# M3U Import Endpoints
# ============================================


@router.post("/m3u/analyze", response_model=M3UAnalyzeResponse)
async def analyze_m3u_playlist(
    m3u_url: str = Form(None),
    m3u_file: UploadFile = File(None),
    user: User = Depends(require_auth),
):
    """
    Analyze an M3U playlist and return preview data with auto-detected content types.

    Returns the first 50 entries with detected types (TV/Movie/Series) and
    tries to match movies/series to IMDb/TMDB metadata.

    The full playlist data is cached in Redis for subsequent import.
    """
    # Check if IPTV import feature is enabled
    if not settings.enable_iptv_import:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IPTV import feature is disabled on this server.",
        )

    if not m3u_url and not m3u_file:
        return M3UAnalyzeResponse(
            status="error",
            redis_key="",
            total_count=0,
            channels=[],
            summary={},
            error="Either M3U URL or file must be provided.",
        )

    try:
        # Parse the playlist
        playlist_content = None
        if m3u_file:
            content_bytes = await m3u_file.read()
            playlist_content = content_bytes.decode("utf-8")

        entries, summary, total_count = await parse_m3u_playlist_for_preview(
            playlist_content=playlist_content,
            playlist_url=m3u_url,
            preview_limit=50,
        )

        # Try to match movies/series to metadata
        channels = []
        for entry in entries:
            detected_type = M3UContentType(entry["detected_type"])
            matched_media = None

            # Search for metadata for movies and series
            if detected_type in (M3UContentType.MOVIE, M3UContentType.SERIES):
                try:
                    media_type = "movie" if detected_type == M3UContentType.MOVIE else "series"
                    matches = await meta_fetcher.search_multiple_results(
                        title=entry["parsed_title"],
                        year=entry.get("parsed_year"),
                        media_type=media_type,
                    )
                    if matches:
                        # Take the first (best) match
                        match = matches[0]
                        matched_media = M3UMatchedMedia(
                            id=match.get("id", ""),
                            title=match.get("title", ""),
                            year=match.get("year"),
                            poster=match.get("poster"),
                            type=media_type,
                        )
                except Exception as e:
                    logger.warning(f"Failed to search metadata for {entry['name']}: {e}")

            channels.append(
                M3UChannelPreview(
                    index=entry["index"],
                    name=entry["name"],
                    url=entry["url"],
                    logo=entry.get("logo"),
                    genres=entry.get("genres", []),
                    country=entry.get("country"),
                    detected_type=detected_type,
                    matched_media=matched_media,
                    season=entry.get("season"),
                    episode=entry.get("episode"),
                    parsed_title=entry.get("parsed_title"),
                    parsed_year=entry.get("parsed_year"),
                )
            )

        # Cache the full playlist data in Redis for import step
        redis_key = f"m3u_analyze_{uuid4().hex[:12]}"
        cache_data = {
            "entries": entries,
            "summary": summary,
            "total_count": total_count,
            "user_id": user.id,
            "source_url": m3u_url,
        }

        # If file upload, also store the content
        if playlist_content:
            cache_data["playlist_content"] = playlist_content

        await REDIS_ASYNC_CLIENT.set(
            redis_key,
            json.dumps(cache_data),
            ex=3600,  # 1 hour expiry
        )

        return M3UAnalyzeResponse(
            status="success",
            redis_key=redis_key,
            total_count=total_count,
            channels=channels,
            summary=summary,
        )

    except Exception as e:
        logger.exception(f"Failed to analyze M3U playlist: {e}")
        return M3UAnalyzeResponse(
            status="error",
            redis_key="",
            total_count=0,
            channels=[],
            summary={},
            error=f"Failed to analyze playlist: {str(e)}",
        )


@router.post("/m3u", response_model=ImportResponse)
async def import_m3u_playlist(
    m3u_url: str = Form(None),
    m3u_file: UploadFile = File(None),
    redis_key: str = Form(None),
    source: str = Form("custom"),
    is_public: bool = Form(True),
    overrides: str = Form(None),  # JSON string of M3UImportOverride list
    save_source: bool = Form(False),  # Save URL for re-sync
    source_name: str = Form(None),  # Custom name for saved source
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import an M3U playlist with support for TV, movies, and series.

    Can use a redis_key from a prior analyze call, or directly import from URL/file.
    Supports user overrides for content types and metadata matching.

    For playlists with more than 100 items, the import is processed in the background.
    The response will include a job_id that can be used to poll for status.

    If save_source=True and m3u_url is provided, the URL will be saved for
    future re-sync via the IPTV sources management.
    """
    # Check if IPTV import feature is enabled
    if not settings.enable_iptv_import:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="IPTV import feature is disabled on this server.",
        )

    # Enforce private-only if public sharing is disabled
    if not settings.allow_public_iptv_sharing:
        is_public = False

    import_id = uuid4().hex[:10]

    # Parse overrides if provided
    override_map = {}
    if overrides:
        try:
            override_list = json.loads(overrides)
            for override in override_list:
                override_map[override["index"]] = override
        except json.JSONDecodeError:
            logger.warning("Failed to parse overrides JSON")

    try:
        entries = []
        cached_m3u_url = None

        # Try to load from redis cache first (from analyze step)
        if redis_key:
            cached_data = await REDIS_ASYNC_CLIENT.get(redis_key)
            if cached_data:
                cache = json.loads(cached_data)
                entries = cache.get("entries", [])
                cached_m3u_url = cache.get("source_url")  # Retrieve cached URL for saving
                # Don't delete cache yet - might need it for background task

        # If no cached data, parse directly
        if not entries:
            if not m3u_url and not m3u_file:
                return ImportResponse(
                    status="error",
                    message="Either M3U URL, file, or valid redis_key must be provided.",
                )

            playlist_content = None
            if m3u_file:
                content_bytes = await m3u_file.read()
                playlist_content = content_bytes.decode("utf-8")

            entries, _, _ = await parse_m3u_playlist_for_preview(
                playlist_content=playlist_content,
                playlist_url=m3u_url,
                preview_limit=100000,  # Import all entries
            )

        # Use direct m3u_url or cached URL from analyze step
        final_m3u_url = m3u_url or cached_m3u_url

        # For large imports (>100 items), use background processing
        BACKGROUND_THRESHOLD = 100
        if len(entries) > BACKGROUND_THRESHOLD:
            # Create import job
            job_id = f"m3u_{import_id}"
            await create_import_job(
                job_id=job_id,
                user_id=user.id,
                source_type="m3u",
                total_items=len(entries),
            )

            # Queue background task
            await asyncio.to_thread(
                run_m3u_import.send,
                job_id=job_id,
                user_id=user.id,
                entries=entries,
                source=source,
                is_public=is_public,
                override_map=override_map,
                save_source=save_source,
                source_name=source_name,
                m3u_url=final_m3u_url,
            )

            # Delete redis cache after queueing
            if redis_key:
                await REDIS_ASYNC_CLIENT.delete(redis_key)

            return ImportResponse(
                status="processing",
                message=f"Import of {len(entries)} items started in background.",
                import_id=import_id,
                details={
                    "job_id": job_id,
                    "total_items": len(entries),
                    "background": True,
                },
            )

        # For small imports, process synchronously
        stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}

        for entry in entries:
            try:
                idx = entry["index"]

                # Apply override if exists
                if idx in override_map:
                    override = override_map[idx]
                    entry["detected_type"] = override.get("type", entry["detected_type"])
                    if "media_id" in override and override["media_id"]:
                        entry["matched_media_id"] = override["media_id"]

                content_type = M3UContentType(entry["detected_type"])

                if content_type == M3UContentType.TV:
                    # Import as TV channel (handles deduplication)
                    import_result = await _import_tv_entry(
                        session=session,
                        entry=entry,
                        source=source,
                        user_id=user.id,
                        is_public=is_public,
                    )
                    if import_result["stream_created"]:
                        stats["tv"] += 1
                    elif import_result["stream_existed"]:
                        stats["skipped"] += 1

                elif content_type == M3UContentType.MOVIE:
                    # Import as movie stream
                    await _import_movie_entry(
                        session=session,
                        entry=entry,
                        source=source,
                        user_id=user.id,
                        is_public=is_public,
                    )
                    stats["movie"] += 1

                elif content_type == M3UContentType.SERIES:
                    # Import as series episode stream
                    await _import_series_entry(
                        session=session,
                        entry=entry,
                        source=source,
                        user_id=user.id,
                        is_public=is_public,
                    )
                    stats["series"] += 1

                else:
                    # Skip unknown types
                    logger.info(f"Skipping unknown content type for: {entry['name']}")
                    stats["skipped"] += 1

            except Exception as e:
                logger.warning(f"Failed to import entry {entry.get('name', 'unknown')}: {e}")
                stats["failed"] += 1

        await session.commit()

        # Delete redis cache after processing
        if redis_key:
            await REDIS_ASYNC_CLIENT.delete(redis_key)

        total_imported = stats["tv"] + stats["movie"] + stats["series"]

        # Save IPTV source for re-sync if requested
        source_id = None
        if save_source and final_m3u_url:
            # Generate a name if not provided
            if not source_name:
                parsed = urlparse(final_m3u_url)
                source_name = f"M3U - {parsed.netloc or 'playlist'}"

            iptv_source = IPTVSource(
                user_id=user.id,
                source_type=IPTVSourceType.M3U,
                name=source_name,
                m3u_url=final_m3u_url,
                is_public=is_public,
                import_live=True,
                import_vod=True,
                import_series=True,
                last_synced_at=datetime.now(pytz.UTC),
                last_sync_stats=stats,
                is_active=True,
            )
            session.add(iptv_source)
            await session.commit()
            await session.refresh(iptv_source)
            source_id = iptv_source.id
            logger.info(f"Saved M3U source '{source_name}' for user {user.id}")

        return ImportResponse(
            status="success",
            message=f"Successfully imported {total_imported} items from M3U playlist.",
            import_id=import_id,
            details={
                "source": source,
                "is_public": is_public,
                "stats": stats,
                "source_saved": save_source and final_m3u_url is not None,
                "source_id": source_id,
            },
        )

    except Exception as e:
        logger.exception(f"Failed to import M3U playlist: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import playlist: {str(e)}",
        )


@router.get("/job/{job_id}")
async def get_import_job_status(
    job_id: str,
    user: User = Depends(require_auth),
):
    """
    Get the status of a background import job.

    Returns progress, stats, and completion status.
    """
    job_data = await get_job_status(job_id)
    if not job_data:
        return {"status": "not_found", "message": f"Job {job_id} not found"}

    # Verify user owns this job
    if job_data.get("user_id") != user.id:
        return {"status": "error", "message": "Unauthorized"}

    return job_data


class IPTVImportSettings(BaseModel):
    """IPTV import feature settings from server configuration."""

    enabled: bool
    allow_public_sharing: bool


@router.get("/iptv-settings", response_model=IPTVImportSettings)
async def get_iptv_import_settings():
    """
    Get IPTV import feature settings.

    Returns whether the feature is enabled and whether public sharing is allowed.
    This endpoint does not require authentication so the UI can check before
    showing import options.
    """
    return IPTVImportSettings(
        enabled=settings.enable_iptv_import,
        allow_public_sharing=settings.allow_public_iptv_sharing,
    )
