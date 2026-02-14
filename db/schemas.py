"""
Pydantic schemas for data validation.

DEPRECATED: Import from db.schemas instead.
This file re-exports all schemas for backward compatibility.

Example:
    # Old way (still works)
    from db.schemas import UserData, TorrentStreamData

    # New way (preferred)
    from db.schemas import UserData, TorrentStreamData
"""

# Re-export everything from the schemas package for backward compatibility
from db.schemas import (
    AuthorizeData,
    BlockTorrent,
    # Cache
    CacheStatusRequest,
    CacheStatusResponse,
    CacheSubmitRequest,
    CacheSubmitResponse,
    Catalog,
    CatalogPattern,
    CatalogStats,
    EpisodeFileData,
    KnownFile,
    KodiConfig,
    MDBListConfig,
    MDBListItem,
    MediaFlowConfig,
    MediaFusionEventsMetaData,
    Meta,
    MetadataData,
    # Media
    MetaIdProjection,
    MetaItem,
    Metas,
    MetaSearchProjection,
    MigrateID,
    # RSS
    ParsingPattern,
    # Stremio
    PosterData,
    # Config
    QBittorrentConfig,
    RichStream,
    RichStreamMetadata,
    RPDBConfig,
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
    # Admin
    ScraperTask,
    SeriesEpisodeData,
    SortingOption,
    Stream,
    StreamBehaviorHints,
    StreamingProvider,
    Streams,
    TorrentStreamData,
    TorrentStreamsList,
    TVMetaData,
    TVMetaDataUpload,
    TVMetaProjection,
    TVStreams,
    UserData,
    UserRSSFeedCatalogPatternSchema,
    UserRSSFeedCreate,
    UserRSSFeedOwner,
    UserRSSFeedResponse,
    UserRSSFeedTestRequest,
    UserRSSFeedTestResponse,
    UserRSSFeedUpdate,
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
    "UserData",
    "AuthorizeData",
    # Media
    "MetaIdProjection",
    "MetaSearchProjection",
    "TVMetaProjection",
    "TVStreams",
    "TVMetaData",
    "KnownFile",
    "SeriesEpisodeData",
    "CatalogStats",
    "EpisodeFileData",
    "TorrentStreamData",
    "TorrentStreamsList",
    "MetadataData",
    "MediaFusionEventsMetaData",
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
