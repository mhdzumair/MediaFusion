"""
Stream Suggestions API endpoints for reporting broken streams and quality corrections.
Includes auto-approval for trusted users and full field editing support.
"""

import json
import logging
from datetime import datetime
from typing import Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import (
    AudioChannel,
    AudioFormat,
    ContributionSettings,
    FileMediaLink,
    HDRFormat,
    Media,
    Stream,
    StreamFile,
    StreamMediaLink,
    StreamSuggestion,
    User,
)
from db.models.links import StreamLanguageLink
from db.crud.reference import get_or_create_language

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
    file_index: int | None = Field(None, description="Specific file index within torrent (for multi-file torrents)")


class StreamSuggestionResponse(BaseModel):
    """Response for a stream suggestion"""

    id: str  # UUID string
    user_id: int
    username: str | None = None
    stream_id: int
    stream_name: str | None = None
    media_id: int | None = None  # Internal media ID
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


async def apply_stream_changes(
    base_stream: Stream,
    suggestion_type: str,
    field_name: str | None,
    value: str | None,
    session: AsyncSession,
    target_media_id: int | None = None,
    file_index: int | None = None,
) -> bool:
    """Apply suggested changes to stream. Returns True if successful.

    Works with the base Stream table directly, so all stream types
    (torrent, youtube, http, usenet, telegram, acestream) are supported.
    """
    try:
        if suggestion_type == "relink_media":
            # Re-link stream to different media (replaces current links)
            if not target_media_id:
                logger.error("relink_media requires target_media_id")
                return False

            # Verify target media exists
            media_result = await session.exec(select(Media).where(Media.id == target_media_id))
            target_media = media_result.first()
            if not target_media:
                logger.error(f"Target media {target_media_id} not found")
                return False

            # Get existing stream media links to find the old media_id(s)
            existing_stream_links = await session.exec(
                select(StreamMediaLink).where(StreamMediaLink.stream_id == base_stream.id)
            )
            old_media_ids = {link.media_id for link in existing_stream_links.all()}
            logger.info(f"Found old stream-level media IDs: {old_media_ids} for stream {base_stream.id}")

            # Remove existing stream-level links
            existing_links = await session.exec(
                select(StreamMediaLink).where(StreamMediaLink.stream_id == base_stream.id)
            )
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

            # For series: Also update file-level links (FileMediaLink)
            # This handles the case where a series torrent's episodes need to be
            # re-linked to a different series while keeping season/episode numbers

            # Get all files for this stream
            files_result = await session.exec(select(StreamFile).where(StreamFile.stream_id == base_stream.id))
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

            return True

        elif suggestion_type == "add_media_link":
            # Add additional media link (for collections)
            if not target_media_id:
                logger.error("add_media_link requires target_media_id")
                return False

            # Verify target media exists
            media_result = await session.exec(select(Media).where(Media.id == target_media_id))
            target_media = media_result.first()
            if not target_media:
                logger.error(f"Target media {target_media_id} not found")
                return False

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
        logger.error(f"Failed to apply stream change: {e}")
        return False


def build_suggestion_response(
    suggestion: StreamSuggestion,
    username: str | None = None,
    stream: Stream | None = None,
    reviewer_name: str | None = None,
    user: User | None = None,
) -> StreamSuggestionResponse:
    """Build a suggestion response object.

    Accepts the base Stream directly so it works for all stream types.
    """
    stream_name = None
    media_id = None

    if stream:
        stream_name = stream.name
        media_links = getattr(stream, "media_links", None)
        if media_links:
            try:
                first_link = media_links[0]
                if hasattr(first_link, "media") and first_link.media:
                    media_id = first_link.media.id
            except (IndexError, AttributeError):
                pass

    return StreamSuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=username,
        stream_id=suggestion.stream_id,
        stream_name=stream_name,
        media_id=media_id,
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

    # Validate target_media_id is provided for relink/add_link types
    if request.suggestion_type in ("relink_media", "add_media_link") and not request.target_media_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_media_id is required for relink_media/add_media_link suggestions",
        )

    # Verify stream exists - query the base Stream table directly
    stream_query = select(Stream).where(Stream.id == stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

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
            current_value = json.dumps(field_value) if isinstance(field_value, (list, dict)) else str(field_value)

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
                "file_index": request.file_index,
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
            file_index=request.file_index,
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

    return build_suggestion_response(
        suggestion,
        username=current_user.username,
        stream=stream,
        reviewer_name=current_user.username if suggestion.reviewed_by else None,
        user=current_user,
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

    responses = []
    for s in suggestions:
        username = await get_username(session, s.user_id)
        reviewer_name = await get_username(session, s.reviewed_by) if s.reviewed_by else None

        # Get user info
        user_query = select(User).where(User.id == s.user_id)
        user_result = await session.exec(user_query)
        user = user_result.first()

        responses.append(build_suggestion_response(s, username, stream=stream, reviewer_name=reviewer_name, user=user))

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
    status_filter: Literal["pending", "approved", "rejected", "auto_approved"] | None = Query(None, alias="status"),
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

    if status_filter:
        query = query.where(StreamSuggestion.status == status_filter)
        count_query = count_query.where(StreamSuggestion.status == status_filter)
    else:
        query = query.where(StreamSuggestion.status == STATUS_PENDING)
        count_query = count_query.where(StreamSuggestion.status == STATUS_PENDING)

    if suggestion_type:
        query = query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))
        count_query = count_query.where(StreamSuggestion.suggestion_type.startswith(suggestion_type))

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Latest first (desc) for better review experience
    query = query.order_by(StreamSuggestion.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await session.exec(query)
    suggestions = result.all()

    responses = []
    for s in suggestions:
        username = await get_username(session, s.user_id)

        stream_query = select(Stream).where(Stream.id == s.stream_id)
        stream_result = await session.exec(stream_query)
        stream = stream_result.first()

        # Get user info
        user_query = select(User).where(User.id == s.user_id)
        user_result = await session.exec(user_query)
        user = user_result.first()

        responses.append(build_suggestion_response(s, username, stream=stream, reviewer_name=None, user=user))

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

    # Get stream
    stream_query = select(Stream).where(Stream.id == suggestion.stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

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

    if request.action == "approve" and stream:
        # Parse suggestion type to get field name if applicable
        suggestion_type = suggestion.suggestion_type
        field_name = None
        if ":" in suggestion_type:
            suggestion_type, field_name = suggestion_type.split(":", 1)

        # For relink/add_link suggestions, parse target_media_id and file_index from suggested_value
        target_media_id = None
        file_index = None
        if suggestion_type in ("relink_media", "add_media_link") and suggestion.suggested_value:
            try:
                link_data = json.loads(suggestion.suggested_value)
                target_media_id = link_data.get("target_media_id")
                file_index = link_data.get("file_index")
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse link data from suggestion {suggestion.id}")

        apply_success = await apply_stream_changes(
            stream,
            suggestion_type,
            field_name,
            suggestion.suggested_value,
            session,
            target_media_id=target_media_id,
            file_index=file_index,
        )

        if apply_success and author:
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

    return build_suggestion_response(
        suggestion,
        username=username,
        stream=stream,
        reviewer_name=current_user.username,
        user=author,
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

        stream_query = select(Stream).where(Stream.id == suggestion.stream_id)
        stream_result = await session.exec(stream_query)
        stream = stream_result.first()

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
            file_index = None
            if suggestion_type in ("relink_media", "add_media_link") and suggestion.suggested_value:
                try:
                    link_data = json.loads(suggestion.suggested_value)
                    target_media_id = link_data.get("target_media_id")
                    file_index = link_data.get("file_index")
                except json.JSONDecodeError:
                    pass

            await apply_stream_changes(
                stream,
                suggestion_type,
                field_name,
                suggestion.suggested_value,
                session,
                target_media_id=target_media_id,
                file_index=file_index,
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
