"""
User Content CRUD operations.

Handles UserCatalog, UserCatalogItem, UserCatalogSubscription, UserLibraryItem.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

import pytz
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import (
    UserCatalog,
    UserCatalogItem,
    UserCatalogSubscription,
    UserLibraryItem,
)

logger = logging.getLogger(__name__)


# =============================================================================
# USER CATALOG CRUD
# =============================================================================


async def get_catalog_by_id(
    session: AsyncSession,
    catalog_id: int,
    *,
    load_items: bool = False,
) -> UserCatalog | None:
    """Get user catalog by ID."""
    query = select(UserCatalog).where(UserCatalog.id == catalog_id)

    if load_items:
        query = query.options(selectinload(UserCatalog.items))

    result = await session.exec(query)
    return result.first()


async def get_catalog_by_uuid(
    session: AsyncSession,
    uuid: str,
    *,
    load_items: bool = False,
) -> UserCatalog | None:
    """Get user catalog by UUID (for sharing)."""
    query = select(UserCatalog).where(UserCatalog.uuid == uuid)

    if load_items:
        query = query.options(selectinload(UserCatalog.items))

    result = await session.exec(query)
    return result.first()


async def get_catalogs_for_user(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[UserCatalog]:
    """Get all catalogs owned by a user."""
    query = (
        select(UserCatalog)
        .where(UserCatalog.user_id == user_id)
        .order_by(UserCatalog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()


async def get_public_catalogs(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[UserCatalog]:
    """Get public catalogs for discovery."""
    query = (
        select(UserCatalog)
        .where(UserCatalog.is_public == True)
        .order_by(UserCatalog.subscriber_count.desc(), UserCatalog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()


async def create_catalog(
    session: AsyncSession,
    user_id: int,
    name: str,
    *,
    description: str | None = None,
    poster: str | None = None,
    is_public: bool = False,
) -> UserCatalog:
    """Create a new user catalog."""
    catalog = UserCatalog(
        user_id=user_id,
        name=name,
        description=description,
        poster=poster,
        is_public=is_public,
    )
    session.add(catalog)
    await session.flush()
    return catalog


async def update_catalog(
    session: AsyncSession,
    catalog_id: int,
    **updates,
) -> UserCatalog | None:
    """Update catalog fields."""
    if not updates:
        return await get_catalog_by_id(session, catalog_id)

    updates["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(sa_update(UserCatalog).where(UserCatalog.id == catalog_id).values(**updates))
    await session.flush()

    return await get_catalog_by_id(session, catalog_id)


async def delete_catalog(
    session: AsyncSession,
    catalog_id: int,
) -> bool:
    """Delete a user catalog."""
    result = await session.exec(sa_delete(UserCatalog).where(UserCatalog.id == catalog_id))
    await session.flush()
    return result.rowcount > 0


# =============================================================================
# USER CATALOG ITEM CRUD
# =============================================================================


async def add_item_to_catalog(
    session: AsyncSession,
    catalog_id: int,
    *,
    media_id: int | None = None,
    stream_id: int | None = None,
    position: int | None = None,
    notes: str | None = None,
) -> UserCatalogItem:
    """Add an item to a user catalog."""
    # Get next position if not specified
    if position is None:
        query = select(func.max(UserCatalogItem.position)).where(UserCatalogItem.catalog_id == catalog_id)
        result = await session.exec(query)
        max_pos = result.first()
        position = (max_pos or 0) + 1

    item = UserCatalogItem(
        catalog_id=catalog_id,
        media_id=media_id,
        stream_id=stream_id,
        position=position,
        notes=notes,
    )
    session.add(item)

    # Update catalog item count
    await session.exec(
        sa_update(UserCatalog).where(UserCatalog.id == catalog_id).values(item_count=UserCatalog.item_count + 1)
    )

    await session.flush()
    return item


async def remove_item_from_catalog(
    session: AsyncSession,
    catalog_id: int,
    item_id: int,
) -> bool:
    """Remove an item from a user catalog."""
    result = await session.exec(
        sa_delete(UserCatalogItem).where(
            UserCatalogItem.id == item_id,
            UserCatalogItem.catalog_id == catalog_id,
        )
    )

    if result.rowcount > 0:
        # Update catalog item count
        await session.exec(
            sa_update(UserCatalog)
            .where(UserCatalog.id == catalog_id)
            .values(item_count=func.greatest(0, UserCatalog.item_count - 1))
        )

    await session.flush()
    return result.rowcount > 0


async def get_catalog_items(
    session: AsyncSession,
    catalog_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[UserCatalogItem]:
    """Get items in a catalog."""
    query = (
        select(UserCatalogItem)
        .where(UserCatalogItem.catalog_id == catalog_id)
        .order_by(UserCatalogItem.position)
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()


async def reorder_catalog_items(
    session: AsyncSession,
    catalog_id: int,
    item_ids: list[int],
) -> None:
    """Reorder items in a catalog."""
    for position, item_id in enumerate(item_ids):
        await session.exec(
            sa_update(UserCatalogItem)
            .where(
                UserCatalogItem.id == item_id,
                UserCatalogItem.catalog_id == catalog_id,
            )
            .values(position=position)
        )
    await session.flush()


# =============================================================================
# USER CATALOG SUBSCRIPTION CRUD
# =============================================================================


async def subscribe_to_catalog(
    session: AsyncSession,
    user_id: int,
    catalog_id: int,
) -> UserCatalogSubscription:
    """Subscribe to a public catalog."""
    subscription = UserCatalogSubscription(
        user_id=user_id,
        catalog_id=catalog_id,
    )
    session.add(subscription)

    # Increment subscriber count
    await session.exec(
        sa_update(UserCatalog)
        .where(UserCatalog.id == catalog_id)
        .values(subscriber_count=UserCatalog.subscriber_count + 1)
    )

    await session.flush()
    return subscription


async def unsubscribe_from_catalog(
    session: AsyncSession,
    user_id: int,
    catalog_id: int,
) -> bool:
    """Unsubscribe from a catalog."""
    result = await session.exec(
        sa_delete(UserCatalogSubscription).where(
            UserCatalogSubscription.user_id == user_id,
            UserCatalogSubscription.catalog_id == catalog_id,
        )
    )

    if result.rowcount > 0:
        # Decrement subscriber count
        await session.exec(
            sa_update(UserCatalog)
            .where(UserCatalog.id == catalog_id)
            .values(subscriber_count=func.greatest(0, UserCatalog.subscriber_count - 1))
        )

    await session.flush()
    return result.rowcount > 0


async def get_subscribed_catalogs(
    session: AsyncSession,
    user_id: int,
) -> Sequence[UserCatalog]:
    """Get catalogs a user is subscribed to."""
    query = (
        select(UserCatalog)
        .join(UserCatalogSubscription)
        .where(UserCatalogSubscription.user_id == user_id)
        .order_by(UserCatalogSubscription.subscribed_at.desc())
    )
    result = await session.exec(query)
    return result.all()


async def is_subscribed(
    session: AsyncSession,
    user_id: int,
    catalog_id: int,
) -> bool:
    """Check if user is subscribed to a catalog."""
    query = select(UserCatalogSubscription.id).where(
        UserCatalogSubscription.user_id == user_id,
        UserCatalogSubscription.catalog_id == catalog_id,
    )
    result = await session.exec(query)
    return result.first() is not None


# =============================================================================
# USER LIBRARY ITEM CRUD
# =============================================================================


async def add_to_library(
    session: AsyncSession,
    user_id: int,
    media_id: int,
    *,
    is_favorite: bool = False,
    is_watchlist: bool = False,
    user_rating: float | None = None,
    notes: str | None = None,
) -> UserLibraryItem:
    """Add media to user's library."""
    item = UserLibraryItem(
        user_id=user_id,
        media_id=media_id,
        is_favorite=is_favorite,
        is_watchlist=is_watchlist,
        user_rating=user_rating,
        notes=notes,
    )
    session.add(item)
    await session.flush()
    return item


async def get_library_item(
    session: AsyncSession,
    user_id: int,
    media_id: int,
) -> UserLibraryItem | None:
    """Get a specific library item."""
    query = select(UserLibraryItem).where(
        UserLibraryItem.user_id == user_id,
        UserLibraryItem.media_id == media_id,
    )
    result = await session.exec(query)
    return result.first()


async def update_library_item(
    session: AsyncSession,
    user_id: int,
    media_id: int,
    **updates,
) -> UserLibraryItem | None:
    """Update a library item."""
    if not updates:
        return await get_library_item(session, user_id, media_id)

    updates["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(
        sa_update(UserLibraryItem)
        .where(
            UserLibraryItem.user_id == user_id,
            UserLibraryItem.media_id == media_id,
        )
        .values(**updates)
    )
    await session.flush()

    return await get_library_item(session, user_id, media_id)


async def remove_from_library(
    session: AsyncSession,
    user_id: int,
    media_id: int,
) -> bool:
    """Remove media from user's library."""
    result = await session.exec(
        sa_delete(UserLibraryItem).where(
            UserLibraryItem.user_id == user_id,
            UserLibraryItem.media_id == media_id,
        )
    )
    await session.flush()
    return result.rowcount > 0


async def get_favorites(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[UserLibraryItem]:
    """Get user's favorite media."""
    query = (
        select(UserLibraryItem)
        .where(
            UserLibraryItem.user_id == user_id,
            UserLibraryItem.is_favorite == True,
        )
        .order_by(UserLibraryItem.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()


async def get_watchlist(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[UserLibraryItem]:
    """Get user's watchlist."""
    query = (
        select(UserLibraryItem)
        .where(
            UserLibraryItem.user_id == user_id,
            UserLibraryItem.is_watchlist == True,
        )
        .order_by(UserLibraryItem.added_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()
