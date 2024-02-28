import math
from typing import Optional, Literal

from pydantic import BaseModel, Field, model_validator, field_validator

from db.models import TorrentStreams
from utils import const


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Video(BaseModel):
    id: str
    title: str
    released: str
    season: int | None = None
    episode: int | None = None


class Meta(BaseModel):
    id: str = Field(alias="_id")
    name: str = Field(alias="title")
    type: str = Field(default="movie")
    poster: str | None = None
    background: str | None = None
    videos: list[Video] | None = None
    country: str | None = None
    language: str | None = Field(None, alias="tv_language")
    logo: str | None = None
    genres: list[str] | None = None


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
    service: Literal[
        "realdebrid",
        "seedr",
        "debridlink",
        "alldebrid",
        "offcloud",
        "pikpak",
        "torbox",
        "premiumize",
    ]
    token: str | None = None
    username: str | None = None
    password: str | None = None
    enable_watchlist_catalogs: bool = True

    @model_validator(mode="after")
    def validate_token_or_username_password(self) -> "StreamingProvider":
        # validating the token or username and password
        if not self.token and not self.username and not self.password:
            raise ValueError("Either token or username and password must be present")
        return self

    class Config:
        extra = "ignore"


class UserData(BaseModel):
    streaming_provider: StreamingProvider | None = None
    selected_catalogs: list[str] = Field(default=const.CATALOG_ID_DATA)
    selected_resolutions: list[str | None] = Field(default=const.RESOLUTIONS)
    enable_catalogs: bool = True
    max_size: int | str | float = math.inf
    max_streams_per_resolution: int = 3
    show_full_torrent_name: bool = False
    torrent_sorting_priority: list[str] = Field(default=const.TORRENT_SORTING_PRIORITY)
    api_password: str | None = None

    @model_validator(mode="after")
    def validate_selected_resolutions(self) -> "UserData":
        if "" in self.selected_resolutions:
            self.selected_resolutions.remove("")
            self.selected_resolutions.append(None)

        # validating the selected resolutions
        for resolution in self.selected_resolutions:
            if resolution not in const.RESOLUTIONS:
                raise ValueError("Invalid resolution")
        return self

    @field_validator("max_size", mode="before")
    def parse_max_size(cls, v):
        if isinstance(v, int):
            return v
        elif v == "inf":
            return math.inf
        if v.isdigit():
            return int(v)
        raise ValueError("Invalid max_size")

    @field_validator("torrent_sorting_priority", mode="after")
    def validate_torrent_sorting_priority(cls, v):
        for priority in v:
            if priority not in const.TORRENT_SORTING_PRIORITY:
                raise ValueError("Invalid priority")
        return v

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
    poster: str | None = None
    background: Optional[str] = None
    country: str
    tv_language: str
    logo: Optional[str] = None
    genres: list[str] = []
    streams: list[TVStreams]


class TorrentStreamsList(BaseModel):
    streams: list[TorrentStreams]
