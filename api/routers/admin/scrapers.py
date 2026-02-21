"""
Admin API endpoints for scraper management.
Migrated from scrapers/routes.py - admin-only functionality with proper auth.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db import crud, schemas
from db.config import settings
from db.crud.scraper_helpers import (
    delete_metadata,
    delete_torrent_stream,
    get_metadata_by_id,
    get_movie_data_by_id,
    get_series_data_by_id,
    migrate_torrent_streams,
    update_meta_stream,
    update_metadata,
)
from db.database import get_async_session
from db.enums import UserRole
from db.models import FileMediaLink, Media, MediaExternalID, StreamFile, StreamMediaLink, User
from db.redis_database import REDIS_ASYNC_CLIENT
from mediafusion_scrapy.task import run_spider
from scrapers.scraper_tasks import meta_fetcher
from scrapers.tv import add_tv_metadata
from utils import const
from utils.telegram_bot import telegram_notifier
from utils.validation_helper import validate_image_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/scrapers", tags=["Admin Scrapers"])


# ============================================
# Pydantic Schemas
# ============================================


class RunScraperRequest(BaseModel):
    """Request to run a scraper spider."""

    spider_name: str
    pages: int = Field(default=1, ge=1, le=100)
    start_page: int = Field(default=1, ge=1)
    search_keyword: str | None = None
    scrape_all: bool = False
    scrap_catalog_id: str | None = None
    total_pages: int | None = None


class BlockTorrentRequest(BaseModel):
    """Request to block a torrent."""

    info_hash: str
    reason: str | None = None


class ScraperStatusResponse(BaseModel):
    """Response for scraper status."""

    spider_name: str
    status: str
    last_run: str | None = None


class MigrateIdRequest(BaseModel):
    """Request to migrate MediaFusion ID to IMDb ID."""

    mediafusion_id: str = Field(..., description="The MediaFusion ID to migrate from")
    imdb_id: str = Field(..., description="The IMDb ID to migrate to (e.g., tt1234567)")
    media_type: Literal["movie", "series"] = Field(..., description="Type of media")


class UpdateImagesRequest(BaseModel):
    """Request to update media images."""

    meta_id: str = Field(..., description="IMDb ID or MediaFusion ID")
    poster: str | None = Field(None, description="New poster URL")
    background: str | None = Field(None, description="New background URL")
    logo: str | None = Field(None, description="New logo URL")


class DeleteTorrentRequest(BaseModel):
    """Request to delete a torrent."""

    info_hash: str
    reason: str | None = None


# ============================================
# Scraper Management Endpoints
# ============================================


@router.get("/spiders")
async def list_spiders(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List all available scrapy spiders (Admin only)."""
    return {
        "spiders": [{"id": spider_id, "name": spider_name} for spider_id, spider_name in const.SCRAPY_SPIDERS.items()]
    }


@router.post("/run")
async def run_scraper(
    request: RunScraperRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Run a scrapy spider (Admin only)."""
    if request.spider_name not in const.SCRAPY_SPIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown spider: {request.spider_name}. Available: {list(const.SCRAPY_SPIDERS.keys())}",
        )

    # Send task to dramatiq
    run_spider.send(
        request.spider_name,
        pages=request.pages,
        start_page=request.start_page,
        search_keyword=request.search_keyword,
        scrape_all=str(request.scrape_all),
        scrap_catalog_id=request.scrap_catalog_id,
        total_pages=request.total_pages,
    )

    logger.info(f"Admin scheduled scraper: {request.spider_name}")
    return {
        "status": "success",
        "message": f"Scraping {request.spider_name} task has been scheduled.",
    }


@router.post("/block-torrent")
async def block_torrent(
    request: BlockTorrentRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Block a torrent by info_hash (Admin only).

    Marks the Stream as blocked.
    """
    # Find torrent by info_hash
    torrent = await crud.streams.get_torrent_by_info_hash(session, request.info_hash)
    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent not found: {request.info_hash}",
        )

    # Get base stream and block it
    base_stream = torrent.stream
    base_stream.is_blocked = True
    session.add(base_stream)
    await session.commit()

    logger.info(f"Admin blocked torrent: {request.info_hash}, reason: {request.reason}")
    return {
        "status": "success",
        "message": f"Torrent {request.info_hash} has been blocked.",
    }


@router.post("/unblock-torrent")
async def unblock_torrent(
    info_hash: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Unblock a torrent by info_hash (Admin only)."""
    torrent = await crud.streams.get_torrent_by_info_hash(session, info_hash)
    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent not found: {info_hash}",
        )

    base_stream = torrent.stream
    base_stream.is_blocked = False
    session.add(base_stream)
    await session.commit()

    logger.info(f"Admin unblocked torrent: {info_hash}")
    return {
        "status": "success",
        "message": f"Torrent {info_hash} has been unblocked.",
    }


@router.get("/catalogs")
async def get_catalog_data(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get catalog configuration data (Admin only)."""
    return {
        "catalog_data": const.CATALOG_DATA,
        "supported_series_catalogs": const.USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS,
        "supported_movie_catalogs": const.USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS,
        "supported_languages": sorted(const.SUPPORTED_LANGUAGES - {None}),
    }


@router.get("/status")
async def get_scraper_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get status of all scrapers (Admin only)."""
    statuses = []
    for spider_id, spider_name in const.SCRAPY_SPIDERS.items():
        # Get last run info from cache/db
        last_run_info = await crud.scraper_helpers.fetch_last_run(spider_id, spider_name)
        statuses.append(
            {
                "spider_id": spider_id,
                "spider_name": spider_name,
                "last_run": last_run_info.get("last_run"),
                "time_since_last_run": last_run_info.get("time_since_last_run"),
            }
        )

    return {"scrapers": statuses}


# ============================================
# Metadata Management Endpoints
# ============================================


@router.post("/migrate-id")
async def migrate_metadata_id(
    request: MigrateIdRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Migrate a MediaFusion ID to an IMDb ID (Admin only).

    This will:
    1. Fetch metadata for the IMDb ID if it doesn't exist
    2. Move all torrent streams from the old ID to the new ID
    3. Delete the old metadata
    4. Update stream statistics for the new ID
    """
    # First check if the IMDb metadata exists
    if request.media_type == "series":
        target_metadata = await get_series_data_by_id(session, request.imdb_id)
    else:
        target_metadata = await get_movie_data_by_id(session, request.imdb_id)

    # If IMDb metadata doesn't exist, fetch and create it
    if not target_metadata:
        logger.info(f"Fetching metadata for {request.imdb_id} from IMDB/TMDB")
        fetched_metadata = await meta_fetcher.get_metadata(request.imdb_id, media_type=request.media_type)
        if not fetched_metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not fetch metadata for {request.imdb_id} from IMDB/TMDB.",
            )

        # Create the metadata in database
        created_metadata = await crud.scraper_helpers.get_or_create_metadata(
            session,
            fetched_metadata,
            request.media_type,
            is_search_imdb_title=False,
            is_imdb_only=True,
        )
        if not created_metadata:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create metadata for {request.imdb_id}.",
            )
        logger.info(f"Created metadata for {request.imdb_id}")

    # Get old metadata for notification before deleting
    old_metadata = await get_metadata_by_id(session, request.mediafusion_id)
    if not old_metadata:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Metadata with ID {request.mediafusion_id} not found.",
        )
    old_title = old_metadata.title

    # Perform the migration - update torrent streams to point to new ID
    migrated_count = await migrate_torrent_streams(session, request.mediafusion_id, request.imdb_id)

    # Delete old metadata
    await delete_metadata(session, request.mediafusion_id)

    # Update stream stats for new ID
    await update_meta_stream(session, request.imdb_id, request.media_type)

    await session.commit()

    # Send notification
    if settings.telegram_bot_token:
        new_poster = f"{settings.poster_host_url}/poster/{request.media_type}/{request.imdb_id}.jpg"
        await telegram_notifier.send_migration_notification(
            old_id=request.mediafusion_id,
            new_id=request.imdb_id,
            title=old_title,
            meta_type=request.media_type,
            poster=new_poster,
        )

    logger.info(f"Admin migrated {request.mediafusion_id} to {request.imdb_id}, moved {migrated_count} streams")

    return {
        "status": "success",
        "message": f"Successfully migrated {request.mediafusion_id} to {request.imdb_id}.",
        "streams_migrated": migrated_count,
    }


@router.post("/update-images")
async def update_media_images(
    request: UpdateImagesRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update poster, background, and/or logo for existing content (Admin only).

    At least one image URL must be provided.
    """
    # Validate that at least one image URL is provided
    if not any([request.poster, request.background, request.logo]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image URL (poster, background, or logo) must be provided",
        )

    # Get metadata to validate it exists and get current values
    metadata = await get_metadata_by_id(session, request.meta_id)
    if not metadata:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Content with ID {request.meta_id} not found",
        )

    old_poster = metadata.poster
    old_background = metadata.background
    old_logo = metadata.logo
    meta_type = metadata.type.value if metadata.type else "movie"

    # Build update fields and validate image URLs
    update_fields = {}
    if request.poster:
        if not await validate_image_url(request.poster):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid poster image URL, unable to fetch image",
            )
        update_fields["poster"] = request.poster
        update_fields["is_poster_working"] = True

    if request.background:
        if not await validate_image_url(request.background):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid background image URL, unable to fetch image",
            )
        update_fields["background"] = request.background

    if request.logo:
        if not await validate_image_url(request.logo):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid logo image URL, unable to fetch image",
            )
        update_fields["logo"] = request.logo

    # Update metadata
    await update_metadata(session, request.meta_id, update_fields)
    await session.commit()

    # Send Telegram notification
    if settings.telegram_bot_token:
        await telegram_notifier.send_image_update_notification(
            meta_id=request.meta_id,
            title=metadata.title,
            meta_type=meta_type,
            poster=f"{settings.poster_host_url}/poster/{meta_type}/{request.meta_id}.jpg",
            old_poster=old_poster,
            new_poster=request.poster,
            old_background=old_background,
            new_background=request.background,
            old_logo=old_logo,
            new_logo=request.logo,
        )

    # Cleanup redis cache
    cache_keys = [
        f"{meta_type}_{request.meta_id}.jpg",
        f"{meta_type}_data:{request.meta_id}",
    ]
    await REDIS_ASYNC_CLIENT.delete(*cache_keys)

    logger.info(f"Admin updated images for {request.meta_id}")

    return {
        "status": "success",
        "message": f"Successfully updated images for {request.meta_id}",
    }


@router.get("/update-imdb/{meta_id}")
async def refresh_imdb_data(
    meta_id: str,
    media_type: Literal["movie", "series"],
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Refresh IMDb/TMDB data for existing content (Admin only).

    This fetches fresh metadata from external providers and updates the database.
    """
    # Validate content exists
    if media_type == "series":
        series = await get_series_data_by_id(session, meta_id)
        if not series:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Series with ID {meta_id} not found.",
            )
    else:
        movie = await get_movie_data_by_id(session, meta_id)
        if not movie:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Movie with ID {meta_id} not found.",
            )

    if meta_id.startswith("tt"):
        # Fetch fresh data from IMDB/TMDB and update
        success = await crud.update_single_imdb_metadata(session, meta_id, media_type)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update IMDb data for {meta_id}.",
            )
    else:
        await update_meta_stream(session, meta_id, media_type)

    await session.commit()

    logger.info(f"Admin refreshed IMDb data for {meta_id}")

    return {
        "status": "success",
        "message": f"Successfully updated IMDb data for {meta_id}.",
    }


@router.delete("/torrent/{info_hash}")
async def delete_torrent(
    info_hash: str,
    reason: str | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a torrent by info_hash (Admin only).

    This completely removes the torrent from the database.
    """
    # Find torrent by info_hash
    torrent = await crud.streams.get_torrent_by_info_hash(session, info_hash)
    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Torrent not found: {info_hash}",
        )

    # Get metadata for notification
    metadata = None
    media_id = None

    stream_media_result = await session.exec(
        select(StreamMediaLink.media_id).where(StreamMediaLink.stream_id == torrent.stream_id).limit(1)
    )
    media_id = stream_media_result.first()

    # Fallback for streams linked at file level only.
    if media_id is None:
        file_media_result = await session.exec(
            select(FileMediaLink.media_id)
            .join(StreamFile, StreamFile.id == FileMediaLink.file_id)
            .where(StreamFile.stream_id == torrent.stream_id)
            .limit(1)
        )
        media_id = file_media_result.first()

    if media_id is not None:
        metadata = await session.get(Media, media_id)

    try:
        await delete_torrent_stream(session, info_hash)
        await session.commit()

        # Send Telegram notification
        if settings.telegram_bot_token and metadata:
            meta_type = metadata.type.value if metadata.type else "movie"
            # Get external ID for the media
            imdb_ext_result = await session.exec(
                select(MediaExternalID.external_id)
                .where(
                    MediaExternalID.media_id == metadata.id,
                    MediaExternalID.provider == "imdb",
                )
                .limit(1)
            )
            imdb_id = imdb_ext_result.first()
            meta_id = imdb_id if imdb_id else f"mf{metadata.id}"

            poster = f"{settings.poster_host_url}/poster/{meta_type}/{meta_id}.jpg"
            await telegram_notifier.send_block_notification(
                info_hash=info_hash,
                action="delete",
                meta_id=meta_id,
                title=metadata.title,
                meta_type=meta_type,
                poster=poster,
                name=torrent.stream.name if torrent.stream else "Unknown",
            )
    except Exception as e:
        logger.error(f"Failed to delete torrent {info_hash}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete torrent: {str(e)}",
        )

    logger.info(f"Admin deleted torrent: {info_hash}, reason: {reason}")

    return {
        "status": "success",
        "message": f"Torrent {info_hash} has been successfully deleted.",
    }


@router.post("/add-tv-metadata")
async def add_tv_metadata_endpoint(
    tv_metadata: schemas.TVMetaData,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Add TV channel metadata (Admin only).

    This creates TV channel entries with their streams.
    """
    # Use a default namespace for admin-added content
    namespace = "admin"

    await add_tv_metadata([tv_metadata.model_dump()], namespace=namespace)

    logger.info(f"Admin added TV metadata: {tv_metadata.title}")

    return {
        "status": "success",
        "message": "TV metadata has been added.",
    }
