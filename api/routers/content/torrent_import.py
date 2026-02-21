"""
Torrent Import API endpoints for importing magnet links and torrent files.
"""

import json
import logging
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from api.routers.content.anonymous_utils import normalize_anonymous_display_name, resolve_uploader_identity
from db.config import settings
from db.crud.media import get_media_by_external_id, add_external_id, parse_external_id
from db.crud.reference import get_or_create_language
from db.crud.scraper_helpers import get_or_create_metadata
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType, TorrentType, UserRole
from db.models import Contribution, Media, Stream, StreamFile, StreamMediaLink, User
from db.models.streams import (
    FileMediaLink,
    StreamLanguageLink,
    StreamType,
    TorrentStream,
)
from utils import torrent

logger = logging.getLogger(__name__)


async def fetch_and_create_media_from_external(
    session: AsyncSession,
    external_id: str,
    media_type: str,
    fallback_title: str | None = None,
) -> Media | None:
    """
    Fetch metadata from external provider and create a Media record.

    Args:
        session: Database session
        external_id: External ID (e.g., tt1234567 for IMDB)
        media_type: 'movie' or 'series'
        fallback_title: Title to use if fetch fails

    Returns:
        Created Media object or None
    """
    from scrapers.scraper_tasks import meta_fetcher

    # Determine provider from external_id format
    provider = None
    provider_id = external_id

    if external_id.startswith("tt"):
        provider = "imdb"
    elif external_id.startswith("tmdb:"):
        provider = "tmdb"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("tvdb:"):
        provider = "tvdb"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("mal:"):
        provider = "mal"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("kitsu:"):
        provider = "kitsu"
        provider_id = external_id.split(":")[-1]
    else:
        # Try to infer - if it starts with tt, it's IMDB
        # Otherwise, assume it might be a numeric TMDB ID
        if external_id.isdigit():
            provider = "tmdb"
        else:
            # Unknown format, use get_or_create_metadata as fallback
            return await get_or_create_metadata(
                session,
                {"id": external_id, "title": fallback_title or "Unknown"},
                media_type,
            )

    try:
        # Fetch full metadata from the provider
        metadata = await meta_fetcher.get_metadata_from_provider(provider, provider_id, media_type)

        if metadata:
            # Create media with full metadata
            return await get_or_create_metadata(session, metadata, media_type)
        else:
            # Fallback to basic creation
            logger.warning(f"Could not fetch metadata from {provider} for {external_id}, using fallback")
            return await get_or_create_metadata(
                session,
                {"id": external_id, "title": fallback_title or "Unknown"},
                media_type,
            )
    except Exception as e:
        logger.warning(f"Error fetching metadata from {provider} for {external_id}: {e}")
        # Fallback to basic creation
        return await get_or_create_metadata(
            session,
            {"id": external_id, "title": fallback_title or "Unknown"},
            media_type,
        )


router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# Torrent Import Processing
# ============================================


async def process_torrent_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User | None,
) -> dict:
    """
    Process a torrent import - creates the actual TorrentStream record in the database.

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """

    info_hash = contribution_data.get("info_hash", "").lower()
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "Unknown")
    name = contribution_data.get("name", title)
    total_size = contribution_data.get("total_size", 0)

    is_anonymous = contribution_data.get("is_anonymous", False)
    anonymous_display_name = contribution_data.get("anonymous_display_name")

    if not info_hash:
        raise ValueError("Missing info_hash in contribution data")

    # Check if torrent already exists
    existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
    if existing.first():
        return {"status": "exists", "message": "Torrent already exists in database"}

    # Get or create media metadata
    media = None
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    if not media:
        # Try to fetch full metadata from external provider and create
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id or f"user_{info_hash[:8]}",
                meta_type,
                fallback_title=title,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch/create media for {meta_id}: {e}")
            # Create a basic media record as fallback
            media_type_enum = MediaType.MOVIE if meta_type == "movie" else MediaType.SERIES
            media = Media(
                title=title,
                type=media_type_enum,
            )
            session.add(media)
            await session.flush()
            # Add external ID to MediaExternalID table
            ext_id_to_add = meta_id or f"user_{info_hash[:8]}"
            provider, ext_id = parse_external_id(ext_id_to_add)
            if provider and ext_id:
                await add_external_id(session, media.id, provider, ext_id)

    uploader_name, uploader_user_id = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)

    # Create Stream base record
    stream = Stream(
        stream_type=StreamType.TORRENT,
        name=name,
        source="Contribution Stream",
        resolution=contribution_data.get("resolution"),
        codec=contribution_data.get("codec"),
        quality=contribution_data.get("quality"),
        uploader=uploader_name,
        uploader_user_id=uploader_user_id,
    )
    session.add(stream)
    await session.flush()

    # Create TorrentStream record
    torrent_stream = TorrentStream(
        stream_id=stream.id,
        info_hash=info_hash,
        total_size=total_size,
        torrent_type=TorrentType.PUBLIC,
        file_count=contribution_data.get("file_count", 1),
        uploaded_at=datetime.now(pytz.UTC),
    )
    session.add(torrent_stream)

    # Link stream to media
    stream_media_link = StreamMediaLink(
        stream_id=stream.id,
        media_id=media.id,
    )
    session.add(stream_media_link)

    # Add languages
    languages = contribution_data.get("languages", [])
    for lang_name in languages:
        if lang_name:
            try:
                lang = await get_or_create_language(session, lang_name)
                lang_link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
                session.add(lang_link)
            except Exception as e:
                logger.warning(f"Failed to add language {lang_name}: {e}")

    # Add files with per-file metadata support
    file_data = contribution_data.get("file_data", [])

    # Track all media IDs we need to link the stream to
    linked_media_ids = {media.id}  # Start with the primary media

    for idx, file_info in enumerate(file_data):
        stream_file = StreamFile(
            stream_id=stream.id,
            file_index=file_info.get("index", idx),
            filename=file_info.get("filename", ""),
            size=file_info.get("size", 0),
        )
        session.add(stream_file)
        await session.flush()

        # Determine which media to link this file to
        file_meta_id = file_info.get("meta_id")
        file_media = None

        if file_meta_id:
            # Per-file metadata: look up the specified media
            file_media = await get_media_by_external_id(session, file_meta_id)

            # If media doesn't exist, fetch from external provider and create
            if not file_media:
                file_meta_title = file_info.get("meta_title")
                file_meta_type = file_info.get("meta_type", meta_type)

                try:
                    # Fetch full metadata from external provider
                    file_media = await fetch_and_create_media_from_external(
                        session,
                        file_meta_id,
                        file_meta_type,
                        fallback_title=file_meta_title,
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch/create media for file meta_id {file_meta_id}: {e}")

            if file_media:
                linked_media_ids.add(file_media.id)

        # Fall back to primary media if no per-file metadata
        target_media = file_media or media

        # Link file to media with season/episode info if present
        file_media_link = FileMediaLink(
            file_id=stream_file.id,
            media_id=target_media.id,
            season_number=file_info.get("season_number"),
            episode_number=file_info.get("episode_number"),
            episode_end=file_info.get("episode_end"),
        )
        session.add(file_media_link)

    # Create StreamMediaLink for each unique media (multi-content support)
    for extra_media_id in linked_media_ids:
        if extra_media_id != media.id:  # Primary already linked above
            extra_link = StreamMediaLink(
                stream_id=stream.id,
                media_id=extra_media_id,
                is_primary=False,
            )
            session.add(extra_link)

    # Update media stream count for primary media
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = datetime.now(pytz.UTC)

    # Also update stream count for linked media
    for extra_media_id in linked_media_ids:
        if extra_media_id != media.id:
            extra_media = await session.get(Media, extra_media_id)
            if extra_media:
                extra_media.total_streams = (extra_media.total_streams or 0) + 1
                extra_media.last_stream_added = datetime.now(pytz.UTC)

    await session.flush()

    logger.info(
        f"Successfully imported torrent {info_hash} for media_id={media.id}, "
        f"linked to {len(linked_media_ids)} media entries"
    )

    return {
        "status": "success",
        "stream_id": stream.id,
        "media_id": media.id,
        "info_hash": info_hash,
        "linked_media_count": len(linked_media_ids),
        "linked_media_ids": list(linked_media_ids),
    }


# ============================================
# Pydantic Schemas
# ============================================


class MagnetImportRequest(BaseModel):
    """Request schema for importing a magnet link."""

    magnet_link: str
    meta_type: str = Field(..., pattern="^(movie|series|sports)$")
    meta_id: str | None = None  # IMDb ID if known
    title: str | None = None
    catalogs: list[str] | None = None
    languages: list[str] | None = None


class TorrentAnalyzeResponse(BaseModel):
    """Response from torrent analysis."""

    status: str
    info_hash: str | None = None
    torrent_name: str | None = None  # Original torrent name
    total_size: int | None = None
    total_size_readable: str | None = None
    file_count: int | None = None
    files: list[dict[str, Any]] | None = None
    parsed_title: str | None = None
    year: int | None = None
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    audio: list[str] | None = None
    matches: list[dict[str, Any]] | None = None
    error: str | None = None


class ImportResponse(BaseModel):
    """Generic import response."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


# ============================================
# API Endpoints
# ============================================


@router.post("/magnet/analyze", response_model=TorrentAnalyzeResponse)
async def analyze_magnet(
    data: MagnetImportRequest,
    user: User = Depends(require_auth),
):
    """
    Analyze a magnet link and return torrent metadata.
    Also searches for matching content in IMDb/TMDB.
    """
    from db.config import settings
    from scrapers.scraper_tasks import meta_fetcher

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fetching torrent metadata from P2P is disabled.",
        )

    # Parse magnet link
    info_hash, trackers = torrent.parse_magnet(data.magnet_link)
    if not info_hash:
        return TorrentAnalyzeResponse(
            status="error",
            error="Failed to parse magnet link. Invalid format.",
        )

    try:
        # Fetch torrent metadata
        torrent_data_list = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers, is_raise_error=True)

        if not torrent_data_list or not torrent_data_list[0]:
            return TorrentAnalyzeResponse(
                status="error",
                error="Failed to fetch torrent metadata from DHT network.",
            )

        torrent_data = torrent_data_list[0]

        # Convert size to readable format
        total_size = torrent_data.get("total_size", 0)
        if total_size > 0:
            from utils.parser import convert_bytes_to_readable

            size_readable = convert_bytes_to_readable(total_size)
        else:
            size_readable = "Unknown"

        # Search for matching content if title is available
        matches = []
        if torrent_data.get("title"):
            try:
                matches = await meta_fetcher.search_multiple_results(
                    title=torrent_data["title"],
                    year=torrent_data.get("year"),
                    media_type=data.meta_type,
                )
            except Exception:
                pass  # Ignore search errors

        return TorrentAnalyzeResponse(
            status="success",
            info_hash=info_hash.lower(),
            torrent_name=torrent_data.get("torrent_name"),
            total_size=total_size,
            total_size_readable=size_readable,
            file_count=len(torrent_data.get("file_data", [])),
            files=torrent_data.get("file_data", []),
            parsed_title=torrent_data.get("title"),
            year=torrent_data.get("year"),
            resolution=torrent_data.get("resolution"),
            quality=torrent_data.get("quality"),
            codec=torrent_data.get("codec"),
            audio=torrent_data.get("audio"),
            matches=matches,
        )

    except ExceptionGroup as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=str(e.exceptions[0]) if e.exceptions else "Unknown error",
        )
    except Exception as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=f"Failed to analyze magnet: {str(e)}",
        )


@router.post("/torrent/analyze", response_model=TorrentAnalyzeResponse)
async def analyze_torrent_file(
    torrent_file: UploadFile = File(...),
    meta_type: str = Form(...),
    user: User = Depends(require_auth),
):
    """
    Analyze a torrent file and return metadata.
    Also searches for matching content in IMDb/TMDB.
    """
    from scrapers.scraper_tasks import meta_fetcher

    if not torrent_file.filename or not torrent_file.filename.endswith(".torrent"):
        return TorrentAnalyzeResponse(
            status="error",
            error="Invalid file. Please upload a .torrent file.",
        )

    try:
        content = await torrent_file.read()

        if len(content) > settings.max_torrent_file_size:
            return TorrentAnalyzeResponse(
                status="error",
                error=f"Torrent file too large. Maximum size is {settings.max_torrent_file_size // (1024 * 1024)} MB.",
            )

        torrent_data = torrent.extract_torrent_metadata(content, is_raise_error=True)

        if not torrent_data:
            return TorrentAnalyzeResponse(
                status="error",
                error="Failed to parse torrent file.",
            )

        # Convert size to readable format
        total_size = torrent_data.get("total_size", 0)
        if total_size > 0:
            from utils.parser import convert_bytes_to_readable

            size_readable = convert_bytes_to_readable(total_size)
        else:
            size_readable = "Unknown"

        # Search for matching content
        matches = []
        if torrent_data.get("title"):
            try:
                matches = await meta_fetcher.search_multiple_results(
                    title=torrent_data["title"],
                    year=torrent_data.get("year"),
                    media_type=meta_type,
                )
            except Exception:
                pass

        return TorrentAnalyzeResponse(
            status="success",
            info_hash=torrent_data.get("info_hash", "").lower(),
            torrent_name=torrent_data.get("torrent_name"),
            total_size=total_size,
            total_size_readable=size_readable,
            file_count=len(torrent_data.get("file_data", [])),
            files=torrent_data.get("file_data", []),
            parsed_title=torrent_data.get("title"),
            year=torrent_data.get("year"),
            resolution=torrent_data.get("resolution"),
            quality=torrent_data.get("quality"),
            codec=torrent_data.get("codec"),
            audio=torrent_data.get("audio"),
            matches=matches,
        )

    except ValueError as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=str(e),
        )
    except Exception as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=f"Failed to analyze torrent: {str(e)}",
        )


@router.post("/magnet", response_model=ImportResponse)
async def import_magnet(
    meta_type: str = Form(...),
    magnet_link: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    poster: str = Form(None),
    background: str = Form(None),
    catalogs: str = Form(None),
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    audio: str = Form(None),
    hdr: str = Form(None),
    file_data: str = Form(None),  # JSON stringified array
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    anonymous_display_name: str | None = Form(None),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import a magnet link.
    For active users, imports are auto-approved and processed immediately.
    For deactivated users, imports require manual review.

    Set is_anonymous=True to contribute anonymously (uploader shows as "Anonymous").
    Set is_anonymous=False to show your username.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fetching torrent metadata from P2P is disabled.",
        )

    # Parse magnet link
    info_hash, trackers = torrent.parse_magnet(magnet_link)
    if not info_hash:
        return ImportResponse(
            status="error",
            message="Failed to parse magnet link.",
        )

    info_hash = info_hash.lower()

    # Check if torrent already exists
    if not force_import:
        existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
        existing_torrent = existing.first()
        if existing_torrent:
            return ImportResponse(
                status="warning",
                message=f"⚠️ Torrent {info_hash} already exists in the database. "
                f"Thank you for trying to contribute ✨. If the torrent is not visible, please contact support with the Torrent InfoHash.",
            )

    try:
        # Fetch basic metadata from P2P
        torrent_data_list = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers, is_raise_error=True)

        if not torrent_data_list or not torrent_data_list[0]:
            return ImportResponse(
                status="error",
                message="Failed to fetch torrent metadata from P2P network.",
            )

        torrent_data = torrent_data_list[0]

        # Parse file_data if provided, otherwise use from torrent
        parsed_file_data = []
        if file_data:
            try:
                parsed_file_data = json.loads(file_data)
            except json.JSONDecodeError:
                logger.warning("Failed to parse file_data JSON")

        if not parsed_file_data and torrent_data.get("file_data"):
            parsed_file_data = torrent_data.get("file_data", [])

        # Build contribution data with all fields
        contribution_data = {
            "info_hash": info_hash,
            "magnet_link": magnet_link,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title or torrent_data.get("title"),
            "name": torrent_data.get("torrent_name"),
            "total_size": torrent_data.get("total_size"),
            "catalogs": [c.strip() for c in catalogs.split(",") if c.strip()] if catalogs else [],
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "resolution": resolution or torrent_data.get("resolution"),
            "quality": quality or torrent_data.get("quality"),
            "codec": codec or torrent_data.get("codec"),
            "audio": [a.strip() for a in audio.split(",") if a.strip()] if audio else [],
            "hdr": [h.strip() for h in hdr.split(",") if h.strip()] if hdr else [],
            "file_data": parsed_file_data,
            "file_count": len(parsed_file_data) or len(torrent_data.get("file_data", [])) or 1,
            "poster": poster,
            "background": background,
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
        }

        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=None if resolved_is_anonymous else user.id,
            contribution_type="torrent",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes=(
                "Auto-approved: Privileged reviewer"
                if is_privileged_reviewer
                else ("Auto-approved: Active user content import" if should_auto_approve else None)
            ),
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_torrent_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process torrent import: {e}")
                # Still keep the contribution but mark as needing attention
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return ImportResponse(
                status="success",
                message="Torrent imported successfully!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return ImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return ImportResponse(
                status="success",
                message="Magnet link submitted for review. Thank you for your contribution!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import magnet: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import magnet: {str(e)}",
        )


@router.post("/torrent", response_model=ImportResponse)
async def import_torrent_file(
    torrent_file: UploadFile = File(...),
    meta_type: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    poster: str = Form(None),
    background: str = Form(None),
    catalogs: str = Form(None),
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    audio: str = Form(None),
    hdr: str = Form(None),
    file_data: str = Form(None),  # JSON stringified array
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    anonymous_display_name: str | None = Form(None),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import a torrent file.
    For active users, imports are auto-approved and processed immediately.
    For deactivated users, imports require manual review.

    Set is_anonymous=True to contribute anonymously (uploader shows as "Anonymous").
    Set is_anonymous=False to show your username.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)

    if not torrent_file.filename or not torrent_file.filename.endswith(".torrent"):
        return ImportResponse(
            status="error",
            message="Invalid file. Please upload a .torrent file.",
        )

    try:
        content = await torrent_file.read()

        if len(content) > settings.max_torrent_file_size:
            return ImportResponse(
                status="error",
                message=f"Torrent file too large. Maximum size is {settings.max_torrent_file_size // (1024 * 1024)} MB.",
            )

        torrent_data = torrent.extract_torrent_metadata(content, is_raise_error=True)

        if not torrent_data:
            return ImportResponse(
                status="error",
                message="Failed to parse torrent file.",
            )

        info_hash = torrent_data.get("info_hash", "").lower()

        # Check if torrent already exists
        if not force_import:
            existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
            existing_torrent = existing.first()
            if existing_torrent:
                return ImportResponse(
                    status="warning",
                    message=f"⚠️ Torrent {info_hash} already exists in the database. "
                    f"Thank you for trying to contribute ✨. If the torrent is not visible, please contact support with the Torrent InfoHash.",
                )

        # Parse file_data if provided, otherwise use from torrent
        parsed_file_data = []
        if file_data:
            try:
                parsed_file_data = json.loads(file_data)
            except json.JSONDecodeError:
                logger.warning("Failed to parse file_data JSON")

        if not parsed_file_data and torrent_data.get("file_data"):
            parsed_file_data = torrent_data.get("file_data", [])

        # Build contribution data with all fields
        contribution_data = {
            "info_hash": info_hash,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title or torrent_data.get("title"),
            "name": torrent_data.get("torrent_name"),
            "total_size": torrent_data.get("total_size"),
            "catalogs": [c.strip() for c in catalogs.split(",") if c.strip()] if catalogs else [],
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "resolution": resolution or torrent_data.get("resolution"),
            "quality": quality or torrent_data.get("quality"),
            "codec": codec or torrent_data.get("codec"),
            "audio": [a.strip() for a in audio.split(",") if a.strip()] if audio else [],
            "hdr": [h.strip() for h in hdr.split(",") if h.strip()] if hdr else [],
            "file_data": parsed_file_data,
            "file_count": len(parsed_file_data) or len(torrent_data.get("file_data", [])) or 1,
            "poster": poster,
            "background": background,
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
        }

        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=None if resolved_is_anonymous else user.id,
            contribution_type="torrent",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes=(
                "Auto-approved: Privileged reviewer"
                if is_privileged_reviewer
                else ("Auto-approved: Active user content import" if should_auto_approve else None)
            ),
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_torrent_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process torrent import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return ImportResponse(
                status="success",
                message="Torrent imported successfully!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return ImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return ImportResponse(
                status="success",
                message="Torrent file submitted for review. Thank you for your contribution!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except ValueError as e:
        return ImportResponse(
            status="error",
            message=str(e),
        )
    except Exception as e:
        logger.exception(f"Failed to import torrent: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import torrent: {str(e)}",
        )
