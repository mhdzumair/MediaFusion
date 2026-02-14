"""User authentication and management models."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import pytz
from sqlalchemy import JSON, DateTime, Index
from sqlmodel import Field, Relationship, SQLModel

from db.enums import HistorySource, IntegrationType, IPTVSourceType, UserRole, WatchAction
from db.models.base import TimestampMixin

if TYPE_CHECKING:
    from db.models.contributions import (
        Contribution,
        EpisodeSuggestion,
        MetadataSuggestion,
        MetadataVote,
        StreamSuggestion,
        StreamVote,
    )
    from db.models.rss import RSSFeed
    from db.models.user_content import UserCatalog, UserLibraryItem


class User(TimestampMixin, table=True):
    """User account table for authentication."""

    __tablename__ = "users"
    __table_args__ = (
        Index("idx_user_email", "email"),
        Index("idx_user_username", "username"),
        Index("idx_user_contribution_level", "contribution_level"),
        Index("idx_user_telegram_user_id", "telegram_user_id"),
    )

    # Integer auto-increment PK
    id: int = Field(default=None, primary_key=True)
    # UUID for external APIs (Stremio, etc.)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True)
    email: str = Field(unique=True, index=True)
    username: str | None = Field(default=None, unique=True, index=True)
    password_hash: str | None = Field(default=None)  # NULL for OAuth users
    role: UserRole = Field(default=UserRole.USER, index=True)
    is_verified: bool = Field(default=False)
    is_active: bool = Field(default=True, index=True)
    last_login: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Telegram account linking (user-level, not profile-level)
    telegram_user_id: str | None = Field(default=None, unique=True, index=True)
    telegram_linked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

    # Contribution reputation system
    contribution_points: int = Field(default=0, index=True)
    metadata_edits_approved: int = Field(default=0)
    stream_edits_approved: int = Field(default=0)
    contribution_level: str = Field(default="new", index=True)  # new, contributor, trusted, expert

    # Contribution preferences
    contribute_anonymously: bool = Field(default=False)  # Default to show name on contributions

    # Relationships
    profiles: list["UserProfile"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    watch_history: list["WatchHistory"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    contributions: list["Contribution"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    rss_feeds: list["RSSFeed"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    library_items: list["UserLibraryItem"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    stream_votes: list["StreamVote"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    metadata_votes: list["MetadataVote"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    metadata_suggestions: list["MetadataSuggestion"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    stream_suggestions: list["StreamSuggestion"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    episode_suggestions: list["EpisodeSuggestion"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    catalogs: list["UserCatalog"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    iptv_sources: list["IPTVSource"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class UserProfile(TimestampMixin, table=True):
    """User profile configuration - supports multiple profiles per user."""

    __tablename__ = "user_profiles"
    __table_args__ = (Index("idx_profile_user_default", "user_id", "is_default"),)

    id: int = Field(default=None, primary_key=True)
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()), unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    name: str
    config: dict = Field(default_factory=dict, sa_type=JSON)  # Non-sensitive config
    encrypted_secrets: str | None = Field(default=None)  # AES-encrypted sensitive data
    is_default: bool = Field(default=False)

    # Relationships
    user: User = Relationship(back_populates="profiles")
    watch_history: list["WatchHistory"] = Relationship(
        back_populates="profile",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    integrations: list["ProfileIntegration"] = Relationship(
        back_populates="profile",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class WatchHistory(SQLModel, table=True):
    """
    Unified user activity history for tracking viewing and download activity.

    Replaces the separate DownloadHistory table - now tracks all user actions
    with content including watching, downloading, queueing, and copying links.
    """

    __tablename__ = "watch_history"
    __table_args__ = (
        Index("idx_watch_user_media", "user_id", "media_id"),
        Index("idx_watch_profile_media", "profile_id", "media_id"),
        Index("idx_watch_watched_at", "watched_at"),
        Index("idx_watch_action", "action"),
    )

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    profile_id: int = Field(foreign_key="user_profiles.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True)
    title: str = Field(default="")  # Cached title for display
    media_type: str = Field(default="movie")  # 'movie' or 'series'
    season: int | None = Field(default=None)
    episode: int | None = Field(default=None)
    progress: int = Field(default=0)  # Progress in seconds
    duration: int | None = Field(default=None)  # Total duration in seconds

    # Action tracking - what the user did with this content
    action: WatchAction = Field(default=WatchAction.WATCHED, index=True)

    # Source tracking - where this history entry came from
    source: HistorySource = Field(default=HistorySource.MEDIAFUSION, index=True)

    # Stream info for downloads (moved from DownloadHistory)
    stream_info: dict = Field(default_factory=dict, sa_type=JSON)

    watched_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    user: User = Relationship(back_populates="watch_history")
    profile: UserProfile = Relationship(back_populates="watch_history")


class PlaybackTracking(SQLModel, table=True):
    """
    Normalized playback tracking table.

    For authenticated users: tracks individual playback with user reference.
    For anonymous users: aggregated via playback_count on Stream.
    """

    __tablename__ = "playback_tracking"
    __table_args__ = (
        Index("idx_playback_user_stream", "user_id", "stream_id"),
        Index("idx_playback_user_media", "user_id", "media_id"),
        Index("idx_playback_stream", "stream_id"),
        Index("idx_playback_at", "last_played_at"),
    )

    id: int = Field(default=None, primary_key=True)
    # User reference (nullable for anonymous tracking aggregates)
    user_id: int | None = Field(default=None, foreign_key="users.id", index=True, ondelete="CASCADE")
    profile_id: int | None = Field(default=None, foreign_key="user_profiles.id", index=True, ondelete="CASCADE")

    # Stream reference
    stream_id: int = Field(foreign_key="stream.id", index=True)
    media_id: int = Field(foreign_key="media.id", index=True)

    # Episode info (for series)
    season: int | None = Field(default=None)
    episode: int | None = Field(default=None)

    # Provider used for playback
    provider_name: str | None = Field(default=None)
    provider_service: str | None = Field(default=None)

    # Timestamps
    first_played_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        sa_type=DateTime(timezone=True),
    )
    last_played_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        sa_type=DateTime(timezone=True),
    )
    play_count: int = Field(default=1)


class ProfileIntegration(TimestampMixin, table=True):
    """
    External platform integration credentials and settings per profile.

    Stores OAuth tokens and platform-specific configuration for services
    like Trakt, Simkl, MAL, etc. Each profile can have its own set of
    integrations, allowing different family members to sync to their
    own accounts.

    Only authenticated users can have integrations (requires profile_id).
    """

    __tablename__ = "profile_integration"
    __table_args__ = (
        Index("idx_integration_profile", "profile_id"),
        Index("idx_integration_platform", "platform"),
        Index("idx_integration_profile_platform", "profile_id", "platform", unique=True),
        Index("idx_integration_enabled", "is_enabled"),
    )

    id: int = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="user_profiles.id", index=True, ondelete="CASCADE")

    # Platform identification
    platform: IntegrationType = Field(index=True)

    # OAuth credentials (encrypted)
    encrypted_credentials: str | None = Field(default=None)
    # Contains: { access_token, refresh_token, expires_at, client_id?, client_secret? }

    # Integration settings
    is_enabled: bool = Field(default=True, index=True)
    sync_direction: str = Field(default="two_way")  # mf_to_platform, platform_to_mf, two_way
    scrobble_enabled: bool = Field(default=True)  # Real-time sync while watching

    # Platform-specific settings (JSON)
    settings: dict = Field(default_factory=dict, sa_type=JSON)
    # e.g., { min_watch_percent: 80 }

    # Sync state (moved from IntegrationSyncState for simplicity)
    last_sync_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_sync_status: str | None = Field(default=None)  # success, failed, partial
    last_sync_error: str | None = Field(default=None)
    sync_cursor: dict = Field(default_factory=dict, sa_type=JSON)
    last_sync_stats: dict = Field(default_factory=dict, sa_type=JSON)

    # Relationships
    profile: UserProfile = Relationship(back_populates="integrations")


class IPTVSource(TimestampMixin, table=True):
    """
    User's IPTV sources for import and re-sync.

    Stores M3U URLs and Xtream Codes credentials for users to
    re-fetch/sync their IPTV playlists later.
    """

    __tablename__ = "iptv_source"
    __table_args__ = (
        Index("idx_iptv_source_user", "user_id"),
        Index("idx_iptv_source_type", "source_type"),
        Index("idx_iptv_source_active", "is_active"),
    )

    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE")  # index via __table_args__

    # Source identification
    source_type: IPTVSourceType  # index via __table_args__
    name: str  # User-defined name for this source

    # M3U specific
    m3u_url: str | None = Field(default=None)

    # Xtream/Stalker specific
    server_url: str | None = Field(default=None)
    encrypted_credentials: str | None = Field(default=None)  # Encrypted JSON {username, password}

    # Import settings (remembered for re-sync)
    is_public: bool = Field(default=False)
    import_live: bool = Field(default=True)
    import_vod: bool = Field(default=True)
    import_series: bool = Field(default=True)

    # Category filters (for Xtream - JSON arrays of category IDs)
    live_category_ids: list | None = Field(default=None, sa_type=JSON)
    vod_category_ids: list | None = Field(default=None, sa_type=JSON)
    series_category_ids: list | None = Field(default=None, sa_type=JSON)

    # Sync metadata
    last_synced_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    last_sync_stats: dict | None = Field(default=None, sa_type=JSON)
    # Stats format: {tv: 26, movie: 0, series: 0, new: 5, updated: 2, skipped: 19}

    is_active: bool = Field(default=True)

    # Relationships
    user: User = Relationship(back_populates="iptv_sources")
