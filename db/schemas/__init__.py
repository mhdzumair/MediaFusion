"""
Database schemas package.

This module re-exports all Pydantic schemas for easy importing:
    from db.schemas import UserData, TorrentStreamData, ...
"""

# Stremio schemas
# Admin/task schemas
from db.schemas.admin import (
    BlockTorrent,
    KodiConfig,
    MigrateID,
    ScraperTask,
    TVMetaDataUpload,
)

# Cache schemas
from db.schemas.cache import (
    CacheStatusRequest,
    CacheStatusResponse,
    CacheSubmitRequest,
    CacheSubmitResponse,
)

# Configuration schemas
from db.schemas.config import (
    AuthorizeData,
    CatalogConfig,
    EasynewsConfig,
    MDBListConfig,
    MDBListItem,
    MediaFlowConfig,
    NewznabIndexerConfig,
    NZBGetConfig,
    QBittorrentConfig,
    RPDBConfig,
    SABnzbdConfig,
    SortingOption,
    StreamingProvider,
    StreamTemplate,
    UserData,
)

# Media/torrent data schemas
from db.schemas.media import (
    CatalogStats,
    # Episode/Season schemas
    EpisodeData,
    ExternalIDs,
    HTTPStreamData,
    # Multi-provider schemas
    ImageData,
    KnownFile,
    MediaFusionEventsMetaData,
    MediaFusionRatingData,
    MediaImages,
    MediaRatings,
    # Metadata schemas
    MetadataData,
    # Projection/lookup schemas
    MetaIdProjection,
    MetaSearchProjection,
    RatingData,
    SeasonData,
    SeriesEpisodeData,
    # Stream file schemas (v5)
    StreamFileData,
    # Stream schemas
    TorrentStreamData,
    TorrentStreamsList,
    TVMetaData,
    TVMetaProjection,
    TVStreams,
    # Telegram stream schemas
    TelegramStreamData,
    TelegramStreamsList,
    # Usenet stream schemas
    UsenetStreamData,
    UsenetStreamsList,
)

# RSS feed schemas
from db.schemas.rss import (
    CatalogPattern,
    ParsingPattern,
    RSSFeed,
    RSSFeedBulkImport,
    RSSFeedCatalogPatternSchema,
    RSSFeedCreate,
    RSSFeedExamine,
    RSSFeedFilters,
    RSSFeedMetrics,
    RSSFeedParsingPatterns,
    RSSFeedSchema,
    RSSFeedUpdate,
    RSSSchedulerStatus,
    UserRSSFeedCatalogPatternSchema,
    UserRSSFeedCreate,
    UserRSSFeedOwner,
    UserRSSFeedResponse,
    UserRSSFeedTestRequest,
    UserRSSFeedTestResponse,
    UserRSSFeedUpdate,
)
from db.schemas.stremio import (
    Catalog,
    Meta,
    MetaItem,
    Metas,
    PosterData,
    RichStream,
    RichStreamMetadata,
    Stream,
    StreamBehaviorHints,
    Streams,
    Video,
)

__all__ = [
    # Stremio
    "PosterData",
    "Catalog",
    "Video",
    "Meta",
    "MetaItem",
    "Metas",
    "StreamBehaviorHints",
    "Stream",
    "Streams",
    "RichStreamMetadata",
    "RichStream",
    # Config
    "QBittorrentConfig",
    "MediaFlowConfig",
    "RPDBConfig",
    "StreamingProvider",
    "SortingOption",
    "MDBListItem",
    "MDBListConfig",
    "CatalogConfig",
    "StreamTemplate",
    "UserData",
    "AuthorizeData",
    # Usenet Config
    "NewznabIndexerConfig",
    "SABnzbdConfig",
    "NZBGetConfig",
    "EasynewsConfig",
    # Media - Multi-provider
    "ImageData",
    "MediaImages",
    "RatingData",
    "MediaRatings",
    "MediaFusionRatingData",
    "ExternalIDs",
    # Media - Episode/Season
    "EpisodeData",
    "SeasonData",
    # Media - Stream files (v5)
    "StreamFileData",
    # Media - Streams
    "TorrentStreamData",
    "TorrentStreamsList",
    "HTTPStreamData",
    "TVStreams",
    "TVMetaData",
    # Telegram Streams
    "TelegramStreamData",
    "TelegramStreamsList",
    # Usenet Streams
    "UsenetStreamData",
    "UsenetStreamsList",
    # Media - Metadata
    "MetadataData",
    "MediaFusionEventsMetaData",
    # Media - Projections
    "MetaIdProjection",
    "MetaSearchProjection",
    "TVMetaProjection",
    "KnownFile",
    "SeriesEpisodeData",
    "CatalogStats",
    # Admin
    "ScraperTask",
    "TVMetaDataUpload",
    "KodiConfig",
    "BlockTorrent",
    "MigrateID",
    # Cache
    "CacheStatusRequest",
    "CacheStatusResponse",
    "CacheSubmitRequest",
    "CacheSubmitResponse",
    # RSS
    "ParsingPattern",
    "RSSFeedParsingPatterns",
    "CatalogPattern",
    "RSSFeedFilters",
    "RSSFeedMetrics",
    "RSSFeed",
    "RSSFeedCreate",
    "RSSFeedUpdate",
    "RSSFeedBulkImport",
    "RSSFeedExamine",
    "RSSFeedCatalogPatternSchema",
    "RSSFeedSchema",
    "UserRSSFeedOwner",
    "UserRSSFeedCatalogPatternSchema",
    "UserRSSFeedCreate",
    "UserRSSFeedUpdate",
    "UserRSSFeedResponse",
    "UserRSSFeedTestRequest",
    "UserRSSFeedTestResponse",
    "RSSSchedulerStatus",
]
