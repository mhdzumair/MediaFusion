from typing import Optional, Literal

from pydantic import BaseModel, Field, model_validator

from utils.const import CATALOG_ID_DATA


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Meta(BaseModel):
    id: str = Field(alias="_id")
    name: str = Field(alias="title")
    type: str = Field(default="movie")
    poster: str
    background: str | None = None
    videos: list | None = None
    country: str | None = None
    language: str | None = Field(None, alias="tv_language")
    logo: Optional[str] = None
    genres: Optional[list[str]] = None


class MetaItem(BaseModel):
    meta: Meta


class Metas(BaseModel):
    metas: list[Meta] = []


class StreamBehaviorHints(BaseModel):
    notWebReady: Optional[bool] = None
    bingeGroup: Optional[str] = None
    proxyHeaders: Optional[dict[Literal["request", "response"], dict]] = None


class Stream(BaseModel):
    name: str
    description: str
    infoHash: str | None = None
    fileIdx: int | None = None
    url: str | None = None
    ytId: str | None = None
    behaviorHints: StreamBehaviorHints | None = None


class Streams(BaseModel):
    streams: Optional[list[Stream]] = []


class StreamingProvider(BaseModel):
    service: Literal["realdebrid", "seedr", "debridlink"]
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
    id: str = Field(alias="_id")


class TVStreamsBehaviorHints(StreamBehaviorHints):
    is_redirect: bool = False


class TVStreams(BaseModel):
    name: str
    url: str | None = None
    ytId: str | None = None
    source: str
    behaviorHints: TVStreamsBehaviorHints | None = None

    @model_validator(mode="after")
    def validate_url_or_yt_id(self) -> "TVStreams":
        if not self.url and not self.ytId:
            raise ValueError("Either url or ytId must be present")
        return self


class TVMetaData(BaseModel):
    title: str
    poster: str
    background: Optional[str] = None
    country: str
    tv_language: str
    logo: Optional[str] = None
    genres: list[str] = []
    streams: list[TVStreams]
