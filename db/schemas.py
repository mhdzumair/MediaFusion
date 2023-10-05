from typing import Optional, Any, Literal

from pydantic import BaseModel, Field

from utils.const import CATALOG_ID_DATA


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Meta(BaseModel):
    id: str
    name: str = Field(alias="title")
    type: str = Field(default="movie")
    poster: str
    videos: list | None = None


class Metas(BaseModel):
    metas: list[Meta] = []


class Stream(BaseModel):
    name: str
    description: str
    infoHash: str | None = None
    fileIdx: int | None = None
    url: str | None = None
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
    selected_catalogs: list[str] = Field(default=CATALOG_ID_DATA)

    class Config:
        extra = "ignore"


class AuthorizeData(BaseModel):
    device_code: str


class MetaIdProjection(BaseModel):
    id: str
