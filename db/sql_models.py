from datetime import datetime
from typing import ClassVar, List, Optional

import pytz
from sqlalchemy import (
    DateTime,
    BigInteger,
    UniqueConstraint,
    Index,
    JSON,
    Column,
    Computed,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import SQLModel, Field, Relationship

from db.enums import MediaType, IndexerType, NudityStatus


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


class MediaGenreLink(SQLModel, table=True):
    __tablename__ = "media_genre_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    genre_id: int = Field(foreign_key="genre.id", primary_key=True, ondelete="CASCADE")


class MediaCatalogLink(SQLModel, table=True):
    __tablename__ = "media_catalog_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    catalog_id: int = Field(
        foreign_key="catalog.id", primary_key=True, ondelete="CASCADE"
    )


class MediaStarLink(SQLModel, table=True):
    __tablename__ = "media_star_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    star_id: int = Field(foreign_key="star.id", primary_key=True, ondelete="CASCADE")


class MediaParentalCertificateLink(SQLModel, table=True):
    __tablename__ = "media_parental_certificate_link"

    media_id: str = Field(
        foreign_key="base_metadata.id", primary_key=True, ondelete="CASCADE"
    )
    certificate_id: int = Field(
        foreign_key="parental_certificate.id", primary_key=True, ondelete="CASCADE"
    )


class TorrentLanguageLink(SQLModel, table=True):
    __tablename__ = "torrent_language_link"

    torrent_id: str = Field(
        foreign_key="torrent_stream.id", primary_key=True, ondelete="CASCADE"
    )
    language_id: int = Field(
        foreign_key="language.id", primary_key=True, ondelete="CASCADE"
    )


class TorrentAnnounceLink(SQLModel, table=True):
    __tablename__ = "torrent_announce_link"

    torrent_id: str = Field(
        foreign_key="torrent_stream.id", primary_key=True, ondelete="CASCADE"
    )
    announce_id: int = Field(
        foreign_key="announce_url.id", primary_key=True, ondelete="CASCADE"
    )


class TVStreamNamespaceLink(SQLModel, table=True):
    __tablename__ = "tv_stream_namespace_link"

    stream_id: int = Field(
        foreign_key="tv_stream.id", primary_key=True, ondelete="CASCADE"
    )
    namespace_id: int = Field(
        foreign_key="namespace.id", primary_key=True, ondelete="CASCADE"
    )


class GenreName(SQLModel):
    name: str


class CatalogName(SQLModel):
    name: str


class ParentalCertificateName(SQLModel):
    name: str


class AkaTitleName(SQLModel):
    title: str


class SeriesSeason(SQLModel, table=True):
    """Series season - primarily for organizing episodes"""

    __tablename__ = "series_season"
    __table_args__ = (UniqueConstraint("series_id", "season_number"),)

    id: int = Field(default=None, primary_key=True)
    series_id: str = Field(foreign_key="series_metadata.id", index=True)
    season_number: int = Field(index=True)

    # Relationships
    series: "SeriesMetadata" = Relationship(back_populates="seasons")
    episodes: List["SeriesEpisode"] = Relationship(
        back_populates="season",
        sa_relationship_kwargs={"order_by": "SeriesEpisode.episode_number"},
    )


class SeriesEpisode(SQLModel, table=True):
    """Series episode metadata from IMDb"""

    __tablename__ = "series_episode"
    __table_args__ = (UniqueConstraint("season_id", "episode_number"),)

    id: int = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="series_season.id", index=True)
    episode_number: int = Field(index=True)
    title: str
    overview: Optional[str] = None
    released: Optional[datetime] = Field(default=None, sa_type=DateTime(timezone=True))
    imdb_rating: Optional[float] = None
    thumbnail: Optional[str] = None

    # Relationships
    season: SeriesSeason = Relationship(back_populates="episodes")


class BaseMetadata(TimestampMixin, table=True):
    """Base table for all metadata"""

    __tablename__ = "base_metadata"
    __table_args__ = (
        Index("idx_base_meta_type_title", "type", "title"),
        UniqueConstraint("title", "year"),
        # Materialized tsvector columns for faster searches with multilingual titles
        Column(
            "title_tsv",
            TSVECTOR,
            Computed("to_tsvector('simple'::regconfig, title)"),
            nullable=False,
        ),
        Index("idx_base_title_fts", "title_tsv", postgresql_using="gin"),
        Index(
            "idx_base_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "idx_base_meta_last_stream_added",
            "last_stream_added",
        ),
        Index("idx_last_stream_added", "last_stream_added", "type"),
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
    last_stream_added: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        index=True,
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    genres: List["Genre"] = Relationship(
        link_model=MediaGenreLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    catalogs: List["Catalog"] = Relationship(
        link_model=MediaCatalogLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    aka_titles: List["AkaTitle"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete"}
    )


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

    # Relationships
    base_metadata: BaseMetadata = Relationship(
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete"}
    )

    parental_certificates: List["ParentalCertificate"] = Relationship(
        link_model=MediaParentalCertificateLink,
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "primaryjoin": "MovieMetadata.id == MediaParentalCertificateLink.media_id",
            "overlaps": "parental_certificates",
        },
    )

    stars: List["Star"] = Relationship(
        link_model=MediaStarLink,
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "primaryjoin": "MovieMetadata.id == MediaStarLink.media_id",
            "overlaps": "stars",
        },
    )


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

    # Relationships
    base_metadata: BaseMetadata = Relationship(
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete"}
    )
    seasons: List[SeriesSeason] = Relationship(
        back_populates="series",
        sa_relationship_kwargs={"order_by": "SeriesSeason.season_number"},
    )
    parental_certificates: List["ParentalCertificate"] = Relationship(
        link_model=MediaParentalCertificateLink,
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "primaryjoin": "SeriesMetadata.id == MediaParentalCertificateLink.media_id",
            "overlaps": "parental_certificates",
        },
    )
    stars: List["Star"] = Relationship(
        link_model=MediaStarLink,
        sa_relationship_kwargs={
            "cascade": "all, delete",
            "primaryjoin": "SeriesMetadata.id == MediaStarLink.media_id",
            "overlaps": "stars",
        },
    )


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

    base_metadata: BaseMetadata = Relationship(
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete"}
    )


# Supporting Models
class Genre(SQLModel, table=True):
    __tablename__ = "genre"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class Catalog(SQLModel, table=True):
    __tablename__ = "catalog"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class AkaTitle(SQLModel, table=True):
    __tablename__ = "aka_title"
    __table_args__ = (
        # Materialized tsvector columns for faster searches with multilingual titles
        Column(
            "title_tsv",
            TSVECTOR,
            Computed("to_tsvector('simple'::regconfig, title)"),
            nullable=False,
        ),
        Index("idx_aka_title_fts", "title_tsv", postgresql_using="gin"),
        Index(
            "idx_aka_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    media_id: str = Field(
        foreign_key="base_metadata.id", index=True, ondelete="CASCADE"
    )


class ParentalCertificate(SQLModel, table=True):
    __tablename__ = "parental_certificate"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class Star(SQLModel, table=True):
    __tablename__ = "star"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)


class EpisodeFile(SQLModel, table=True):
    """Episode file information within a torrent"""

    __tablename__ = "episode_file"
    __table_args__ = (
        UniqueConstraint("torrent_stream_id", "season_number", "episode_number"),
    )

    id: int = Field(default=None, primary_key=True)
    torrent_stream_id: str = Field(foreign_key="torrent_stream.id", index=True)
    season_number: int
    episode_number: int

    # File details - nullable as they might not be known initially
    file_index: Optional[int] = None
    filename: Optional[str] = None
    size: Optional[int] = Field(default=None, sa_type=BigInteger)

    episode_id: Optional[int] = Field(default=None, foreign_key="series_episode.id")

    # Relationships
    torrent_stream: "TorrentStream" = Relationship(back_populates="episode_files")


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
    source: str = Field(index=True)
    resolution: str | None = Field(default=None)
    codec: str | None
    quality: str | None = Field(default=None)
    audio: str | None
    seeders: int | None = Field(default=None)
    is_blocked: bool = Field(default=False, index=True)
    indexer_flag: IndexerType = Field(default=IndexerType.FREELEACH)

    # For movies only (nullable for series)
    filename: Optional[str] = None
    file_index: Optional[int] = None

    # Relationships
    episode_files: List[EpisodeFile] = Relationship(back_populates="torrent_stream")
    languages: List["Language"] = Relationship(
        link_model=TorrentLanguageLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )
    announce_urls: List["AnnounceURL"] = Relationship(
        link_model=TorrentAnnounceLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
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

    # Relationships
    namespaces: List["Namespace"] = Relationship(
        link_model=TVStreamNamespaceLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )


# Stream Relationship Models
class Language(SQLModel, table=True):
    __tablename__ = "language"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class AnnounceURL(SQLModel, table=True):
    __tablename__ = "announce_url"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class Namespace(SQLModel, table=True):
    __tablename__ = "namespace"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
