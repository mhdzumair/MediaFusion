"""
User Catalog API endpoints for creating and managing custom catalogs.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import optional_auth, require_auth
from db.crud import (
    # Catalog items
    add_item_to_catalog,
    create_catalog,
    delete_catalog,
    # Catalog operations
    get_catalog_by_id,
    get_catalog_by_uuid,
    get_catalog_items,
    get_catalogs_for_user,
    get_public_catalogs,
    get_subscribed_catalogs,
    is_subscribed,
    remove_item_from_catalog,
    reorder_catalog_items,
    # Subscriptions
    subscribe_to_catalog,
    unsubscribe_from_catalog,
    update_catalog,
)
from db.database import get_async_session
from db.models import User

router = APIRouter(prefix="/api/v1/user/catalogs", tags=["User Catalogs"])


# ============================================
# Pydantic Schemas
# ============================================


class CatalogCreate(BaseModel):
    """Request to create a catalog"""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    poster: str | None = None
    is_public: bool = False


class CatalogUpdate(BaseModel):
    """Request to update a catalog"""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    poster: str | None = None
    is_public: bool | None = None


class CatalogResponse(BaseModel):
    """Response for a catalog"""

    id: int
    uuid: str
    user_id: int
    name: str
    description: str | None
    poster: str | None
    is_public: bool
    item_count: int
    subscriber_count: int
    created_at: datetime
    updated_at: datetime | None

    class Config:
        from_attributes = True


class CatalogListResponse(BaseModel):
    """Response for catalog list"""

    catalogs: list[CatalogResponse]
    total: int


class CatalogItemAdd(BaseModel):
    """Request to add an item to catalog"""

    media_id: int | None = None
    stream_id: int | None = None
    notes: str | None = Field(None, max_length=500)


class CatalogItemResponse(BaseModel):
    """Response for a catalog item"""

    id: int
    catalog_id: int
    media_id: int | None
    stream_id: int | None
    position: int
    notes: str | None
    added_at: datetime

    class Config:
        from_attributes = True


class CatalogItemListResponse(BaseModel):
    """Response for catalog items list"""

    items: list[CatalogItemResponse]
    total: int


class ReorderRequest(BaseModel):
    """Request to reorder catalog items"""

    item_ids: list[int] = Field(..., min_length=1, description="Item IDs in new order")


# ============================================
# Catalog CRUD Endpoints
# ============================================


@router.post("", response_model=CatalogResponse, status_code=status.HTTP_201_CREATED)
async def create_user_catalog(
    request: CatalogCreate,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create a new custom catalog.
    """
    catalog = await create_catalog(
        session,
        user_id=current_user.id,
        name=request.name,
        description=request.description,
        poster=request.poster,
        is_public=request.is_public,
    )
    await session.commit()

    return CatalogResponse.model_validate(catalog)


@router.get("", response_model=CatalogListResponse)
async def list_user_catalogs(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    List current user's catalogs.
    """
    catalogs = await get_catalogs_for_user(session, current_user.id, limit=limit, offset=offset)

    return CatalogListResponse(
        catalogs=[CatalogResponse.model_validate(c) for c in catalogs],
        total=len(catalogs),
    )


@router.get("/public", response_model=CatalogListResponse)
async def list_public_catalogs(
    session: AsyncSession = Depends(get_async_session),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    List public catalogs for discovery.
    """
    catalogs = await get_public_catalogs(session, limit=limit, offset=offset)

    return CatalogListResponse(
        catalogs=[CatalogResponse.model_validate(c) for c in catalogs],
        total=len(catalogs),
    )


@router.get("/subscribed", response_model=CatalogListResponse)
async def list_subscribed_catalogs(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List catalogs the current user is subscribed to.
    """
    catalogs = await get_subscribed_catalogs(session, current_user.id)

    return CatalogListResponse(
        catalogs=[CatalogResponse.model_validate(c) for c in catalogs],
        total=len(catalogs),
    )


@router.get("/{catalog_id}", response_model=CatalogResponse)
async def get_user_catalog(
    catalog_id: int,
    current_user: User | None = Depends(optional_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get a specific catalog by ID.
    """
    catalog = await get_catalog_by_id(session, catalog_id, load_items=False)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    # Check access permissions
    if not catalog.is_public:
        if not current_user or catalog.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this catalog",
            )

    return CatalogResponse.model_validate(catalog)


@router.get("/share/{uuid}", response_model=CatalogResponse)
async def get_catalog_by_share_link(
    uuid: str,
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get a public catalog by its share UUID.
    """
    catalog = await get_catalog_by_uuid(session, uuid)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if not catalog.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This catalog is not public",
        )

    return CatalogResponse.model_validate(catalog)


@router.patch("/{catalog_id}", response_model=CatalogResponse)
async def update_user_catalog(
    catalog_id: int,
    request: CatalogUpdate,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update a catalog's details.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if catalog.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this catalog",
        )

    # Build update dict from non-None values
    updates = {k: v for k, v in request.model_dump().items() if v is not None}

    if updates:
        catalog = await update_catalog(session, catalog_id, **updates)
        await session.commit()

    return CatalogResponse.model_validate(catalog)


@router.delete("/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_catalog(
    catalog_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a catalog and all its items.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if catalog.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this catalog",
        )

    await delete_catalog(session, catalog_id)
    await session.commit()


# ============================================
# Catalog Items Endpoints
# ============================================


@router.get("/{catalog_id}/items", response_model=CatalogItemListResponse)
async def list_catalog_items(
    catalog_id: int,
    current_user: User | None = Depends(optional_auth),
    session: AsyncSession = Depends(get_async_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    List items in a catalog.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    # Check access
    if not catalog.is_public:
        if not current_user or catalog.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this catalog",
            )

    items = await get_catalog_items(session, catalog_id, limit=limit, offset=offset)

    return CatalogItemListResponse(
        items=[CatalogItemResponse.model_validate(i) for i in items],
        total=len(items),
    )


@router.post(
    "/{catalog_id}/items",
    response_model=CatalogItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_catalog_item(
    catalog_id: int,
    request: CatalogItemAdd,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Add an item (media or stream) to a catalog.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if catalog.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to add to this catalog",
        )

    if not request.media_id and not request.stream_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either media_id or stream_id must be provided",
        )

    item = await add_item_to_catalog(
        session,
        catalog_id=catalog_id,
        media_id=request.media_id,
        stream_id=request.stream_id,
        notes=request.notes,
    )
    await session.commit()

    return CatalogItemResponse.model_validate(item)


@router.delete("/{catalog_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_catalog_item(
    catalog_id: int,
    item_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Remove an item from a catalog.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if catalog.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to remove from this catalog",
        )

    success = await remove_item_from_catalog(session, catalog_id, item_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found in catalog",
        )

    await session.commit()


@router.put("/{catalog_id}/items/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_items(
    catalog_id: int,
    request: ReorderRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Reorder items in a catalog.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if catalog.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to reorder this catalog",
        )

    await reorder_catalog_items(session, catalog_id, request.item_ids)
    await session.commit()


# ============================================
# Subscription Endpoints
# ============================================


@router.post("/{catalog_id}/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_catalog(
    catalog_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Subscribe to a public catalog.
    """
    catalog = await get_catalog_by_id(session, catalog_id)

    if not catalog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog not found",
        )

    if not catalog.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot subscribe to a private catalog",
        )

    if catalog.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to your own catalog",
        )

    # Check if already subscribed
    if await is_subscribed(session, current_user.id, catalog_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Already subscribed to this catalog",
        )

    await subscribe_to_catalog(session, current_user.id, catalog_id)
    await session.commit()

    return {"message": "Subscribed successfully"}


@router.delete("/{catalog_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe_catalog(
    catalog_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Unsubscribe from a catalog.
    """
    success = await unsubscribe_from_catalog(session, current_user.id, catalog_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not subscribed to this catalog",
        )

    await session.commit()


@router.get("/{catalog_id}/subscribed")
async def check_subscription(
    catalog_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Check if current user is subscribed to a catalog.
    """
    subscribed = await is_subscribed(session, current_user.id, catalog_id)
    return {"subscribed": subscribed}
