from datetime import datetime
from typing import Optional, Any

import pymongo
from beanie import Document, Link, BackLink
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING


class Episode(BaseModel):
    episode_number: int
    filename: str
    size: int
    file_index: int


class Season(BaseModel):
    season_number: int
    episodes: list[Episode]


class TorrentStreams(Document):
    id: str
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
    encoder: Optional[str] = None
    seeders: Optional[int] = None
    cached: Optional[bool] = None
    meta_id: Optional[str] = None

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
    name: str
    url: str | None = None
    ytId: str | None = None
    source: str
    behaviorHints: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    meta_id: Optional[str] = None


class MediaFusionMetaData(Document):
    id: str
    title: str
    year: Optional[int] = None
    poster: str
    is_poster_working: Optional[bool] = True
    background: Optional[str] = None
    streams: list[Link[TorrentStreams]]
    type: str

    class Settings:
        is_root = True
        indexes = [
            IndexModel([("title", ASCENDING), ("year", ASCENDING)], unique=True),
            IndexModel([("title", pymongo.TEXT)]),
        ]


class MediaFusionMovieMetaData(MediaFusionMetaData):
    type: str = "movie"


class MediaFusionSeriesMetaData(MediaFusionMetaData):
    type: str = "series"


class MediaFusionTVMetaData(MediaFusionMetaData):
    type: str = "tv"
    country: str
    tv_language: str
    logo: Optional[str] = None
    genres: Optional[list[str]] = None
    is_approved: bool = False
    streams: list[Link[TVStreams]]


class SearchHistory(Document):
    query: str
    last_searched: datetime = Field(default_factory=datetime.now)
