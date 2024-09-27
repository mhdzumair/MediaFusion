import math
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl

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
    qbittorrent_url: str = Field(alias="qur")
    qbittorrent_username: str = Field(alias="qus")
    qbittorrent_password: str = Field(alias="qpw")
    seeding_time_limit: int = Field(default=1440, alias="stl")  # 24 hours
    seeding_ratio_limit: float = Field(default=1.0, alias="srl")
    play_video_after: int = Field(
        default=100, le=100, ge=0, alias="pva"
    )  # 100% downloaded
    category: str = Field(default="MediaFusion", alias="cat")
    webdav_url: str = Field(alias="wur")
    webdav_username: str = Field(alias="wus")
    webdav_password: str = Field(alias="wpw")
    webdav_downloads_path: str = Field(
        default="/", alias="wdp"
    )  # Default to a root path if not specified

    class Config:
        extra = "ignore"
        populate_by_name = True


class MediaFlowConfig(BaseModel):
    proxy_url: str | None = Field(alias="pu")
    api_password: str | None = Field(alias="ap")
    proxy_live_streams: bool = Field(default=False, alias="pls")
    proxy_debrid_streams: bool = Field(default=False, alias="pds")

    class Config:
        extra = "ignore"
        populate_by_name = True


class RPDBConfig(BaseModel):
    api_key: str = Field(alias="ak")

    class Config:
        extra = "ignore"
        populate_by_name = True


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
    ] = Field(alias="sv")
    token: str | None = Field(default=None, alias="tk")
    email: str | None = Field(default=None, alias="em")
    password: str | None = Field(default=None, alias="pw")
    enable_watchlist_catalogs: bool = Field(default=True, alias="ewc")
    qbittorrent_config: QBittorrentConfig | None = Field(default=None, alias="qbc")
    download_via_browser: bool = Field(default=False, alias="dvb")

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
        populate_by_name = True


class UserData(BaseModel):
    streaming_provider: StreamingProvider | None = Field(default=None, alias="sp")
    selected_catalogs: list[str] = Field(
        default=["prowlarr_streams", "torrentio_streams", "zilean_dmm_streams"],
        alias="sc",
    )
    selected_resolutions: list[str | None] = Field(
        default=const.RESOLUTIONS, alias="sr"
    )
    enable_catalogs: bool = Field(default=True, alias="ec")
    enable_imdb_metadata: bool = Field(default=True, alias="eim")
    max_size: int | str | float = Field(default=math.inf, alias="ms")
    max_streams_per_resolution: int = Field(default=3, alias="mspr")
    show_full_torrent_name: bool = Field(default=True, alias="sftn")
    torrent_sorting_priority: list[str] = Field(
        default=const.TORRENT_SORTING_PRIORITY, alias="tsp"
    )
    nudity_filter: list[Literal["Disable", "None", "Mild", "Moderate", "Severe"]] = (
        Field(default=["Severe"], alias="nf")
    )
    certification_filter: list[
        Literal[
            "Disable", "All Ages", "Children", "Parental Guidance", "Teens", "Adults"
        ]
    ] = Field(default=["Adults"], alias="cf")
    api_password: str | None = Field(default=None, alias="ap")
    language_sorting: list[str | None] = Field(
        default=list(const.SUPPORTED_LANGUAGES), alias="ls"
    )
    quality_filter: list[str] = Field(
        default=list(const.QUALITY_GROUPS.keys()), alias="qf"
    )
    mediaflow_config: MediaFlowConfig | None = Field(default=None, alias="mfc")
    rpdb_config: RPDBConfig | None = Field(default=None, alias="rpc")

    @field_validator("selected_resolutions", mode="after")
    def validate_selected_resolutions(cls, v):
        # validating the selected resolutions
        for resolution in v:
            if resolution not in const.RESOLUTIONS:
                raise ValueError("Invalid resolution")
        return v

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
            if priority not in const.TORRENT_SORTING_PRIORITY_OPTIONS:
                raise ValueError("Invalid priority")
        return v

    @field_validator("nudity_filter", mode="after")
    def validate_nudity_filter(cls, v):
        return v or ["Severe"]

    @field_validator("certification_filter", mode="after")
    def validate_certification_filter(cls, v):
        return v or ["Adults"]

    @field_validator("quality_filter", mode="after")
    def validate_quality_filter(cls, v):
        for quality in v:
            if quality not in const.QUALITY_GROUPS:
                raise ValueError("Invalid quality")
        return v

    @field_validator("language_sorting", mode="after")
    def validate_language_sorting(cls, v):
        for language in v:
            if language not in const.SUPPORTED_LANGUAGES:
                raise ValueError("Invalid language")
        return v

    class Config:
        extra = "ignore"
        populate_by_name = True


class AuthorizeData(BaseModel):
    device_code: str


class MetaIdProjection(BaseModel):
    id: str = Field(alias="_id")


class TVMetaProjection(BaseModel):
    id: str = Field(alias="_id")
    title: str


class TVStreams(BaseModel):
    name: str
    url: str | None = None
    ytId: str | None = None
    source: str
    country: str | None = None
    behaviorHints: StreamBehaviorHints | None = None
    drm_key_id: str | None = None
    drm_key: str | None = None

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
    namespace: str = Field(default="mediafusion")


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
        "tamilmv",
        "tamil_blasters",
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


class KodiConfig(BaseModel):
    code: str = Field(max_length=6)
    manifest_url: HttpUrl
