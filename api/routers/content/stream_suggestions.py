"""
Stream Suggestions API endpoints for reporting broken streams and quality corrections.
Includes auto-approval for trusted users and full field editing support.
"""

import json
import logging
from datetime import datetime
from typing import Literal, TypedDict

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import aliased, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from api.routers.content.torrent_import import fetch_and_create_media_from_external
from db.database import get_async_session
from db.enums import MediaType, UserRole
from db.crud.media import get_media_by_external_id, parse_external_id
from db.models import (
    AudioChannel,
    AudioFormat,
    ContributionSettings,
    FileMediaLink,
    HDRFormat,
    Media,
    MediaImage,
    Stream,
    StreamFile,
    StreamMediaLink,
    StreamSuggestion,
    User,
)
from db.models.links import StreamLanguageLink
from db.crud.reference import get_or_create_language
from utils.notification_registry import send_pending_stream_suggestion_notification

logger = logging.getLogger(__name__)

# Suggestion status constants
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_APPROVED = "auto_approved"


router = APIRouter(prefix="/api/v1", tags=["Stream Suggestions"])


# ============================================
# Constants
# ============================================

# All editable stream fields (updated for v5 schema)
STREAM_EDITABLE_FIELDS = [
    "name",  # Display name (was torrent_name, now on Stream base)
    "resolution",  # 4K, 1080p, 720p, etc.
    "codec",  # HEVC, AVC, etc.
    "quality",  # WEB-DL, BluRay, etc.
    "bit_depth",  # 8-bit, 10-bit, 12-bit
    "audio_formats",  # Atmos, DTS, etc. (normalized table)
    "channels",  # 5.1, 7.1, etc. (normalized table)
    "hdr_formats",  # HDR10, Dolby Vision, etc. (normalized table)
    # "source",            # Source name shouldn't be editable
    "languages",  # Language corrections (requires special handling)
]

# Suggestion types
SUGGESTION_TYPES = [
    "report_broken",  # Stream no longer works
    "field_correction",  # Correct a specific field
    "language_add",  # Add a missing language
    "language_remove",  # Remove incorrect language
    "mark_duplicate",  # Mark as duplicate of another stream
    "relink_media",  # Re-link stream to different media (replaces current link)
    "add_media_link",  # Add additional media link (for collections/multi-content)
    "other",  # Other issues
]

# Quality/resolution options
RESOLUTION_OPTIONS = ["4K", "2160p", "1080p", "720p", "480p", "SD"]
QUALITY_OPTIONS = [
    "WEB-DL",
    "WEBRip",
    "BluRay",
    "BDRip",
    "HDRip",
    "DVDRip",
    "HDTV",
    "CAM",
    "TC",
    "TS",
]
CODEC_OPTIONS = ["HEVC", "H.265", "AVC", "H.264", "VP9", "AV1", "XviD", "DivX"]
AUDIO_OPTIONS = [
    "Atmos",
    "TrueHD",
    "DTS-HD MA",
    "DTS-HD",
    "DTS",
    "DD+",
    "DD5.1",
    "AAC",
    "MP3",
]
HDR_OPTIONS = ["HDR10", "HDR10+", "Dolby Vision", "HLG"]

LANGUAGE_OPTIONS = [
    "English",
    "Tamil",
    "Hindi",
    "Malayalam",
    "Kannada",
    "Telugu",
    "Chinese",
    "Russian",
    "Arabic",
    "Japanese",
    "Korean",
    "Taiwanese",
    "Latino",
    "French",
    "Spanish",
    "Portuguese",
    "Italian",
    "German",
    "Ukrainian",
    "Polish",
    "Czech",
    "Thai",
    "Indonesian",
    "Vietnamese",
    "Dutch",
    "Bengali",
    "Turkish",
    "Greek",
    "Swedish",
    "Romanian",
    "Hungarian",
    "Finnish",
    "Norwegian",
    "Danish",
    "Hebrew",
    "Lithuanian",
    "Punjabi",
    "Marathi",
    "Gujarati",
    "Bhojpuri",
    "Nepali",
    "Urdu",
    "Tagalog",
    "Filipino",
    "Malay",
    "Mongolian",
    "Armenian",
    "Georgian",
]


# ============================================
# Pydantic Schemas
# ============================================


SuggestionTypeLiteral = Literal[
    "report_broken",
    "field_correction",
    "language_add",
    "language_remove",
    "mark_duplicate",
    "relink_media",
    "add_media_link",
    "other",
]

TargetMediaTypeLiteral = Literal["movie", "series", "tv"]

StreamFieldLiteral = Literal[
    "torrent_name",
    "resolution",
    "codec",
    "quality",
    "audio",
    "hdr",
    "source",
    "languages",
    # v5 schema field names
    "name",
    "codec",
    "bit_depth",
    "audio_formats",
    "channels",
    "hdr_formats",
]


class StreamSuggestionCreateRequest(BaseModel):
    """Request to create a stream suggestion"""

    suggestion_type: SuggestionTypeLiteral
    # Allow any string for field_name to support episode_link:file_id:field pattern
    field_name: str | None = Field(
        None,
        description="Field to edit (required for field_correction). Can also be episode_link:<file_id>:<field> for episode link corrections.",
    )
    current_value: str | None = Field(None, description="Current value")
    suggested_value: str | None = Field(None, description="Suggested correction")
    reason: str | None = Field(None, max_length=1000, description="Description of the issue")
    related_stream_id: str | None = Field(None, description="Related stream ID (for duplicates)")
    # Fields for stream re-linking suggestions
    target_media_id: int | None = Field(
        None, description="Target media ID to link stream to (for relink_media/add_media_link)"
    )
    target_external_id: str | None = Field(
        None,
        description="External ID (e.g., tt1234567, tmdb:550) used when target media does not yet exist",
    )
    target_media_type: TargetMediaTypeLiteral | None = Field(
        None,
        description="Media type for target_external_id (movie, series, or tv)",
    )
    target_title: str | None = Field(
        None,
        description="Optional target title hint used when creating media from external ID",
    )
    file_index: int | None = Field(None, description="Specific file index within torrent (for multi-file torrents)")
    season_number: int | None = Field(
        None,
        description="Optional season number for a specific file mapping (requires file_index)",
    )
    episode_number: int | None = Field(
        None,
        description="Optional episode number for a specific file mapping (requires file_index)",
    )
    episode_end: int | None = Field(
        None,
        description="Optional ending episode number for multi-episode files (requires file_index)",
    )


class StreamSuggestionResponse(BaseModel):
    """Response for a stream suggestion"""

    id: str  # UUID string
    user_id: int
    username: str | None = None
    stream_id: int
    stream_name: str | None = None
    media_id: int | None = None  # Internal media ID
    source_media_id: int | None = None
    source_media_type: str | None = None
    source_media_title: str | None = None
    source_media_year: int | None = None
    source_media_poster_url: str | None = None
    target_media_id: int | None = None
    target_media_type: str | None = None
    target_media_title: str | None = None
    target_media_year: int | None = None
    target_media_poster_url: str | None = None
    suggestion_type: str
    field_name: str | None = None
    current_value: str | None = None
    suggested_value: str | None = None
    reason: str | None = None
    status: str
    was_auto_approved: bool = False
    created_at: datetime
    reviewed_by: str | None = None  # User ID as string, or None
    reviewer_name: str | None = None  # Reviewer's username for display
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    # User reputation info
    user_contribution_level: str | None = None
    user_contribution_points: int | None = None


class StreamSuggestionListResponse(BaseModel):
    """List of stream suggestions"""

    suggestions: list[StreamSuggestionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool = False


class StreamSuggestionReviewRequest(BaseModel):
    """Request to review a stream suggestion"""

    action: Literal["approve", "reject"]
    review_notes: str | None = Field(None, max_length=500)


class StreamSuggestionStats(BaseModel):
    """Stats for stream suggestions"""

    total: int
    pending: int
    approved: int
    auto_approved: int
    rejected: int
    # Today's stats (for moderators)
    approved_today: int = 0
    rejected_today: int = 0
    # User-specific stats
    user_pending: int = 0
    user_approved: int = 0
    user_auto_approved: int = 0
    user_rejected: int = 0


class StreamFieldInfo(BaseModel):
    """Information about editable stream fields"""

    field_name: str
    display_name: str
    current_value: str | None
    field_type: str  # text, select, multi_select
    options: list[str] | None = None


class StreamEditableFields(BaseModel):
    """All editable fields for a stream"""

    stream_id: int
    stream_name: str
    fields: list[StreamFieldInfo]


class BrokenReportStatus(BaseModel):
    """Status of broken reports for a stream"""

    stream_id: int
    is_blocked: bool
    report_count: int
    threshold: int
    user_has_reported: bool
    reports_needed: int  # How many more reports needed to block (0 if already blocked)


# ============================================
# Helper Functions
# ============================================


async def get_contribution_settings(session: AsyncSession) -> ContributionSettings:
    """Get or create contribution settings"""
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    settings = result.first()

    if not settings:
        settings = ContributionSettings(id="default")
        session.add(settings)
        await session.commit()
        await session.refresh(settings)

    return settings


async def get_username(session: AsyncSession, user_id: int | str | None) -> str | None:
    """Get username for a user. Accepts int or string user_id."""
    if user_id is None:
        return None
    # Convert string to int if needed
    try:
        uid = int(user_id) if isinstance(user_id, str) else user_id
    except (ValueError, TypeError):
        return None
    query = select(User.username).where(User.id == uid)
    result = await session.exec(query)
    return result.first()


def _normalize_filter_query(value: str | None) -> str | None:
    """Normalize optional query string filters."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_external_id_input(value: str | None) -> str | None:
    """Normalize manual external ID input to supported internal formats."""
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    # Friendly shorthand: plain numeric IDs are treated as TMDB IDs.
    if normalized.isdigit():
        return f"tmdb:{normalized}"

    provider, provider_id = parse_external_id(normalized)
    if not provider or not provider_id:
        return normalized

    if provider == "imdb":
        return provider_id if provider_id.startswith("tt") else f"tt{provider_id}"
    if provider == "mediafusion":
        return f"mf:{provider_id}"
    return f"{provider}:{provider_id}"


async def _resolve_target_media_id(
    session: AsyncSession,
    target_media_id: int | None,
    target_external_id: str | None,
    target_media_type: TargetMediaTypeLiteral | None,
    target_title: str | None,
) -> int | None:
    """Resolve the final target media ID from internal/external identifiers."""
    if target_media_id:
        media_result = await session.exec(select(Media).where(Media.id == target_media_id))
        target_media = media_result.first()
        if target_media:
            return target_media.id
        logger.error("Target media %s not found", target_media_id)
        return None

    normalized_external_id = _normalize_external_id_input(target_external_id)
    if not normalized_external_id:
        return None

    if normalized_external_id.startswith("mf:"):
        try:
            media_id = int(normalized_external_id.split(":", 1)[1])
        except (TypeError, ValueError):
            logger.error("Invalid MediaFusion media ID format: %s", normalized_external_id)
            return None

        media_result = await session.exec(select(Media).where(Media.id == media_id))
        target_media = media_result.first()
        if target_media:
            return target_media.id

        logger.error("Target media %s not found", normalized_external_id)
        return None

    media_type_literal = target_media_type or "movie"
    media_type_enum_map = {
        "movie": MediaType.MOVIE,
        "series": MediaType.SERIES,
        "tv": MediaType.TV,
    }
    media_type_enum = media_type_enum_map.get(media_type_literal, MediaType.MOVIE)

    # Primary lookup with the requested media type.
    existing_media = await get_media_by_external_id(session, normalized_external_id, media_type_enum)
    if existing_media:
        return existing_media.id

    # Fallback lookup without media-type filter so manual type selection mistakes
    # still resolve to the already linked media in DB.
    existing_media_any_type = await get_media_by_external_id(session, normalized_external_id, None)
    if existing_media_any_type:
        logger.info(
            "Resolved external ID %s to media %s with type %s despite requested type %s",
            normalized_external_id,
            existing_media_any_type.id,
            existing_media_any_type.type.value if existing_media_any_type.type else "unknown",
            media_type_literal,
        )
        return existing_media_any_type.id

    created_media = await fetch_and_create_media_from_external(
        session,
        normalized_external_id,
        media_type_literal,
        fallback_title=target_title,
    )
    if created_media:
        return created_media.id

    logger.error("Failed to resolve or create media for external ID %s", normalized_external_id)
    return None


class MediaPreviewContext(TypedDict):
    media_id: int | None
    media_type: str | None
    media_title: str | None
    media_year: int | None
    media_poster_url: str | None


def _empty_media_preview_context() -> MediaPreviewContext:
    return {
        "media_id": None,
        "media_type": None,
        "media_title": None,
        "media_year": None,
        "media_poster_url": None,
    }


async def _get_media_preview_context_map(
    session: AsyncSession,
    media_ids: list[int],
) -> dict[int, MediaPreviewContext]:
    """Build media preview context map for a batch of media IDs."""
    if not media_ids:
        return {}

    unique_media_ids = list(dict.fromkeys(media_ids))
    context_by_media_id: dict[int, MediaPreviewContext] = {
        media_id: _empty_media_preview_context() for media_id in unique_media_ids
    }

    media_query = select(Media).where(Media.id.in_(unique_media_ids))
    media_result = await session.exec(media_query)
    for media in media_result.all():
        context_by_media_id[media.id].update(
            {
                "media_id": media.id,
                "media_type": media.type.value if media.type else None,
                "media_title": media.title,
                "media_year": media.year,
            }
        )

    images_query = (
        select(MediaImage)
        .where(
            MediaImage.media_id.in_(unique_media_ids),
            MediaImage.image_type == "poster",
        )
        .order_by(
            MediaImage.media_id,
            MediaImage.is_primary.desc(),
            MediaImage.display_order.asc(),
            MediaImage.id.asc(),
        )
    )
    images_result = await session.exec(images_query)
    for image in images_result.all():
        media_context = context_by_media_id.get(image.media_id)
        if media_context and not media_context["media_poster_url"]:
            media_context["media_poster_url"] = image.url

    return context_by_media_id


def _parse_stream_suggestion_type(raw_type: str) -> str:
    """Normalize stored suggestion type values."""
    if ":" in raw_type:
        return raw_type.split(":", 1)[0]
    return raw_type


def _parse_link_suggestion_data(suggestion: StreamSuggestion) -> dict[str, object]:
    """Extract relink/add-link payload from suggested_value JSON."""
    suggestion_type = _parse_stream_suggestion_type(suggestion.suggestion_type)
    if suggestion_type not in {"relink_media", "add_media_link"}:
        return {}
    if not suggestion.suggested_value:
        return {}

    try:
        parsed = json.loads(suggestion.suggested_value)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}
    return parsed


def _resolve_media_type_literal(value: object) -> TargetMediaTypeLiteral | None:
    """Normalize media type value from persisted suggestion payload."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"movie", "series", "tv"}:
        return normalized
    return None


async def _build_source_media_context_map(
    session: AsyncSession,
    stream_ids: list[int],
) -> dict[int, MediaPreviewContext]:
    """Map stream_id -> source media preview context."""
    if not stream_ids:
        return {}

    unique_stream_ids = list(dict.fromkeys(stream_ids))
    stream_links_query = (
        select(StreamMediaLink)
        .where(StreamMediaLink.stream_id.in_(unique_stream_ids))
        .order_by(StreamMediaLink.stream_id, StreamMediaLink.is_primary.desc(), StreamMediaLink.id.asc())
    )
    stream_links_result = await session.exec(stream_links_query)
    stream_links = stream_links_result.all()

    primary_media_by_stream: dict[int, int] = {}
    for link in stream_links:
        if link.stream_id not in primary_media_by_stream:
            primary_media_by_stream[link.stream_id] = link.media_id

    media_preview_map = await _get_media_preview_context_map(session, list(primary_media_by_stream.values()))
    source_context_by_stream: dict[int, MediaPreviewContext] = {}
    for stream_id, media_id in primary_media_by_stream.items():
        source_context_by_stream[stream_id] = media_preview_map.get(media_id, _empty_media_preview_context())

    return source_context_by_stream


async def _build_target_media_context_map(
    session: AsyncSession,
    suggestions: list[StreamSuggestion],
) -> dict[str, MediaPreviewContext]:
    """Map suggestion_id -> target media preview context for relink/add-link suggestions."""
    if not suggestions:
        return {}

    target_media_id_by_suggestion: dict[str, int] = {}
    target_title_hint_by_suggestion: dict[str, str] = {}
    fallback_type_by_suggestion: dict[str, TargetMediaTypeLiteral] = {}

    for suggestion in suggestions:
        link_data = _parse_link_suggestion_data(suggestion)
        if not link_data:
            continue

        target_title = link_data.get("target_title")
        if isinstance(target_title, str) and target_title.strip():
            target_title_hint_by_suggestion[suggestion.id] = target_title.strip()

        media_type_literal = _resolve_media_type_literal(link_data.get("target_media_type"))
        if media_type_literal:
            fallback_type_by_suggestion[suggestion.id] = media_type_literal

        raw_target_media_id = link_data.get("target_media_id")
        if isinstance(raw_target_media_id, int) and raw_target_media_id > 0:
            target_media_id_by_suggestion[suggestion.id] = raw_target_media_id
            continue

        target_external_id = _normalize_external_id_input(link_data.get("target_external_id"))
        if not target_external_id:
            continue

        if target_external_id.startswith("mf:"):
            try:
                parsed_media_id = int(target_external_id.split(":", 1)[1])
            except (TypeError, ValueError):
                continue
            if parsed_media_id > 0:
                target_media_id_by_suggestion[suggestion.id] = parsed_media_id
            continue

        media_type_enum_map = {
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "tv": MediaType.TV,
        }
        media_type_enum = media_type_enum_map.get(media_type_literal)
        existing_media = await get_media_by_external_id(session, target_external_id, media_type_enum)
        if existing_media:
            target_media_id_by_suggestion[suggestion.id] = existing_media.id
            continue

        existing_media_any_type = await get_media_by_external_id(session, target_external_id, None)
        if existing_media_any_type:
            target_media_id_by_suggestion[suggestion.id] = existing_media_any_type.id

    media_preview_map = await _get_media_preview_context_map(session, list(target_media_id_by_suggestion.values()))
    context_by_suggestion: dict[str, MediaPreviewContext] = {}

    for suggestion in suggestions:
        media_id = target_media_id_by_suggestion.get(suggestion.id)
        if media_id:
            context_by_suggestion[suggestion.id] = media_preview_map.get(media_id, _empty_media_preview_context())
            continue

        title_hint = target_title_hint_by_suggestion.get(suggestion.id)
        media_type_hint = fallback_type_by_suggestion.get(suggestion.id)
        if title_hint or media_type_hint:
            fallback_context = _empty_media_preview_context()
            fallback_context["media_title"] = title_hint
            fallback_context["media_type"] = media_type_hint
            context_by_suggestion[suggestion.id] = fallback_context

    return context_by_suggestion


async def should_auto_approve(user: User, session: AsyncSession) -> bool:
    """Check if user has enough reputation for auto-approval.

    Moderators and admins are always auto-approved since they have
    the authority to approve suggestions anyway.
    """
    # Moderators and admins are always auto-approved
    if user.role in (UserRole.MODERATOR, UserRole.ADMIN):
        return True

    settings = await get_contribution_settings(session)

    if not settings.allow_auto_approval:
        return False

    return user.contribution_points >= settings.auto_approval_threshold


def calculate_contribution_level(points: int, settings: ContributionSettings) -> str:
    """Calculate user contribution level based on points"""
    if points >= settings.expert_threshold:
        return "expert"
    elif points >= settings.trusted_threshold:
        return "trusted"
    elif points >= settings.contributor_threshold:
        return "contributor"
    return "new"


async def award_points(
    user: User,
    points: int,
    session: AsyncSession,
) -> None:
    """Award points to a user and update their contribution level"""
    settings = await get_contribution_settings(session)

    user.contribution_points = max(0, user.contribution_points + points)
    user.stream_edits_approved += 1

    # Update contribution level
    user.contribution_level = calculate_contribution_level(user.contribution_points, settings)

    session.add(user)
    logger.info(
        f"Awarded {points} points to user {user.id}. "
        f"Total: {user.contribution_points}, Level: {user.contribution_level}"
    )


async def get_stream_for_suggestion_apply(session: AsyncSession, stream_id: int) -> Stream | None:
    """Load stream with normalized relationships required for field-apply flows."""
    query = (
        select(Stream)
        .where(Stream.id == stream_id)
        .options(
            selectinload(Stream.audio_formats),
            selectinload(Stream.channels),
            selectinload(Stream.hdr_formats),
            selectinload(Stream.languages),
        )
    )
    result = await session.exec(query)
    return result.first()


async def apply_stream_changes(
    base_stream: Stream,
    suggestion_type: str,
    field_name: str | None,
    value: str | None,
    session: AsyncSession,
    target_media_id: int | None = None,
    target_external_id: str | None = None,
    target_media_type: TargetMediaTypeLiteral | None = None,
    target_title: str | None = None,
    file_index: int | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_end: int | None = None,
) -> bool:
    """Apply suggested changes to stream. Returns True if successful.

    Works with the base Stream table directly, so all stream types
    (torrent, youtube, http, usenet, telegram, acestream) are supported.
    """
    try:
        if suggestion_type == "relink_media":
            # Re-link stream to different media (replaces current links)
            resolved_target_media_id = await _resolve_target_media_id(
                session,
                target_media_id=target_media_id,
                target_external_id=target_external_id,
                target_media_type=target_media_type,
                target_title=target_title or base_stream.name,
            )
            if not resolved_target_media_id:
                logger.error("relink_media requires a valid target_media_id or target_external_id")
                return False
            target_media_id = resolved_target_media_id

            # Get existing stream media links to find the old media_id(s)
            existing_stream_links = await session.exec(
                select(StreamMediaLink).where(StreamMediaLink.stream_id == base_stream.id)
            )
            old_media_ids = {link.media_id for link in existing_stream_links.all()}
            logger.info(f"Found old stream-level media IDs: {old_media_ids} for stream {base_stream.id}")

            # Remove existing stream-level links.
            # If file_index is provided, relink only that file's stream-level mapping.
            existing_links_query = select(StreamMediaLink).where(StreamMediaLink.stream_id == base_stream.id)
            if file_index is not None:
                existing_links_query = existing_links_query.where(StreamMediaLink.file_index == file_index)

            existing_links = await session.exec(existing_links_query)
            deleted_count = 0
            for link in existing_links.all():
                await session.delete(link)
                deleted_count += 1
            logger.info(f"Deleted {deleted_count} existing stream-level links")

            # Create new stream-level link
            new_link = StreamMediaLink(
                stream_id=base_stream.id,
                media_id=target_media_id,
                file_index=file_index,
            )
            session.add(new_link)
            logger.info(f"Re-linked stream {base_stream.id} to media {target_media_id}")

            # For series: Also update file-level links (FileMediaLink).
            # If file_index is provided, only that file mapping is updated.
            files_query = select(StreamFile).where(StreamFile.stream_id == base_stream.id)
            if file_index is not None:
                files_query = files_query.where(StreamFile.file_index == file_index)
            files_result = await session.exec(files_query)
            stream_files = files_result.all()
            logger.info(f"Found {len(stream_files)} files for stream {base_stream.id}")

            # Update file-level links regardless of old_media_ids
            # (the FileMediaLink might have different media_id than StreamMediaLink)
            updated_file_links = 0
            for stream_file in stream_files:
                # Get ALL file media links for this file (not just those matching old_media_ids)
                file_links_result = await session.exec(
                    select(FileMediaLink).where(FileMediaLink.file_id == stream_file.id)
                )
                for file_link in file_links_result.all():
                    old_file_media_id = file_link.media_id
                    # Only update if it's pointing to a different media than the target
                    if file_link.media_id != target_media_id:
                        file_link.media_id = target_media_id
                        session.add(file_link)
                        updated_file_links += 1
                        logger.info(
                            f"Updated file link: file {stream_file.id} from media {old_file_media_id} "
                            f"to {target_media_id} (S{file_link.season_number}E{file_link.episode_number})"
                        )

            if updated_file_links > 0:
                logger.info(
                    f"Updated {updated_file_links} file-level links for stream {base_stream.id} "
                    f"to media {target_media_id}"
                )
            else:
                logger.info(f"No file-level links needed updating for stream {base_stream.id}")

            # Optional one-step episode mapping for targeted file relink flows.
            if file_index is not None and (
                season_number is not None or episode_number is not None or episode_end is not None
            ):
                target_file_result = await session.exec(
                    select(StreamFile).where(
                        StreamFile.stream_id == base_stream.id,
                        StreamFile.file_index == file_index,
                    )
                )
                target_file = target_file_result.first()
                if not target_file:
                    logger.error("File index %s not found for stream %s", file_index, base_stream.id)
                    return False

                target_file_links_result = await session.exec(
                    select(FileMediaLink).where(
                        FileMediaLink.file_id == target_file.id,
                        FileMediaLink.media_id == target_media_id,
                    )
                )
                target_file_links = target_file_links_result.all()
                target_file_link = target_file_links[0] if target_file_links else None

                # Keep only one link row for this file+media pair.
                for duplicate in target_file_links[1:]:
                    await session.delete(duplicate)

                if not target_file_link:
                    target_file_link = FileMediaLink(
                        file_id=target_file.id,
                        media_id=target_media_id,
                    )

                target_file_link.season_number = season_number
                target_file_link.episode_number = episode_number
                target_file_link.episode_end = episode_end
                session.add(target_file_link)

            return True

        elif suggestion_type == "add_media_link":
            # Add additional media link (for collections)
            resolved_target_media_id = await _resolve_target_media_id(
                session,
                target_media_id=target_media_id,
                target_external_id=target_external_id,
                target_media_type=target_media_type,
                target_title=target_title or base_stream.name,
            )
            if not resolved_target_media_id:
                logger.error("add_media_link requires a valid target_media_id or target_external_id")
                return False
            target_media_id = resolved_target_media_id

            # Check if link already exists
            existing_result = await session.exec(
                select(StreamMediaLink).where(
                    StreamMediaLink.stream_id == base_stream.id,
                    StreamMediaLink.media_id == target_media_id,
                    StreamMediaLink.file_index == file_index,
                )
            )
            if existing_result.first():
                logger.warning(f"Link already exists: stream {base_stream.id} -> media {target_media_id}")
                return True  # Not an error, just already exists

            # Create new link
            new_link = StreamMediaLink(
                stream_id=base_stream.id,
                media_id=target_media_id,
                file_index=file_index,
            )
            session.add(new_link)
            logger.info(f"Added link: stream {base_stream.id} -> media {target_media_id}")

            # Optional one-step file-level episode mapping for add-link flows.
            if file_index is not None and (
                season_number is not None or episode_number is not None or episode_end is not None
            ):
                target_file_result = await session.exec(
                    select(StreamFile).where(
                        StreamFile.stream_id == base_stream.id,
                        StreamFile.file_index == file_index,
                    )
                )
                target_file = target_file_result.first()
                if not target_file:
                    logger.error("File index %s not found for stream %s", file_index, base_stream.id)
                    return False

                file_links_result = await session.exec(
                    select(FileMediaLink).where(
                        FileMediaLink.file_id == target_file.id,
                        FileMediaLink.media_id == target_media_id,
                    )
                )
                file_links = file_links_result.all()
                file_link = file_links[0] if file_links else None

                for duplicate in file_links[1:]:
                    await session.delete(duplicate)

                if not file_link:
                    file_link = FileMediaLink(
                        file_id=target_file.id,
                        media_id=target_media_id,
                    )

                file_link.season_number = season_number
                file_link.episode_number = episode_number
                file_link.episode_end = episode_end
                session.add(file_link)

            return True

        elif suggestion_type == "report_broken":
            # Consensus-based blocking: Only block after multiple unique users report
            # Count approved/auto-approved broken reports for this stream from unique users
            broken_reports_query = select(StreamSuggestion).where(
                StreamSuggestion.stream_id == base_stream.id,
                StreamSuggestion.suggestion_type == "report_broken",
                StreamSuggestion.status.in_([STATUS_APPROVED, STATUS_AUTO_APPROVED]),
            )
            broken_reports_result = await session.exec(broken_reports_query)
            broken_reports = broken_reports_result.all()

            # Count unique users who reported (excluding duplicates from same user)
            unique_reporters = {report.user_id for report in broken_reports}
            # Add +1 for the current report being processed (not yet in DB)
            report_count = len(unique_reporters) + 1

            # Get threshold from settings
            settings_query = select(ContributionSettings).where(ContributionSettings.id == "default")
            settings_result = await session.exec(settings_query)
            settings = settings_result.first()
            threshold = settings.broken_report_threshold if settings else 3

            if report_count >= threshold:
                base_stream.is_blocked = True
                logger.info(
                    f"Stream {base_stream.id} blocked after {report_count} broken reports (threshold: {threshold})"
                )
            else:
                logger.info(f"Stream {base_stream.id} has {report_count}/{threshold} broken reports - not yet blocked")
        elif suggestion_type == "field_correction" and field_name:
            # Fields on base Stream
            if field_name == "name":
                base_stream.name = value
            elif field_name == "resolution":
                base_stream.resolution = value
            elif field_name == "codec":
                base_stream.codec = value
            elif field_name == "quality":
                base_stream.quality = value
            elif field_name == "bit_depth":
                base_stream.bit_depth = value
            elif field_name == "source":
                base_stream.source = value
            # Normalized tables require special handling
            elif field_name == "audio_formats":
                # Parse JSON array and update audio formats
                try:
                    formats = json.loads(value) if value else []
                    # Clear existing and add new
                    base_stream.audio_formats.clear()
                    for fmt_name in formats:
                        fmt = await session.exec(select(AudioFormat).where(AudioFormat.name == fmt_name))
                        audio_fmt = fmt.first()
                        if not audio_fmt:
                            audio_fmt = AudioFormat(name=fmt_name)
                            session.add(audio_fmt)
                            await session.flush()
                        base_stream.audio_formats.append(audio_fmt)
                except json.JSONDecodeError:
                    if value:
                        fmt = await session.exec(select(AudioFormat).where(AudioFormat.name == value))
                        audio_fmt = fmt.first()
                        if not audio_fmt:
                            audio_fmt = AudioFormat(name=value)
                            session.add(audio_fmt)
                            await session.flush()
                        base_stream.audio_formats.clear()
                        base_stream.audio_formats.append(audio_fmt)
            elif field_name == "channels":
                # Parse JSON array and update channels
                try:
                    channels = json.loads(value) if value else []
                    base_stream.channels.clear()
                    for ch_name in channels:
                        ch = await session.exec(select(AudioChannel).where(AudioChannel.name == ch_name))
                        channel = ch.first()
                        if not channel:
                            channel = AudioChannel(name=ch_name)
                            session.add(channel)
                            await session.flush()
                        base_stream.channels.append(channel)
                except json.JSONDecodeError:
                    if value:
                        ch = await session.exec(select(AudioChannel).where(AudioChannel.name == value))
                        channel = ch.first()
                        if not channel:
                            channel = AudioChannel(name=value)
                            session.add(channel)
                            await session.flush()
                        base_stream.channels.clear()
                        base_stream.channels.append(channel)
            elif field_name == "hdr_formats":
                # Parse JSON array and update HDR formats
                try:
                    formats = json.loads(value) if value else []
                    base_stream.hdr_formats.clear()
                    for fmt_name in formats:
                        fmt = await session.exec(select(HDRFormat).where(HDRFormat.name == fmt_name))
                        hdr_fmt = fmt.first()
                        if not hdr_fmt:
                            hdr_fmt = HDRFormat(name=fmt_name)
                            session.add(hdr_fmt)
                            await session.flush()
                        base_stream.hdr_formats.append(hdr_fmt)
                except json.JSONDecodeError:
                    if value:
                        fmt = await session.exec(select(HDRFormat).where(HDRFormat.name == value))
                        hdr_fmt = fmt.first()
                        if not hdr_fmt:
                            hdr_fmt = HDRFormat(name=value)
                            session.add(hdr_fmt)
                            await session.flush()
                        base_stream.hdr_formats.clear()
                        base_stream.hdr_formats.append(hdr_fmt)
            elif field_name == "languages":
                try:
                    languages = json.loads(value) if value else []
                except json.JSONDecodeError:
                    languages = [v.strip() for v in value.split(",")] if value else []

                existing_links = await session.exec(
                    select(StreamLanguageLink).where(StreamLanguageLink.stream_id == base_stream.id)
                )
                for link in existing_links.all():
                    await session.delete(link)
                await session.flush()

                for lang_name in languages:
                    lang_name = lang_name.strip()
                    if not lang_name:
                        continue
                    lang = await get_or_create_language(session, lang_name)
                    lang_link = StreamLanguageLink(stream_id=base_stream.id, language_id=lang.id)
                    session.add(lang_link)
            elif field_name.startswith("episode_link:"):
                parts = field_name.split(":")
                if len(parts) == 3:
                    try:
                        file_id = int(parts[1])
                        link_field = parts[2]

                        # Find the file media link
                        link_query = select(FileMediaLink).where(FileMediaLink.file_id == file_id)
                        link_result = await session.exec(link_query)
                        link = link_result.first()

                        if link and link_field in [
                            "season_number",
                            "episode_number",
                            "episode_end",
                        ]:
                            new_value = int(value) if value else None
                            setattr(link, link_field, new_value)
                            session.add(link)
                            logger.info(f"Updated episode link {file_id}.{link_field} to {new_value}")
                            return True
                    except (ValueError, IndexError) as e:
                        logger.error(f"Failed to parse episode link field: {field_name}: {e}")
                        return False
                return False

        session.add(base_stream)
        return True
    except Exception as e:
        logger.exception(f"Failed to apply stream change: {e}")
        return False


def build_suggestion_response(
    suggestion: StreamSuggestion,
    username: str | None = None,
    stream: Stream | None = None,
    reviewer_name: str | None = None,
    user: User | None = None,
    source_media_context: MediaPreviewContext | None = None,
    target_media_context: MediaPreviewContext | None = None,
) -> StreamSuggestionResponse:
    """Build a suggestion response object.

    Accepts the base Stream directly so it works for all stream types.
    """
    stream_name = None
    media_id = None

    if stream:
        stream_name = stream.name
        if source_media_context and source_media_context.get("media_id"):
            media_id = source_media_context["media_id"]

    return StreamSuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=username,
        stream_id=suggestion.stream_id,
        stream_name=stream_name,
        media_id=media_id,
        source_media_id=source_media_context.get("media_id") if source_media_context else None,
        source_media_type=source_media_context.get("media_type") if source_media_context else None,
        source_media_title=source_media_context.get("media_title") if source_media_context else None,
        source_media_year=source_media_context.get("media_year") if source_media_context else None,
        source_media_poster_url=source_media_context.get("media_poster_url") if source_media_context else None,
        target_media_id=target_media_context.get("media_id") if target_media_context else None,
        target_media_type=target_media_context.get("media_type") if target_media_context else None,
        target_media_title=target_media_context.get("media_title") if target_media_context else None,
        target_media_year=target_media_context.get("media_year") if target_media_context else None,
        target_media_poster_url=target_media_context.get("media_poster_url") if target_media_context else None,
        suggestion_type=suggestion.suggestion_type,
        field_name=getattr(suggestion, "field_name", None),
        current_value=suggestion.current_value,
        suggested_value=suggestion.suggested_value,
        reason=suggestion.reason,
        status=suggestion.status,
        was_auto_approved=suggestion.status == STATUS_AUTO_APPROVED,
        created_at=suggestion.created_at,
        reviewed_by=suggestion.reviewed_by,  # User ID as string from DB
        reviewer_name=reviewer_name,  # Username for display
        reviewed_at=suggestion.reviewed_at,
        review_notes=suggestion.review_notes,
        user_contribution_level=user.contribution_level if user else None,
        user_contribution_points=user.contribution_points if user else None,
    )


# ============================================
# Stream Information Endpoints
# ============================================


@router.get("/streams/{stream_id}/editable-fields", response_model=StreamEditableFields)
async def get_stream_editable_fields(
    stream_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get all editable fields for a stream with their current values and options.

    Queries the base Stream table directly so all stream types are supported.
    """
    query = (
        select(Stream)
        .where(Stream.id == stream_id)
        .options(
            selectinload(Stream.audio_formats),
            selectinload(Stream.channels),
            selectinload(Stream.hdr_formats),
            selectinload(Stream.languages),
        )
    )
    result = await session.exec(query)
    base_stream = result.first()

    if not base_stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    fields = [
        StreamFieldInfo(
            field_name="name",
            display_name="Stream Name",
            current_value=base_stream.name,
            field_type="text",
        ),
        StreamFieldInfo(
            field_name="resolution",
            display_name="Resolution",
            current_value=base_stream.resolution,
            field_type="select",
            options=RESOLUTION_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="quality",
            display_name="Quality",
            current_value=base_stream.quality,
            field_type="select",
            options=QUALITY_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="codec",
            display_name="Codec",
            current_value=base_stream.codec,
            field_type="select",
            options=CODEC_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="bit_depth",
            display_name="Bit Depth",
            current_value=base_stream.bit_depth,
            field_type="select",
            options=["8-bit", "10-bit", "12-bit"],
        ),
        StreamFieldInfo(
            field_name="audio_formats",
            display_name="Audio Formats",
            current_value=json.dumps([af.name for af in base_stream.audio_formats])
            if base_stream.audio_formats
            else None,
            field_type="multi_select",
            options=AUDIO_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="channels",
            display_name="Audio Channels",
            current_value=json.dumps([ch.name for ch in base_stream.channels]) if base_stream.channels else None,
            field_type="multi_select",
            options=["2.0", "5.1", "7.1", "Atmos"],
        ),
        StreamFieldInfo(
            field_name="hdr_formats",
            display_name="HDR Formats",
            current_value=json.dumps([hf.name for hf in base_stream.hdr_formats]) if base_stream.hdr_formats else None,
            field_type="multi_select",
            options=HDR_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="languages",
            display_name="Languages",
            current_value=json.dumps([lang.name for lang in base_stream.languages]) if base_stream.languages else None,
            field_type="multi_select",
            options=LANGUAGE_OPTIONS,
        ),
        StreamFieldInfo(
            field_name="source",
            display_name="Source",
            current_value=base_stream.source,
            field_type="text",
        ),
    ]

    return StreamEditableFields(
        stream_id=stream_id,
        stream_name=base_stream.name,
        fields=fields,
    )


# ============================================
# Stream Suggestion Endpoints
# ============================================


@router.post("/streams/{stream_id}/suggest", response_model=StreamSuggestionResponse)
async def create_stream_suggestion(
    stream_id: int,
    request: StreamSuggestionCreateRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create a stream suggestion (report broken, quality correction, etc.)
    Trusted users may have their suggestions auto-approved.
    """
    settings = await get_contribution_settings(session)

    # Validate field_name is provided for field_correction type
    if request.suggestion_type == "field_correction" and not request.field_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="field_name is required for field_correction suggestions",
        )

    # Validate target media for relink/add_link types
    if request.suggestion_type in ("relink_media", "add_media_link") and not (
        request.target_media_id or request.target_external_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_media_id or target_external_id is required for relink_media/add_media_link suggestions",
        )
    if request.target_external_id and not request.target_media_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_media_type is required when target_external_id is provided",
        )
    if (
        request.season_number is not None or request.episode_number is not None or request.episode_end is not None
    ) and request.file_index is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_index is required when season/episode mapping is provided",
        )
    if request.episode_end is not None and request.episode_number is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="episode_number is required when episode_end is provided",
        )

    # Verify stream exists and load relationships needed for apply logic
    stream = await get_stream_for_suggestion_apply(session, stream_id)

    if not stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    # Check for duplicate suggestion from same user
    # For report_broken: check any status (user can only report once ever)
    # For other types: only check pending status
    if request.suggestion_type == "report_broken":
        existing_query = select(StreamSuggestion).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.stream_id == stream_id,
            StreamSuggestion.suggestion_type == request.suggestion_type,
        )
    else:
        existing_query = select(StreamSuggestion).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.stream_id == stream_id,
            StreamSuggestion.suggestion_type == request.suggestion_type,
            StreamSuggestion.status == STATUS_PENDING,
        )

    existing_result = await session.exec(existing_query)
    existing = existing_result.first()

    if existing:
        if request.suggestion_type == "report_broken":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already reported this stream as broken",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending suggestion of this type for this stream",
        )

    # For report_broken: Check if stream is already blocked
    if request.suggestion_type == "report_broken" and stream.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This stream is already blocked",
        )

    # Check if user qualifies for auto-approval
    can_auto_approve = await should_auto_approve(current_user, session)

    now = datetime.now(pytz.UTC)

    # Build current value if not provided
    current_value = request.current_value
    if not current_value and request.field_name:
        field_value = getattr(stream, request.field_name, None)
        if field_value is not None:
            if isinstance(field_value, list):
                # Normalize ORM relationship collections to plain strings for storage.
                current_value = json.dumps([getattr(item, "name", str(item)) for item in field_value])
            elif isinstance(field_value, dict):
                current_value = json.dumps(field_value)
            else:
                current_value = str(field_value)

    # Create suggestion
    # Note: We store field_name in the suggestion_type field for now
    # Format: "field_correction:field_name" for field corrections
    suggestion_type_stored = request.suggestion_type
    if request.suggestion_type == "field_correction" and request.field_name:
        suggestion_type_stored = f"field_correction:{request.field_name}"

    # For relink/add_link, encode target_media_id and file_index in suggested_value
    suggested_value = request.suggested_value
    if request.suggestion_type in ("relink_media", "add_media_link"):
        suggested_value = json.dumps(
            {
                "target_media_id": request.target_media_id,
                "target_external_id": request.target_external_id,
                "target_media_type": request.target_media_type,
                "target_title": request.target_title,
                "file_index": request.file_index,
                "season_number": request.season_number,
                "episode_number": request.episode_number,
                "episode_end": request.episode_end,
            }
        )

    suggestion = StreamSuggestion(
        user_id=current_user.id,
        stream_id=stream_id,
        suggestion_type=suggestion_type_stored,
        current_value=current_value,
        suggested_value=suggested_value,
        reason=request.reason,
        status=STATUS_AUTO_APPROVED if can_auto_approve else STATUS_PENDING,
    )

    # If auto-approved, apply changes immediately
    if can_auto_approve:
        suggestion.reviewed_by = str(current_user.id)
        suggestion.reviewed_at = now
        suggestion.review_notes = "Auto-approved based on user reputation"

        # Apply the changes
        apply_success = await apply_stream_changes(
            stream,
            request.suggestion_type,
            request.field_name,
            request.suggested_value,
            session,
            target_media_id=request.target_media_id,
            target_external_id=request.target_external_id,
            target_media_type=request.target_media_type,
            target_title=request.target_title,
            file_index=request.file_index,
            season_number=request.season_number,
            episode_number=request.episode_number,
            episode_end=request.episode_end,
        )

        if apply_success:
            # Award points
            await award_points(
                current_user,
                settings.points_per_stream_edit,
                session,
            )
        else:
            # If application failed, set to pending for moderator review
            suggestion.status = STATUS_PENDING
            suggestion.reviewed_by = None
            suggestion.reviewed_at = None
            suggestion.review_notes = None

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    if suggestion.status == STATUS_PENDING:
        await send_pending_stream_suggestion_notification(
            {
                "suggestion_id": suggestion.id,
                "stream_id": suggestion.stream_id,
                "user_id": current_user.id,
                "username": current_user.username or f"User #{current_user.id}",
                "suggestion_type": suggestion.suggestion_type,
                "field_name": request.field_name,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "reason": suggestion.reason,
                "target_media_id": request.target_media_id,
                "file_index": request.file_index,
                "season_number": request.season_number,
                "episode_number": request.episode_number,
                "episode_end": request.episode_end,
            }
        )

    source_media_context_map = await _build_source_media_context_map(session, [suggestion.stream_id])
    target_media_context_map = await _build_target_media_context_map(session, [suggestion])

    return build_suggestion_response(
        suggestion,
        username=current_user.username,
        stream=stream,
        reviewer_name=current_user.username if suggestion.reviewed_by else None,
        user=current_user,
        source_media_context=source_media_context_map.get(suggestion.stream_id),
        target_media_context=target_media_context_map.get(suggestion.id),
    )


@router.get("/streams/{stream_id}/suggestions", response_model=StreamSuggestionListResponse)
async def get_stream_suggestions(
    stream_id: int,
    status_filter: str | None = Query(None, alias="status"),
    suggestion_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get suggestions for a specific stream.
    """
    query = select(StreamSuggestion).where(StreamSuggestion.stream_id == stream_id)
    count_query = select(func.count(StreamSuggestion.id)).where(StreamSuggestion.stream_id == stream_id)

    if status_filter:
        query = query.where(StreamSuggestion.status == status_filter)
        count_query = count_query.where(StreamSuggestion.status == status_filter)

    if suggestion_type:
        query = query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))
        count_query = count_query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))

    # Get total count
    count_result = await session.exec(count_query)
    total = count_result.one()

    # Paginate
    query = query.order_by(StreamSuggestion.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.exec(query)
    suggestions = result.all()

    # Get stream info
    stream_query = select(Stream).where(Stream.id == stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()
    source_media_context_map = await _build_source_media_context_map(session, [stream_id])
    target_media_context_map = await _build_target_media_context_map(session, suggestions)

    responses = []
    for s in suggestions:
        username = await get_username(session, s.user_id)
        reviewer_name = await get_username(session, s.reviewed_by) if s.reviewed_by else None

        # Get user info
        user_query = select(User).where(User.id == s.user_id)
        user_result = await session.exec(user_query)
        user = user_result.first()

        responses.append(
            build_suggestion_response(
                s,
                username,
                stream=stream,
                reviewer_name=reviewer_name,
                user=user,
                source_media_context=source_media_context_map.get(stream_id),
                target_media_context=target_media_context_map.get(s.id),
            )
        )

    return StreamSuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get("/stream-suggestions/my", response_model=StreamSuggestionListResponse)
async def get_my_stream_suggestions(
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get current user's stream suggestions.
    """
    query = select(StreamSuggestion).where(StreamSuggestion.user_id == current_user.id)
    count_query = select(func.count(StreamSuggestion.id)).where(StreamSuggestion.user_id == current_user.id)

    if status_filter:
        query = query.where(StreamSuggestion.status == status_filter)
        count_query = count_query.where(StreamSuggestion.status == status_filter)

    count_result = await session.exec(count_query)
    total = count_result.one()

    query = query.order_by(StreamSuggestion.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.exec(query)
    suggestions = result.all()
    source_media_context_map = await _build_source_media_context_map(session, [s.stream_id for s in suggestions])
    target_media_context_map = await _build_target_media_context_map(session, suggestions)

    responses = []
    for s in suggestions:
        stream_query = select(Stream).where(Stream.id == s.stream_id)
        stream_result = await session.exec(stream_query)
        stream = stream_result.first()

        reviewer_name = await get_username(session, s.reviewed_by) if s.reviewed_by else None
        responses.append(
            build_suggestion_response(
                s,
                current_user.username,
                stream=stream,
                reviewer_name=reviewer_name,
                user=current_user,
                source_media_context=source_media_context_map.get(s.stream_id),
                target_media_context=target_media_context_map.get(s.id),
            )
        )

    return StreamSuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get("/stream-suggestions/pending", response_model=StreamSuggestionListResponse)
async def get_pending_stream_suggestions(
    suggestion_type: str | None = Query(None),
    status_filter: Literal["pending", "approved", "rejected", "auto_approved", "all"] | None = Query(
        None, alias="status"
    ),
    uploader_query: str | None = Query(None, max_length=120),
    reviewer_query: str | None = Query(None, max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get moderator-visible stream suggestions.
    Defaults to pending unless `status` is provided.
    """
    # Default is pending-only for backwards compatibility.
    query = select(StreamSuggestion)
    count_query = select(func.count(StreamSuggestion.id))

    if status_filter and status_filter != "all":
        query = query.where(StreamSuggestion.status == status_filter)
        count_query = count_query.where(StreamSuggestion.status == status_filter)
    elif status_filter is None:
        query = query.where(StreamSuggestion.status == STATUS_PENDING)
        count_query = count_query.where(StreamSuggestion.status == STATUS_PENDING)

    if suggestion_type:
        query = query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))
        count_query = count_query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))

    normalized_uploader_query = _normalize_filter_query(uploader_query)
    if normalized_uploader_query:
        uploader_alias = aliased(User)
        query = query.join(uploader_alias, StreamSuggestion.user_id == uploader_alias.id)
        count_query = count_query.join(uploader_alias, StreamSuggestion.user_id == uploader_alias.id)

        if normalized_uploader_query.isdigit():
            uploader_id = int(normalized_uploader_query)
            uploader_condition = or_(
                StreamSuggestion.user_id == uploader_id,
                uploader_alias.username.ilike(f"%{normalized_uploader_query}%"),
            )
        else:
            uploader_condition = uploader_alias.username.ilike(f"%{normalized_uploader_query}%")

        query = query.where(uploader_condition)
        count_query = count_query.where(uploader_condition)

    normalized_reviewer_query = _normalize_filter_query(reviewer_query)
    if normalized_reviewer_query:
        if normalized_reviewer_query.lower() == "auto":
            query = query.where(StreamSuggestion.reviewed_by == "auto")
            count_query = count_query.where(StreamSuggestion.reviewed_by == "auto")
        else:
            reviewer_alias = aliased(User)
            query = query.join(
                reviewer_alias,
                cast(reviewer_alias.id, String) == StreamSuggestion.reviewed_by,
            )
            count_query = count_query.join(
                reviewer_alias,
                cast(reviewer_alias.id, String) == StreamSuggestion.reviewed_by,
            )

            if normalized_reviewer_query.isdigit():
                reviewer_id = int(normalized_reviewer_query)
                reviewer_condition = or_(
                    StreamSuggestion.reviewed_by == str(reviewer_id),
                    reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%"),
                )
            else:
                reviewer_condition = reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%")

            query = query.where(reviewer_condition)
            count_query = count_query.where(reviewer_condition)

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Latest first (desc) for better review experience
    query = query.order_by(StreamSuggestion.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.exec(query)
    suggestions = result.all()
    source_media_context_map = await _build_source_media_context_map(session, [s.stream_id for s in suggestions])
    target_media_context_map = await _build_target_media_context_map(session, suggestions)

    responses = []
    for s in suggestions:
        username = await get_username(session, s.user_id)
        reviewer_name = await get_username(session, s.reviewed_by) if s.reviewed_by else None

        stream_query = select(Stream).where(Stream.id == s.stream_id)
        stream_result = await session.exec(stream_query)
        stream = stream_result.first()

        # Get user info
        user_query = select(User).where(User.id == s.user_id)
        user_result = await session.exec(user_query)
        user = user_result.first()

        responses.append(
            build_suggestion_response(
                s,
                username,
                stream=stream,
                reviewer_name=reviewer_name,
                user=user,
                source_media_context=source_media_context_map.get(s.stream_id),
                target_media_context=target_media_context_map.get(s.id),
            )
        )

    return StreamSuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.put(
    "/stream-suggestions/{suggestion_id}/review",
    response_model=StreamSuggestionResponse,
)
async def review_stream_suggestion(
    suggestion_id: str,
    request: StreamSuggestionReviewRequest,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Review a stream suggestion (moderator only).
    If approved, changes will be applied and points awarded.
    """
    settings = await get_contribution_settings(session)

    query = select(StreamSuggestion).where(StreamSuggestion.id == suggestion_id)
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suggestion has already been reviewed",
        )

    # Get stream with relationships needed for field apply
    stream = await get_stream_for_suggestion_apply(session, suggestion.stream_id)

    # Get suggestion author
    author_query = select(User).where(User.id == suggestion.user_id)
    author_result = await session.exec(author_query)
    author = author_result.first()

    now = datetime.now(pytz.UTC)

    suggestion.status = STATUS_APPROVED if request.action == "approve" else STATUS_REJECTED
    suggestion.reviewed_by = str(current_user.id)
    suggestion.reviewed_at = now
    suggestion.review_notes = request.review_notes
    suggestion.updated_at = now

    if request.action == "approve":
        if stream:
            # Parse suggestion type to get field name if applicable
            suggestion_type = suggestion.suggestion_type
            field_name = None
            if ":" in suggestion_type:
                suggestion_type, field_name = suggestion_type.split(":", 1)

            # For relink/add_link suggestions, parse target_media_id and file_index from suggested_value
            target_media_id = None
            target_external_id = None
            target_media_type = None
            target_title = None
            file_index = None
            season_number = None
            episode_number = None
            episode_end = None
            if suggestion_type in ("relink_media", "add_media_link") and suggestion.suggested_value:
                try:
                    link_data = json.loads(suggestion.suggested_value)
                    target_media_id = link_data.get("target_media_id")
                    target_external_id = link_data.get("target_external_id")
                    target_media_type = link_data.get("target_media_type")
                    target_title = link_data.get("target_title")
                    file_index = link_data.get("file_index")
                    season_number = link_data.get("season_number")
                    episode_number = link_data.get("episode_number")
                    episode_end = link_data.get("episode_end")
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse link data from suggestion {suggestion.id}")

            apply_success = await apply_stream_changes(
                stream,
                suggestion_type,
                field_name,
                suggestion.suggested_value,
                session,
                target_media_id=target_media_id,
                target_external_id=target_external_id,
                target_media_type=target_media_type,
                target_title=target_title,
                file_index=file_index,
                season_number=season_number,
                episode_number=episode_number,
                episode_end=episode_end,
            )
            if not apply_success:
                logger.warning("Approved suggestion %s but failed to apply stream changes", suggestion.id)
        else:
            logger.warning("Approved suggestion %s but stream %s was not found", suggestion.id, suggestion.stream_id)

        if author:
            await award_points(author, settings.points_per_stream_edit, session)
    elif request.action == "reject" and author:
        if settings.points_for_rejection_penalty < 0:
            author.contribution_points = max(0, author.contribution_points + settings.points_for_rejection_penalty)
            session.add(author)

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    if author:
        await session.refresh(author)

    username = await get_username(session, suggestion.user_id)
    source_media_context_map = await _build_source_media_context_map(session, [suggestion.stream_id])
    target_media_context_map = await _build_target_media_context_map(session, [suggestion])

    return build_suggestion_response(
        suggestion,
        username=username,
        stream=stream,
        reviewer_name=current_user.username,
        user=author,
        source_media_context=source_media_context_map.get(suggestion.stream_id),
        target_media_context=target_media_context_map.get(suggestion.id),
    )


@router.post("/stream-suggestions/bulk-review")
async def bulk_review_stream_suggestions(
    suggestion_ids: list[str],
    action: Literal["approve", "reject"],
    review_notes: str | None = None,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Bulk review multiple stream suggestions (moderator only).
    """
    settings = await get_contribution_settings(session)
    now = datetime.now(pytz.UTC)

    results = {"approved": 0, "rejected": 0, "skipped": 0}

    for suggestion_id in suggestion_ids:
        query = select(StreamSuggestion).where(StreamSuggestion.id == suggestion_id)
        result = await session.exec(query)
        suggestion = result.first()

        if not suggestion or suggestion.status != STATUS_PENDING:
            results["skipped"] += 1
            continue

        stream = await get_stream_for_suggestion_apply(session, suggestion.stream_id)

        # Get author
        author_query = select(User).where(User.id == suggestion.user_id)
        author_result = await session.exec(author_query)
        author = author_result.first()

        suggestion.status = STATUS_APPROVED if action == "approve" else STATUS_REJECTED
        suggestion.reviewed_by = str(current_user.id)
        suggestion.reviewed_at = now
        suggestion.review_notes = review_notes
        suggestion.updated_at = now

        if action == "approve" and stream:
            suggestion_type = suggestion.suggestion_type
            field_name = None
            if ":" in suggestion_type:
                suggestion_type, field_name = suggestion_type.split(":", 1)

            # For relink/add_link suggestions, parse target_media_id and file_index
            target_media_id = None
            target_external_id = None
            target_media_type = None
            target_title = None
            file_index = None
            season_number = None
            episode_number = None
            episode_end = None
            if suggestion_type in ("relink_media", "add_media_link") and suggestion.suggested_value:
                try:
                    link_data = json.loads(suggestion.suggested_value)
                    target_media_id = link_data.get("target_media_id")
                    target_external_id = link_data.get("target_external_id")
                    target_media_type = link_data.get("target_media_type")
                    target_title = link_data.get("target_title")
                    file_index = link_data.get("file_index")
                    season_number = link_data.get("season_number")
                    episode_number = link_data.get("episode_number")
                    episode_end = link_data.get("episode_end")
                except json.JSONDecodeError:
                    pass

            await apply_stream_changes(
                stream,
                suggestion_type,
                field_name,
                suggestion.suggested_value,
                session,
                target_media_id=target_media_id,
                target_external_id=target_external_id,
                target_media_type=target_media_type,
                target_title=target_title,
                file_index=file_index,
                season_number=season_number,
                episode_number=episode_number,
                episode_end=episode_end,
            )

            if author:
                await award_points(author, settings.points_per_stream_edit, session)

            results["approved"] += 1
        else:
            if author and settings.points_for_rejection_penalty < 0:
                author.contribution_points = max(
                    0,
                    author.contribution_points + settings.points_for_rejection_penalty,
                )
                session.add(author)
            results["rejected"] += 1

        session.add(suggestion)

    await session.commit()

    return results


@router.delete("/stream-suggestions/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream_suggestion(
    suggestion_id: str,  # UUID string
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a pending stream suggestion. Users can only delete their own pending suggestions.
    """
    query = select(StreamSuggestion).where(
        StreamSuggestion.id == suggestion_id,
        StreamSuggestion.user_id == current_user.id,
    )
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete pending suggestions",
        )

    await session.delete(suggestion)
    await session.commit()


@router.get("/stream-suggestions/stats", response_model=StreamSuggestionStats)
async def get_stream_suggestion_stats(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get stream suggestion statistics.
    Moderators see global stats including today's counts, users see their own.
    """
    is_moderator = current_user.role in [UserRole.MODERATOR, UserRole.ADMIN]

    # Initialize today's stats
    approved_today = 0
    rejected_today = 0

    if is_moderator:
        total_result = await session.exec(select(func.count(StreamSuggestion.id)))
        total = total_result.one()

        pending_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == STATUS_PENDING)
        )
        pending = pending_result.one()

        approved_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == STATUS_APPROVED)
        )
        approved = approved_result.one()

        auto_approved_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == STATUS_AUTO_APPROVED)
        )
        auto_approved = auto_approved_result.one()

        rejected_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == STATUS_REJECTED)
        )
        rejected = rejected_result.one()

        # Today's stats
        today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        approved_today_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(
                StreamSuggestion.status.in_([STATUS_APPROVED, STATUS_AUTO_APPROVED]),
                StreamSuggestion.reviewed_at >= today_start,
            )
        )
        approved_today = approved_today_result.one()

        rejected_today_result = await session.exec(
            select(func.count(StreamSuggestion.id)).where(
                StreamSuggestion.status == STATUS_REJECTED,
                StreamSuggestion.reviewed_at >= today_start,
            )
        )
        rejected_today = rejected_today_result.one()
    else:
        total = pending = approved = auto_approved = rejected = 0

    # User stats
    user_pending_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.status == STATUS_PENDING,
        )
    )
    user_pending = user_pending_result.one()

    user_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.status == STATUS_APPROVED,
        )
    )
    user_approved = user_approved_result.one()

    user_auto_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.status == STATUS_AUTO_APPROVED,
        )
    )
    user_auto_approved = user_auto_approved_result.one()

    user_rejected_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == current_user.id,
            StreamSuggestion.status == STATUS_REJECTED,
        )
    )
    user_rejected = user_rejected_result.one()

    return StreamSuggestionStats(
        total=total,
        pending=pending,
        approved=approved,
        auto_approved=auto_approved,
        rejected=rejected,
        approved_today=approved_today,
        rejected_today=rejected_today,
        user_pending=user_pending,
        user_approved=user_approved,
        user_auto_approved=user_auto_approved,
        user_rejected=user_rejected,
    )


@router.get("/streams/{stream_id}/broken-status", response_model=BrokenReportStatus)
async def get_broken_report_status(
    stream_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get the broken report status for a stream.
    Shows how many users have reported it and how many more are needed to block it.
    """
    stream_query = select(Stream).where(Stream.id == stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

    if not stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    is_blocked = stream.is_blocked

    # Get threshold from settings
    settings = await get_contribution_settings(session)
    threshold = settings.broken_report_threshold

    # Count approved/auto-approved broken reports from unique users
    broken_reports_query = select(StreamSuggestion).where(
        StreamSuggestion.stream_id == stream_id,
        StreamSuggestion.suggestion_type == "report_broken",
        StreamSuggestion.status.in_([STATUS_APPROVED, STATUS_AUTO_APPROVED]),
    )
    broken_reports_result = await session.exec(broken_reports_query)
    broken_reports = broken_reports_result.all()
    unique_reporters = {report.user_id for report in broken_reports}
    report_count = len(unique_reporters)

    # Check if current user has already reported
    user_report_query = select(StreamSuggestion).where(
        StreamSuggestion.stream_id == stream_id,
        StreamSuggestion.suggestion_type == "report_broken",
        StreamSuggestion.user_id == current_user.id,
    )
    user_report_result = await session.exec(user_report_query)
    user_has_reported = user_report_result.first() is not None

    reports_needed = max(0, threshold - report_count) if not is_blocked else 0

    return BrokenReportStatus(
        stream_id=stream_id,
        is_blocked=is_blocked,
        report_count=report_count,
        threshold=threshold,
        user_has_reported=user_has_reported,
        reports_needed=reports_needed,
    )
