"""Junction/link tables for many-to-many relationships.

All link tables use integer foreign keys to match the new integer PK schema.

Note: Star/MediaStarLink has been replaced by Person/MediaCast in cast_crew.py
Note: Namespace has been removed - use user ownership with is_public flag
"""

from datetime import datetime

import pytz
from sqlalchemy import BigInteger, DateTime, Index
from sqlmodel import Field, SQLModel


class MediaGenreLink(SQLModel, table=True):
    """Link between media and genres."""

    __tablename__ = "media_genre_link"

    media_id: int = Field(foreign_key="media.id", primary_key=True, ondelete="CASCADE")
    genre_id: int = Field(foreign_key="genre.id", primary_key=True, index=True, ondelete="CASCADE")


class MediaCatalogLink(SQLModel, table=True):
    """Link between media and catalogs."""

    __tablename__ = "media_catalog_link"

    media_id: int = Field(foreign_key="media.id", primary_key=True, ondelete="CASCADE")
    catalog_id: int = Field(foreign_key="catalog.id", primary_key=True, index=True, ondelete="CASCADE")


class MediaParentalCertificateLink(SQLModel, table=True):
    """Link between media and parental certificates."""

    __tablename__ = "media_parental_certificate_link"

    media_id: int = Field(foreign_key="media.id", primary_key=True, ondelete="CASCADE")
    certificate_id: int = Field(
        foreign_key="parental_certificate.id",
        primary_key=True,
        index=True,
        ondelete="CASCADE",
    )


class MediaKeywordLink(SQLModel, table=True):
    """Link between media and keywords."""

    __tablename__ = "media_keyword_link"

    media_id: int = Field(foreign_key="media.id", primary_key=True, ondelete="CASCADE")
    keyword_id: int = Field(foreign_key="keyword.id", primary_key=True, index=True, ondelete="CASCADE")


class MediaProductionCompanyLink(SQLModel, table=True):
    """Link between media and production companies."""

    __tablename__ = "media_production_company_link"

    media_id: int = Field(foreign_key="media.id", primary_key=True, ondelete="CASCADE")
    company_id: int = Field(
        foreign_key="production_company.id",
        primary_key=True,
        index=True,
        ondelete="CASCADE",
    )


class StreamLanguageLink(SQLModel, table=True):
    """Link between streams and languages - unified for all stream types."""

    __tablename__ = "stream_language_link"

    stream_id: int = Field(foreign_key="stream.id", primary_key=True, ondelete="CASCADE")
    language_id: int = Field(foreign_key="language.id", primary_key=True, index=True, ondelete="CASCADE")
    language_type: str = Field(default="audio")  # audio, subtitle


class StreamAudioLink(SQLModel, table=True):
    """Link between streams and audio formats - normalized audio codec storage."""

    __tablename__ = "stream_audio_link"

    stream_id: int = Field(foreign_key="stream.id", primary_key=True, ondelete="CASCADE")
    audio_format_id: int = Field(foreign_key="audio_format.id", primary_key=True, index=True, ondelete="CASCADE")


class StreamChannelLink(SQLModel, table=True):
    """Link between streams and audio channel configurations."""

    __tablename__ = "stream_channel_link"

    stream_id: int = Field(foreign_key="stream.id", primary_key=True, ondelete="CASCADE")
    channel_id: int = Field(foreign_key="audio_channel.id", primary_key=True, index=True, ondelete="CASCADE")


class StreamHDRLink(SQLModel, table=True):
    """Link between streams and HDR formats.

    A stream can have multiple HDR formats (e.g., HDR10 + Dolby Vision combo).
    """

    __tablename__ = "stream_hdr_link"

    stream_id: int = Field(foreign_key="stream.id", primary_key=True, ondelete="CASCADE")
    hdr_format_id: int = Field(foreign_key="hdr_format.id", primary_key=True, index=True, ondelete="CASCADE")


class TorrentTrackerLink(SQLModel, table=True):
    """Link between torrents and trackers - optimized with integer IDs."""

    __tablename__ = "torrent_tracker_link"

    torrent_id: int = Field(foreign_key="torrent_stream.id", primary_key=True, ondelete="CASCADE")
    tracker_id: int = Field(foreign_key="tracker.id", primary_key=True, index=True, ondelete="CASCADE")


class StreamMediaLink(SQLModel, table=True):
    """
    Many-to-many link between streams and media with file-level granularity.

    This is the KEY table for linking streams to metadata. It supports:
    - Single stream to single metadata (normal case)
    - Single stream to multiple metadata (multi-movie torrent pack)
    - Specific file within stream to specific metadata (link file_index=2 to "Movie 2")
    - User-created links (user links their torrent to their custom metadata)

    Examples:
    1. Normal torrent: 1 row (file_index=NULL, links whole torrent to media)
    2. Multi-movie pack (3 movies in 1 torrent): 3 rows:
       - stream_id=1, media_id=100, file_index=0 (first movie)
       - stream_id=1, media_id=101, file_index=1 (second movie)
       - stream_id=1, media_id=102, file_index=2 (third movie)
    3. User custom link: User creates metadata (media_id=200), links existing
       stream via new StreamMediaLink row
    """

    __tablename__ = "stream_media_link"
    __table_args__ = (
        Index("idx_stream_media_stream", "stream_id"),
        Index("idx_stream_media_media", "media_id"),
        Index("idx_stream_media_link_media_stream", "media_id", "stream_id"),  # Composite for lookups
        Index("idx_stream_media_user", "linked_by_user_id"),
        Index("idx_stream_media_primary", "stream_id", "is_primary"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", ondelete="CASCADE")  # indexes via __table_args__
    media_id: int = Field(foreign_key="media.id", ondelete="CASCADE")  # indexes via __table_args__

    # User who created this link (NULL if system/scraper)
    linked_by_user_id: int | None = Field(
        default=None,
        foreign_key="users.id",  # index via __table_args__
    )

    # File-level granularity for multi-movie torrents
    file_index: int | None = None  # NULL=all files, N=specific file in torrent/archive
    filename: str | None = None  # Specific filename pattern match
    file_size: int | None = Field(default=None, sa_type=BigInteger)  # Size of linked content

    # Metadata
    is_primary: bool = Field(default=True)  # index via idx_stream_media_primary composite
    is_verified: bool = Field(default=False)  # Admin/trusted user verified
    confidence_score: float | None = None  # 0-1 for auto-match

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )
