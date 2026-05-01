"""
Admin API endpoints for scraper management.
Migrated from scrapers/routes.py - admin-only functionality with proper auth.
"""

import logging
from datetime import datetime
from typing import Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlmodel import func, select
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
    migrate_media_links,
    migrate_torrent_streams,
    update_meta_stream,
    update_metadata,
)
from db.crud.stream_cache import invalidate_media_stream_cache
from db.database import get_async_session, get_async_session_context
from db.enums import UserRole
from db.models import FileMediaLink, Media, MediaExternalID, StreamFile, StreamMediaLink, User
from db.redis_database import REDIS_ASYNC_CLIENT
from mediafusion_scrapy.task import run_spider
from scrapers.dmm_hashlist import (
    BACKFILL_DONE_SENTINEL,
    BACKFILL_NEXT_COMMIT_SHA_KEY,
    DEFAULT_FULL_INGEST_BACKFILL_COMMITS,
    DEFAULT_FULL_INGEST_INCREMENTAL_COMMITS,
    DEFAULT_FULL_INGEST_MAX_ITERATIONS,
    LATEST_COMMIT_SHA_KEY,
    PROCESSED_FILE_SHA_KEY,
    DMMHashlistScraper,
    run_dmm_hashlist_full_ingestion,
    run_dmm_hashlist_full_ingestion_job,
    run_dmm_hashlist_scraper,
)
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


class MigrateMediaRequest(BaseModel):
    """Request to merge duplicate media entries by internal IDs."""

    from_media_id: int | None = Field(
        default=None,
        ge=1,
        description="Legacy single source media ID to migrate and delete",
    )
    from_media_ids: list[int] | None = Field(
        default=None,
        description="Source media IDs to migrate and delete in one operation",
    )
    to_media_id: int = Field(..., ge=1, description="Target media ID to keep")

    @model_validator(mode="after")
    def validate_sources(self) -> "MigrateMediaRequest":
        source_ids: list[int] = []
        if self.from_media_ids:
            source_ids.extend(self.from_media_ids)
        if self.from_media_id is not None:
            source_ids.append(self.from_media_id)

        if not source_ids:
            raise ValueError("At least one source media ID is required.")

        normalized_ids: list[int] = []
        seen_ids: set[int] = set()
        for source_id in source_ids:
            if source_id < 1:
                raise ValueError("Source media IDs must be positive integers.")
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            normalized_ids.append(source_id)

        self.from_media_ids = normalized_ids
        self.from_media_id = normalized_ids[0]
        return self


class DeleteTorrentRequest(BaseModel):
    """Request to delete a torrent."""

    info_hash: str
    reason: str | None = None


class DMMHashlistStatusResponse(BaseModel):
    """Operational status for DMM hashlist ingestion."""

    enabled: bool
    scheduler_disabled: bool
    cron_expression: str
    repo: str
    branch: str
    sync_interval_hours: int
    commits_per_run: int
    backfill_commits_per_run: int
    latest_commit_sha: str | None = None
    backfill_next_commit_sha: str | None = None
    backfill_complete: bool = False
    processed_file_sha_count: int = 0


class RunDMMHashlistRequest(BaseModel):
    """Request to run DMM hashlist ingestion."""

    sync: bool = True
    incremental_commits: int | None = Field(default=None, ge=0, le=100)
    backfill_commits: int | None = Field(default=None, ge=0, le=100)


class RunDMMHashlistFullRequest(BaseModel):
    """Request to run full DMM hashlist ingestion until backfill completes."""

    sync: bool = False
    reset_checkpoints: bool = False
    max_iterations: int = Field(default=DEFAULT_FULL_INGEST_MAX_ITERATIONS, ge=1, le=2000)
    incremental_commits: int = Field(default=DEFAULT_FULL_INGEST_INCREMENTAL_COMMITS, ge=0, le=100)
    backfill_commits: int = Field(default=DEFAULT_FULL_INGEST_BACKFILL_COMMITS, ge=0, le=100)


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

    await run_spider.async_send(
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


@router.get("/dmm-hashlist/status", response_model=DMMHashlistStatusResponse)
async def get_dmm_hashlist_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get DMM hashlist ingestion status and checkpoints (Admin only)."""
    redis_values = await REDIS_ASYNC_CLIENT.mget(
        [
            LATEST_COMMIT_SHA_KEY,
            BACKFILL_NEXT_COMMIT_SHA_KEY,
        ]
    )
    latest_commit = redis_values[0] if redis_values else None
    backfill_next = redis_values[1] if len(redis_values) > 1 else None

    def decode_value(value: bytes | str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    latest_commit_text = decode_value(latest_commit)
    backfill_next_text = decode_value(backfill_next)
    backfill_complete = backfill_next_text == BACKFILL_DONE_SENTINEL

    return DMMHashlistStatusResponse(
        enabled=settings.is_scrap_from_dmm_hashlist,
        scheduler_disabled=settings.disable_dmm_hashlist_scraper,
        cron_expression=settings.dmm_hashlist_scraper_crontab,
        repo=f"{settings.dmm_hashlist_repo_owner}/{settings.dmm_hashlist_repo_name}",
        branch=settings.dmm_hashlist_branch,
        sync_interval_hours=settings.dmm_hashlist_sync_interval_hour,
        commits_per_run=settings.dmm_hashlist_commits_per_run,
        backfill_commits_per_run=settings.dmm_hashlist_backfill_commits_per_run,
        latest_commit_sha=latest_commit_text,
        backfill_next_commit_sha=None if backfill_complete else backfill_next_text,
        backfill_complete=backfill_complete,
        processed_file_sha_count=await REDIS_ASYNC_CLIENT.scard(PROCESSED_FILE_SHA_KEY),
    )


@router.post("/dmm-hashlist/run")
async def run_dmm_hashlist_ingestion(
    request: RunDMMHashlistRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Run DMM hashlist ingestion now (Admin only)."""
    if not settings.is_scrap_from_dmm_hashlist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="DMM hashlist ingestion is disabled. Enable is_scrap_from_dmm_hashlist in environment settings.",
        )

    if not request.sync:
        await run_dmm_hashlist_scraper.async_send()
        return {
            "status": "scheduled",
            "mode": "async_queue",
            "message": "DMM hashlist ingestion task has been queued.",
        }

    scraper = DMMHashlistScraper()
    try:
        if request.incremental_commits is not None:
            scraper.max_incremental_commits = request.incremental_commits
        if request.backfill_commits is not None:
            scraper.max_backfill_commits = request.backfill_commits
        result = await scraper.run()
    finally:
        await scraper.close()

    return {
        "status": "success",
        "mode": "sync",
        "result": result,
    }


@router.post("/dmm-hashlist/run-full")
async def run_dmm_hashlist_full_ingestion_endpoint(
    request: RunDMMHashlistFullRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Run full DMM ingestion to completion (Admin only)."""
    if not settings.is_scrap_from_dmm_hashlist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="DMM hashlist ingestion is disabled. Enable is_scrap_from_dmm_hashlist in environment settings.",
        )

    if not request.sync:
        await run_dmm_hashlist_full_ingestion_job.async_send(
            max_iterations=request.max_iterations,
            incremental_commits=request.incremental_commits,
            backfill_commits=request.backfill_commits,
            reset_checkpoints=request.reset_checkpoints,
        )
        return {
            "status": "scheduled",
            "mode": "async_queue",
            "message": "Full DMM hashlist ingestion task has been queued.",
            "params": request.model_dump(),
        }

    result = await run_dmm_hashlist_full_ingestion(
        max_iterations=request.max_iterations,
        incremental_commits=request.incremental_commits,
        backfill_commits=request.backfill_commits,
        reset_checkpoints=request.reset_checkpoints,
    )
    return {
        "status": result.get("status", "success"),
        "mode": "sync",
        "result": result,
    }


# ============================================
# Metadata Management Endpoints
# ============================================


@router.post("/migrate-media")
async def migrate_media(
    request: MigrateMediaRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Merge duplicate non-user-created media entries into one target by internal IDs."""
    source_media_ids = request.from_media_ids or []
    if request.to_media_id in source_media_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target media IDs must be different.",
        )

    to_media = await session.get(Media, request.to_media_id)
    if not to_media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target media ID {request.to_media_id} was not found.",
        )

    if to_media.is_user_created:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only non-user-created media can be migrated with this endpoint.",
        )

    source_media_result = await session.exec(select(Media).where(Media.id.in_(source_media_ids)))
    source_media_items = source_media_result.all()
    source_media_by_id = {media.id: media for media in source_media_items}
    missing_source_ids = [media_id for media_id in source_media_ids if media_id not in source_media_by_id]
    if missing_source_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source media IDs not found: {', '.join(str(media_id) for media_id in missing_source_ids)}",
        )

    for source_media_id in source_media_ids:
        source_media = source_media_by_id[source_media_id]
        if source_media.is_user_created:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Source media #{source_media_id} is user-created and cannot be migrated.",
            )

        if source_media.type != to_media.type:
            source_stream_link_count_result = await session.exec(
                select(func.count()).select_from(StreamMediaLink).where(StreamMediaLink.media_id == source_media.id)
            )
            source_stream_link_count = source_stream_link_count_result.one()
            source_file_link_count_result = await session.exec(
                select(func.count()).select_from(FileMediaLink).where(FileMediaLink.media_id == source_media.id)
            )
            source_file_link_count = source_file_link_count_result.one()

            # Allow cross-type cleanup only when source has no links to move.
            if source_stream_link_count > 0 or source_file_link_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Media type mismatch is only allowed when source has no linked streams/files. "
                        f"Source #{source_media_id} has streams/files linked."
                    ),
                )

    migrated_sources: list[dict[str, int]] = []
    total_stream_links_migrated = 0
    total_stream_links_deleted = 0
    total_file_links_migrated = 0
    total_file_links_deleted = 0
    stream_cache_media_ids: set[int] = {to_media.id}
    target_meta_id = f"mf:{to_media.id}"
    meta_cache_ids: set[str] = {target_meta_id}

    for source_media_id in source_media_ids:
        source_media = source_media_by_id[source_media_id]
        source_meta_id = f"mf:{source_media.id}"
        old_canonical_id = await crud.get_canonical_external_id(session, source_media.id)
        migration_stats = await migrate_media_links(session, source_media.id, to_media.id)

        total_stream_links_migrated += migration_stats["stream_links_migrated"]
        total_stream_links_deleted += migration_stats["stream_links_deleted_as_duplicates"]
        total_file_links_migrated += migration_stats["file_links_migrated"]
        total_file_links_deleted += migration_stats["file_links_deleted_as_duplicates"]

        if old_canonical_id and not to_media.migrated_from_id:
            to_media.migrated_from_id = old_canonical_id

        deleted = await delete_metadata(session, source_meta_id, new_media_id=to_media.id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete source media #{source_media.id} after migration.",
            )

        stream_cache_media_ids.add(source_media.id)
        meta_cache_ids.add(source_meta_id)
        migrated_sources.append({"from_media_id": source_media.id, **migration_stats})

    to_media.migrated_by_user_id = moderator.id
    to_media.migrated_at = datetime.now(pytz.UTC)
    session.add(to_media)

    await update_meta_stream(session, target_meta_id, to_media.type.value)

    for media_id in stream_cache_media_ids:
        await invalidate_media_stream_cache(media_id)
    for meta_id in meta_cache_ids:
        await crud.invalidate_meta_cache(meta_id)

    await session.commit()

    logger.info(
        "Moderator %s migrated media %s -> %s (sources=%s, streams=%s, files=%s)",
        moderator.username,
        source_media_ids,
        request.to_media_id,
        len(source_media_ids),
        total_stream_links_migrated,
        total_file_links_migrated,
    )

    return {
        "status": "success",
        "message": f"Migrated {len(source_media_ids)} media item(s) to {request.to_media_id}",
        "from_media_id": source_media_ids[0],
        "from_media_ids": source_media_ids,
        "migrated_sources_count": len(source_media_ids),
        "migrated_sources": migrated_sources,
        "to_media_id": request.to_media_id,
        "stream_links_migrated": total_stream_links_migrated,
        "stream_links_deleted_as_duplicates": total_stream_links_deleted,
        "file_links_migrated": total_file_links_migrated,
        "file_links_deleted_as_duplicates": total_file_links_deleted,
    }


@router.post("/migrate-id")
async def migrate_metadata_id(
    request: MigrateIdRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Migrate a MediaFusion ID to an IMDb ID (Admin only).

    This will:
    1. Fetch metadata for the IMDb ID if it doesn't exist
    2. Move all torrent streams from the old ID to the new ID
    3. Delete the old metadata
    4. Update stream statistics for the new ID
    """
    async with get_async_session_context() as session:
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

        async with get_async_session_context() as session:
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
            await session.commit()
        logger.info(f"Created metadata for {request.imdb_id}")

    async with get_async_session_context() as session:
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

    async with get_async_session_context() as session:
        metadata = await get_metadata_by_id(session, request.meta_id)
        if not metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Content with ID {request.meta_id} not found",
            )
        old_poster = metadata.poster
        old_background = metadata.background
        old_logo = metadata.logo
        old_title = metadata.title
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

    async with get_async_session_context() as session:
        await update_metadata(session, request.meta_id, update_fields)
        await session.commit()

    # Send Telegram notification
    if settings.telegram_bot_token:
        await telegram_notifier.send_image_update_notification(
            meta_id=request.meta_id,
            title=old_title,
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
):
    """
    Refresh IMDb/TMDB data for existing content (Admin only).

    This fetches fresh metadata from external providers and updates the database.
    """
    # Validate content exists
    async with get_async_session_context() as session:
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
        async with get_async_session_context() as session:
            success = await crud.update_single_imdb_metadata(session, meta_id, media_type)
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to update IMDb data for {meta_id}.",
                )
            await session.commit()
    else:
        async with get_async_session_context() as session:
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
):
    """
    Delete a torrent by info_hash (Admin only).

    This completely removes the torrent from the database.
    """
    notification_payload = None

    try:
        async with get_async_session_context() as session:
            torrent = await crud.streams.get_torrent_by_info_hash(session, info_hash)
            if not torrent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Torrent not found: {info_hash}",
                )
            stream_name = torrent.stream.name if torrent.stream else "Unknown"

            metadata = None
            stream_media_result = await session.exec(
                select(StreamMediaLink.media_id).where(StreamMediaLink.stream_id == torrent.stream_id).limit(1)
            )
            media_id = stream_media_result.first()

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

            if settings.telegram_bot_token and metadata:
                meta_type = metadata.type.value if metadata.type else "movie"
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
                notification_payload = {
                    "info_hash": info_hash,
                    "meta_id": meta_id,
                    "title": metadata.title,
                    "meta_type": meta_type,
                    "poster": f"{settings.poster_host_url}/poster/{meta_type}/{meta_id}.jpg",
                    "name": stream_name,
                }

            await delete_torrent_stream(session, info_hash)
            await session.commit()

        if notification_payload:
            await telegram_notifier.send_block_notification(
                info_hash=notification_payload["info_hash"],
                action="delete",
                meta_id=notification_payload["meta_id"],
                title=notification_payload["title"],
                meta_type=notification_payload["meta_type"],
                poster=notification_payload["poster"],
                name=notification_payload["name"],
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
