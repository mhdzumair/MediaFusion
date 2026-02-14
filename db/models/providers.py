"""Metadata and rating provider models."""

from datetime import datetime
from typing import TYPE_CHECKING

import pytz
from sqlalchemy import JSON, DateTime, UniqueConstraint, text
from sqlmodel import Field, Index, Relationship, SQLModel

from db.models.base import TimestampMixin

if TYPE_CHECKING:
    from db.models.media import Media


class MetadataProvider(SQLModel, table=True):
    """Metadata providers - sources for media information.

    Examples: tmdb, tvdb, imdb, mal, kitsu, fanart, mediafusion
    """

    __tablename__ = "metadata_provider"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # tmdb, tvdb, imdb, mal, kitsu, fanart, mediafusion
    display_name: str  # TMDB, TheTVDB, IMDb, etc.
    api_base_url: str | None = None
    is_external: bool = Field(default=True)  # false for mediafusion
    is_active: bool = Field(default=True)
    priority: int = Field(default=100)  # lower = higher priority
    default_priority: int = Field(default=100)  # Default priority for conflict resolution
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )


class RatingProvider(SQLModel, table=True):
    """Rating providers - sources for ratings.

    Examples: imdb, tmdb, trakt, letterboxd, rottentomatoes, metacritic, rogerebert, mediafusion
    """

    __tablename__ = "rating_provider"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    display_name: str
    icon_url: str | None = None
    max_rating: float = Field(default=10.0)  # 10.0, 100, 5.0, etc.
    is_percentage: bool = Field(default=False)  # true for RT, Metacritic
    is_active: bool = Field(default=True)
    display_order: int = Field(default=100)


class MediaExternalID(TimestampMixin, table=True):
    """External IDs from all providers (IMDb, TMDB, TVDB, MAL, etc.)

    Maps Media to external provider IDs. One Media can have multiple external IDs
    (e.g., both IMDb and TMDB IDs).

    Canonical ID Format:
    - IMDb: tt{id} (e.g., tt1234567) - stored as external_id with provider='imdb'
    - TMDB: {id} (e.g., 550) - stored with provider='tmdb'
    - TVDB: {id} (e.g., 81189) - stored with provider='tvdb'
    - MAL: {id} (e.g., 21) - stored with provider='mal'
    - Kitsu: {id} (e.g., 1) - stored with provider='kitsu'

    Performance: JOIN approach benchmarked at 14,400 QPS at 50 concurrent connections.
    """

    __tablename__ = "media_external_id"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_provider_external_id"),
        Index("idx_media_external_provider_id", "provider", "external_id"),
        Index("idx_media_external_media", "media_id"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    provider: str = Field(index=True)  # imdb, tmdb, tvdb, mal, kitsu, anidb, trakt
    external_id: str  # Provider-specific ID (tt1234567 for imdb, 550 for tmdb, etc.)

    # Relationships
    media: "Media" = Relationship(back_populates="external_ids")


class MediaImage(SQLModel, table=True):
    """Media images from various providers.

    Stores all images (posters, backgrounds, logos) from different providers.
    """

    __tablename__ = "media_image"
    __table_args__ = (UniqueConstraint("media_id", "provider_id", "image_type", "url", name="uq_media_image"),)

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    provider_id: int = Field(foreign_key="metadata_provider.id")
    image_type: str = Field(index=True)  # poster, background, logo, banner, thumb, clearart
    url: str
    language: str | None = None  # en, ja, null
    aspect_ratio: float | None = None
    width: int | None = None
    height: int | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    is_primary: bool = Field(default=False, index=True)
    display_order: int = Field(default=100)

    # Relationships
    # media: "Media" = Relationship()
    provider: MetadataProvider = Relationship()


class EpisodeImage(SQLModel, table=True):
    """Episode images from various providers.

    Stores episode stills/thumbnails from different providers.
    """

    __tablename__ = "episode_image"
    __table_args__ = (
        UniqueConstraint("episode_id", "provider_id", "image_type", "url", name="uq_episode_image"),
        Index("idx_episode_image_episode", "episode_id"),
    )

    id: int = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    provider_id: int = Field(foreign_key="metadata_provider.id")
    image_type: str = Field(default="still")  # still, thumbnail
    url: str
    language: str | None = None
    aspect_ratio: float | None = None
    width: int | None = None
    height: int | None = None
    is_primary: bool = Field(default=False, index=True)

    # Relationships
    provider: MetadataProvider = Relationship()


class MediaRating(SQLModel, table=True):
    """Multi-provider rating storage.

    Stores ratings from external providers like IMDb, TMDB, Trakt, etc.
    """

    __tablename__ = "media_rating"
    __table_args__ = (UniqueConstraint("media_id", "rating_provider_id", "rating_type", name="uq_media_rating"),)

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    rating_provider_id: int = Field(foreign_key="rating_provider.id")
    rating: float  # Normalized value (0-10)
    rating_raw: float | None = None  # Original scale value
    vote_count: int | None = None
    rating_type: str | None = None  # audience, critic, fresh, certified_fresh
    certification: str | None = None  # for RT: fresh, rotten, certified_fresh
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    provider: RatingProvider = Relationship()


class MediaFusionRating(SQLModel, table=True):
    """MediaFusion community ratings - aggregated user votes.

    One record per media item, aggregates all user votes.
    """

    __tablename__ = "mediafusion_rating"

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", unique=True, index=True)
    average_rating: float = Field(default=0.0)  # 1-10 scale
    total_votes: int = Field(default=0)
    upvotes: int = Field(default=0)
    downvotes: int = Field(default=0)
    # Detailed breakdown
    five_star_count: int = Field(default=0)
    four_star_count: int = Field(default=0)
    three_star_count: int = Field(default=0)
    two_star_count: int = Field(default=0)
    one_star_count: int = Field(default=0)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )


class ProviderMetadata(SQLModel, table=True):
    """Cached metadata from external providers with priority-based conflict resolution.

    Stores structured data per provider with priority for field-level conflict resolution.
    Uses waterfall fallback: if top priority provider has empty data, falls back to next.

    Priority System:
    - Lower priority value = higher priority
    - IMDb: 10, TVDB: 15, TMDB: 20, MAL: 25, Kitsu: 30, Fanart: 50, MediaFusion: 100
    """

    __tablename__ = "provider_metadata"
    __table_args__ = (
        Index(
            "idx_provider_metadata_canonical",
            "media_id",
            postgresql_where=text("is_canonical = true"),
        ),
        Index("idx_provider_metadata_media_provider", "media_id", "provider_id"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    provider_id: int = Field(foreign_key="metadata_provider.id")
    provider_content_id: str  # ID used by this provider

    # Structured fields (not just raw_data) for waterfall resolution
    title: str | None = None
    original_title: str | None = None
    description: str | None = None
    tagline: str | None = None
    release_date: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    runtime: int | None = None
    popularity: float | None = None

    # Priority & canonical tracking
    priority: int = Field(default=100)  # Lower = higher priority
    is_canonical: bool = Field(default=False)  # Is this the source of Media canonical data

    # Cache management
    raw_data: dict | None = Field(default=None, sa_type=JSON)  # JSONB - full API response
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Relationships
    provider: MetadataProvider = Relationship()
