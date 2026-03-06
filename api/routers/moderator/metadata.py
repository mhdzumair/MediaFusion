"""Moderator metadata endpoints for migration and external metadata workflows."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.admin.admin import (
    ExternalMetadataPreview,
    FetchExternalRequest,
    MetadataListResponse,
    MetadataResponse,
    MigrateIdRequest,
    SearchExternalRequest,
    SearchExternalResponse,
    apply_external_metadata as admin_apply_external_metadata,
    fetch_external_metadata as admin_fetch_external_metadata,
    get_metadata as admin_get_metadata,
    list_metadata as admin_list_metadata,
    migrate_metadata_id as admin_migrate_metadata_id,
    search_external_metadata as admin_search_external_metadata,
)
from api.routers.user.auth import require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import User

router = APIRouter(prefix="/api/v1/moderator/metadata", tags=["Moderator Metadata"])


@router.get("", response_model=MetadataListResponse)
async def moderator_list_metadata(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    media_type: Literal["movie", "series", "tv"] | None = None,
    search: str | None = None,
    has_streams: bool | None = None,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """List metadata for moderator migration workflows."""
    return await admin_list_metadata(
        page=page,
        per_page=per_page,
        media_type=media_type,
        search=search,
        has_streams=has_streams,
        _admin=moderator,
        session=session,
    )


@router.get("/{media_id}", response_model=MetadataResponse)
async def moderator_get_metadata(
    media_id: int,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get one metadata item for moderator migration workflows."""
    return await admin_get_metadata(
        media_id=media_id,
        _admin=moderator,
        session=session,
    )


@router.post("/search-external", response_model=SearchExternalResponse)
async def moderator_search_external_metadata(
    request: SearchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
):
    """Search external providers for metadata candidates."""
    return await admin_search_external_metadata(
        request=request,
        _admin=moderator,
    )


@router.post("/{media_id}/fetch-external", response_model=ExternalMetadataPreview)
async def moderator_fetch_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Preview external metadata from IMDb or TMDB."""
    return await admin_fetch_external_metadata(
        media_id=media_id,
        request=request,
        _admin=moderator,
        session=session,
    )


@router.post("/{media_id}/apply-external", response_model=MetadataResponse)
async def moderator_apply_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Apply external metadata from IMDb or TMDB."""
    return await admin_apply_external_metadata(
        media_id=media_id,
        request=request,
        _admin=moderator,
        session=session,
    )


@router.post("/{media_id}/migrate-id", response_model=MetadataResponse)
async def moderator_migrate_metadata_id(
    media_id: int,
    request: MigrateIdRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Migrate metadata IDs for moderator workflows."""
    return await admin_migrate_metadata_id(
        media_id=media_id,
        request=request,
        _admin=moderator,
        session=session,
    )
