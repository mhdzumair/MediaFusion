from typing import Optional, Any, Literal

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
    infoHash: str | None = None
    url: str | None = None
    stream_name: str | None = Field(exclude=True)
    behaviorHints: dict[str, Any] | None = None


class Streams(BaseModel):
    streams: Optional[list[Stream]] = []


class StreamingProvider(BaseModel):
    service: Literal["realdebrid", "seedr"]
    token: str

    class Config:
        extra = "ignore"


class UserData(BaseModel):
    streaming_provider: StreamingProvider | None = None
    preferred_movie_languages: list[str] = Field(
        default=["Tamil", "Malayalam", "Telugu", "Hindi", "Kannada", "English", "Dubbed"]
    )
    preferred_series_languages: list[str] = Field(
        default=["Tamil", "Malayalam", "Telugu", "Hindi", "Kannada", "English", "Dubbed"]
    )

    class Config:
        extra = "ignore"


class AuthorizeData(BaseModel):
    device_code: str
