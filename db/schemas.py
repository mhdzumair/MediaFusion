import math
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
    description: str | None = None
    runtime: str | None = None
    website: str | None = None
    imdbRating: str | float | None = Field(None, alias="imdb_rating")
    releaseInfo: str | int | None = Field(None, alias="year")

    @model_validator(mode="after")
    def parse_meta(self) -> "Meta":
        if self.releaseInfo:
            self.releaseInfo = (
                f"{self.releaseInfo}-"
                if self.type == "series"
                else str(self.releaseInfo)
            )
        if self.imdbRating:
            self.imdbRating = str(self.imdbRating)

        return self


class MetaItem(BaseModel):
    meta: Meta


class Metas(BaseModel):
    metas: list[Meta] = []


class StreamBehaviorHints(BaseModel):
    notWebReady: Optional[bool] = None
    bingeGroup: Optional[str] = None
    proxyHeaders: Optional[dict[Literal["request", "response"], dict]] = None
    filename: Optional[str] = None
    videoSize: Optional[int] = None


class Stream(BaseModel):
    name: str
    description: str
    infoHash: str | None = None
    fileIdx: int | None = None
    url: str | None = None
    ytId: str | None = None
    externalUrl: str | None = None
    behaviorHints: StreamBehaviorHints | None = None
    sources: list[str] | None = None


class Streams(BaseModel):
    streams: Optional[list[Stream]] = []


class QBittorrentConfig(BaseModel):
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: str
    seeding_time_limit: int = 1440  # 24 hours
    seeding_ratio_limit: float = 1.0
    play_video_after: int = Field(default=100, le=100, ge=0)
    category: str = "MediaFusion"
    webdav_url: str
    webdav_username: str
    webdav_password: str
    webdav_downloads_path: str = "/"  # Default to a root path if not specified


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
        "qbittorrent",
    ]
    token: str | None = None
    email: str | None = None
    password: str | None = None
    enable_watchlist_catalogs: bool = True
    qbittorrent_config: QBittorrentConfig | None = None

    @model_validator(mode="after")
    def validate_token_or_username_password(self) -> "StreamingProvider":
        # validating the token or (email and password) or qbittorrent_config
        required_fields = const.STREAMING_SERVICE_REQUIREMENTS.get(
            self.service, const.STREAMING_SERVICE_REQUIREMENTS["default"]
        )

        # check if the required fields are present
        for field in required_fields:
            if getattr(self, field, None) is None:
                raise ValueError(f"{field} is required")

        return self

    class Config:
        extra = "ignore"


class UserData(BaseModel):
    streaming_provider: StreamingProvider | None = None
    selected_catalogs: list[str] = Field(
        default=["prowlarr_streams", "torrentio_streams"]
    )
    selected_resolutions: list[str | None] = Field(default=const.RESOLUTIONS)
    enable_catalogs: bool = True
    max_size: int | str | float = math.inf
    max_streams_per_resolution: int = 3
    show_full_torrent_name: bool = True
    torrent_sorting_priority: list[str] = Field(default=const.TORRENT_SORTING_PRIORITY)
    nudity_filter: list[
        Literal["Disable", "None", "Mild", "Moderate", "Severe"]
    ] = Field(default=["Severe"])
    certification_filter: list[
        Literal[
            "Disable", "All Ages", "Children", "Parental Guidance", "Teens", "Adults"
        ]
    ] = Field(default=["Adults"])
    api_password: str | None = None
    proxy_debrid_stream: bool = False

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

    @field_validator("nudity_filter", mode="after")
    def validate_nudity_filter(cls, v):
        return v or ["Severe"]

    @field_validator("certification_filter", mode="after")
    def validate_certification_filter(cls, v):
        return v or ["Adults"]

    class Config:
        extra = "ignore"


class AuthorizeData(BaseModel):
    device_code: str


class MetaIdProjection(BaseModel):
    id: str = Field(alias="_id")


class TVStreams(BaseModel):
    name: str
    url: str | None = None
    ytId: str | None = None
    source: str
    country: str | None = None
    behaviorHints: StreamBehaviorHints | None = None

    @model_validator(mode="after")
    def validate_url_or_yt_id(self) -> "TVStreams":
        if not self.url and not self.ytId:
            raise ValueError("Either url or ytId must be present")
        return self


class TVMetaData(BaseModel):
    title: str
    poster: str | None = None
    background: Optional[str] = None
    country: str | None = None
    tv_language: str | None = None
    logo: Optional[str] = None
    genres: list[str] = []
    streams: list[TVStreams]
    namespace: str = "mediafusion"


class TorrentStreamsList(BaseModel):
    streams: list[TorrentStreams]


class ScraperTask(BaseModel):
    spider_name: Literal[
        "formula_tgx",
        "nowmetv",
        "nowsports",
        "tamilultra",
        "sport_video",
        "streamed",
        "mrgamingstreams",
        "tamilmv",
        "tamil_blasters",
        "crictime",
        "streambtw",
        "dlhd",
        "motogp_tgx",
    ]
    pages: int | None = 1
    start_page: int | None = 1
    search_keyword: str | None = None
    scrape_all: bool = False
    scrap_catalog_id: Literal[
        "all",
        "tamil_hdrip",
        "tamil_tcrip",
        "tamil_dubbed",
        "tamil_series",
        "malayalam_hdrip",
        "malayalam_tcrip",
        "malayalam_dubbed",
        "malayalam_series",
        "telugu_tcrip",
        "telugu_hdrip",
        "telugu_dubbed",
        "telugu_series",
        "hindi_tcrip",
        "hindi_hdrip",
        "hindi_dubbed",
        "hindi_series",
        "kannada_tcrip",
        "kannada_hdrip",
        "kannada_series",
        "english_tcrip",
        "english_hdrip",
        "english_series",
    ] = "all"
    api_password: str = None


class TVMetaDataUpload(BaseModel):
    api_password: str = None
    tv_metadata: TVMetaData
