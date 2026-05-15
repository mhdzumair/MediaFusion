"""User content models - library items, catalogs, subscriptions."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import pytz
from sqlalchemy import DateTime, Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from db.models.base import TimestampMixin

if TYPE_CHECKING:
    from db.models.users import User


class UserCatalog(TimestampMixin, table=True):
    """
    User-created custom catalog.

    Users can create their own catalogs, add media items, and share them
    with other users via share_code.
    """

    __tablename__ = "user_catalog"
    __table_args__ = (
        Index("idx_user_catalog_owner", "user_id"),
        Index("idx_user_catalog_public", "is_public"),
        Index("idx_user_catalog_listed", "is_listed"),
    )

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    name: str
    description: str | None = None
    poster_url: str | None = None

    # Visibility settings
    is_public: bool = Field(default=False, index=True)  # Anyone can view
    is_listed: bool = Field(default=False, index=True)  # Discoverable in public catalogs
    share_code: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8], unique=True, index=True
    )  # For sharing via link

    # Cached aggregates
    item_count: int = Field(default=0)
    subscriber_count: int = Field(default=0)

    # Relationships
    items: list["UserCatalogItem"] = Relationship(
        back_populates="catalog",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    subscriptions: list["UserCatalogSubscription"] = Relationship(
        back_populates="catalog",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class UserCatalogItem(SQLModel, table=True):
    """
    Item in a user's custom catalog.

    Links a media item to a user catalog with optional notes and ordering.
    """

    __tablename__ = "user_catalog_item"
    __table_args__ = (
        UniqueConstraint("catalog_id", "media_id"),
        Index("idx_catalog_item_catalog", "catalog_id"),
        Index("idx_catalog_item_media", "media_id"),
        Index("idx_catalog_item_order", "catalog_id", "display_order"),
    )

    id: int = Field(default=None, primary_key=True)
    catalog_id: int = Field(foreign_key="user_catalog.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    display_order: int = Field(default=0, index=True)
    notes: str | None = None  # User's personal notes about this item
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    catalog: UserCatalog = Relationship(back_populates="items")


class UserCatalogSubscription(SQLModel, table=True):
    """
    User subscription to another user's catalog.

    When a user subscribes to a public catalog, they can see updates
    and access its contents in their own catalog list.
    """

    __tablename__ = "user_catalog_subscription"
    __table_args__ = (
        UniqueConstraint("user_id", "catalog_id"),
        Index("idx_subscription_user", "user_id"),
        Index("idx_subscription_catalog", "catalog_id"),
    )

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    catalog_id: int = Field(foreign_key="user_catalog.id", index=True, ondelete="CASCADE")
    subscribed_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    catalog: UserCatalog = Relationship(back_populates="subscriptions")


class UserLibraryItem(SQLModel, table=True):
    """
    User's personal library - saved movies, series, TV channels.

    This is the simple "favorites" or "watchlist" functionality.
    """

    __tablename__ = "user_library_item"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id"),
        Index("idx_library_user", "user_id"),
        Index("idx_library_media", "media_id"),
        Index("idx_library_type", "catalog_type"),
        Index("idx_library_added", "added_at"),
    )

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    catalog_type: str  # 'movie', 'series', 'tv'

    # Cached data for display
    title_cached: str = Field(default="")
    poster_cached: str | None = None

    added_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    user: "User" = Relationship(back_populates="library_items")
