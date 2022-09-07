from datetime import datetime
from typing import Optional

import pymongo
from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class TamilBlasterMovie(Document):
    name: str
    catalog: str
    poster: str
    imdb_id: Optional[str]
    tamilblaster_id: Optional[str]
    created_at: datetime = Field(default_factory=datetime.now)
    video_qualities: dict

    class Settings:
        indexes = [
            IndexModel(
                [("name", pymongo.ASCENDING), ("catalog", pymongo.ASCENDING)],
                unique=True,
            )
        ]
