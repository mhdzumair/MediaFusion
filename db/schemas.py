from typing import List, Optional, Any

from pydantic import BaseModel, Field


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Meta(BaseModel):
    id: Any
    name: str
    type: str = Field(default="movie")
    poster: str


class Movie(BaseModel):
    metas: list[Meta] = []


class Stream(BaseModel):
    name: str | None = None
    description: str | None = None
    infoHash: str


class Streams(BaseModel):
    streams: Optional[List[Stream]] = []
