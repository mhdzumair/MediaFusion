"""
IPTV Source Management API endpoints.
"""

import asyncio
import logging
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.database import get_async_session
from db.enums import IPTVSourceType
from db.models import IPTVSource, User
from scrapers.import_tasks import create_import_job, run_m3u_sync, run_xtream_sync

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# IPTV Source Management Schemas
# ============================================


class IPTVSourceResponse(BaseModel):
    """Response for a single IPTV source."""

    id: int
    source_type: str  # m3u, xtream, stalker
    name: str
    is_public: bool
    import_live: bool
    import_vod: bool
    import_series: bool
    last_synced_at: datetime | None = None
    last_sync_stats: dict[str, int] | None = None
    is_active: bool
    created_at: datetime
    # Don't expose sensitive data (m3u_url, credentials)
    has_url: bool = False
    has_credentials: bool = False


class IPTVSourceListResponse(BaseModel):
    """Response for list of IPTV sources."""

    sources: list[IPTVSourceResponse]
    total: int


class IPTVSourceUpdateRequest(BaseModel):
    """Request to update an IPTV source."""

    name: str | None = None
    is_active: bool | None = None
    import_live: bool | None = None
    import_vod: bool | None = None
    import_series: bool | None = None


class SyncResponse(BaseModel):
    """Response from syncing an IPTV source."""

    status: str
    message: str
    stats: dict[str, int] | None = None
    error: str | None = None
    job_id: str | None = None  # For background task tracking


# ============================================
# IPTV Source Management Endpoints
# ============================================


@router.get("/sources", response_model=IPTVSourceListResponse)
async def list_iptv_sources(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List all IPTV sources for the current user.
    """
    query = select(IPTVSource).where(IPTVSource.user_id == user.id).order_by(IPTVSource.created_at.desc())

    result = await session.exec(query)
    sources = result.all()

    response_sources = [
        IPTVSourceResponse(
            id=src.id,
            source_type=src.source_type,
            name=src.name,
            is_public=src.is_public,
            import_live=src.import_live,
            import_vod=src.import_vod,
            import_series=src.import_series,
            last_synced_at=src.last_synced_at,
            last_sync_stats=src.last_sync_stats,
            is_active=src.is_active,
            created_at=src.created_at,
            has_url=bool(src.m3u_url),
            has_credentials=bool(src.encrypted_credentials),
        )
        for src in sources
    ]

    return IPTVSourceListResponse(
        sources=response_sources,
        total=len(response_sources),
    )


@router.get("/sources/{source_id}", response_model=IPTVSourceResponse)
async def get_iptv_source(
    source_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get details of a specific IPTV source.
    """
    query = select(IPTVSource).where(
        IPTVSource.id == source_id,
        IPTVSource.user_id == user.id,
    )
    result = await session.exec(query)
    source = result.first()

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    return IPTVSourceResponse(
        id=source.id,
        source_type=source.source_type,
        name=source.name,
        is_public=source.is_public,
        import_live=source.import_live,
        import_vod=source.import_vod,
        import_series=source.import_series,
        last_synced_at=source.last_synced_at,
        last_sync_stats=source.last_sync_stats,
        is_active=source.is_active,
        created_at=source.created_at,
        has_url=bool(source.m3u_url),
        has_credentials=bool(source.encrypted_credentials),
    )


@router.patch("/sources/{source_id}", response_model=IPTVSourceResponse)
async def update_iptv_source(
    source_id: int,
    update: IPTVSourceUpdateRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update an IPTV source's settings.
    """
    query = select(IPTVSource).where(
        IPTVSource.id == source_id,
        IPTVSource.user_id == user.id,
    )
    result = await session.exec(query)
    source = result.first()

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    # Update fields
    if update.name is not None:
        source.name = update.name
    if update.is_active is not None:
        source.is_active = update.is_active
    if update.import_live is not None:
        source.import_live = update.import_live
    if update.import_vod is not None:
        source.import_vod = update.import_vod
    if update.import_series is not None:
        source.import_series = update.import_series

    session.add(source)
    await session.commit()
    await session.refresh(source)

    return IPTVSourceResponse(
        id=source.id,
        source_type=source.source_type,
        name=source.name,
        is_public=source.is_public,
        import_live=source.import_live,
        import_vod=source.import_vod,
        import_series=source.import_series,
        last_synced_at=source.last_synced_at,
        last_sync_stats=source.last_sync_stats,
        is_active=source.is_active,
        created_at=source.created_at,
        has_url=bool(source.m3u_url),
        has_credentials=bool(source.encrypted_credentials),
    )


@router.delete("/sources/{source_id}")
async def delete_iptv_source(
    source_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete an IPTV source.

    Note: This only removes the saved source configuration.
    Imported content (channels, movies, etc.) is NOT deleted.
    """
    query = select(IPTVSource).where(
        IPTVSource.id == source_id,
        IPTVSource.user_id == user.id,
    )
    result = await session.exec(query)
    source = result.first()

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    await session.delete(source)
    await session.commit()

    return {"status": "success", "message": "Source deleted"}


@router.post("/sources/{source_id}/sync", response_model=SyncResponse)
async def sync_iptv_source(
    source_id: int,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Re-sync an IPTV source to fetch new content.

    Re-imports content from the saved M3U URL or Xtream server,
    adding new items and skipping existing ones.

    For sources with many items, this runs as a background task.
    """
    query = select(IPTVSource).where(
        IPTVSource.id == source_id,
        IPTVSource.user_id == user.id,
    )
    result = await session.exec(query)
    source = result.first()

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    if not source.is_active:
        return SyncResponse(
            status="error",
            message="Source is inactive. Enable it first to sync.",
        )

    try:
        if source.source_type == IPTVSourceType.M3U:
            if not source.m3u_url:
                return SyncResponse(
                    status="error",
                    message="No M3U URL saved for this source.",
                )

            # Create job and queue background task
            job_id = f"m3u_sync_{uuid4().hex[:10]}"
            await create_import_job(
                job_id=job_id,
                user_id=user.id,
                source_type="m3u_sync",
                total_items=0,  # Will be updated when parsing starts
                source_id=source_id,
            )

            # Queue background task
            await asyncio.to_thread(
                run_m3u_sync.send,
                job_id=job_id,
                source_id=source_id,
                user_id=user.id,
                m3u_url=source.m3u_url,
                is_public=source.is_public,
                import_live=source.import_live,
                import_vod=source.import_vod,
                import_series=source.import_series,
            )

            return SyncResponse(
                status="processing",
                message="Sync started in background. Check job status for progress.",
                job_id=job_id,
            )

        elif source.source_type == IPTVSourceType.XTREAM:
            if not source.server_url or not source.encrypted_credentials:
                return SyncResponse(
                    status="error",
                    message="Missing server credentials for this source.",
                )

            # Create job and queue background task
            job_id = f"xtream_sync_{uuid4().hex[:10]}"
            await create_import_job(
                job_id=job_id,
                user_id=user.id,
                source_type="xtream_sync",
                total_items=0,  # Will be updated when fetching starts
                source_id=source_id,
            )

            # Queue background task
            await asyncio.to_thread(
                run_xtream_sync.send,
                job_id=job_id,
                source_id=source_id,
                user_id=user.id,
                server_url=source.server_url,
                encrypted_credentials=source.encrypted_credentials,
                is_public=source.is_public,
                import_live=source.import_live,
                import_vod=source.import_vod,
                import_series=source.import_series,
                live_category_ids=source.live_category_ids,
                vod_category_ids=source.vod_category_ids,
                series_category_ids=source.series_category_ids,
            )

            return SyncResponse(
                status="processing",
                message="Sync started in background. Check job status for progress.",
                job_id=job_id,
            )

        else:
            return SyncResponse(
                status="error",
                message=f"Unsupported source type: {source.source_type}",
            )

    except Exception as e:
        logger.exception(f"Failed to start IPTV source sync: {e}")
        return SyncResponse(
            status="error",
            message=f"Failed to start sync: {str(e)}",
            error=str(e),
        )
