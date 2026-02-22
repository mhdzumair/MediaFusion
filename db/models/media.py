"""Media metadata models with integer auto-increment PKs.

All images (poster, background, logo) are stored in MediaImage table.
All ratings (IMDb, TMDB, etc.) are stored in MediaRating table.
Cast/Crew are stored via Person, MediaCast, MediaCrew tables.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, ClassVar, Optional

import pytz
from sqlalchemy import Column, Computed, DateTime, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, Relationship, SQLModel

from db.enums import MediaType, NudityStatus
from db.models.base import TimestampMixin
from db.models.links import (
    MediaCatalogLink,
    MediaGenreLink,
    MediaKeywordLink,
    MediaParentalCertificateLink,
)

if TYPE_CHECKING:
    from db.models.cast_crew import MediaCast, MediaCrew
    from db.models.contributions import MetadataSuggestion, MetadataVote
    from db.models.providers import (
        MediaExternalID,
        MediaFusionRating,
        MediaImage,
        MediaRating,
        MetadataProvider,
    )
    from db.models.reference import (
        AkaTitle,
        Catalog,
        Genre,
        Keyword,
        ParentalCertificate,
    )
    from db.models.users import User


class TrailerType(str):
    """Trailer type enum values."""

    TRAILER = "trailer"
    TEASER = "teaser"
    CLIP = "clip"
    FEATURETTE = "featurette"
    BEHIND_THE_SCENES = "behind_the_scenes"
    BLOOPERS = "bloopers"


class Season(SQLModel, table=True):
    """Series season - primarily for organizing episodes."""

    __tablename__ = "season"
    __table_args__ = (UniqueConstraint("series_id", "season_number"),)

    id: int = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series_metadata.id", index=True)
    season_number: int = Field(index=True)
    name: str | None = None
    overview: str | None = None
    air_date: date | None = None
    episode_count: int = Field(default=0)
    provider_id: int | None = Field(default=None, foreign_key="metadata_provider.id")

    # Relationships
    series: "SeriesMetadata" = Relationship(back_populates="seasons")
    episodes: list["Episode"] = Relationship(
        back_populates="season",
        sa_relationship_kwargs={"order_by": "Episode.episode_number", "cascade": "all, delete-orphan"},
    )


class Episode(SQLModel, table=True):
    """Series episode metadata with multi-provider source tracking.

    Supports MERGE strategy: episodes from different providers are combined.
    User-created episodes are tracked separately with is_user_created/is_user_addition flags.
    """

    __tablename__ = "episode"
    __table_args__ = (
        UniqueConstraint("season_id", "episode_number"),
        Index("idx_episode_external_ids", "imdb_id", "tmdb_id", "tvdb_id"),
        Index(
            "idx_episode_user_created",
            "is_user_created",
            postgresql_where="is_user_created = true",
        ),
        Index(
            "idx_episode_user_addition",
            "is_user_addition",
            postgresql_where="is_user_addition = true",
        ),
    )

    id: int = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", index=True)
    episode_number: int = Field(index=True)
    title: str
    overview: str | None = None
    air_date: date | None = None
    runtime_minutes: int | None = None

    # External IDs for cross-referencing
    imdb_id: str | None = Field(default=None, index=True)
    tmdb_id: int | None = Field(default=None, index=True)
    tvdb_id: int | None = Field(default=None, index=True)
    provider_id: int | None = Field(default=None, foreign_key="metadata_provider.id")

    # SOURCE TRACKING - For multi-provider MERGE strategy and user content
    source_provider_id: int | None = Field(
        default=None,
        foreign_key="metadata_provider.id",
        description="Provider that originally contributed this episode data",
    )
    created_by_user_id: int | None = Field(
        default=None,
        foreign_key="users.id",
        ondelete="SET NULL",
        description="User who created this episode (for user-created content)",
    )
    is_user_created: bool = Field(
        default=False,
        index=True,
        description="True if user created the entire episode (not from any provider)",
    )
    is_user_addition: bool = Field(
        default=False,
        index=True,
        description="True if user added this episode to existing official series",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    season: Season = Relationship(back_populates="episodes")
    source_provider: Optional["MetadataProvider"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Episode.source_provider_id]"}
    )
    created_by_user: Optional["User"] = Relationship(
        sa_relationship_kwargs={"foreign_keys": "[Episode.created_by_user_id]"}
    )


class Media(TimestampMixin, table=True):
    """Base table for all media (movies, series, TV).

    Uses integer auto-increment PK. External IDs (IMDb, TMDB, TVDB, etc.) are stored
    in the MediaExternalID table for multi-provider support.
    Images are stored in MediaImage table, ratings in MediaRating table.
    Cast/Crew are stored via Person, MediaCast, MediaCrew tables.
    """

    __tablename__ = "media"
    __table_args__ = (
        Index("idx_media_type_title", "type", "title"),
        Column(
            "title_tsv",
            TSVECTOR,
            Computed("to_tsvector('simple'::regconfig, title)"),
            nullable=False,
        ),
        Index("idx_media_title_fts", "title_tsv", postgresql_using="gin"),
        Index(
            "idx_media_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index("idx_media_last_stream_added", "last_stream_added"),
        Index("idx_media_last_stream_added_type", "last_stream_added", "type"),
        Index("idx_media_user_created", "is_user_created"),
        Index("idx_media_created_by_user", "created_by_user_id"),
        Index(
            "idx_media_blocked",
            "is_blocked",
            postgresql_where="is_blocked = true",
        ),
    )

    # Integer auto-increment PK
    id: int = Field(default=None, primary_key=True)
    type: MediaType = Field(index=True)
    title: str
    original_title: str | None = None
    year: int | None = Field(default=None, index=True)
    release_date: date | None = None
    end_date: date | None = None  # For series
    status: str | None = None  # released, in_production, canceled, ended
    runtime_minutes: int | None = None
    description: str | None = None
    tagline: str | None = None
    adult: bool = Field(default=False)
    original_language: str | None = None
    popularity: float | None = None
    website: str | None = None

    # Provider attribution
    primary_provider_id: int | None = Field(default=None, foreign_key="metadata_provider.id")
    created_by_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    is_user_created: bool = Field(default=False, index=True)
    is_public: bool = Field(default=True)  # Visibility for user-created

    # For user-linked content: preserves original user title when linking to official provider
    user_original_title: str | None = Field(
        default=None,
        description="Original user-provided title before linking to official provider",
    )

    # Metadata refresh tracking
    last_refreshed_by_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    last_refreshed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    migrated_from_id: str | None = None  # Original mf ID before migration
    migrated_by_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    migrated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Content guidance (applies to all media types)
    nudity_status: NudityStatus = Field(default=NudityStatus.UNKNOWN, index=True)

    # Content moderation - blocking
    is_blocked: bool = Field(default=False, index=True)
    blocked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    blocked_by_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    block_reason: str | None = Field(default=None, max_length=500)

    # Poster generation
    is_add_title_to_poster: bool = Field(default=False)

    # Scraping tracking
    last_scraped_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_scraped_by_user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="SET NULL")

    # Aggregates
    total_streams: int = Field(default=0, index=True)
    last_stream_added: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        index=True,
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    # External IDs from all providers (IMDb, TMDB, TVDB, etc.)
    external_ids: list["MediaExternalID"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    genres: list["Genre"] = Relationship(
        link_model=MediaGenreLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    catalogs: list["Catalog"] = Relationship(
        link_model=MediaCatalogLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    aka_titles: list["AkaTitle"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})
    keywords: list["Keyword"] = Relationship(
        link_model=MediaKeywordLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    parental_certificates: list["ParentalCertificate"] = Relationship(
        link_model=MediaParentalCertificateLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    # Images from all providers
    images: list["MediaImage"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})
    # Ratings from all providers
    ratings: list["MediaRating"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})
    # MediaFusion community rating
    mediafusion_rating: Optional["MediaFusionRating"] = Relationship(
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete"}
    )
    # Cast and Crew (via Person table)
    cast: list["MediaCast"] = Relationship(
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "order_by": "MediaCast.display_order",
        }
    )
    crew: list["MediaCrew"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})
    # Trailers/Videos
    trailers: list["MediaTrailer"] = Relationship(
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "order_by": "MediaTrailer.is_primary.desc()",
        }
    )
    # Type-specific metadata
    movie_metadata: Optional["MovieMetadata"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"}
    )
    series_metadata: Optional["SeriesMetadata"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"}
    )
    tv_metadata: Optional["TVMetadata"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"}
    )

    # User votes and suggestions
    metadata_votes: list["MetadataVote"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    metadata_suggestions: list["MetadataSuggestion"] = Relationship(
        back_populates="media", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


def get_canonical_external_id_sync(media_id: int, external_ids: list["MediaExternalID"]) -> str:
    """Get the canonical external ID for a media item (sync version).

    Use this when you already have the external_ids loaded.

    Returns IMDb ID if available (for Stremio compatibility), otherwise
    falls back through TVDB -> TMDB -> first available -> mf:{id}.

    Args:
        media_id: The internal media ID
        external_ids: List of loaded MediaExternalID objects
    """
    if not external_ids:
        return f"mf:{media_id}"

    # Priority order for canonical external ID
    priority_order = ["imdb", "tvdb", "tmdb", "mal", "kitsu"]

    # Build a lookup dict
    id_by_provider = {ext.provider: ext.external_id for ext in external_ids}

    # Return first match by priority
    for provider in priority_order:
        if provider in id_by_provider:
            ext_id = id_by_provider[provider]
            # IMDb IDs are returned as-is (already have tt prefix)
            if provider == "imdb":
                return ext_id
            # Other providers use prefix format
            return f"{provider}:{ext_id}"

    # Fallback to first available
    if external_ids:
        first = external_ids[0]
        return f"{first.provider}:{first.external_id}"

    return f"mf:{media_id}"


class MovieMetadata(TimestampMixin, table=True):
    """Movie specific metadata table."""

    __tablename__ = "movie_metadata"
    __table_args__ = (
        UniqueConstraint("media_id"),  # 1:1 with Media
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", unique=True, index=True, ondelete="CASCADE")
    budget: int | None = None
    revenue: int | None = None
    mpaa_rating: str | None = None  # PG-13, R, etc.
    type: ClassVar[MediaType] = MediaType.MOVIE

    # Relationships
    media: Media = Relationship(back_populates="movie_metadata", sa_relationship_kwargs={"uselist": False})


class SeriesMetadata(TimestampMixin, table=True):
    """Series specific metadata table."""

    __tablename__ = "series_metadata"
    __table_args__ = (
        UniqueConstraint("media_id"),  # 1:1 with Media
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", unique=True, index=True, ondelete="CASCADE")
    total_seasons: int | None = None
    total_episodes: int | None = None
    network: str | None = None
    type: ClassVar[MediaType] = MediaType.SERIES

    # Relationships
    media: Media = Relationship(back_populates="series_metadata", sa_relationship_kwargs={"uselist": False})
    seasons: list[Season] = Relationship(
        back_populates="series",
        sa_relationship_kwargs={"order_by": "Season.season_number", "cascade": "all, delete-orphan"},
    )


class TVMetadata(TimestampMixin, table=True):
    """TV channel specific metadata table."""

    __tablename__ = "tv_metadata"
    __table_args__ = (
        UniqueConstraint("media_id"),  # 1:1 with Media
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", unique=True, index=True, ondelete="CASCADE")
    country: str | None = Field(default=None, index=True)
    tv_language: str | None = Field(default=None, index=True)
    type: ClassVar[MediaType] = MediaType.TV

    media: Media = Relationship(back_populates="tv_metadata", sa_relationship_kwargs={"uselist": False})


class MediaTrailer(TimestampMixin, table=True):
    """Trailers and promotional videos for media."""

    __tablename__ = "media_trailer"
    __table_args__ = (
        UniqueConstraint("media_id", "video_key", "site"),
        Index("idx_media_trailer_media", "media_id"),
        Index("idx_media_trailer_type", "trailer_type"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")

    # Video identifiers
    video_key: str  # YouTube video ID, Vimeo ID, etc.
    site: str = Field(default="YouTube")  # YouTube, Vimeo, etc.

    # Metadata
    name: str | None = None  # Trailer name/title
    trailer_type: str = Field(default="trailer")  # trailer, teaser, clip, featurette, etc.
    language: str | None = Field(default="en")  # ISO 639-1 language code
    country: str | None = None  # ISO 3166-1 country code

    # Properties
    is_official: bool = Field(default=True)
    is_primary: bool = Field(default=False)  # Main trailer to show
    size: int | None = None  # Video quality: 360, 480, 720, 1080, 2160

    # Relationships
    media: Media = Relationship(back_populates="trailers")
