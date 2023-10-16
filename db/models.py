from datetime import datetime
from typing import Optional

import pymongo
from beanie import Document, Link
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


class Streams(Document):
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
    resolution: Optional[str]
    codec: Optional[str]
    quality: Optional[str]
    audio: Optional[str]
    encoder: Optional[str]
    seeders: Optional[int] = None
    cached: Optional[bool] = None

    def get_episode(self, season_number: int, episode_number: int) -> Optional[Episode]:
        """
        Returns the Episode object for the given season and episode number.
        """
        if self.season and self.season.season_number == season_number:
            for episode in self.season.episodes:
                if episode.episode_number == episode_number:
                    return episode
        return None


class MediaFusionMetaData(Document):
    id: str
    title: str
    year: Optional[int]
    poster: str
    background: str
    streams: list[Link[Streams]]
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
