"""
Metadata API endpoints for refreshing and migrating content metadata.
Available to all authenticated users.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db import crud
from db.database import get_async_session
from db.models import MediaExternalID, User
from scrapers.scraper_tasks import meta_fetcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/metadata", tags=["Metadata"])


# ============================================
# Pydantic Schemas
# ============================================


class RefreshMetadataRequest(BaseModel):
    """Request to refresh metadata from external sources."""

    media_type: Literal["movie", "series"]
    providers: list[str] | None = None  # List of providers to refresh from, or None for all


class RefreshMetadataResponse(BaseModel):
    """Response from metadata refresh operation."""

    status: str
    message: str
    media_id: int  # Internal media_id
    title: str | None = None
    refreshed_providers: list[str] | None = None


class LinkExternalIdRequest(BaseModel):
    """Request to link an external provider ID to media."""

    provider: Literal["imdb", "tmdb", "tvdb", "mal", "kitsu"] = Field(..., description="External provider to link")
    external_id: str = Field(..., description="External ID (e.g., tt1234567 for IMDb, 12345 for TMDB)")
    media_type: Literal["movie", "series"]
    fetch_metadata: bool = Field(default=True, description="Whether to fetch and update metadata from the provider")


class LinkExternalIdResponse(BaseModel):
    """Response from link external ID operation."""

    status: str
    message: str
    media_id: int
    provider: str
    external_id: str
    title: str | None = None
    metadata_updated: bool = False


class LinkMultipleExternalIdsRequest(BaseModel):
    """Request to link multiple external IDs from a search result."""

    imdb_id: str | None = None
    tmdb_id: str | int | None = None
    tvdb_id: str | int | None = None
    mal_id: str | int | None = None
    kitsu_id: str | int | None = None
    media_type: Literal["movie", "series"]
    fetch_metadata: bool = Field(default=True, description="Whether to fetch and update metadata from providers")


class LinkMultipleExternalIdsResponse(BaseModel):
    """Response from linking multiple external IDs."""

    status: str
    message: str
    media_id: int
    linked_providers: list[str]  # List of successfully linked providers
    failed_providers: list[str] = []  # List of providers that failed to link
    metadata_updated: bool = False


# Keep old names for backward compatibility
MigrateIdRequest = LinkExternalIdRequest
MigrateIdResponse = LinkExternalIdResponse


class SearchExternalRequest(BaseModel):
    """Request to search for external metadata."""

    title: str
    year: int | None = None
    media_type: Literal["movie", "series"]


class ExternalSearchResult(BaseModel):
    """External metadata search result."""

    id: str  # Primary ID (imdb_id or tmdb_id as fallback)
    title: str
    year: int | None = None
    poster: str | None = None
    description: str | None = None
    provider: str | None = None  # 'imdb', 'tmdb', 'tvdb', 'mal', 'kitsu'
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | int | None = None
    external_ids: dict | None = None  # All external IDs from the result


class SearchExternalResponse(BaseModel):
    """Response from external metadata search."""

    status: str
    results: list[ExternalSearchResult]


# ============================================
# API Endpoints
# ============================================


@router.post("/{media_id}/refresh", response_model=RefreshMetadataResponse)
async def refresh_metadata(
    media_id: int,
    request: RefreshMetadataRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Refresh metadata from external sources (IMDB/TMDB/TVDB/MAL/Kitsu).

    Fetches fresh data from all configured providers and updates the metadata.
    If specific providers are requested, only fetches from those.
    """
    from sqlmodel import select

    from db.models import Media

    # Get the media record
    result = await session.exec(select(Media).where(Media.id == media_id))
    media = result.first()
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with ID {media_id} not found.",
        )

    title = media.title

    # Get all external IDs for this media
    external_ids_result = await session.exec(select(MediaExternalID).where(MediaExternalID.media_id == media_id))
    external_ids_records = external_ids_result.all()

    # Build external_ids dict
    external_ids: dict[str, str] = {}
    for ext_id in external_ids_records:
        external_ids[ext_id.provider] = ext_id.external_id

    if not external_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No external IDs found for media {media_id}.",
        )

    # Filter to requested providers if specified
    if request.providers:
        external_ids = {provider: ext_id for provider, ext_id in external_ids.items() if provider in request.providers}
        if not external_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="None of the requested providers have IDs for this media.",
            )

    # Fetch metadata from all available providers
    provider_data = await meta_fetcher.get_metadata_from_all_providers(external_ids, request.media_type)

    refreshed_providers = list(provider_data.keys())

    if not provider_data:
        # Fallback: update stream counts even if metadata refresh failed
        canonical_id = external_ids.get("imdb") or external_ids.get("tmdb") or list(external_ids.values())[0]
        # Recalculate stream time to fix any incorrect values
        await crud.update_meta_stream(session, canonical_id, request.media_type, recalculate_stream_time=True)
        message = "Could not fetch fresh metadata from any provider. Stream counts updated."
    else:
        # Apply metadata updates from providers
        # Priority: IMDB > TMDB > TVDB > MAL > Kitsu
        provider_priority = ["imdb", "tmdb", "tvdb", "mal", "kitsu"]

        # Store raw provider data in provider_metadata table
        for provider in provider_priority:
            if provider in provider_data:
                data = provider_data[provider]
                # Store raw provider data for reference
                await crud.update_provider_metadata(session, media_id, provider, data)

        # Apply multi-provider metadata to normalized tables
        # This uses waterfall fallback and merges cast/crew/genres from all providers
        success = await crud.apply_multi_provider_metadata(session, media_id, provider_data, request.media_type)

        if not success:
            logger.warning(f"Failed to apply multi-provider metadata for media_id={media_id}")

        # Recalculate stream time to fix any incorrect values
        canonical_id = external_ids.get("imdb") or external_ids.get("tmdb") or list(external_ids.values())[0]
        await crud.update_meta_stream(session, canonical_id, request.media_type, recalculate_stream_time=True)

        message = f"Successfully refreshed metadata from {len(refreshed_providers)} provider(s): {', '.join(refreshed_providers)}."

    # Commit changes
    await session.commit()

    logger.info(f"User {user.id} refreshed metadata for media_id={media_id} from providers: {refreshed_providers}")

    return RefreshMetadataResponse(
        status="success",
        message=message,
        media_id=media_id,
        title=title,
        refreshed_providers=refreshed_providers,
    )


@router.post("/{media_id}/link-external", response_model=LinkExternalIdResponse)
async def link_external_id(
    media_id: int,
    request: LinkExternalIdRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Link an external provider ID to existing media.

    This will:
    1. Add the external ID to the media's external IDs
    2. Optionally fetch and update metadata from the provider
    """
    from sqlmodel import select

    from db.models import Media

    # Validate external ID format based on provider
    external_id = request.external_id.strip()
    if request.provider == "imdb":
        if not external_id.startswith("tt"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="IMDb ID must start with 'tt' (e.g., tt1234567).",
            )
    elif request.provider in ("tmdb", "tvdb", "mal"):
        # These should be numeric IDs
        if external_id.isdigit():
            external_id = external_id  # Keep as string but validate it's numeric
        else:
            # Allow prefixed format like "tmdb:12345" and extract the number
            if ":" in external_id:
                external_id = external_id.split(":")[-1]
            if not external_id.isdigit():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{request.provider.upper()} ID must be numeric.",
                )

    # Check if media exists
    result = await session.exec(select(Media).where(Media.id == media_id))
    media = result.first()
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with ID {media_id} not found.",
        )

    # Check if this external ID is already linked to another media
    existing_link = await crud.get_media_by_external_id(
        session, external_id if request.provider == "imdb" else f"{request.provider}:{external_id}"
    )
    if existing_link and existing_link.id != media_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This {request.provider.upper()} ID is already linked to another media (ID: {existing_link.id}).",
        )

    # Add the external ID
    await crud.add_external_id(
        session,
        media_id=media_id,
        provider=request.provider,
        external_id=external_id,
    )

    metadata_updated = False

    # Optionally fetch and update metadata
    if request.fetch_metadata:
        try:
            # Fetch metadata from the provider
            if request.provider == "imdb":
                provider_data = await meta_fetcher.get_metadata_from_provider("imdb", external_id, request.media_type)
            elif request.provider == "tmdb":
                provider_data = await meta_fetcher.get_metadata_from_provider("tmdb", external_id, request.media_type)
            elif request.provider == "tvdb":
                provider_data = await meta_fetcher.get_metadata_from_provider("tvdb", external_id, request.media_type)
            else:
                provider_data = None

            if provider_data:
                # Apply metadata to the media
                await crud.apply_multi_provider_metadata(
                    session,
                    media_id,
                    {request.provider: provider_data},
                    request.media_type,
                )
                metadata_updated = True
                logger.info(f"Updated metadata for media {media_id} from {request.provider}")
        except Exception as e:
            logger.warning(f"Failed to fetch metadata from {request.provider}: {e}")

    # Commit changes
    await session.commit()

    logger.info(f"User {user.id} linked {request.provider}:{external_id} to media {media_id}")

    return LinkExternalIdResponse(
        status="success",
        message=f"Successfully linked {request.provider.upper()} ID to media.",
        media_id=media_id,
        provider=request.provider,
        external_id=external_id,
        title=media.title,
        metadata_updated=metadata_updated,
    )


@router.post("/{media_id}/link-multiple", response_model=LinkMultipleExternalIdsResponse)
async def link_multiple_external_ids(
    media_id: int,
    request: LinkMultipleExternalIdsRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Link multiple external provider IDs to a media item at once.

    This is useful when a search result contains multiple IDs (IMDb, TMDB, TVDB)
    and you want to link all of them in one operation.
    """
    from sqlmodel import select

    from db.models import Media

    # Check if media exists
    result = await session.exec(select(Media).where(Media.id == media_id))
    media = result.first()
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with ID {media_id} not found.",
        )

    # Build list of IDs to link
    ids_to_link: list[tuple[str, str]] = []  # (provider, external_id)

    if request.imdb_id:
        imdb_id = request.imdb_id.strip()
        if imdb_id.startswith("tt"):
            ids_to_link.append(("imdb", imdb_id))

    if request.tmdb_id:
        tmdb_id = str(request.tmdb_id).strip()
        if tmdb_id.isdigit() or (tmdb_id.startswith("tmdb:") and tmdb_id.split(":")[-1].isdigit()):
            if ":" in tmdb_id:
                tmdb_id = tmdb_id.split(":")[-1]
            ids_to_link.append(("tmdb", tmdb_id))

    if request.tvdb_id:
        tvdb_id = str(request.tvdb_id).strip()
        if tvdb_id.isdigit() or (tvdb_id.startswith("tvdb:") and tvdb_id.split(":")[-1].isdigit()):
            if ":" in tvdb_id:
                tvdb_id = tvdb_id.split(":")[-1]
            ids_to_link.append(("tvdb", tvdb_id))

    if request.mal_id:
        mal_id = str(request.mal_id).strip()
        if mal_id.isdigit():
            ids_to_link.append(("mal", mal_id))

    if request.kitsu_id:
        kitsu_id = str(request.kitsu_id).strip()
        if kitsu_id.isdigit():
            ids_to_link.append(("kitsu", kitsu_id))

    if not ids_to_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid external IDs provided.",
        )

    linked_providers = []
    failed_providers = []

    # Link each ID
    for provider, external_id in ids_to_link:
        try:
            # Check if this external ID is already linked to another media
            lookup_id = external_id if provider == "imdb" else f"{provider}:{external_id}"
            existing_link = await crud.get_media_by_external_id(session, lookup_id)

            if existing_link and existing_link.id != media_id:
                logger.warning(f"{provider}:{external_id} is already linked to media {existing_link.id}")
                failed_providers.append(provider)
                continue

            # Add the external ID
            await crud.add_external_id(
                session,
                media_id=media_id,
                provider=provider,
                external_id=external_id,
            )
            linked_providers.append(provider)
            logger.info(f"Linked {provider}:{external_id} to media {media_id}")

        except Exception as e:
            logger.error(f"Failed to link {provider}:{external_id}: {e}")
            failed_providers.append(provider)

    metadata_updated = False

    # Optionally fetch and update metadata from all linked providers
    if request.fetch_metadata and linked_providers:
        try:
            # Build external_ids dict for fetching
            external_ids_for_fetch = {
                provider: ext_id for provider, ext_id in ids_to_link if provider in linked_providers
            }

            # Fetch metadata from all providers
            provider_data = await meta_fetcher.get_metadata_from_all_providers(
                external_ids_for_fetch, request.media_type
            )

            if provider_data:
                await crud.apply_multi_provider_metadata(
                    session,
                    media_id,
                    provider_data,
                    request.media_type,
                )
                metadata_updated = True
                logger.info(f"Updated metadata for media {media_id} from {list(provider_data.keys())}")
        except Exception as e:
            logger.warning(f"Failed to fetch metadata: {e}")

    # Commit changes
    await session.commit()

    logger.info(f"User {user.id} linked {linked_providers} to media {media_id}")

    return LinkMultipleExternalIdsResponse(
        status="success" if linked_providers else "partial",
        message=f"Successfully linked {len(linked_providers)} provider(s) to media.",
        media_id=media_id,
        linked_providers=linked_providers,
        failed_providers=failed_providers,
        metadata_updated=metadata_updated,
    )


# Legacy migrate endpoint for backward compatibility
@router.post("/{meta_id}/migrate", response_model=LinkExternalIdResponse)
async def migrate_metadata_id(
    meta_id: str,
    request: LinkExternalIdRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    [DEPRECATED] Migrate internal MediaFusion ID to a proper external ID.
    Use /link-external endpoint instead.

    This endpoint is kept for backward compatibility.
    """
    # Get the media by external ID (meta_id is the old external ID format)
    media = await crud.get_media_by_external_id(session, meta_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Metadata with ID {meta_id} not found.",
        )

    # Forward to the new link-external endpoint
    return await link_external_id(media.id, request, user, session)


@router.post("/search-external", response_model=SearchExternalResponse)
async def search_external_metadata(
    request: SearchExternalRequest,
    user: User = Depends(require_auth),
):
    """
    Search for metadata in external sources (IMDb, TMDB, TVDB, MAL, Kitsu).

    Returns results from all available providers, each with their IDs.
    The primary `id` field uses IMDb ID if available, otherwise TMDB ID.
    """
    try:
        results = await meta_fetcher.search_multiple_results(
            title=request.title,
            year=request.year,
            media_type=request.media_type,
        )

        # First pass: collect all results indexed by their primary (canonical) ID
        # We merge results that refer to the same content, keeping the richest data
        results_by_id: dict[str, dict] = {}

        for r in results:
            # Get IDs from the result
            imdb_id = r.get("imdb_id")
            tmdb_id = r.get("tmdb_id")
            tvdb_id = r.get("tvdb_id") or (r.get("external_ids") or {}).get("tvdb")

            # Get the actual source provider (where the result came from)
            source_provider = r.get("_source_provider", "imdb")

            # Determine the primary ID for deduplication (IMDb > TMDB > TVDB)
            if imdb_id:
                primary_id = imdb_id
            elif tmdb_id:
                primary_id = f"tmdb:{tmdb_id}"
            elif tvdb_id:
                primary_id = f"tvdb:{tvdb_id}"
            else:
                continue

            # Count how many external IDs this result has
            id_count = sum(1 for x in [imdb_id, tmdb_id, tvdb_id] if x)

            # Check if we already have a result for this primary ID
            if primary_id in results_by_id:
                existing = results_by_id[primary_id]
                existing_id_count = existing.get("_id_count", 0)

                # Prefer result with more external IDs, or merge IDs
                if id_count > existing_id_count:
                    # New result is richer, replace but keep any IDs from old
                    old_imdb = existing.get("imdb_id")
                    old_tmdb = existing.get("tmdb_id")
                    old_tvdb = existing.get("tvdb_id")

                    results_by_id[primary_id] = {
                        "primary_id": primary_id,
                        "title": r.get("title", ""),
                        "year": r.get("year"),
                        "poster": r.get("poster"),
                        "description": r.get("description"),
                        "provider": source_provider,
                        "imdb_id": imdb_id or old_imdb,
                        "tmdb_id": tmdb_id or old_tmdb,
                        "tvdb_id": tvdb_id or old_tvdb,
                        "external_ids": r.get("external_ids"),
                        "_id_count": id_count,
                    }
                else:
                    # Existing result is richer, just merge any new IDs
                    if imdb_id and not existing.get("imdb_id"):
                        existing["imdb_id"] = imdb_id
                    if tmdb_id and not existing.get("tmdb_id"):
                        existing["tmdb_id"] = tmdb_id
                    if tvdb_id and not existing.get("tvdb_id"):
                        existing["tvdb_id"] = tvdb_id
            else:
                # New entry
                results_by_id[primary_id] = {
                    "primary_id": primary_id,
                    "title": r.get("title", ""),
                    "year": r.get("year"),
                    "poster": r.get("poster"),
                    "description": r.get("description"),
                    "provider": source_provider,
                    "imdb_id": imdb_id,
                    "tmdb_id": tmdb_id,
                    "tvdb_id": tvdb_id,
                    "external_ids": r.get("external_ids"),
                    "_id_count": id_count,
                }

        # Convert to final results
        formatted_results = []
        for data in results_by_id.values():
            formatted_results.append(
                ExternalSearchResult(
                    id=data["primary_id"],
                    title=data["title"],
                    year=data["year"],
                    poster=data["poster"],
                    description=data["description"],
                    provider=data["provider"],
                    imdb_id=data["imdb_id"],
                    tmdb_id=str(data["tmdb_id"]) if data["tmdb_id"] else None,
                    tvdb_id=str(data["tvdb_id"]) if data["tvdb_id"] else None,
                    external_ids=data["external_ids"],
                )
            )

        return SearchExternalResponse(
            status="success",
            results=formatted_results,
        )
    except Exception as e:
        logger.error(f"Failed to search external metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to search external metadata: {str(e)}",
        )
