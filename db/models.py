from datetime import datetime
from typing import Optional, Any

import pymongo
import pytz
from beanie import Document, Link
from pydantic import BaseModel, Field, ConfigDict, field_validator
from pymongo import IndexModel, ASCENDING, DESCENDING


class Episode(BaseModel):
    episode_number: int
    filename: str | None = None
    size: int | None = None
    file_index: int | None = None
    title: str | None = None
    released: datetime | None = None


class Season(BaseModel):
    season_number: int
    episodes: list[Episode]


class TorrentStreams(Document):
    model_config = ConfigDict(extra="allow")

    id: str
    meta_id: str
    torrent_name: str
    size: int
    season: Optional[Season] = None
    filename: Optional[str] = None
    file_index: Optional[int] = None
    announce_list: list[str]
    languages: list[str]
    source: str
    catalog: list[str]
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    resolution: Optional[str] = None
    codec: Optional[str] = None
    quality: Optional[str] = None
    audio: Optional[str] = None
    seeders: Optional[int] = None
    cached: Optional[bool] = Field(default=False, exclude=True)
    indexer_flags: Optional[list[str]] = Field(default_factory=list)
    is_blocked: Optional[bool] = False

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

    @field_validator("audio", mode="before")
    def validate_audio(cls, v):
        # Ensure audio is a string
        if v and isinstance(v, list):
            return v[0]
        return v

    class Settings:
        indexes = [
            IndexModel(
                [
                    ("meta_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("catalog", ASCENDING),
                ]
            )
        ]

    def get_episode(self, season_number: int, episode_number: int) -> Optional[Episode]:
        """
        Returns the Episode object for the given season and episode number.
        """
        if self.season and self.season.season_number == season_number:
            for episode in self.season.episodes:
                if episode.episode_number == episode_number:
                    return episode
        return None


class TVStreams(Document):
    meta_id: str
    name: str
    url: str | None = None
    ytId: str | None = None
    externalUrl: str | None = None
    source: str
    behaviorHints: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    country: str | None = None
    is_working: Optional[bool] = True
    test_failure_count: int = 0
    namespaces: list[str] = Field(default_factory=lambda: ["mediafusion"])
    drm_key_id: str | None = None
    drm_key: str | None = None

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
            IndexModel([("url", pymongo.TEXT), ("ytId", pymongo.TEXT)]),
        ]


class MediaFusionMetaData(Document):
    id: str
    title: str
    aka_titles: Optional[list[str]] = Field(default_factory=list)
    year: Optional[int] = None
    poster: Optional[str] = None
    is_poster_working: Optional[bool] = True
    is_add_title_to_poster: Optional[bool] = False
    background: Optional[str] = None
    streams: list[Link[TorrentStreams]]
    type: str
    description: Optional[str] = None
    runtime: Optional[str] = None
    website: Optional[str] = None
    genres: Optional[list[str]] = Field(default_factory=list)

    class Settings:
        is_root = True
        indexes = [
            IndexModel([("title", ASCENDING), ("year", ASCENDING)], unique=True),
            IndexModel([("title", pymongo.TEXT)]),
        ]

    @field_validator("runtime", mode="before")
    def validate_runtime(cls, v):
        if v and isinstance(v, int):
            return f"{v} min"
        return v


class MediaFusionMovieMetaData(MediaFusionMetaData):
    type: str = "movie"
    imdb_rating: Optional[float] = None
    parent_guide_nudity_status: Optional[str] = "None"
    parent_guide_certificates: Optional[list[str]] = Field(default_factory=list)
    stars: Optional[list[str]] = Field(default_factory=list)


class MediaFusionSeriesMetaData(MediaFusionMetaData):
    type: str = "series"
    end_year: Optional[int] = None
    imdb_rating: Optional[float] = None
    parent_guide_nudity_status: Optional[str] = "None"
    parent_guide_certificates: Optional[list[str]] = Field(default_factory=list)
    stars: Optional[list[str]] = Field(default_factory=list)


class MediaFusionTVMetaData(MediaFusionMetaData):
    type: str = "tv"
    country: str | None = None
    tv_language: str | None = None
    logo: Optional[str] = None
    streams: list[Link[TVStreams]]


class MediaFusionEventsMetaData(MediaFusionMetaData):
    type: str = "events"
    event_start_timestamp: Optional[int] = None
    logo: Optional[str] = None
    streams: list[TVStreams]
