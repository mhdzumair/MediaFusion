from datetime import datetime
from enum import Enum as PyEnum
from typing import ClassVar

import pytz
from sqlalchemy import DateTime, BigInteger, UniqueConstraint, Index, JSON
from sqlmodel import SQLModel, Field


# Enums
class MediaType(str, PyEnum):
    MOVIE = "movie"
    SERIES = "series"
    TV = "tv"
    EVENTS = "events"


class IndexerType(str, PyEnum):
    FREELEACH = "freeleech"
    SEMI_PRIVATE = "semi-private"
    PRIVATE = "private"


class NudityStatus(str, PyEnum):
    NONE = "None"
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"
    UNKNOWN = "Unknown"


# Base Models and Mixins
class TimestampMixin(SQLModel):
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"onupdate": datetime.now(pytz.UTC)},
        index=True,
        sa_type=DateTime(timezone=True),
    )


class BaseMetadata(TimestampMixin, table=True):
    """Base table for all metadata"""

    __tablename__ = "base_metadata"
    __table_args__ = (
        Index("idx_base_meta_type_title", "type", "title"),
        UniqueConstraint("title", "year"),
        # Pattern matching index for partial title searches
        Index(
            "idx_base_title_search",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: str = Field(primary_key=True)
    type: MediaType = Field(index=True)
    title: str
    year: int | None = Field(default=None)
    poster: str | None
    is_poster_working: bool = Field(default=True)
    is_add_title_to_poster: bool = Field(default=False)
    background: str | None
    description: str | None
    runtime: str | None
    website: str | None


class MovieMetadata(TimestampMixin, table=True):
    """Movie specific metadata table"""

    __tablename__ = "movie_metadata"

    id: str = Field(
        primary_key=True, foreign_key="base_metadata.id", ondelete="CASCADE"
    )
    imdb_rating: float | None = Field(default=None, index=True)
    parent_guide_nudity_status: NudityStatus = Field(
        default=NudityStatus.UNKNOWN, index=True
    )
    type: ClassVar[MediaType] = MediaType.MOVIE


class SeriesMetadata(TimestampMixin, table=True):
    """Series specific metadata table"""

    __tablename__ = "series_metadata"

    id: str = Field(
        primary_key=True, foreign_key="base_metadata.id", ondelete="CASCADE"
    )
    end_year: int | None = Field(default=None, index=True)
    imdb_rating: float | None = Field(default=None, index=True)
    parent_guide_nudity_status: NudityStatus = Field(
        default=NudityStatus.UNKNOWN, index=True
    )
    type: ClassVar[MediaType] = MediaType.SERIES


class TVMetadata(TimestampMixin, table=True):
    """TV specific metadata table"""

    __tablename__ = "tv_metadata"

    id: str = Field(
        primary_key=True, foreign_key="base_metadata.id", ondelete="CASCADE"
    )
    country: str | None = Field(default=None, index=True)
    tv_language: str | None = Field(default=None, index=True)
    logo: str | None
    type: ClassVar[MediaType] = MediaType.TV


# Supporting Models
class Genre(SQLModel, table=True):
    __tablename__ = "genre"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class MediaGenreLink(SQLModel, table=True):
    __tablename__ = "media_genre_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    genre_id: int = Field(foreign_key="genre.id", primary_key=True, ondelete="CASCADE")


class Catalog(SQLModel, table=True):
    __tablename__ = "catalog"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class MediaCatalogLink(SQLModel, table=True):
    __tablename__ = "media_catalog_link"
    __table_args__ = {"postgresql_partition_by": "LIST (catalog_id)"}

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    catalog_id: int = Field(
        foreign_key="catalog.id", primary_key=True, ondelete="CASCADE"
    )
    priority: int = Field(default=0, index=True)

    class Config:
        arbitrary_types_allowed = True


class AkaTitle(SQLModel, table=True):
    __tablename__ = "aka_title"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    media_id: str = Field(
        foreign_key="base_metadata.id", index=True, ondelete="CASCADE"
    )


class ParentalCertificate(SQLModel, table=True):
    __tablename__ = "parental_certificate"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class MediaParentalCertificateLink(SQLModel, table=True):
    __tablename__ = "media_parental_certificate_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    certificate_id: int = Field(
        foreign_key="parental_certificate.id", primary_key=True, ondelete="CASCADE"
    )


class Star(SQLModel, table=True):
    __tablename__ = "star"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)


class MediaStarLink(SQLModel, table=True):
    __tablename__ = "media_star_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    star_id: int = Field(foreign_key="star.id", primary_key=True, ondelete="CASCADE")


# Stream Models
class TorrentStream(TimestampMixin, table=True):
    __tablename__ = "torrent_stream"
    __table_args__ = (
        Index(
            "idx_torrent_stream_meta_blocked",
            "meta_id",
            postgresql_where="NOT is_blocked",
        ),
        Index(
            "idx_torrent_meta_created",
            "meta_id",
            "created_at",
            postgresql_where="NOT is_blocked",
        ),
        Index(
            "idx_torrent_meta_source",
            "meta_id",
            "source",
        ),
    )

    id: str = Field(primary_key=True)
    meta_id: str = Field(foreign_key="base_metadata.id", index=True, ondelete="CASCADE")
    torrent_name: str
    size: int = Field(sa_type=BigInteger, gt=0)
    filename: str | None
    file_index: int | None
    source: str = Field(index=True)
    resolution: str | None = Field(default=None)
    codec: str | None
    quality: str | None = Field(default=None)
    audio: str | None
    seeders: int | None = Field(default=None)
    is_blocked: bool = Field(default=False, index=True)
    indexer_flag: IndexerType = Field(default=IndexerType.FREELEACH)


class Season(SQLModel, table=True):
    __tablename__ = "season"
    __table_args__ = (
        Index("idx_season_torrent_number", "torrent_stream_id", "season_number"),
    )

    id: int | None = Field(default=None, primary_key=True)
    torrent_stream_id: str = Field(foreign_key="torrent_stream.id", ondelete="CASCADE")
    season_number: int


class Episode(SQLModel, table=True):
    __tablename__ = "episode"
    __table_args__ = (UniqueConstraint("season_id", "episode_number"),)

    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id", ondelete="CASCADE")
    episode_number: int = Field(index=True)
    filename: str | None
    size: int | None = Field(default=None, sa_type=BigInteger)
    file_index: int | None
    title: str | None
    released: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
    )


class TVStream(TimestampMixin, table=True):
    __tablename__ = "tv_stream"
    __table_args__ = (
        UniqueConstraint("url", "ytId"),
        Index("idx_tv_stream_meta_working", "meta_id", "is_working"),
    )

    id: int | None = Field(default=None, primary_key=True)
    meta_id: str = Field(foreign_key="base_metadata.id", index=True, ondelete="CASCADE")
    name: str
    url: str | None = Field(default=None)
    ytId: str | None = Field(default=None)
    externalUrl: str | None
    source: str = Field(index=True)
    country: str | None = Field(default=None, index=True)
    is_working: bool = Field(default=True, index=True)
    test_failure_count: int = Field(default=0)
    drm_key_id: str | None
    drm_key: str | None
    behaviorHints: dict | None = Field(default=None, sa_type=JSON)


# Stream Relationship Models
class Language(SQLModel, table=True):
    __tablename__ = "language"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class TorrentLanguageLink(SQLModel, table=True):
    __tablename__ = "torrent_language_link"

    torrent_id: str = Field(
        foreign_key="torrent_stream.id", primary_key=True, ondelete="CASCADE"
    )
    language_id: int = Field(
        foreign_key="language.id", primary_key=True, ondelete="CASCADE"
    )


class AnnounceURL(SQLModel, table=True):
    __tablename__ = "announce_url"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class TorrentAnnounceLink(SQLModel, table=True):
    __tablename__ = "torrent_announce_link"

    torrent_id: str = Field(
        foreign_key="torrent_stream.id", primary_key=True, ondelete="CASCADE"
    )
    announce_id: int = Field(
        foreign_key="announce_url.id", primary_key=True, ondelete="CASCADE"
    )


class Namespace(SQLModel, table=True):
    __tablename__ = "namespace"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class TVStreamNamespaceLink(SQLModel, table=True):
    __tablename__ = "tv_stream_namespace_link"

    stream_id: int = Field(
        foreign_key="tv_stream.id", primary_key=True, ondelete="CASCADE"
    )
    namespace_id: int = Field(
        foreign_key="namespace.id", primary_key=True, ondelete="CASCADE"
    )
