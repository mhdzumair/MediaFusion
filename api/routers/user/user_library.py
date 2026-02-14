"""
User Library API endpoints for managing user's personal content library.
Allows users to save movies, series, and TV channels for quick access.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.dependencies import get_profile_context
from api.routers.user.auth import require_auth
from db.config import settings
from db.crud.media import get_all_external_ids_batch
from db.database import get_async_session
from db.models import MediaImage, User, UserLibraryItem
from utils.profile_context import ProfileContext

router = APIRouter(prefix="/api/v1/library", tags=["User Library"])


# ============================================
# Pydantic Schemas
# ============================================


class ExternalIds(BaseModel):
    """External IDs for a media item"""

    imdb: str | None = None
    tmdb: int | None = None
    tvdb: int | None = None
    mal: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalIds":
        return cls(
            imdb=data.get("imdb"),
            tmdb=int(data["tmdb"]) if data.get("tmdb") else None,
            tvdb=int(data["tvdb"]) if data.get("tvdb") else None,
            mal=int(data["mal"]) if data.get("mal") else None,
        )


class LibraryItemCreate(BaseModel):
    """Request to add item to library"""

    media_id: int  # Internal media ID
    catalog_type: Literal["movie", "series", "tv"]


class LibraryItemResponse(BaseModel):
    """Response for a library item"""

    id: int  # Library item ID
    media_id: int  # FK to media table
    external_ids: ExternalIds  # All external IDs for display
    catalog_type: str
    title: str
    poster: str | None = None
    added_at: datetime


class LibraryListResponse(BaseModel):
    """Paginated library list response"""

    items: list[LibraryItemResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class LibraryStatsResponse(BaseModel):
    """Library statistics"""

    total_items: int
    movies: int
    series: int
    tv: int


# ============================================
# Helper Functions
# ============================================


def get_poster_url(
    media_id: int,
    catalog_type: str,
    actual_poster: str | None = None,
    imdb_id: str | None = None,
    rpdb_api_key: str | None = None,
) -> str | None:
    """
    Generate poster URL for a metadata item.
    Priority:
    1. RPDB poster (for IMDB IDs with API key)
    2. Actual poster from database
    3. Generated poster URL using host_url
    """
    # Use RPDB if API key is configured and external_id is an IMDB ID
    if imdb_id and rpdb_api_key:
        return f"https://api.ratingposterdb.com/{rpdb_api_key}/imdb/poster-default/{imdb_id}.jpg?fallback=true"

    if actual_poster:
        return actual_poster

    # Use imdb_id if available, otherwise use media_id
    poster_id = imdb_id or f"mf:{media_id}"
    return f"{settings.host_url}/poster/{catalog_type}/{poster_id}.jpg"


async def _get_library_item_with_media(
    session: AsyncSession,
    item: UserLibraryItem,
    rpdb_api_key: str | None = None,
    ext_ids_dict: dict | None = None,
) -> LibraryItemResponse:
    """Convert UserLibraryItem to LibraryItemResponse with Media lookup."""
    # Use pre-fetched external IDs or fetch them
    if ext_ids_dict is None:
        from db.crud.media import get_all_external_ids_dict

        ext_ids_dict = await get_all_external_ids_dict(session, item.media_id)

    imdb_id = ext_ids_dict.get("imdb")

    return LibraryItemResponse(
        id=item.id,
        media_id=item.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        catalog_type=item.catalog_type,
        title=item.title_cached,
        poster=get_poster_url(item.media_id, item.catalog_type, item.poster_cached, imdb_id, rpdb_api_key),
        added_at=item.added_at,
    )


# ============================================
# API Endpoints
# ============================================


@router.get("", response_model=LibraryListResponse)
async def get_library(
    catalog_type: Literal["movie", "series", "tv"] | None = Query(None, description="Filter by type"),
    search: str | None = Query(None, description="Search by title"),
    sort: Literal["added", "title"] | None = Query("added", description="Sort order"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    profile_ctx: ProfileContext = Depends(get_profile_context),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get user's library items with filtering and pagination.
    """
    offset = (page - 1) * page_size

    # Get user's RPDB API key from profile context
    rpdb_api_key = profile_ctx.rpdb_api_key

    # Base query - select library items (external_id lookup done after query)
    base_query = select(UserLibraryItem).where(UserLibraryItem.user_id == profile_ctx.user_id)
    count_query = select(func.count(UserLibraryItem.id)).where(UserLibraryItem.user_id == profile_ctx.user_id)

    # Apply catalog type filter
    if catalog_type:
        base_query = base_query.where(UserLibraryItem.catalog_type == catalog_type)
        count_query = count_query.where(UserLibraryItem.catalog_type == catalog_type)

    # Apply search filter
    if search:
        search_pattern = f"%{search}%"
        base_query = base_query.where(UserLibraryItem.title_cached.ilike(search_pattern))
        count_query = count_query.where(UserLibraryItem.title_cached.ilike(search_pattern))

    # Apply sorting
    if sort == "added":
        base_query = base_query.order_by(UserLibraryItem.added_at.desc())
    elif sort == "title":
        base_query = base_query.order_by(UserLibraryItem.title_cached.asc())

    # Apply pagination
    base_query = base_query.offset(offset).limit(page_size)

    # Execute queries
    result = await session.exec(base_query)
    items = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    if not items:
        return LibraryListResponse(items=[], total=total, page=page, page_size=page_size, has_more=False)

    # Batch lookup all external_ids for all media_ids
    media_ids = [item.media_id for item in items]
    all_external_ids = await get_all_external_ids_batch(session, media_ids)

    # Format response with RPDB support
    library_items = []
    for item in items:
        ext_ids_dict = all_external_ids.get(item.media_id, {})
        imdb_id = ext_ids_dict.get("imdb")
        library_items.append(
            LibraryItemResponse(
                id=item.id,
                media_id=item.media_id,
                external_ids=ExternalIds.from_dict(ext_ids_dict),
                catalog_type=item.catalog_type,
                title=item.title_cached,
                poster=get_poster_url(
                    item.media_id,
                    item.catalog_type,
                    item.poster_cached,
                    imdb_id,
                    rpdb_api_key,
                ),
                added_at=item.added_at,
            )
        )

    return LibraryListResponse(
        items=library_items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.get("/stats", response_model=LibraryStatsResponse)
async def get_library_stats(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Get library statistics for the current user."""
    # Total count
    total_query = select(func.count(UserLibraryItem.id)).where(UserLibraryItem.user_id == current_user.id)
    total_result = await session.exec(total_query)
    total = total_result.one()

    # Count by type
    movies_query = select(func.count(UserLibraryItem.id)).where(
        UserLibraryItem.user_id == current_user.id,
        UserLibraryItem.catalog_type == "movie",
    )
    movies_result = await session.exec(movies_query)
    movies = movies_result.one()

    series_query = select(func.count(UserLibraryItem.id)).where(
        UserLibraryItem.user_id == current_user.id,
        UserLibraryItem.catalog_type == "series",
    )
    series_result = await session.exec(series_query)
    series_count = series_result.one()

    tv_query = select(func.count(UserLibraryItem.id)).where(
        UserLibraryItem.user_id == current_user.id, UserLibraryItem.catalog_type == "tv"
    )
    tv_result = await session.exec(tv_query)
    tv = tv_result.one()

    return LibraryStatsResponse(
        total_items=total,
        movies=movies,
        series=series_count,
        tv=tv,
    )


@router.post("", response_model=LibraryItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_library(
    request: LibraryItemCreate,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Add an item to user's library.
    Uses media_id (internal ID) directly.
    """
    from db.crud.media import get_all_external_ids_dict
    from db.models import Media

    # Verify media exists
    media_query = select(Media).where(Media.id == request.media_id)
    media_result = await session.exec(media_query)
    media = media_result.first()

    if not media:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    # Check if already in library (using media_id)
    existing_query = select(UserLibraryItem).where(
        UserLibraryItem.user_id == current_user.id,
        UserLibraryItem.media_id == request.media_id,
    )
    existing_result = await session.exec(existing_query)
    existing = existing_result.first()

    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Item already in library")

    # Get primary poster from MediaImage table
    actual_poster = None
    poster_query = select(MediaImage).where(
        MediaImage.media_id == request.media_id,
        MediaImage.image_type == "poster",
        MediaImage.is_primary,
    )
    poster_result = await session.exec(poster_query)
    poster_image = poster_result.first()
    if poster_image:
        actual_poster = poster_image.url

    # Create library item with media_id
    library_item = UserLibraryItem(
        user_id=current_user.id,
        media_id=request.media_id,
        catalog_type=request.catalog_type,
        title_cached=media.title,
        poster_cached=actual_poster,
    )

    session.add(library_item)
    await session.commit()
    await session.refresh(library_item)

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, request.media_id)
    imdb_id = ext_ids_dict.get("imdb")

    return LibraryItemResponse(
        id=library_item.id,
        media_id=library_item.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        catalog_type=library_item.catalog_type,
        title=library_item.title_cached,
        poster=get_poster_url(request.media_id, library_item.catalog_type, actual_poster, imdb_id),
        added_at=library_item.added_at,
    )


@router.get("/{item_id}", response_model=LibraryItemResponse)
async def get_library_item(
    item_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific library item."""
    from db.crud.media import get_all_external_ids_dict

    query = select(UserLibraryItem).where(
        UserLibraryItem.id == item_id,
        UserLibraryItem.user_id == current_user.id,
    )
    result = await session.exec(query)
    item = result.first()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library item not found")

    # Get all external IDs
    ext_ids_dict = await get_all_external_ids_dict(session, item.media_id)
    imdb_id = ext_ids_dict.get("imdb")

    return LibraryItemResponse(
        id=item.id,
        media_id=item.media_id,
        external_ids=ExternalIds.from_dict(ext_ids_dict),
        catalog_type=item.catalog_type,
        title=item.title_cached,
        poster=item.poster_cached or get_poster_url(item.media_id, item.catalog_type, None, imdb_id),
        added_at=item.added_at,
    )


@router.get("/check/{media_id}")
async def check_in_library(
    media_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Check if an item is in the user's library by media_id."""
    # Check library using media_id directly
    query = select(UserLibraryItem.id).where(
        UserLibraryItem.media_id == media_id,
        UserLibraryItem.user_id == current_user.id,
    )
    result = await session.exec(query)
    item_id = result.first()

    return {"in_library": item_id is not None, "item_id": item_id if item_id else None}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_library(
    item_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Remove an item from user's library."""
    query = select(UserLibraryItem).where(
        UserLibraryItem.id == item_id,
        UserLibraryItem.user_id == current_user.id,
    )
    result = await session.exec(query)
    item = result.first()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library item not found")

    await session.delete(item)
    await session.commit()


@router.delete("/by-media-id/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_library_by_media_id(
    media_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Remove an item from user's library by media_id."""
    # Find and delete library item using media_id
    query = select(UserLibraryItem).where(
        UserLibraryItem.media_id == media_id,
        UserLibraryItem.user_id == current_user.id,
    )
    result = await session.exec(query)
    item = result.first()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library item not found")

    await session.delete(item)
    await session.commit()
