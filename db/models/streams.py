"""
Unified stream models for all media types.

This implements the unified stream architecture where:
- Stream: Base table with common attributes (quality, codec, etc.)
- Type-specific tables: TorrentStream, HTTPStream, YouTubeStream, etc.

All stream types can be linked to any media type (movie, series, TV)
via StreamMediaLink.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

import pytz
from sqlalchemy import JSON, BigInteger, DateTime, Index, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

from db.enums import TorrentType
from db.models.base import TimestampMixin
from db.models.links import (
    StreamAudioLink,
    StreamChannelLink,
    StreamHDRLink,
    StreamLanguageLink,
    TorrentTrackerLink,
)

if TYPE_CHECKING:
    from db.models.reference import AudioChannel, AudioFormat, HDRFormat, Language


class StreamType(str, Enum):
    """Types of streams supported."""

    TORRENT = "torrent"
    HTTP = "http"
    YOUTUBE = "youtube"
    USENET = "usenet"
    TELEGRAM = "telegram"
    EXTERNAL_LINK = "external_link"
    ACESTREAM = "acestream"


class TrackerStatus(str, Enum):
    """Tracker health status."""

    WORKING = "working"
    FAILING = "failing"
    UNKNOWN = "unknown"


class FileType(str, Enum):
    """Types of files within a stream."""

    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    ARCHIVE = "archive"  # .zip, .rar containing videos
    SAMPLE = "sample"
    TRAILER = "trailer"
    NFO = "nfo"
    OTHER = "other"


class LinkSource(str, Enum):
    """Source of how a file-media link was created."""

    USER = "user"  # User manually linked
    PTT_PARSER = "ptt_parser"  # PTT parsed from filename
    TORRENT_METADATA = "torrent"  # From .torrent file itself
    DEBRID_REALDEBRID = "rd"  # Real-Debrid provided
    DEBRID_ALLDEBRID = "ad"  # AllDebrid provided
    DEBRID_PREMIUMIZE = "pm"  # Premiumize provided
    DEBRID_TORBOX = "tb"  # TorBox provided
    DEBRID_DEBRIDLINK = "dl"  # DebridLink provided
    MANUAL = "manual"  # Admin/system created
    FILENAME = "filename"  # Parsed from filename pattern


class Tracker(SQLModel, table=True):
    """Torrent tracker/announce URL with status tracking.

    Optimized from the old AnnounceURL table to track tracker health.
    """

    __tablename__ = "tracker"

    id: int = Field(default=None, primary_key=True)
    url: str = Field(unique=True, index=True)  # Announce URL
    status: TrackerStatus = Field(default=TrackerStatus.UNKNOWN, index=True)
    success_count: int = Field(default=0)
    failure_count: int = Field(default=0)
    success_rate: float = Field(default=0.0)  # Calculated
    last_checked: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_success: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )


class Stream(TimestampMixin, table=True):
    """
    Base stream table - common attributes for all stream types.

    This is the core table that all stream types extend. Quality attributes
    are stored as explicit columns (not JSONB) for efficient querying.
    """

    __tablename__ = "stream"
    __table_args__ = (
        Index("idx_stream_source", "source"),
        Index("idx_stream_type", "stream_type"),
        Index("idx_stream_active", "is_active"),
        Index("idx_stream_blocked", "is_blocked"),
        Index("idx_stream_public", "is_public"),
        # Composite index for common query pattern
        Index("idx_stream_active_blocked", "is_active", "is_blocked"),
        # Partial index for streams with uploader_user_id set
        Index(
            "idx_stream_uploader_user",
            "uploader_user_id",
            postgresql_where="uploader_user_id IS NOT NULL",
        ),
    )

    id: int = Field(default=None, primary_key=True)
    stream_type: StreamType  # index via idx_stream_type
    name: str  # Display name (torrent name, etc.)
    source: str  # index via idx_stream_source

    # =========================================================================
    # CONTRIBUTOR & RELEASE GROUP - Separate fields for different purposes
    # =========================================================================

    # User-provided uploader name from contribution (could be Anonymous, username, or mistaken value)
    # This is the original value from MongoDB that users entered
    uploader: str | None = Field(default=None, index=True)

    # MediaFusion v5+: FK to user who contributed this stream (with consent)
    # When a user opts to link their account to their contributions
    uploader_user_id: int | None = Field(default=None, foreign_key="users.id")  # partial index via __table_args__

    # Torrent release group from PTT parsing (e.g., "AFG", "RARBG", "MeGusta", "NTb")
    # Extracted automatically from torrent name using PTT.parse_title
    release_group: str | None = Field(default=None, index=True)

    # Status - indexes via __table_args__
    is_active: bool = Field(default=True)
    is_blocked: bool = Field(default=False)

    # Visibility for user-uploaded streams - index via idx_stream_public
    is_public: bool = Field(default=True)

    # Aggregates
    playback_count: int = Field(default=0)

    # =========================================================================
    # QUALITY ATTRIBUTES - Hybrid Approach for Scale (1M+ users)
    # =========================================================================
    # Single-value fields as VARCHAR with indexes (fast filtering, no JOINs)
    # Multi-value fields normalized into separate tables
    # =========================================================================

    # Single-value quality attributes (VARCHAR - indexed for fast filtering)
    resolution: str | None = Field(default=None, index=True)  # 4k, 1080p, 720p, 480p
    codec: str | None = Field(default=None, index=True)  # x264, x265, hevc, av1
    quality: str | None = Field(default=None, index=True)  # web-dl, bluray, cam, hdtv, webrip
    bit_depth: str | None = Field(default=None, index=True)  # 8bit, 10bit, 12bit

    # Boolean flags from PTT parsing
    is_remastered: bool = Field(default=False)  # Remastered release
    is_upscaled: bool = Field(default=False)  # AI upscaled release
    is_proper: bool = Field(default=False)  # PROPER release
    is_repack: bool = Field(default=False)  # REPACK release
    is_extended: bool = Field(default=False)  # Extended edition
    is_complete: bool = Field(default=False)  # Complete series/season
    is_dubbed: bool = Field(default=False)  # Dubbed audio
    is_subbed: bool = Field(default=False)  # Has subtitles

    # =========================================================================
    # MULTI-VALUE RELATIONSHIPS (Normalized for flexibility)
    # =========================================================================

    # Languages (many-to-many via StreamLanguageLink)
    languages: list["Language"] = Relationship(
        link_model=StreamLanguageLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )

    # Audio formats/codecs (many-to-many via StreamAudioLink)
    # e.g., AAC, DTS, DTS-HD MA, Atmos, TrueHD, EAC3, FLAC
    audio_formats: list["AudioFormat"] = Relationship(
        link_model=StreamAudioLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )

    # Audio channels (many-to-many via StreamChannelLink)
    # e.g., 2.0, 5.1, 7.1, Atmos
    channels: list["AudioChannel"] = Relationship(
        link_model=StreamChannelLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )

    # HDR formats (many-to-many via StreamHDRLink)
    # e.g., HDR10, HDR10+, Dolby Vision, HLG
    # A stream can have multiple (e.g., HDR10 + Dolby Vision combo)
    hdr_formats: list["HDRFormat"] = Relationship(
        link_model=StreamHDRLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )

    # =========================================================================
    # FILE STRUCTURE (for torrents and streams with multiple files)
    # =========================================================================

    # Files within this stream (for torrents)
    files: list["StreamFile"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    # Back-populates for type-specific tables
    torrent_stream: Optional["TorrentStream"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"uselist": False},
    )
    http_stream: Optional["HTTPStream"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"uselist": False},
    )
    usenet_stream: Optional["UsenetStream"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"uselist": False},
    )
    telegram_stream: Optional["TelegramStream"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"uselist": False},
    )
    acestream_stream: Optional["AceStreamStream"] = Relationship(
        back_populates="stream",
        sa_relationship_kwargs={"uselist": False},
    )


class TorrentStream(TimestampMixin, table=True):
    """Torrent-specific stream data."""

    __tablename__ = "torrent_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
        Index("idx_torrent_info_hash", "info_hash"),
        Index("idx_torrent_seeders", "seeders"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    # Torrent-specific
    info_hash: str = Field(unique=True, index=True)  # 40-char hex
    total_size: int = Field(sa_type=BigInteger)
    seeders: int | None = Field(default=None)
    leechers: int | None = Field(default=None)
    torrent_type: TorrentType = Field(default=TorrentType.PUBLIC)
    uploaded_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    torrent_file: bytes | None = Field(default=None, sa_type=LargeBinary)  # Optional .torrent content
    file_count: int = Field(default=1)

    # Relationships
    stream: Stream = Relationship(sa_relationship_kwargs={"uselist": False, "back_populates": "torrent_stream"})
    trackers: list[Tracker] = Relationship(
        link_model=TorrentTrackerLink,
        sa_relationship_kwargs={"cascade": "all, delete"},
    )


class StreamFile(SQLModel, table=True):
    """
    Files within a stream (primarily for torrents).

    This is a pure file structure table - no media assumptions.
    Media linking is done through FileMediaLink.

    Supports:
    - Regular torrent files
    - Nested folder structures (via file_path)
    - Archives containing videos (is_archive=True, archive_contents for nested files)
    """

    __tablename__ = "stream_file"
    __table_args__ = (
        UniqueConstraint("stream_id", "file_index"),
        Index("idx_stream_file_stream", "stream_id"),
        Index("idx_stream_file_type", "file_type"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", index=True, ondelete="CASCADE")

    # File structure
    file_index: int | None = None  # Position in torrent (NULL for HTTP)
    filename: str  # Just the filename
    file_path: str | None = None  # Full path including folders
    size: int | None = Field(default=None, sa_type=BigInteger)

    # File classification
    file_type: FileType = Field(default=FileType.VIDEO, index=True)

    # Archive support - for .zip/.rar containing videos
    is_archive: bool = Field(default=False)
    archive_contents: dict | None = Field(default=None, sa_type=JSON)  # [{filename, size, type}, ...]

    # Relationships
    stream: Stream = Relationship(sa_relationship_kwargs={"back_populates": "files"})
    media_links: list["FileMediaLink"] = Relationship(
        back_populates="file",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class FileMediaLink(TimestampMixin, table=True):
    """
    Flexible many-to-many linking between files and media.

    This allows:
    - 1 file → 1 movie
    - 1 file → multiple movies (compilation/collection)
    - 1 file → 1 episode
    - 1 file → multiple episodes (combined episodes like S01E01-E03)
    - Multiple files → 1 movie (split files)
    - Collection torrents → each file linked to different media
    """

    __tablename__ = "file_media_link"
    __table_args__ = (
        UniqueConstraint("file_id", "media_id", "season_number", "episode_number"),
        Index("idx_file_media_link_file", "file_id"),
        Index("idx_file_media_link_media", "media_id"),
        Index("idx_file_media_link_episode", "media_id", "season_number", "episode_number"),
        # Composite index for annotation queries (checking unmapped files)
        Index("idx_file_media_link_file_episode", "file_id", "episode_number"),
    )

    id: int = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="stream_file.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")

    # For series episodes (optional - NULL for movies)
    season_number: int | None = None
    episode_number: int | None = None

    # For multi-episode files (e.g., S01E01-E03.mkv)
    # If set, this file covers episodes from episode_number to episode_end
    episode_end: int | None = None

    # Link metadata
    is_primary: bool = Field(default=True)  # Primary file for this media
    confidence: float = Field(default=1.0)  # How confident is this link (0.0-1.0)
    link_source: LinkSource = Field(default=LinkSource.PTT_PARSER)

    # For debrid-provided links, track which service
    debrid_service: str | None = None  # realdebrid, alldebrid, etc.

    # Relationships
    file: StreamFile = Relationship(back_populates="media_links")


# NOTE: TorrentFile and StreamEpisodeFile have been REMOVED
# Use StreamFile for file structure and FileMediaLink for media linking


class HTTPStream(SQLModel, table=True):
    """HTTP/HLS/DASH stream data for direct URLs."""

    __tablename__ = "http_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    url: str  # Playback URL
    format: str | None = None  # mp4, mkv, hls, dash, webm
    size: int | None = Field(default=None, sa_type=BigInteger)  # File size if known
    bitrate_kbps: int | None = None
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # For temporary URLs

    # Stremio behavior hints (contains headers, proxy settings, etc.)
    behavior_hints: dict | None = Field(default=None, sa_type=JSONB)

    # DRM support for encrypted streams
    drm_key_id: str | None = None  # DRM key ID (for Widevine/PlayReady)
    drm_key: str | None = None  # DRM decryption key

    # MediaFlow extractor support
    extractor_name: str | None = None  # doodstream, filelions, filemoon, etc.

    # Relationships
    stream: Stream = Relationship(
        back_populates="http_stream",
        sa_relationship_kwargs={"uselist": False},
    )


class YouTubeStream(SQLModel, table=True):
    """YouTube video stream data."""

    __tablename__ = "youtube_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
        Index("idx_youtube_video_id", "video_id"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    video_id: str = Field(unique=True, index=True)  # YouTube video ID
    channel_id: str | None = None
    channel_name: str | None = None
    duration_seconds: int | None = None
    is_live: bool = Field(default=False)
    is_premiere: bool = Field(default=False)
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Relationships
    stream: Stream = Relationship(sa_relationship_kwargs={"uselist": False})


class UsenetStream(SQLModel, table=True):
    """Usenet/NZB stream data."""

    __tablename__ = "usenet_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
        Index("idx_usenet_guid", "nzb_guid"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    nzb_guid: str = Field(unique=True, index=True)  # Unique NZB identifier
    nzb_url: str | None = None  # URL to NZB file
    size: int = Field(sa_type=BigInteger)
    indexer: str = Field(index=True)  # Indexer source
    group_name: str | None = None  # Usenet group
    uploader: str | None = None  # Usenet poster/uploader name
    files_count: int | None = None
    parts_count: int | None = None
    posted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    is_passworded: bool = Field(default=False)

    # Relationships
    stream: Stream = Relationship(sa_relationship_kwargs={"uselist": False})


class TelegramStream(SQLModel, table=True):
    """Telegram channel/group stream data.

    Stores content from Telegram channels/groups or contributed via the bot.
    Includes backup channel support for redundancy if the bot gets suspended.
    """

    __tablename__ = "telegram_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
        Index("idx_telegram_chat_message", "chat_id", "message_id"),
        Index("idx_telegram_file_unique_id", "file_unique_id"),  # For cross-bot matching
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    # Source location (where content was originally found/contributed)
    chat_id: str  # Channel/group ID or user's DM chat ID
    chat_username: str | None = None  # Channel @username (if public)
    message_id: int

    # File identifiers
    file_id: str | None = None  # Bot-specific file_id for sendVideo
    file_unique_id: str | None = None  # Universal identifier (same across all bots)

    # File metadata
    size: int | None = Field(default=None, sa_type=BigInteger)
    mime_type: str | None = None
    file_name: str | None = None
    posted_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Backup channel info (for redundancy if bot gets suspended)
    backup_chat_id: str | None = None  # Backup channel ID
    backup_message_id: int | None = None  # Message ID in backup channel

    # Relationships
    stream: Stream = Relationship(
        back_populates="telegram_stream",
        sa_relationship_kwargs={"uselist": False},
    )
    user_forwards: list["TelegramUserForward"] = Relationship(
        back_populates="telegram_stream",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TelegramUserForward(SQLModel, table=True):
    """Per-user forwarded copy of a Telegram stream for MediaFlow access.

    When a user wants to play a Telegram stream, the bot forwards the content
    to their DM, creating a copy they can access with their own MediaFlow session.

    This table caches these forwards to avoid re-forwarding on every play.
    """

    __tablename__ = "telegram_user_forward"
    __table_args__ = (
        UniqueConstraint("telegram_stream_id", "user_id", name="uq_tg_forward_stream_user"),
        Index("idx_tg_forward_user_stream", "user_id", "telegram_stream_id"),
    )

    id: int = Field(default=None, primary_key=True)
    telegram_stream_id: int = Field(foreign_key="telegram_stream.id", index=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    # Telegram user ID (for the user's DM with the bot)
    telegram_user_id: int

    # The forwarded copy's location (in user's DM with bot)
    forwarded_chat_id: str  # User's Telegram ID as string (bot DM chat_id)
    forwarded_message_id: int  # Message ID of the forwarded copy

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    telegram_stream: TelegramStream = Relationship(back_populates="user_forwards")


class ExternalLinkStream(SQLModel, table=True):
    """External service links (Netflix, Prime, Disney+, etc.)."""

    __tablename__ = "external_link_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    url: str  # External service URL
    service_name: str  # netflix, prime, disney, hulu, etc.
    service_icon_url: str | None = None
    requires_subscription: bool = Field(default=True)
    region: str | None = None  # Available region codes
    behavior_hints: dict | None = Field(default=None, sa_type=JSON)  # Stremio behaviorHints

    # Relationships
    stream: Stream = Relationship(sa_relationship_kwargs={"uselist": False})


# StreamEpisodeFile is REMOVED - replaced by StreamFile + FileMediaLink
# This provides more flexibility:
# - StreamFile: Pure file structure (filename, size, index)
# - FileMediaLink: Flexible linking to any media with optional season/episode


class AceStreamStream(SQLModel, table=True):
    """AceStream content data - supports both content_id and info_hash for MediaFlow proxy.

    AceStream is a P2P streaming protocol. Content can be identified by either:
    - content_id: AceStream-specific content identifier (40-char hex)
    - info_hash: Standard BitTorrent info hash (40-char hex)

    At least one identifier must be present. Both can be provided for flexibility.

    MediaFlow proxy URLs:
    - Using content_id: /proxy/acestream/stream?id={content_id}
    - Using info_hash: /proxy/acestream/stream?infohash={info_hash}
    """

    __tablename__ = "acestream_stream"
    __table_args__ = (
        UniqueConstraint("stream_id"),  # 1:1 with Stream
        Index("idx_acestream_content_id", "content_id"),
        Index("idx_acestream_info_hash", "info_hash"),
    )

    id: int = Field(default=None, primary_key=True)
    stream_id: int = Field(foreign_key="stream.id", unique=True, index=True)

    # AceStream identifiers - at least one required (enforced in application code)
    content_id: str | None = Field(default=None, index=True)  # AceStream content ID (40-char hex)
    info_hash: str | None = Field(default=None, index=True)  # Torrent info hash (40-char hex)

    # Relationships
    stream: Stream = Relationship(
        back_populates="acestream_stream",
        sa_relationship_kwargs={"uselist": False},
    )
