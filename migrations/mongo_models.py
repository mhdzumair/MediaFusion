import logging
from datetime import datetime
from typing import Optional, Any

import pytz
from beanie import (
    Document,
    Save,
    after_event,
    Insert,
    Delete,
    before_event,
    Update,
    Replace,
)
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT

from db.enums import TorrentType, NudityStatus
from db.redis_database import REDIS_ASYNC_CLIENT


class EpisodeFile(BaseModel):
    season_number: int
    episode_number: int
    size: int | None = None
    filename: str | None = None
    file_index: int | None = None
    title: str | None = None
    released: datetime | None = None
    thumbnail: str | None = None
    overview: str | None = None


class CatalogStats(BaseModel):
    catalog: str
    total_streams: int = 0
    last_stream_added: datetime | None = None


class MediaFusionMetaData(Document):
    id: str
    title: str
    is_custom: bool = Field(default=False)
    aka_titles: Optional[list[str]] = Field(default_factory=list)
    year: Optional[int] = None
    poster: Optional[str] = None
    is_poster_working: Optional[bool] = True
    is_add_title_to_poster: Optional[bool] = False
    background: Optional[str] = None
    type: str
    description: Optional[str] = None
    runtime: Optional[str] = None
    website: Optional[str] = None
    genres: Optional[list[str]] = Field(default_factory=list)
    last_updated_at: datetime = Field(default_factory=datetime.now)

    catalog_stats: list[CatalogStats] = Field(default_factory=list)
    last_stream_added: datetime | None = Field(default_factory=datetime.now)
    total_streams: int | None = 0

    class Settings:
        is_root = True
        indexes = [
            # Partial index for custom IDs (mf prefix) to enforce uniqueness
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=True,
                partialFilterExpression={"is_custom": True},
                name="unique_title_year_type_for_mf_id",
            ),
            # Regular index for all documents
            IndexModel(
                [("title", ASCENDING), ("year", ASCENDING), ("type", ASCENDING)],
                unique=False,
            ),
            IndexModel([("title", TEXT)]),
            IndexModel([("year", ASCENDING), ("end_year", ASCENDING)]),
            IndexModel([("_class_id", ASCENDING)]),
            IndexModel([("type", ASCENDING), ("genres", ASCENDING)]),
            IndexModel(
                [
                    ("type", ASCENDING),
                    ("catalog_stats.catalog", ASCENDING),
                    ("catalog_stats.total_streams", ASCENDING),
                ]
            ),
        ]

    @field_validator("runtime", mode="before")
    def validate_runtime(cls, v):
        if v and isinstance(v, int):
            return f"{v} min"
        return v


class KnownFile(BaseModel):
    size: int
    filename: str


class TorrentStreams(Document):
    model_config = ConfigDict(extra="allow")

    id: str
    meta_id: str
    torrent_name: str
    size: int
    episode_files: list[EpisodeFile] | None = Field(default_factory=list)
    filename: Optional[str] = None
    file_index: Optional[int] = None
    announce_list: list[str]
    languages: list[str]
    source: str
    uploader: Optional[str] = None
    catalog: list[str]
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    uploaded_at: Optional[datetime] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    quality: Optional[str] = None
    audio: list[str] | str | None = None
    hdr: list[str] | None = None
    seeders: Optional[int] = None
    torrent_type: Optional[TorrentType] = TorrentType.PUBLIC
    is_blocked: Optional[bool] = False
    torrent_file: bytes | None = None
    known_file_details: Optional[list[KnownFile]] = None

    @after_event(Insert)
    async def update_metadata_on_create(self):
        """Update metadata when a new stream is created"""
        # Initialize update operations for total stream count
        update_ops = {
            "$inc": {"total_streams": 1},
            "$set": {"last_stream_added": self.created_at},
        }

        # Update catalog_stats array
        for cat in self.catalog:
            # Try to update existing catalog stats first
            result = await MediaFusionMetaData.get_motor_collection().update_one(
                {"_id": self.meta_id, "catalog_stats.catalog": cat},
                {
                    "$inc": {"catalog_stats.$.total_streams": 1},
                    "$set": {
                        "catalog_stats.$.last_stream_added": self.created_at,
                        **update_ops["$set"],
                    },
                },
            )

            # If no existing catalog stats found, add new one
            if result.modified_count == 0:
                # Initialize $push.catalog_stats.$each if it doesn't exist yet
                if "$push" not in update_ops:
                    update_ops["$push"] = {}
                if "catalog_stats" not in update_ops["$push"]:
                    update_ops["$push"]["catalog_stats"] = {"$each": []}

                # Add this catalog to the $each array
                update_ops["$push"]["catalog_stats"]["$each"].append(
                    {
                        "catalog": cat,
                        "total_streams": 1,
                        "last_stream_added": self.created_at,
                    }
                )

        # Handle episode metadata updates for series
        if self.episode_files:
            series_data = await MediaFusionSeriesMetaData.get(self.meta_id)
            if series_data:
                existing_episodes = {
                    (ep.season_number, ep.episode_number): ep
                    for ep in series_data.episodes
                }

                new_episodes = []
                for ep in self.episode_files:
                    key = (ep.season_number, ep.episode_number)
                    if key not in existing_episodes:
                        new_episodes.append(
                            SeriesEpisode(
                                season_number=ep.season_number,
                                episode_number=ep.episode_number,
                                title=ep.title or f"Episode {ep.episode_number}",
                                released=ep.released or self.created_at,
                                overview=ep.overview,
                                thumbnail=ep.thumbnail,
                            )
                        )

                if new_episodes:
                    update_ops["$push"] = update_ops.get("$push", {})
                    update_ops["$push"]["episodes"] = {
                        "$each": [ep.model_dump() for ep in new_episodes]
                    }
                    cache_keys = await REDIS_ASYNC_CLIENT.keys(
                        f"series_{self.meta_id}_meta*"
                    )
                    cache_keys.append(f"series_data:{self.meta_id}")
                    await REDIS_ASYNC_CLIENT.delete(*cache_keys)

        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": self.meta_id}, update_ops
        )
        logging.info(f"Added stream {self.id} to metadata {self.meta_id}")

    @after_event(Delete)
    async def update_metadata_on_delete(self):
        """Update metadata when a stream is deleted"""
        # Get all remaining streams for this meta_id grouped by catalog
        pipeline = [
            {
                "$match": {
                    "meta_id": self.meta_id,
                    "_id": {"$ne": self.id},
                    "is_blocked": {"$ne": True},
                }
            },
            {"$unwind": "$catalog"},
            {
                "$group": {
                    "_id": "$catalog",
                    "total_streams": {"$sum": 1},
                    "last_stream_added": {"$max": "$created_at"},
                }
            },
        ]

        remaining_streams = (
            await TorrentStreams.get_motor_collection()
            .aggregate(pipeline)
            .to_list(None)
        )
        remaining_catalogs = {stream["_id"] for stream in remaining_streams}

        # Build update operations
        update_ops = {
            "$inc": {"total_streams": -1},
            "$pull": {
                "catalog_stats": {
                    "catalog": {
                        "$in": [
                            cat for cat in self.catalog if cat not in remaining_catalogs
                        ]
                    }
                }
            },
        }

        # Update remaining catalog stats
        for stream in remaining_streams:
            await MediaFusionMetaData.get_motor_collection().update_one(
                {"_id": self.meta_id, "catalog_stats.catalog": stream["_id"]},
                {
                    "$set": {
                        "catalog_stats.$.total_streams": stream["total_streams"],
                        "catalog_stats.$.last_stream_added": stream[
                            "last_stream_added"
                        ],
                    }
                },
            )

        # Update last_stream_added
        if remaining_streams:
            update_ops["$set"] = {
                "last_stream_added": max(
                    stream["last_stream_added"] for stream in remaining_streams
                )
            }
        else:
            update_ops["$set"] = {"last_stream_added": None}

        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": self.meta_id}, update_ops
        )
        logging.info(f"Removed stream {self.id} from metadata {self.meta_id}")

    @before_event([Update, Save])
    async def update_metadata_on_block(self):
        """Update metadata when a stream is blocked"""
        if hasattr(self, "is_blocked") and self.is_blocked:
            logging.info(f"Stream {self.id} is blocked")
            await self.update_metadata_on_delete()

    @before_event(Update)
    async def update_metadata_on_change(self):
        """Update metadata when stream episodes change"""
        if not hasattr(self, "episode_files") or not self.episode_files:
            return

        # Only proceed if this is an update with episode changes
        old_stream = await TorrentStreams.get(self.id)
        if not old_stream or old_stream.episode_files == self.episode_files:
            return

        series_data = await MediaFusionSeriesMetaData.get(self.meta_id)
        if not series_data:
            return

        existing_episodes = {
            (ep.season_number, ep.episode_number): ep for ep in series_data.episodes
        }

        new_episodes = []
        for ep in self.episode_files:
            key = (ep.season_number, ep.episode_number)
            if key not in existing_episodes:
                new_episodes.append(
                    SeriesEpisode(
                        season_number=ep.season_number,
                        episode_number=ep.episode_number,
                        title=ep.title or f"Episode {ep.episode_number}",
                        released=ep.released or self.created_at,
                    )
                )

        if new_episodes:
            await MediaFusionMetaData.get_motor_collection().update_one(
                {"_id": self.meta_id},
                {
                    "$push": {
                        "episodes": {"$each": [ep.model_dump() for ep in new_episodes]}
                    }
                },
            )
            cache_keys = await REDIS_ASYNC_CLIENT.keys(f"series_{self.meta_id}_meta*")
            cache_keys.append(f"series_data:{self.meta_id}")
            await REDIS_ASYNC_CLIENT.delete(*cache_keys)
            logging.info(f"Updated episodes for series {self.meta_id}")

    def __eq__(self, other):
        if not isinstance(other, TorrentStreams):
            return False
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    @field_validator("id", mode="after")
    def validate_id(cls, v):
        return v.lower()

    @field_validator("created_at", mode="after")
    def validate_created_at(cls, v):
        # convert to UTC
        return v.astimezone(pytz.utc)

    class Settings:
        indexes = [
            # Optimized compound indexes
            IndexModel(
                [
                    ("meta_id", ASCENDING),
                    ("is_blocked", ASCENDING),
                    ("catalog", ASCENDING),
                    ("created_at", DESCENDING),
                ]
            ),
            IndexModel([("_id", ASCENDING), ("is_blocked", ASCENDING)]),
            IndexModel([("_class_id", ASCENDING)]),
            IndexModel([("source", ASCENDING)]),
            IndexModel([("uploader", ASCENDING)]),
        ]

    def get_episodes(
        self, season_number: int, episode_number: int
    ) -> list[EpisodeFile]:
        """
        Returns a list of Episode objects for the given season and episode number,
        sorted by size in descending order if size is available.
        """
        episodes = [
            ep
            for ep in self.episode_files or []
            if ep.season_number == season_number and ep.episode_number == episode_number
        ]
        return sorted(episodes, key=lambda ep: ep.size or 0, reverse=True)


class TVStreams(Document):
    meta_id: str
    name: str
    source: str
    url: str | None = None
    ytId: str | None = None
    externalUrl: str | None = None
    behaviorHints: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    country: str | None = None
    is_working: Optional[bool] = True
    test_failure_count: int = 0
    namespaces: list[str] = Field(default_factory=lambda: ["mediafusion"])
    drm_key_id: str | None = None
    drm_key: str | None = None

    @after_event([Insert, Replace])
    async def update_metadata_on_create(self):
        """Update metadata when a new stream is created"""
        if self.is_working:
            update_data = {
                "$inc": {"total_streams": 1},
                "$set": {"last_stream_added": self.created_at},
            }
            await MediaFusionMetaData.get_motor_collection().update_one(
                {"_id": self.meta_id}, update_data
            )
            logging.info(f"Added stream {self.id} to metadata {self.meta_id}")

    @after_event(Delete)
    async def update_metadata_on_delete(self):
        """Update metadata when a stream is deleted"""
        # First, check if this was the last stream for any catalog
        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": self.meta_id}, {"$inc": {"total_streams": -1}}
        )
        logging.info(f"Removed stream {self.id} from metadata {self.meta_id}")

    @after_event(Update)
    async def update_metadata_on_not_working(self):
        """Update metadata when a stream is not working"""
        inc_value = 1 if self.is_working else -1
        update_data = {"$inc": {"total_streams": inc_value}}
        if self.is_working:
            update_data["$set"] = {"last_stream_added": datetime.now()}

        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": self.meta_id}, update_data
        )

    def __eq__(self, other):
        if not isinstance(other, TVStreams):
            return False
        return (
            self.url == other.url
            and self.ytId == other.ytId
            and self.externalUrl == other.externalUrl
            and self.drm_key_id == other.drm_key_id
            and self.drm_key == other.drm_key
        )

    def __hash__(self):
        return hash(
            (self.url, self.ytId, self.externalUrl, self.drm_key_id, self.drm_key)
        )

    class Settings:
        indexes = [
            IndexModel(
                [
                    ("meta_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("namespaces", ASCENDING),
                    ("is_working", ASCENDING),
                ]
            ),
            IndexModel(
                [("url", ASCENDING), ("ytId", ASCENDING), ("externalUrl", ASCENDING)],
                unique=True,
                sparse=True,
            ),
            IndexModel([("_class_id", ASCENDING)]),
        ]


class MediaFusionMovieMetaData(MediaFusionMetaData):
    type: str = "movie"
    imdb_rating: Optional[float] = None
    tmdb_rating: Optional[float] = None
    parent_guide_nudity_status: Optional[NudityStatus] = NudityStatus.UNKNOWN
    parent_guide_certificates: Optional[list[str]] = Field(default_factory=list)
    stars: Optional[list[str]] = Field(default_factory=list)


class SeriesEpisode(BaseModel):
    """Series episode metadata from IMDb"""

    season_number: int
    episode_number: int
    title: str | None = None
    overview: Optional[str] = None
    released: datetime | None = None
    imdb_rating: Optional[float] = None
    tmdb_rating: Optional[float] = None
    thumbnail: Optional[str] = None

    @model_validator(mode="after")
    def validate_title(self):
        if not self.title:
            self.title = f"Episode {self.episode_number}"
        return self


class MediaFusionSeriesMetaData(MediaFusionMetaData):
    type: str = "series"
    end_year: Optional[int] = None
    imdb_rating: Optional[float] = None
    tmdb_rating: Optional[float] = None
    parent_guide_nudity_status: Optional[str] = "None"
    parent_guide_certificates: Optional[list[str]] = Field(default_factory=list)
    stars: Optional[list[str]] = Field(default_factory=list)
    episodes: list[SeriesEpisode] = Field(default_factory=list)


class MediaFusionTVMetaData(MediaFusionMetaData):
    type: str = "tv"
    country: str | None = None
    tv_language: str | None = None
    logo: Optional[str] = None


class MediaFusionEventsMetaData(MediaFusionMetaData):
    type: str = "events"
    event_start_timestamp: Optional[int] = None
    logo: Optional[str] = None
    streams: list[TVStreams]


class RSSParsingPatterns(BaseModel):
    title: str | None = None
    description: str | None = None
    pubDate: str | None = None
    poster: str | None = None
    background: str | None = None
    logo: str | None = None
    category: str | None = None
    magnet: str | None = None
    torrent: str | None = None
    size: str | None = None
    seeders: str | None = None

    # Regex patterns
    magnet_regex: str | None = None
    torrent_regex: str | None = None
    size_regex: str | None = None
    seeders_regex: str | None = None
    category_regex: str | None = None
    episode_name_parser: str | None = None  # New field for episode name parsing

    # Regex group numbers (0 = full match, 1+ = capture groups)
    magnet_regex_group: int = 1
    torrent_regex_group: int = 1
    size_regex_group: int = 1
    seeders_regex_group: int = 1
    category_regex_group: int = 1


class RSSFeedFilters(BaseModel):
    title_filter: str | None = None
    title_exclude_filter: str | None = None
    min_size_mb: int | None = None
    max_size_mb: int | None = None
    min_seeders: int | None = None
    category_filter: list[str] | None = None


class RSSFeedMetrics(BaseModel):
    """RSS Feed scraping metrics"""
    total_items_found: int = 0
    total_items_processed: int = 0
    total_items_skipped: int = 0
    total_errors: int = 0
    last_scrape_duration: Optional[float] = None  # in seconds
    items_processed_last_run: int = 0
    items_skipped_last_run: int = 0
    errors_last_run: int = 0
    skip_reasons: dict[str, int] = Field(default_factory=dict)  # reason -> count

class RSSCatalogPattern(BaseModel):
    regex: str
    enabled: bool = True
    target_catalogs: list[str] = Field(default_factory=list)
    case_sensitive: bool = False
    name: str | None = None


class RSSFeed(Document):
    name: str
    url: str
    parsing_patterns: Optional[RSSParsingPatterns] = Field(
        default_factory=RSSParsingPatterns
    )
    filters: Optional[RSSFeedFilters] = Field(
        default_factory=RSSFeedFilters
    )
    active: bool = True
    last_scraped: Optional[datetime] = None
    source: Optional[str] = None
    torrent_type: Optional[str] = "public"
    auto_detect_catalog: Optional[bool] = False
    catalog_patterns: Optional[list[RSSCatalogPattern]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metrics: Optional[RSSFeedMetrics] = Field(default_factory=RSSFeedMetrics)

    class Settings:
        collection = "rss_feeds"

    class Config:
        validate_assignment = True
        json_encoders = {datetime: lambda dt: dt.isoformat()}

    def __str__(self):
        return f"RSSFeed(name={self.name}, url={self.url}, active={self.active})"
