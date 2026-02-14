"""
HTTP Import API endpoints for importing direct HTTP URLs as streams.
Supports MediaFlow extractor URLs, custom headers, and DRM-protected MPD streams.
"""

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import pytz
from fastapi import APIRouter, Depends, Form, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from db.crud.media import get_media_by_external_id
from db.crud.reference import get_or_create_language
from db.crud.streams import create_http_stream, get_http_stream_by_url_for_media
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType
from db.models import Contribution, Media, User
from db.models.streams import (
    HTTPStream,
    StreamLanguageLink,
    StreamType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# MediaFlow Extractor Constants
# ============================================

MEDIAFLOW_EXTRACTORS = [
    "doodstream",
    "filelions",
    "filemoon",
    "f16px",
    "mixdrop",
    "uqload",
    "streamtape",
    "streamwish",
    "supervideo",
    "vixcloud",
    "okru",
    "maxstream",
    "lulustream",
    "fastream",
    "turbovidplay",
    "vidmoly",
    "vidoza",
    "voe",
    "sportsonline",
]


def detect_stream_format(url: str) -> str | None:
    """Detect stream format from URL."""
    url_lower = url.lower()

    if ".m3u8" in url_lower or "manifest" in url_lower and "hls" in url_lower:
        return "hls"
    elif ".mpd" in url_lower or "manifest" in url_lower and "dash" in url_lower:
        return "dash"
    elif ".mp4" in url_lower:
        return "mp4"
    elif ".mkv" in url_lower:
        return "mkv"
    elif ".webm" in url_lower:
        return "webm"
    elif ".avi" in url_lower:
        return "avi"
    elif ".flv" in url_lower:
        return "flv"

    return None


def detect_extractor_from_url(url: str) -> str | None:
    """Try to detect MediaFlow extractor from URL domain."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        for extractor in MEDIAFLOW_EXTRACTORS:
            if extractor in domain:
                return extractor
    except Exception:
        pass

    return None


def validate_url(url: str) -> bool:
    """Validate that the URL is a valid HTTP/HTTPS URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


# ============================================
# Pydantic Schemas
# ============================================


class HTTPAnalyzeRequest(BaseModel):
    """Request to analyze an HTTP URL."""

    url: str
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")


class HTTPAnalyzeResponse(BaseModel):
    """Response from HTTP URL analysis."""

    status: str
    url: str | None = None
    detected_format: str | None = None
    detected_extractor: str | None = None
    is_valid: bool = False
    error: str | None = None


class HTTPImportRequest(BaseModel):
    """Request to import an HTTP stream."""

    url: str
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")
    meta_id: str | None = None
    title: str | None = None

    # MediaFlow extractor
    extractor_name: str | None = None

    # Headers (as JSON strings)
    request_headers: dict[str, str] | None = None
    response_headers: dict[str, str] | None = None

    # DRM for MPD streams
    drm_key_id: str | None = None
    drm_key: str | None = None

    # Quality info
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    languages: str | None = None  # Comma-separated

    is_anonymous: bool | None = None  # None means use user's preference
    force_import: bool = False


class HTTPImportResponse(BaseModel):
    """Response from HTTP import."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


# ============================================
# HTTP Import Processing
# ============================================


async def process_http_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User,
) -> dict:
    """
    Process an HTTP import - creates the actual HTTPStream record in the database.

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """
    url = contribution_data.get("url", "")
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "HTTP Stream")

    # Anonymous contribution handling
    is_anonymous = contribution_data.get("is_anonymous", False)

    if not url:
        raise ValueError("Missing url in contribution data")

    # Get or create media metadata
    media = None
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    if not media:
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id or f"http_{hash(url) % 100000}",
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

    # Check if this URL already exists for this media
    existing = await get_http_stream_by_url_for_media(session, url, media.id)
    if existing:
        return {"status": "exists", "message": "HTTP stream already exists for this media"}

    # Determine uploader name and user_id based on anonymous preference
    if is_anonymous:
        uploader_name = "Anonymous"
        uploader_user_id = None
    else:
        uploader_name = user.username or user.email or f"User #{user.id}"
        uploader_user_id = user.id

    # Build behavior_hints with headers
    behavior_hints = None
    request_headers = contribution_data.get("request_headers")
    response_headers = contribution_data.get("response_headers")

    if request_headers or response_headers:
        behavior_hints = {"proxyHeaders": {}}
        if request_headers:
            behavior_hints["proxyHeaders"]["request"] = request_headers
        if response_headers:
            behavior_hints["proxyHeaders"]["response"] = response_headers

    # Create HTTP stream
    http_stream = await create_http_stream(
        session,
        url=url,
        name=title,
        media_id=media.id,
        source=contribution_data.get("extractor_name") or "direct",
        format=contribution_data.get("format"),
        behavior_hints=behavior_hints,
        drm_key_id=contribution_data.get("drm_key_id"),
        drm_key=contribution_data.get("drm_key"),
        extractor_name=contribution_data.get("extractor_name"),
        uploader=uploader_name,
        uploader_user_id=uploader_user_id,
        resolution=contribution_data.get("resolution"),
        quality=contribution_data.get("quality"),
        codec=contribution_data.get("codec"),
    )

    # Add languages if provided
    languages = contribution_data.get("languages", [])
    for lang_name in languages:
        if lang_name:
            try:
                lang = await get_or_create_language(session, lang_name)
                lang_link = StreamLanguageLink(stream_id=http_stream.stream_id, language_id=lang.id)
                session.add(lang_link)
            except Exception as e:
                logger.warning(f"Failed to add language {lang_name}: {e}")

    # Update media stream count
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = datetime.now(pytz.UTC)

    await session.flush()

    logger.info(f"Successfully imported HTTP stream for media_id={media.id}")

    return {
        "status": "success",
        "stream_id": http_stream.stream_id,
        "media_id": media.id,
        "url": url,
    }


# ============================================
# API Endpoints
# ============================================


@router.get("/http/extractors")
async def get_mediaflow_extractors(
    user: User = Depends(require_auth),
):
    """
    Get list of supported MediaFlow extractors.
    """
    return {
        "extractors": MEDIAFLOW_EXTRACTORS,
    }


@router.post("/http/analyze", response_model=HTTPAnalyzeResponse)
async def analyze_http_url(
    data: HTTPAnalyzeRequest,
    user: User = Depends(require_auth),
):
    """
    Analyze an HTTP URL and return metadata.
    Detects stream format and potential MediaFlow extractor.
    """
    if not validate_url(data.url):
        return HTTPAnalyzeResponse(
            status="error",
            is_valid=False,
            error="Invalid URL. Please provide a valid HTTP/HTTPS URL.",
        )

    try:
        detected_format = detect_stream_format(data.url)
        detected_extractor = detect_extractor_from_url(data.url)

        return HTTPAnalyzeResponse(
            status="success",
            url=data.url,
            detected_format=detected_format,
            detected_extractor=detected_extractor,
            is_valid=True,
        )

    except Exception as e:
        logger.exception(f"Failed to analyze HTTP URL: {e}")
        return HTTPAnalyzeResponse(
            status="error",
            is_valid=False,
            error=f"Failed to analyze URL: {str(e)}",
        )


@router.post("/http", response_model=HTTPImportResponse)
async def import_http_stream(
    url: str = Form(...),
    meta_type: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    extractor_name: str = Form(None),
    request_headers: str = Form(None),  # JSON string
    response_headers: str = Form(None),  # JSON string
    drm_key_id: str = Form(None),
    drm_key: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    languages: str = Form(None),
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import an HTTP URL as a stream.

    Supports:
    - Direct HTTP URLs (mp4, mkv, etc.)
    - HLS streams (.m3u8)
    - DASH streams (.mpd)
    - MediaFlow extractor URLs
    - Custom request/response headers
    - DRM-protected MPD streams (with key_id and key)

    For active users, imports are auto-approved and processed immediately.
    Set is_anonymous=True to contribute anonymously.
    If not provided, uses your account's default contribution preference.
    """
    import json

    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously

    if not validate_url(url):
        return HTTPImportResponse(
            status="error",
            message="Invalid URL. Please provide a valid HTTP/HTTPS URL.",
        )

    try:
        # Parse headers if provided
        parsed_request_headers = None
        parsed_response_headers = None

        if request_headers:
            try:
                parsed_request_headers = json.loads(request_headers)
            except json.JSONDecodeError:
                return HTTPImportResponse(
                    status="error",
                    message="Invalid request_headers format. Must be valid JSON.",
                )

        if response_headers:
            try:
                parsed_response_headers = json.loads(response_headers)
            except json.JSONDecodeError:
                return HTTPImportResponse(
                    status="error",
                    message="Invalid response_headers format. Must be valid JSON.",
                )

        # Detect format
        detected_format = detect_stream_format(url)

        # Auto-detect extractor if not provided
        if not extractor_name:
            extractor_name = detect_extractor_from_url(url)

        # Build contribution data
        contribution_data = {
            "url": url,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title or f"HTTP Stream",
            "extractor_name": extractor_name,
            "request_headers": parsed_request_headers,
            "response_headers": parsed_response_headers,
            "drm_key_id": drm_key_id,
            "drm_key": drm_key,
            "format": detected_format,
            "resolution": resolution,
            "quality": quality,
            "codec": codec,
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "is_anonymous": resolved_is_anonymous,
        }

        # Auto-approve for active users
        should_auto_approve = user.is_active
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=user.id,
            contribution_type="http",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes="Auto-approved: Active user HTTP import" if should_auto_approve else None,
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_http_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process HTTP import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return HTTPImportResponse(
                status="success",
                message="HTTP stream imported successfully!",
                import_id=str(contribution.id),
                details={
                    "url": url,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "extractor_name": extractor_name,
                    "auto_approved": True,
                },
            )
        elif should_auto_approve and import_result and import_result.get("status") == "exists":
            return HTTPImportResponse(
                status="warning",
                message="HTTP stream already exists for this media.",
                import_id=str(contribution.id),
                details={
                    "url": url,
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return HTTPImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=str(contribution.id),
                details={
                    "url": url,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return HTTPImportResponse(
                status="success",
                message="HTTP stream submitted for review. Thank you for your contribution!",
                import_id=str(contribution.id),
                details={
                    "url": url,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import HTTP stream: {e}")
        return HTTPImportResponse(
            status="error",
            message=f"Failed to import HTTP stream: {str(e)}",
        )
