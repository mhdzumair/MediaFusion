"""
Database models package.

This module re-exports all models for easy importing:
    from db.models import User, TorrentStream, Media, ...

Models are now using integer auto-increment primary keys throughout.
All RSS feeds are user-based (no separate system RSS feeds).
Cast/Crew uses Person + MediaCast/MediaCrew (not legacy Star table).
"""

# Base and mixins
from db.models.base import TimestampMixin

# Cast and crew
from db.models.cast_crew import (
    MediaCast,
    MediaCrew,
    MediaReview,
    Person,
)

# Contributions and voting
from db.models.contributions import (
    Contribution,
    ContributionSettings,
    EpisodeSuggestion,
    MetadataSuggestion,
    MetadataVote,
    StreamSuggestion,
    StreamVote,
)

# Link tables (junction tables for many-to-many)
from db.models.links import (
    MediaCatalogLink,
    MediaGenreLink,
    MediaKeywordLink,
    MediaParentalCertificateLink,
    MediaProductionCompanyLink,
    StreamAudioLink,
    StreamChannelLink,
    StreamHDRLink,
    StreamLanguageLink,
    StreamMediaLink,
    TorrentTrackerLink,
)

# Media models
from db.models.media import (
    Episode,
    Media,
    MediaTrailer,
    MovieMetadata,
    Season,
    SeriesMetadata,
    TVMetadata,
)

# Providers
from db.models.providers import (
    EpisodeImage,
    MediaExternalID,
    MediaFusionRating,
    MediaImage,
    MediaRating,
    MetadataProvider,
    ProviderMetadata,
    RatingProvider,
)

# Reference/lookup tables
from db.models.reference import (
    AkaTitle,
    AkaTitleName,
    AudioChannel,
    AudioFormat,
    Catalog,
    CatalogName,
    Genre,
    # Pydantic schemas for names
    GenreName,
    HDRFormat,
    Keyword,
    Language,
    ParentalCertificate,
    ParentalCertificateName,
    ProductionCompany,
)

# RSS feeds (all user-based)
from db.models.rss import (
    RSSFeed,
    RSSFeedCatalogPattern,
)

# Statistics
from db.models.stats import DailyStats

# Stream models (unified architecture)
from db.models.streams import (
    AceStreamStream,
    ExternalLinkStream,
    FileMediaLink,  # Replaces StreamEpisodeFile - flexible file-to-media linking
    FileType,
    HTTPStream,
    LinkSource,
    # Base and tracker
    Stream,
    # File structure and linking (NEW in v5)
    StreamFile,  # Replaces TorrentFile - pure file structure
    # Enums
    StreamType,
    TelegramStream,
    TelegramUserForward,
    # Type-specific streams
    TorrentStream,
    Tracker,
    TrackerStatus,
    UsenetStream,
    YouTubeStream,
)

# User content
from db.models.user_content import (
    UserCatalog,
    UserCatalogItem,
    UserCatalogSubscription,
    UserLibraryItem,
)

# User models
from db.models.users import (
    IPTVSource,
    PlaybackTracking,
    ProfileIntegration,
    User,
    UserProfile,
    WatchHistory,
)

# Export all models for SQLModel metadata registration
__all__ = [
    # Base
    "TimestampMixin",
    # Links
    "MediaGenreLink",
    "MediaCatalogLink",
    "MediaParentalCertificateLink",
    "MediaKeywordLink",
    "MediaProductionCompanyLink",
    "StreamLanguageLink",
    "StreamAudioLink",
    "StreamChannelLink",
    "StreamHDRLink",
    "TorrentTrackerLink",
    "StreamMediaLink",
    # Reference
    "Genre",
    "Catalog",
    "AkaTitle",
    "ParentalCertificate",
    "Language",
    "AudioFormat",
    "AudioChannel",
    "HDRFormat",
    "Keyword",
    "ProductionCompany",
    "GenreName",
    "CatalogName",
    "ParentalCertificateName",
    "AkaTitleName",
    # Providers
    "MetadataProvider",
    "RatingProvider",
    "MediaExternalID",
    "MediaImage",
    "EpisodeImage",
    "MediaRating",
    "MediaFusionRating",
    "ProviderMetadata",
    # Media
    "Media",
    "MediaTrailer",
    "MovieMetadata",
    "SeriesMetadata",
    "TVMetadata",
    "Season",
    "Episode",
    # Streams (unified)
    "StreamType",
    "TrackerStatus",
    "FileType",
    "LinkSource",
    "Stream",
    "Tracker",
    "StreamFile",
    "FileMediaLink",
    "TorrentStream",
    "HTTPStream",
    "YouTubeStream",
    "UsenetStream",
    "TelegramStream",
    "TelegramUserForward",
    "ExternalLinkStream",
    "AceStreamStream",
    # Stats
    "DailyStats",
    # RSS (user-based)
    "RSSFeed",
    "RSSFeedCatalogPattern",
    # Users
    "User",
    "UserProfile",
    "WatchHistory",
    "PlaybackTracking",
    "IPTVSource",
    "ProfileIntegration",
    # Cast and crew
    "Person",
    "MediaCast",
    "MediaCrew",
    "MediaReview",
    # User content
    "UserCatalog",
    "UserCatalogItem",
    "UserCatalogSubscription",
    "UserLibraryItem",
    # Contributions
    "Contribution",
    "StreamVote",
    "MetadataVote",
    "MetadataSuggestion",
    "StreamSuggestion",
    "EpisodeSuggestion",
    "ContributionSettings",
]
