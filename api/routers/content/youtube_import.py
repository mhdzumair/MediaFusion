"""
YouTube Import API endpoints for importing YouTube videos as streams.
"""

import logging
import re
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, Form
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from db.crud.media import get_media_by_external_id
from db.crud.reference import get_or_create_language
from db.crud.streams import create_youtube_stream
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType
from db.models import Contribution, Media, User
from db.models.streams import (
    StreamLanguageLink,
    YouTubeStream,
)
from utils.youtube import analyze_youtube_video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# YouTube URL Patterns
# ============================================

YOUTUBE_URL_PATTERNS = [
    # Standard watch URL
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
    # Short URL
    r"(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})",
    # Embed URL
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})",
    # Old v/ URL
    r"(?:https?://)?(?:www\.)?youtube\.com/v/([a-zA-Z0-9_-]{11})",
    # Shorts URL
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
]


def extract_youtube_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    for pattern in YOUTUBE_URL_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    # Check if the input is already a video ID
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url

    return None


# ============================================
# Pydantic Schemas
# ============================================


class YouTubeAnalyzeRequest(BaseModel):
    """Request to analyze a YouTube URL."""

    youtube_url: str
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")


class YouTubeAnalyzeResponse(BaseModel):
    """Response from YouTube analysis."""

    status: str
    video_id: str | None = None
    title: str | None = None
    channel_name: str | None = None
    channel_id: str | None = None
    thumbnail: str | None = None
    duration_seconds: int | None = None
    is_live: bool = False
    matches: list[dict[str, Any]] | None = None
    error: str | None = None


class YouTubeImportRequest(BaseModel):
    """Request to import a YouTube video."""

    youtube_url: str
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")
    meta_id: str | None = None
    title: str | None = None
    languages: str | None = None  # Comma-separated
    is_anonymous: bool | None = None  # None means use user's preference
    force_import: bool = False


class YouTubeImportResponse(BaseModel):
    """Response from YouTube import."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


# ============================================
# YouTube Import Processing
# ============================================


async def process_youtube_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User,
) -> dict:
    """
    Process a YouTube import - creates the actual YouTubeStream record in the database.

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """
    video_id = contribution_data.get("video_id", "")
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "YouTube Video")

    # Anonymous contribution handling
    is_anonymous = contribution_data.get("is_anonymous", False)

    if not video_id:
        raise ValueError("Missing video_id in contribution data")

    # Check if YouTube video already exists
    existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
    if existing.first():
        return {"status": "exists", "message": "YouTube video already exists in database"}

    # Get or create media metadata
    media = None
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    if not media:
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id or f"yt_{video_id}",
                meta_type,
                fallback_title=title,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch/create media for {meta_id}: {e}")
            media_type_map = {
                "movie": MediaType.MOVIE,
                "series": MediaType.SERIES,
                "tv": MediaType.TV,
                "sports": MediaType.EVENTS,
            }
            media_type_enum = media_type_map.get(meta_type, MediaType.MOVIE)
            media = Media(
                title=title,
                type=media_type_enum,
            )
            session.add(media)
            await session.flush()

    # Determine uploader name and user_id based on anonymous preference
    if is_anonymous:
        uploader_name = "Anonymous"
        uploader_user_id = None
    else:
        uploader_name = user.username or user.email or f"User #{user.id}"
        uploader_user_id = user.id

    # Create YouTube stream
    yt_stream = await create_youtube_stream(
        session,
        video_id=video_id,
        name=title,
        media_id=media.id,
        source="youtube",
        is_live=contribution_data.get("is_live", False),
        uploader=uploader_name,
        uploader_user_id=uploader_user_id,
    )

    # Add languages if provided
    languages = contribution_data.get("languages", [])
    for lang_name in languages:
        if lang_name:
            try:
                lang = await get_or_create_language(session, lang_name)
                lang_link = StreamLanguageLink(stream_id=yt_stream.stream_id, language_id=lang.id)
                session.add(lang_link)
            except Exception as e:
                logger.warning(f"Failed to add language {lang_name}: {e}")

    # Update media stream count
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = datetime.now(pytz.UTC)

    await session.flush()

    logger.info(f"Successfully imported YouTube video {video_id} for media_id={media.id}")

    return {
        "status": "success",
        "stream_id": yt_stream.stream_id,
        "media_id": media.id,
        "video_id": video_id,
    }


# ============================================
# API Endpoints
# ============================================


@router.post("/youtube/analyze", response_model=YouTubeAnalyzeResponse)
async def analyze_youtube_url_endpoint(
    data: YouTubeAnalyzeRequest,
    user: User = Depends(require_auth),
):
    """
    Analyze a YouTube URL and return video metadata.
    Uses yt-dlp to fetch real video information.
    Also searches for matching content in IMDb/TMDB.
    """
    from scrapers.scraper_tasks import meta_fetcher

    video_id = extract_youtube_video_id(data.youtube_url)

    if not video_id:
        return YouTubeAnalyzeResponse(
            status="error",
            error="Invalid YouTube URL. Please provide a valid YouTube video URL.",
        )

    try:
        # Fetch real metadata using yt-dlp via shared module
        video_info = await analyze_youtube_video(video_id)

        # Build response with actual video metadata
        response = YouTubeAnalyzeResponse(
            status="success",
            video_id=video_id,
            title=video_info.title,
            channel_name=video_info.channel,
            channel_id=video_info.channel_id,
            thumbnail=video_info.thumbnail or f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            duration_seconds=video_info.duration,
            is_live=video_info.is_live,
        )

        # Search for matching content based on video title
        matches = []
        if video_info.title:
            try:
                matches = await meta_fetcher.search_multiple_results(
                    title=video_info.title,
                    media_type=data.meta_type,
                )
            except Exception as e:
                logger.warning(f"Failed to search for matches: {e}")

        response.matches = matches
        return response

    except Exception as e:
        logger.exception(f"Failed to analyze YouTube URL: {e}")
        return YouTubeAnalyzeResponse(
            status="error",
            error=f"Failed to analyze YouTube URL: {str(e)}",
        )


@router.post("/youtube", response_model=YouTubeImportResponse)
async def import_youtube_video(
    youtube_url: str = Form(...),
    meta_type: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    languages: str = Form(None),
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import a YouTube video as a stream.
    For active users, imports are auto-approved and processed immediately.

    Set is_anonymous=True to contribute anonymously.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously

    video_id = extract_youtube_video_id(youtube_url)

    if not video_id:
        return YouTubeImportResponse(
            status="error",
            message="Invalid YouTube URL. Please provide a valid YouTube video URL.",
        )

    try:
        # Check if YouTube video already exists
        if not force_import:
            existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
            if existing.first():
                return YouTubeImportResponse(
                    status="warning",
                    message=f"YouTube video {video_id} already exists in the database.",
                )

        # Build contribution data
        contribution_data = {
            "video_id": video_id,
            "youtube_url": youtube_url,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title or f"YouTube Video ({video_id})",
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "is_anonymous": resolved_is_anonymous,
            "is_live": False,  # Could be detected from YouTube API
        }

        # Auto-approve for active users
        should_auto_approve = user.is_active
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=user.id,
            contribution_type="youtube",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes="Auto-approved: Active user YouTube import" if should_auto_approve else None,
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_youtube_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process YouTube import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return YouTubeImportResponse(
                status="success",
                message="YouTube video imported successfully!",
                import_id=str(contribution.id),
                details={
                    "video_id": video_id,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return YouTubeImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=str(contribution.id),
                details={
                    "video_id": video_id,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return YouTubeImportResponse(
                status="success",
                message="YouTube video submitted for review. Thank you for your contribution!",
                import_id=str(contribution.id),
                details={
                    "video_id": video_id,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import YouTube video: {e}")
        return YouTubeImportResponse(
            status="error",
            message=f"Failed to import YouTube video: {str(e)}",
        )
