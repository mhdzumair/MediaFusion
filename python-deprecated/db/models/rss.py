"""RSS Feed models for torrent scraping - all user-based."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from db.models.base import TimestampMixin

if TYPE_CHECKING:
    from db.models.users import User


class RSSFeed(TimestampMixin, table=True):
    """
    RSS Feed configuration for scraping.

    All RSS feeds are user-owned. System feeds are owned by admin users.
    Users can make their feeds public for others to discover.
    """

    __tablename__ = "rss_feed"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_rss_feed_user_url"),
        Index("idx_rss_feed_user", "user_id"),
        Index("idx_rss_feed_active", "is_active"),
        Index("idx_rss_feed_source", "source"),
        Index("idx_rss_feed_public", "is_public"),
    )

    id: int = Field(default=None, primary_key=True)
    # UUID for external APIs (stremio, sharing)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True)

    # Owner
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")

    # Basic info
    name: str = Field(index=True)
    url: str
    is_active: bool = Field(default=True, index=True)

    # Visibility - if True, other users can discover and use this feed
    is_public: bool = Field(default=False, index=True)

    # Source and torrent configuration
    source: str | None = Field(default=None, index=True)
    torrent_type: str = Field(default="public")  # public, private, webseed
    auto_detect_catalog: bool = Field(default=False)

    # JSON fields for complex nested structures
    parsing_patterns: dict | None = Field(default=None, sa_type=JSON)
    filters: dict | None = Field(default=None, sa_type=JSON)
    metrics: dict | None = Field(default=None, sa_type=JSON)

    last_scraped_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Relationships
    user: "User" = Relationship(back_populates="rss_feeds")
    catalog_patterns: list["RSSFeedCatalogPattern"] = Relationship(
        back_populates="rss_feed",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class RSSFeedCatalogPattern(SQLModel, table=True):
    """RSS Feed catalog patterns for auto-detection."""

    __tablename__ = "rss_feed_catalog_pattern"
    __table_args__ = (Index("idx_rss_pattern_feed", "rss_feed_id"),)

    id: int = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True)
    rss_feed_id: int = Field(foreign_key="rss_feed.id", index=True, ondelete="CASCADE")
    name: str | None = None
    regex: str
    enabled: bool = Field(default=True)
    case_sensitive: bool = Field(default=False)
    target_catalogs: list[str] = Field(default_factory=list, sa_type=JSON)

    # Relationships
    rss_feed: RSSFeed = Relationship(back_populates="catalog_patterns")
