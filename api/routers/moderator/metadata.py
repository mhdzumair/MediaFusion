"""Moderator metadata endpoints for migration and external metadata workflows."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from api.schemas.metadata_management import (
    ExternalMetadataPreview,
    FetchExternalRequest,
    MetadataListResponse,
    MetadataResponse,
    MigrateIdRequest,
    SearchExternalRequest,
    SearchExternalResponse,
)
from api.routers.user.auth import require_role
from api.services import moderator_metadata_service
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
    return await moderator_metadata_service.list_metadata(
        session=session,
        page=page,
        per_page=per_page,
        media_type=media_type,
        search=search,
        has_streams=has_streams,
    )


@router.get("/{media_id}", response_model=MetadataResponse)
async def moderator_get_metadata(
    media_id: int,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get one metadata item for moderator migration workflows."""
    return await moderator_metadata_service.get_metadata(
        session=session,
        media_id=media_id,
    )


@router.post("/search-external", response_model=SearchExternalResponse)
async def moderator_search_external_metadata(
    request: SearchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
):
    """Search external providers for metadata candidates."""
    return await moderator_metadata_service.search_external_metadata(
        request=request,
    )


@router.post("/{media_id}/fetch-external", response_model=ExternalMetadataPreview)
async def moderator_fetch_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Preview external metadata from IMDb or TMDB."""
    return await moderator_metadata_service.fetch_external_metadata(
        session=session,
        media_id=media_id,
        request=request,
    )


@router.post("/{media_id}/apply-external", response_model=MetadataResponse)
async def moderator_apply_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Apply external metadata from IMDb or TMDB."""
    return await moderator_metadata_service.apply_external_metadata(
        session=session,
        media_id=media_id,
        request=request,
    )


@router.post("/{media_id}/migrate-id", response_model=MetadataResponse)
async def moderator_migrate_metadata_id(
    media_id: int,
    request: MigrateIdRequest,
    moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Migrate metadata IDs for moderator workflows."""
    return await moderator_metadata_service.migrate_metadata_id(
        session=session,
        media_id=media_id,
        request=request,
    )
