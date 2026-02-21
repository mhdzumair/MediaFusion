"""
AceStream Import API endpoints for importing AceStream content.
Supports both content_id and info_hash identifiers for MediaFlow proxy integration.
"""

import logging
import re
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, Form
from pydantic import BaseModel, Field, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.anonymous_utils import normalize_anonymous_display_name, resolve_uploader_identity
from api.routers.user.auth import require_auth
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from db.crud.media import get_media_by_external_id, get_media_by_title_year
from db.crud.providers import get_or_create_provider
from db.crud.reference import get_or_create_language
from db.crud.streams import (
    create_acestream_stream,
    get_acestream_by_content_id,
    get_acestream_by_info_hash,
)
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType, UserRole
from db.models import Contribution, Media, User
from db.models.providers import MediaImage
from db.models.streams import (
    StreamLanguageLink,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# AceStream Validation
# ============================================

# AceStream content_id and info_hash are both 40-character hex strings
HEX_40_PATTERN = re.compile(r"^[a-fA-F0-9]{40}$")

# AceStream URL pattern: acestream://CONTENT_ID
ACESTREAM_URL_PATTERN = re.compile(r"^acestream://([a-fA-F0-9]{40})$")


def validate_hex_40(value: str | None) -> bool:
    """Validate that a string is a 40-character hex string."""
    if not value:
        return False
    return bool(HEX_40_PATTERN.match(value))


def extract_acestream_id(url_or_id: str) -> str | None:
    """Extract AceStream content ID from URL or validate raw ID."""
    # Check if it's an acestream:// URL
    match = ACESTREAM_URL_PATTERN.match(url_or_id)
    if match:
        return match.group(1).lower()

    # Check if it's already a valid hex ID
    if HEX_40_PATTERN.match(url_or_id):
        return url_or_id.lower()

    return None


# ============================================
# Pydantic Schemas
# ============================================


class AceStreamAnalyzeRequest(BaseModel):
    """Request to analyze AceStream content."""

    content_id: str | None = None
    info_hash: str | None = None
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")

    @field_validator("content_id", "info_hash", mode="before")
    @classmethod
    def normalize_hex(cls, v):
        if v:
            # Try to extract from acestream:// URL
            extracted = extract_acestream_id(v)
            return extracted or v.lower().strip()
        return v


class AceStreamAnalyzeResponse(BaseModel):
    """Response from AceStream analysis."""

    status: str
    content_id: str | None = None
    info_hash: str | None = None
    content_id_valid: bool = False
    info_hash_valid: bool = False
    already_exists: bool = False
    error: str | None = None


class AceStreamImportRequest(BaseModel):
    """Request to import AceStream content."""

    content_id: str | None = None
    info_hash: str | None = None
    meta_type: str = Field(..., pattern="^(movie|series|sports|tv)$")
    title: str = Field(..., min_length=1)  # Title is required
    meta_id: str | None = None  # Optional external ID (e.g. IMDb tt1234567)
    languages: str | None = None  # Comma-separated
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    poster: str | None = None  # Poster image URL
    background: str | None = None  # Background/backdrop image URL
    logo: str | None = None  # Logo image URL
    is_anonymous: bool | None = None  # None means use user's preference
    anonymous_display_name: str | None = None
    force_import: bool = False


class AceStreamImportResponse(BaseModel):
    """Response from AceStream import."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


# ============================================
# AceStream Import Processing
# ============================================


async def process_acestream_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User | None,
) -> dict:
    """
    Process an AceStream import - creates the actual AceStreamStream record in the database.

    Media lookup priority:
    1. By meta_id (external ID like IMDb tt1234567) if provided
    2. By title + media type (reuse existing media with same title)
    3. Fetch from external provider if meta_id looks like an IMDb/TMDB ID
    4. Create new media entry as fallback

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """
    content_id = contribution_data.get("content_id")
    info_hash = contribution_data.get("info_hash")
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "AceStream Content")

    is_anonymous = contribution_data.get("is_anonymous", False)
    anonymous_display_name = contribution_data.get("anonymous_display_name")

    if not content_id and not info_hash:
        raise ValueError("At least one of content_id or info_hash is required")

    # Check if AceStream already exists
    if content_id:
        existing = await get_acestream_by_content_id(session, content_id)
        if existing:
            return {"status": "exists", "message": "AceStream content_id already exists in database"}

    if info_hash:
        existing = await get_acestream_by_info_hash(session, info_hash)
        if existing:
            return {"status": "exists", "message": "AceStream info_hash already exists in database"}

    # Map meta_type string to MediaType enum
    media_type_map = {
        "movie": MediaType.MOVIE,
        "series": MediaType.SERIES,
        "tv": MediaType.TV,
        "sports": MediaType.EVENTS,
    }
    media_type_enum = media_type_map.get(meta_type, MediaType.MOVIE)

    # Get or create media metadata
    # Priority: 1) by meta_id, 2) by title match, 3) fetch external, 4) create new
    media = None
    is_new_media = False

    # 1. Try lookup by external ID (e.g. IMDb tt1234567)
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    # 2. Try lookup by title + type (reuse existing media)
    if not media:
        media = await get_media_by_title_year(session, title, None, media_type_enum)

    # 3. Try to fetch from external provider if meta_id is a recognized format
    if not media and meta_id:
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id,
                meta_type,
                fallback_title=title,
            )
            if media:
                is_new_media = True
        except Exception as e:
            logger.warning(f"Failed to fetch media from external provider for {meta_id}: {e}")

    # 4. Create new media entry as fallback
    if not media:
        media = Media(
            title=title,
            type=media_type_enum,
            is_user_created=True,
            created_by_user_id=user.id if user else None,
        )
        session.add(media)
        await session.flush()
        is_new_media = True

    # Add images for new media entries (or entries without images)
    poster_url = contribution_data.get("poster")
    background_url = contribution_data.get("background")
    logo_url = contribution_data.get("logo")

    if is_new_media and (poster_url or background_url or logo_url):
        mediafusion_provider = await get_or_create_provider(session, "mediafusion")
        if poster_url:
            session.add(
                MediaImage(
                    media_id=media.id,
                    provider_id=mediafusion_provider.id,
                    image_type="poster",
                    url=poster_url,
                    is_primary=True,
                )
            )
        if background_url:
            session.add(
                MediaImage(
                    media_id=media.id,
                    provider_id=mediafusion_provider.id,
                    image_type="background",
                    url=background_url,
                    is_primary=True,
                )
            )
        if logo_url:
            session.add(
                MediaImage(
                    media_id=media.id,
                    provider_id=mediafusion_provider.id,
                    image_type="logo",
                    url=logo_url,
                    is_primary=True,
                )
            )

    uploader_name, uploader_user_id = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)

    # Create AceStream stream
    acestream = await create_acestream_stream(
        session,
        content_id=content_id,
        info_hash=info_hash,
        name=title,
        media_id=media.id,
        source="acestream",
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
                lang_link = StreamLanguageLink(stream_id=acestream.stream_id, language_id=lang.id)
                session.add(lang_link)
            except Exception as e:
                logger.warning(f"Failed to add language {lang_name}: {e}")

    # Update media stream count
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = datetime.now(pytz.UTC)

    await session.flush()

    logger.info(
        f"Successfully imported AceStream content_id={content_id}, info_hash={info_hash} "
        f"for media_id={media.id} (title={title}, new_media={is_new_media})"
    )

    return {
        "status": "success",
        "stream_id": acestream.stream_id,
        "media_id": media.id,
        "content_id": content_id,
        "info_hash": info_hash,
    }


# ============================================
# API Endpoints
# ============================================


@router.post("/acestream/analyze", response_model=AceStreamAnalyzeResponse)
async def analyze_acestream(
    data: AceStreamAnalyzeRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Analyze AceStream content_id and/or info_hash.
    Validates the format and checks if it already exists in the database.
    """
    if not data.content_id and not data.info_hash:
        return AceStreamAnalyzeResponse(
            status="error",
            error="At least one of content_id or info_hash is required.",
        )

    try:
        content_id_valid = validate_hex_40(data.content_id) if data.content_id else False
        info_hash_valid = validate_hex_40(data.info_hash) if data.info_hash else False

        if data.content_id and not content_id_valid:
            return AceStreamAnalyzeResponse(
                status="error",
                content_id=data.content_id,
                content_id_valid=False,
                error="Invalid content_id format. Must be a 40-character hexadecimal string.",
            )

        if data.info_hash and not info_hash_valid:
            return AceStreamAnalyzeResponse(
                status="error",
                info_hash=data.info_hash,
                info_hash_valid=False,
                error="Invalid info_hash format. Must be a 40-character hexadecimal string.",
            )

        # Check if already exists
        already_exists = False
        if data.content_id and content_id_valid:
            existing = await get_acestream_by_content_id(session, data.content_id)
            if existing:
                already_exists = True

        if not already_exists and data.info_hash and info_hash_valid:
            existing = await get_acestream_by_info_hash(session, data.info_hash)
            if existing:
                already_exists = True

        return AceStreamAnalyzeResponse(
            status="success",
            content_id=data.content_id,
            info_hash=data.info_hash,
            content_id_valid=content_id_valid,
            info_hash_valid=info_hash_valid,
            already_exists=already_exists,
        )

    except Exception as e:
        logger.exception(f"Failed to analyze AceStream: {e}")
        return AceStreamAnalyzeResponse(
            status="error",
            error=f"Failed to analyze AceStream: {str(e)}",
        )


@router.post("/acestream", response_model=AceStreamImportResponse)
async def import_acestream(
    content_id: str = Form(None),
    info_hash: str = Form(None),
    meta_type: str = Form(...),
    title: str = Form(...),  # Title is required
    meta_id: str = Form(None),  # Optional external ID (e.g. IMDb tt1234567)
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    poster: str = Form(None),  # Poster image URL
    background: str = Form(None),  # Background/backdrop image URL
    logo: str = Form(None),  # Logo image URL
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    anonymous_display_name: str | None = Form(None),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import AceStream content as a stream.

    **Required:** title and at least one of content_id or info_hash.
    Both content_id and info_hash can be provided for maximum compatibility with MediaFlow proxy.

    If a media entry with the same title and type already exists, the stream will be
    linked to the existing media instead of creating a new one. You can also provide
    a meta_id (e.g. IMDb tt1234567) to link to a specific known media entry.

    Optionally provide poster, background, and logo image URLs for the media entry.

    MediaFlow proxy URLs:
    - Using content_id: /proxy/acestream/stream?id={content_id}
    - Using info_hash: /proxy/acestream/stream?infohash={info_hash}

    For active users, imports are auto-approved and processed immediately.
    Set is_anonymous=True to contribute anonymously.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)
    # Normalize and validate identifiers
    normalized_content_id = extract_acestream_id(content_id) if content_id else None
    normalized_info_hash = info_hash.lower().strip() if info_hash else None

    if not normalized_content_id and not normalized_info_hash:
        return AceStreamImportResponse(
            status="error",
            message="At least one of content_id or info_hash is required.",
        )

    # Validate formats
    if normalized_content_id and not validate_hex_40(normalized_content_id):
        return AceStreamImportResponse(
            status="error",
            message="Invalid content_id format. Must be a 40-character hexadecimal string or acestream:// URL.",
        )

    if normalized_info_hash and not validate_hex_40(normalized_info_hash):
        return AceStreamImportResponse(
            status="error",
            message="Invalid info_hash format. Must be a 40-character hexadecimal string.",
        )

    try:
        # Check if already exists (unless force_import)
        if not force_import:
            if normalized_content_id:
                existing = await get_acestream_by_content_id(session, normalized_content_id)
                if existing:
                    return AceStreamImportResponse(
                        status="warning",
                        message=f"AceStream with content_id {normalized_content_id[:16]}... already exists.",
                    )

            if normalized_info_hash:
                existing = await get_acestream_by_info_hash(session, normalized_info_hash)
                if existing:
                    return AceStreamImportResponse(
                        status="warning",
                        message=f"AceStream with info_hash {normalized_info_hash[:16]}... already exists.",
                    )

        # Build contribution data
        contribution_data = {
            "content_id": normalized_content_id,
            "info_hash": normalized_info_hash,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title,
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "resolution": resolution,
            "quality": quality,
            "codec": codec,
            "poster": poster,
            "background": background,
            "logo": logo,
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
        }

        # Anonymous contributions are always reviewed manually.
        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=None if resolved_is_anonymous else user.id,
            contribution_type="acestream",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes=(
                "Auto-approved: Privileged reviewer"
                if is_privileged_reviewer
                else ("Auto-approved: Active user AceStream import" if should_auto_approve else None)
            ),
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_acestream_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process AceStream import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return AceStreamImportResponse(
                status="success",
                message="AceStream content imported successfully!",
                import_id=str(contribution.id),
                details={
                    "content_id": normalized_content_id,
                    "info_hash": normalized_info_hash,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve and import_result and import_result.get("status") == "exists":
            return AceStreamImportResponse(
                status="warning",
                message="AceStream content already exists in the database.",
                import_id=str(contribution.id),
                details={
                    "content_id": normalized_content_id,
                    "info_hash": normalized_info_hash,
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return AceStreamImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=str(contribution.id),
                details={
                    "content_id": normalized_content_id,
                    "info_hash": normalized_info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return AceStreamImportResponse(
                status="success",
                message="AceStream content submitted for review. Thank you for your contribution!",
                import_id=str(contribution.id),
                details={
                    "content_id": normalized_content_id,
                    "info_hash": normalized_info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import AceStream: {e}")
        return AceStreamImportResponse(
            status="error",
            message=f"Failed to import AceStream: {str(e)}",
        )
