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

from api.routers.content.anonymous_utils import normalize_anonymous_display_name, resolve_uploader_identity
from api.routers.content.contributions import award_import_approval_points
from api.routers.content.import_title_validation import resolve_and_validate_import_title
from api.routers.content.upload_guard import enforce_upload_permissions
from api.routers.user.auth import require_auth
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from db.crud.media import get_media_by_external_id
from db.crud.reference import get_or_create_language
from db.crud.scraper_helpers import get_or_create_metadata
from db.crud.streams import create_youtube_stream
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType, UserRole
from db.models import Contribution, Stream, User
from db.models.streams import (
    StreamLanguageLink,
    YouTubeStream,
)
from scrapers.scraper_tasks import meta_fetcher
from utils.notification_registry import send_pending_contribution_notification
from utils.youtube import analyze_youtube_video

logger = logging.getLogger(__name__)
SPORTS_SERIES_CATEGORIES = {"formula_racing", "motogp_racing"}

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


def _resolve_sports_media_type(sports_category: str | None) -> MediaType:
    if sports_category in SPORTS_SERIES_CATEGORIES:
        return MediaType.SERIES
    return MediaType.MOVIE


def _resolve_fetch_media_type(meta_type: str, sports_category: str | None = None) -> str:
    if meta_type == "series":
        return "series"
    if meta_type == "sports":
        return "series" if _resolve_sports_media_type(sports_category) == MediaType.SERIES else "movie"
    return "movie"


async def _notify_pending_contribution(
    contribution: Contribution,
    user: User,
    is_anonymous: bool,
    anonymous_display_name: str | None,
) -> None:
    if contribution.status != ContributionStatus.PENDING:
        return

    uploader_name, _ = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)
    await send_pending_contribution_notification(
        {
            "contribution_id": contribution.id,
            "contribution_type": contribution.contribution_type,
            "target_id": contribution.target_id,
            "uploader_name": uploader_name,
            "data": contribution.data,
        }
    )


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
    resolution: str | None = None
    geo_restriction_type: str | None = None  # allowed | blocked
    geo_restriction_countries: list[str] | None = None
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
    anonymous_display_name: str | None = None
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
    user: User | None,
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

    is_anonymous = contribution_data.get("is_anonymous", False)
    anonymous_display_name = contribution_data.get("anonymous_display_name")
    is_public = bool(contribution_data.get("is_public", True))

    if not video_id:
        raise ValueError("Missing video_id in contribution data")

    # Check if YouTube video already exists
    existing = await session.exec(select(YouTubeStream).where(YouTubeStream.video_id == video_id))
    existing_stream = existing.first()
    if existing_stream:
        if is_public:
            stream = await session.get(Stream, existing_stream.stream_id)
            if stream and not stream.is_public:
                stream.is_public = True
                await session.flush()
                return {
                    "status": "success",
                    "stream_id": stream.id,
                    "message": "Existing YouTube stream published",
                }
        return {"status": "exists", "message": "YouTube video already exists in database"}

    # Get or create media metadata
    media = None
    sports_category = contribution_data.get("sports_category")
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    if not media:
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id or f"yt_{video_id}",
                _resolve_fetch_media_type(meta_type, sports_category),
                fallback_title=title,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch/create media for {meta_id}: {e}")
            media = await get_or_create_metadata(
                session,
                {
                    "id": meta_id or f"yt_{video_id}",
                    "title": title,
                    "year": contribution_data.get("year"),
                },
                _resolve_fetch_media_type(meta_type, sports_category),
            )

    uploader_name, uploader_user_id = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)

    # Create YouTube stream with quality attributes
    stream_kwargs: dict[str, Any] = {
        "uploader": uploader_name,
        "uploader_user_id": uploader_user_id,
        "is_public": is_public,
    }
    for attr in ("resolution", "quality", "codec"):
        if contribution_data.get(attr):
            stream_kwargs[attr] = contribution_data[attr]
    geo_restriction_type = contribution_data.get("geo_restriction_type")
    geo_restriction_countries = contribution_data.get("geo_restriction_countries", [])
    if geo_restriction_type in {"allowed", "blocked"}:
        stream_kwargs["geo_restriction_type"] = geo_restriction_type
        stream_kwargs["geo_restriction_countries"] = geo_restriction_countries

    yt_stream = await create_youtube_stream(
        session,
        video_id=video_id,
        name=title,
        media_id=media.id,
        source="youtube",
        is_live=contribution_data.get("is_live", False),
        **stream_kwargs,
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
            resolution=video_info.resolution,
            geo_restriction_type=video_info.geo_restriction_type,
            geo_restriction_countries=video_info.geo_restriction_countries,
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
    poster: str = Form(None),
    background: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    languages: str = Form(None),
    geo_restriction_type: str = Form(None),
    geo_restriction_countries: str = Form(None),  # Comma-separated country names/codes
    catalogs: str = Form(None),
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),
    anonymous_display_name: str | None = Form(None),
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
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)
    await enforce_upload_permissions(user, session)

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

        normalized_geo_restriction_type = (geo_restriction_type or "").strip().lower() or None
        if normalized_geo_restriction_type not in {"allowed", "blocked"}:
            normalized_geo_restriction_type = None
        parsed_geo_restriction_countries = (
            [country.strip() for country in geo_restriction_countries.split(",") if country.strip()]
            if geo_restriction_countries
            else []
        )

        resolved_title, title_validation_error = resolve_and_validate_import_title(
            title,
            f"YouTube Video ({video_id})",
        )
        if title_validation_error:
            return YouTubeImportResponse(
                status="error",
                message=title_validation_error,
            )

        # Build contribution data
        contribution_data = {
            "video_id": video_id,
            "youtube_url": youtube_url,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": resolved_title,
            "poster": poster,
            "background": background,
            "resolution": resolution,
            "quality": quality,
            "codec": codec,
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "geo_restriction_type": normalized_geo_restriction_type,
            "geo_restriction_countries": parsed_geo_restriction_countries,
            "catalogs": [c.strip() for c in catalogs.split(",") if c.strip()] if catalogs else [],
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
            "is_live": False,
        }

        # Anonymous contributions are always reviewed manually.
        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING
        contribution_data["is_public"] = should_auto_approve

        contribution = Contribution(
            user_id=None if resolved_is_anonymous else user.id,
            contribution_type="youtube",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            admin_review_requested=False,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes=(
                "Auto-approved: Privileged reviewer"
                if is_privileged_reviewer
                else ("Auto-approved: Active user YouTube import" if should_auto_approve else None)
            ),
        )

        session.add(contribution)
        await session.flush()
        if should_auto_approve:
            await award_import_approval_points(
                session,
                contribution.user_id,
                contribution.contribution_type,
                logger,
            )

        import_result = None
        try:
            import_result = await process_youtube_import(session, contribution_data, user)
        except Exception as e:
            logger.error(f"Failed to process YouTube import: {e}")
            contribution.review_notes = (
                f"Auto-approved but import failed: {str(e)}"
                if should_auto_approve
                else f"Pending private stream creation failed: {str(e)}"
            )

        await session.commit()
        await session.refresh(contribution)
        await _notify_pending_contribution(
            contribution,
            user,
            resolved_is_anonymous,
            normalized_anonymous_display_name,
        )

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
                message="YouTube video submitted for review and saved privately for your account.",
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
