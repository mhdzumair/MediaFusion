"""
NZB Import API endpoints for importing NZB files and URLs.
"""

import json
import logging
from datetime import datetime
from typing import Any

import httpx
import PTT
import pytz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.torrent_import import fetch_and_create_media_from_external
from api.routers.user.auth import require_auth
from db.config import settings
from db.crud.media import get_media_by_external_id
from db.crud.reference import get_or_create_language
from db.database import get_async_session
from db.enums import ContributionStatus, MediaType
from db.models import Contribution, Media, Stream, StreamFile, StreamMediaLink, User
from db.models.streams import (
    FileMediaLink,
    StreamLanguageLink,
    StreamType,
    UsenetStream,
)
from scrapers.scraper_tasks import meta_fetcher
from utils.nzb import generate_nzb_hash, parse_nzb_content
from utils.nzb_storage import get_nzb_storage, verify_nzb_signature
from utils.parser import convert_bytes_to_readable
from utils.zyclops import submit_nzb_to_zyclops

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# Pydantic Schemas
# ============================================


class NZBAnalyzeResponse(BaseModel):
    """Response from NZB analysis."""

    status: str
    nzb_guid: str | None = None
    nzb_title: str | None = None
    total_size: int | None = None
    total_size_readable: str | None = None
    file_count: int | None = None
    files: list[dict[str, Any]] | None = None
    group_name: str | None = None
    parsed_title: str | None = None
    year: int | None = None
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    matches: list[dict[str, Any]] | None = None
    error: str | None = None


class NZBImportResponse(BaseModel):
    """Generic NZB import response."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


class NZBURLAnalyzeRequest(BaseModel):
    """Request schema for analyzing an NZB via URL."""

    nzb_url: str
    meta_type: str = Field(..., pattern="^(movie|series)$")


class NZBURLImportRequest(BaseModel):
    """Request schema for importing an NZB via URL."""

    nzb_url: str
    meta_type: str = Field(..., pattern="^(movie|series)$")
    meta_id: str | None = None  # IMDb ID if known
    title: str | None = None
    indexer: str | None = None
    is_anonymous: bool | None = None  # None means use user's preference


# ============================================
# Shared Analysis Logic
# ============================================


async def _analyze_nzb_content(
    content: bytes,
    meta_type: str,
    fallback_title: str = "Unknown",
) -> NZBAnalyzeResponse:
    """Shared logic for analyzing NZB content from any source (file or URL).

    Args:
        content: Raw NZB file bytes
        meta_type: "movie" or "series"
        fallback_title: Title to use if NZB metadata has none

    Returns:
        NZBAnalyzeResponse with parsed metadata and matches
    """
    nzb_data = parse_nzb_content(content)

    if not nzb_data:
        return NZBAnalyzeResponse(
            status="error",
            error="Failed to parse NZB content.",
        )

    # Forward NZB to Zyclops health API (fire-and-forget)
    submit_nzb_to_zyclops(
        content,
        name=nzb_data.title or fallback_title,
        pub_date=nzb_data.date,
        password=nzb_data.password,
    )

    nzb_guid = nzb_data.nzb_hash or generate_nzb_hash(content)

    total_size = nzb_data.total_size
    size_readable = convert_bytes_to_readable(total_size) if total_size > 0 else "Unknown"

    nzb_title = nzb_data.title or fallback_title
    parsed = PTT.parse_title(nzb_title)

    matches = []
    search_title = parsed.get("title", nzb_title)
    if search_title:
        try:
            matches = await meta_fetcher.search_multiple_results(
                title=search_title,
                year=parsed.get("year"),
                media_type=meta_type,
            )
        except Exception:
            pass

    files_data = [{"filename": f.filename, "size": f.size, "index": i} for i, f in enumerate(nzb_data.files)]

    return NZBAnalyzeResponse(
        status="success",
        nzb_guid=nzb_guid,
        nzb_title=nzb_title,
        total_size=total_size,
        total_size_readable=size_readable,
        file_count=nzb_data.files_count,
        files=files_data,
        group_name=nzb_data.primary_group,
        parsed_title=parsed.get("title"),
        year=parsed.get("year"),
        resolution=parsed.get("resolution"),
        quality=parsed.get("quality"),
        codec=parsed.get("codec"),
        matches=matches,
    )


# ============================================
# NZB Import Processing
# ============================================


async def process_nzb_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User,
) -> dict:
    """
    Process an NZB import - creates the actual UsenetStream record in the database.

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """
    nzb_guid = contribution_data.get("nzb_guid", "")
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "Unknown")
    name = contribution_data.get("name", title)
    total_size = contribution_data.get("total_size", 0)
    indexer = contribution_data.get("indexer", "User Import")

    # Anonymous contribution handling
    is_anonymous = contribution_data.get("is_anonymous", False)

    if not nzb_guid:
        raise ValueError("Missing nzb_guid in contribution data")

    # Check if NZB already exists
    existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
    if existing.first():
        return {"status": "exists", "message": "NZB already exists in database"}

    # Get or create media metadata
    media = None
    if meta_id:
        media = await get_media_by_external_id(session, meta_id)

    if not media:
        # Try to fetch full metadata from external provider and create
        try:
            media = await fetch_and_create_media_from_external(
                session,
                meta_id or f"nzb_{nzb_guid[:8]}",
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

    # Determine uploader name and user_id based on anonymous preference
    if is_anonymous:
        uploader_name = "Anonymous"
        uploader_user_id = None
    else:
        uploader_name = user.username or user.email or f"User #{user.id}"
        uploader_user_id = user.id

    # Create Stream base record
    stream = Stream(
        stream_type=StreamType.USENET,
        name=name,
        source=indexer,
        resolution=contribution_data.get("resolution"),
        codec=contribution_data.get("codec"),
        quality=contribution_data.get("quality"),
        uploader=uploader_name,
        uploader_user_id=uploader_user_id,
        total_size=total_size,
    )
    session.add(stream)
    await session.flush()

    # Create UsenetStream record (nzb_url only, no nzb_content)
    usenet_stream = UsenetStream(
        stream_id=stream.id,
        nzb_guid=nzb_guid,
        nzb_url=contribution_data.get("nzb_url"),
        size=total_size,
        indexer=indexer,
        group_name=contribution_data.get("group_name"),
        uploader=contribution_data.get("poster"),
        files_count=contribution_data.get("file_count", 1),
        posted_at=datetime.now(pytz.UTC),
        is_passworded=contribution_data.get("is_passworded", False),
    )
    session.add(usenet_stream)

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

    for idx, file_info in enumerate(file_data):
        stream_file = StreamFile(
            stream_id=stream.id,
            file_index=file_info.get("index", idx),
            filename=file_info.get("filename", ""),
            size=file_info.get("size", 0),
        )
        session.add(stream_file)
        await session.flush()

        # Link file to media with season/episode info if present
        file_media_link = FileMediaLink(
            file_id=stream_file.id,
            media_id=media.id,
            season_number=file_info.get("season_number"),
            episode_number=file_info.get("episode_number"),
        )
        session.add(file_media_link)

    # Update media stream count
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = datetime.now(pytz.UTC)

    await session.flush()

    logger.info(f"Successfully imported NZB {nzb_guid} for media_id={media.id}")

    return {
        "status": "success",
        "stream_id": stream.id,
        "media_id": media.id,
        "nzb_guid": nzb_guid,
    }


# ============================================
# API Endpoints
# ============================================


@router.post("/nzb/analyze/file", response_model=NZBAnalyzeResponse)
async def analyze_nzb_file(
    nzb_file: UploadFile = File(...),
    meta_type: str = Form(...),
    user: User = Depends(require_auth),
):
    """
    Analyze an uploaded NZB file and return metadata.
    Requires enable_nzb_file_import to be enabled in instance config.
    """
    if not settings.enable_nzb_file_import:
        raise HTTPException(
            status_code=403,
            detail="NZB file upload is not enabled on this instance. Use NZB URL import instead.",
        )

    if not nzb_file.filename or not nzb_file.filename.endswith(".nzb"):
        return NZBAnalyzeResponse(
            status="error",
            error="Invalid file. Please upload a .nzb file.",
        )

    try:
        content = await nzb_file.read()

        if len(content) > settings.max_nzb_file_size:
            return NZBAnalyzeResponse(
                status="error",
                error=f"NZB file too large. Maximum size is {settings.max_nzb_file_size // (1024 * 1024)} MB.",
            )

        return await _analyze_nzb_content(
            content,
            meta_type,
            fallback_title=nzb_file.filename.replace(".nzb", ""),
        )

    except Exception as e:
        logger.exception(f"Failed to analyze NZB file: {e}")
        return NZBAnalyzeResponse(
            status="error",
            error=f"Failed to analyze NZB: {str(e)}",
        )


@router.post("/nzb/analyze/url", response_model=NZBAnalyzeResponse)
async def analyze_nzb_url(
    data: NZBURLAnalyzeRequest,
    user: User = Depends(require_auth),
):
    """
    Analyze an NZB from a URL and return metadata.
    Downloads the NZB, parses it, and searches for matching content.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(data.nzb_url, timeout=30.0)
            response.raise_for_status()
            content = response.content

        return await _analyze_nzb_content(content, data.meta_type)

    except httpx.HTTPError as e:
        return NZBAnalyzeResponse(
            status="error",
            error=f"Failed to download NZB from URL: {str(e)}",
        )
    except Exception as e:
        logger.exception(f"Failed to analyze NZB URL: {e}")
        return NZBAnalyzeResponse(
            status="error",
            error=f"Failed to analyze NZB: {str(e)}",
        )


@router.post("/nzb", response_model=NZBImportResponse)
async def import_nzb_file(
    nzb_file: UploadFile = File(...),
    meta_type: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    indexer: str = Form("User Import"),
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    file_data: str = Form(None),  # JSON stringified array
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import an NZB file.
    Requires enable_nzb_file_import to be enabled in instance config.
    The NZB file is stored via the configured storage backend (local or S3)
    and only the URL is persisted in the database.

    For active users, imports are auto-approved and processed immediately.
    For deactivated users, imports require manual review.
    """
    if not settings.enable_nzb_file_import:
        raise HTTPException(
            status_code=403,
            detail="NZB file upload is not enabled on this instance. Use NZB URL import instead.",
        )

    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously

    if not nzb_file.filename or not nzb_file.filename.endswith(".nzb"):
        return NZBImportResponse(
            status="error",
            message="Invalid file. Please upload a .nzb file.",
        )

    try:
        content = await nzb_file.read()

        if len(content) > settings.max_nzb_file_size:
            return NZBImportResponse(
                status="error",
                message=f"NZB file too large. Maximum size is {settings.max_nzb_file_size // (1024 * 1024)} MB.",
            )

        nzb_data = parse_nzb_content(content)

        if not nzb_data:
            return NZBImportResponse(
                status="error",
                message="Failed to parse NZB file.",
            )

        # Generate unique GUID from content hash
        nzb_guid = nzb_data.nzb_hash or generate_nzb_hash(content)

        # Check if NZB already exists
        if not force_import:
            existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
            if existing.first():
                return NZBImportResponse(
                    status="warning",
                    message=f"NZB {nzb_guid[:16]}... already exists in the database.",
                )

        # Store NZB file (gzip-compressed) to configured storage backend
        storage = get_nzb_storage()
        await storage.store(nzb_guid, content)

        # Parse title with PTT for quality info
        nzb_title = nzb_data.title or nzb_file.filename.replace(".nzb", "")

        # Forward NZB to Zyclops health API (fire-and-forget)
        submit_nzb_to_zyclops(
            content,
            name=nzb_title,
            pub_date=nzb_data.date,
            password=nzb_data.password,
        )
        parsed = PTT.parse_title(nzb_title)

        # Parse file_data if provided
        parsed_file_data = []
        if file_data:
            try:
                parsed_file_data = json.loads(file_data)
            except json.JSONDecodeError:
                logger.warning("Failed to parse file_data JSON")

        if not parsed_file_data and nzb_data.files:
            parsed_file_data = [
                {"filename": f.filename, "size": f.size, "index": i} for i, f in enumerate(nzb_data.files)
            ]

        # Build contribution data — nzb_url is None for file uploads since
        # the file is in our storage and download URLs are generated on-the-fly.
        contribution_data = {
            "nzb_guid": nzb_guid,
            "nzb_url": None,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": title or parsed.get("title") or nzb_title,
            "name": nzb_title,
            "total_size": nzb_data.total_size,
            "indexer": indexer,
            "group_name": nzb_data.primary_group,
            "poster": nzb_data.poster,
            "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] if languages else [],
            "resolution": resolution or parsed.get("resolution"),
            "quality": quality or parsed.get("quality"),
            "codec": codec or parsed.get("codec"),
            "file_data": parsed_file_data,
            "file_count": len(parsed_file_data) or nzb_data.files_count or 1,
            "is_anonymous": resolved_is_anonymous,
            "is_passworded": nzb_data.is_passworded,
        }

        # Auto-approve for active users
        should_auto_approve = user.is_active
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=user.id,
            contribution_type="nzb",
            target_id=meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes="Auto-approved: Active user NZB import" if should_auto_approve else None,
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_nzb_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process NZB import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return NZBImportResponse(
                status="success",
                message="NZB imported successfully!",
                import_id=contribution.id,
                details={
                    "nzb_guid": nzb_guid,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return NZBImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=contribution.id,
                details={
                    "nzb_guid": nzb_guid,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return NZBImportResponse(
                status="success",
                message="NZB file submitted for review. Thank you for your contribution!",
                import_id=contribution.id,
                details={
                    "nzb_guid": nzb_guid,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import NZB: {e}")
        return NZBImportResponse(
            status="error",
            message=f"Failed to import NZB: {str(e)}",
        )


@router.post("/nzb/url", response_model=NZBImportResponse)
async def import_nzb_url(
    data: NZBURLImportRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Import an NZB via URL.
    Downloads the NZB file and processes it.
    The nzb_url is stored directly (no file storage needed).

    Set is_anonymous=True to contribute anonymously.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = data.is_anonymous if data.is_anonymous is not None else user.contribute_anonymously

    try:
        # Download the NZB file
        async with httpx.AsyncClient() as client:
            response = await client.get(data.nzb_url, timeout=30.0)
            response.raise_for_status()
            content = response.content

        nzb_data = parse_nzb_content(content)

        if not nzb_data:
            return NZBImportResponse(
                status="error",
                message="Failed to parse NZB from URL.",
            )

        # Generate unique GUID from content hash
        nzb_guid = nzb_data.nzb_hash or generate_nzb_hash(content)

        # Check if NZB already exists
        existing = await session.exec(select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid))
        if existing.first():
            return NZBImportResponse(
                status="warning",
                message=f"NZB {nzb_guid[:16]}... already exists in the database.",
            )

        # Parse title with PTT
        nzb_title = nzb_data.title or "Unknown"
        parsed = PTT.parse_title(nzb_title)

        # Forward NZB to Zyclops health API (fire-and-forget)
        submit_nzb_to_zyclops(
            content,
            name=nzb_title,
            pub_date=nzb_data.date,
            password=nzb_data.password,
        )

        # Convert NZBFile objects to dicts
        files_data = [{"filename": f.filename, "size": f.size, "index": i} for i, f in enumerate(nzb_data.files)]

        # Build contribution data (URL only, no raw content)
        contribution_data = {
            "nzb_guid": nzb_guid,
            "nzb_url": data.nzb_url,
            "meta_type": data.meta_type,
            "meta_id": data.meta_id,
            "title": data.title or parsed.get("title") or nzb_title,
            "name": nzb_title,
            "total_size": nzb_data.total_size,
            "indexer": data.indexer or "URL Import",
            "group_name": nzb_data.primary_group,
            "poster": nzb_data.poster,
            "resolution": parsed.get("resolution"),
            "quality": parsed.get("quality"),
            "codec": parsed.get("codec"),
            "file_data": files_data,
            "file_count": nzb_data.files_count or 1,
            "is_anonymous": resolved_is_anonymous,
            "is_passworded": nzb_data.is_passworded,
        }

        # Auto-approve for active users
        should_auto_approve = user.is_active
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

        contribution = Contribution(
            user_id=user.id,
            contribution_type="nzb",
            target_id=data.meta_id,
            data=contribution_data,
            status=initial_status,
            reviewed_by="auto" if should_auto_approve else None,
            reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
            review_notes="Auto-approved: Active user NZB URL import" if should_auto_approve else None,
        )

        session.add(contribution)
        await session.flush()

        # If auto-approved, process the import immediately
        import_result = None
        if should_auto_approve:
            try:
                import_result = await process_nzb_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process NZB import: {e}")
                contribution.review_notes = f"Auto-approved but import failed: {str(e)}"

        await session.commit()
        await session.refresh(contribution)

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return NZBImportResponse(
                status="success",
                message="NZB imported successfully!",
                import_id=contribution.id,
                details={
                    "nzb_guid": nzb_guid,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        else:
            return NZBImportResponse(
                status="success",
                message="NZB URL submitted for review.",
                import_id=contribution.id,
                details={
                    "nzb_guid": nzb_guid,
                    "title": contribution_data.get("title"),
                    "auto_approved": should_auto_approve,
                },
            )

    except httpx.HTTPError as e:
        return NZBImportResponse(
            status="error",
            message=f"Failed to download NZB from URL: {str(e)}",
        )
    except Exception as e:
        logger.exception(f"Failed to import NZB from URL: {e}")
        return NZBImportResponse(
            status="error",
            message=f"Failed to import NZB: {str(e)}",
        )


# ============================================
# NZB File Download (signed, time-limited)
# ============================================


@router.get("/nzb/{guid}/download")
async def download_nzb_file(
    guid: str,
    expires: int | None = None,
    sig: str | None = None,
):
    """
    Serve an NZB file from the configured storage backend.
    Requires a valid HMAC-signed URL with expiry timestamp.
    """
    if expires is None or sig is None:
        raise HTTPException(status_code=403, detail="Missing signature parameters.")

    if not verify_nzb_signature(guid, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired download link.")

    storage = get_nzb_storage()
    content = await storage.retrieve(guid)
    if content is None:
        raise HTTPException(status_code=404, detail="NZB file not found.")

    return Response(
        content=content,
        media_type="application/x-nzb",
        headers={"Content-Disposition": f'attachment; filename="{guid}.nzb"'},
    )
