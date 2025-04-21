import math
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl

from db.config import settings
from db.enums import NudityStatus
# Removed TorrentStreams import
from utils import const


class Catalog(BaseModel):
    id: str
    name: str
    type: str


class Video(BaseModel):
    id: str
    title: str
    released: str | None = None
    season: int | None = None
    episode: int | None = None
    thumbnail: str | None = None


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
    cast: list[str] | None = Field(None, alias="stars")

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
    metas: list[Meta] = Field(default_factory=list)


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
    streams: Optional[list[Stream]] = Field(default_factory=list)


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
    public_ip: str | None = Field(alias="pip")
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


# Removed StreamingProvider class

# Removed SortingOption class

class MDBListItem(BaseModel):
    id: int = Field(alias="i")
    title: str = Field(alias="t")
    catalog_type: Literal["movie", "series"] = Field(alias="ct")
    use_filters: bool = Field(default=False, alias="uf")
    sort: str | None = Field(default="rank", alias="s")
    order: Literal["asc", "desc"] = Field(default="desc", alias="o")

    @property
    def catalog_id(self) -> str:
        return f"mdblist_{self.catalog_type}_{self.id}"

    class Config:
        extra = "ignore"
        populate_by_name = True


class MDBListConfig(BaseModel):
    api_key: str = Field(alias="ak")
    lists: list[MDBListItem] = Field(default_factory=list, alias="l")

    class Config:
        extra = "ignore"
        populate_by_name = True


class UserData(BaseModel):
    # Removed streaming_provider field
    selected_catalogs: list[str] = Field(alias="sc", default_factory=list)
    # Removed selected_resolutions field
    enable_catalogs: bool = Field(default=True, alias="ec")
    enable_imdb_metadata: bool = Field(default=False, alias="eim")
    # Removed max_size field
    # Removed max_streams_per_resolution field
    # Removed show_full_torrent_name field
    # Removed torrent_sorting_priority field
    nudity_filter: list[NudityStatus] = Field(default=[NudityStatus.SEVERE], alias="nf")
    certification_filter: list[
        Literal[
            "Disable",
            "Unknown",
            "All Ages",
            "Children",
            "Parental Guidance",
            "Teens",
            "Adults",
            "Adults+",
        ]
    ] = Field(default=["Adults+"], alias="cf")
    api_password: str | None = Field(default=None, alias="ap")
    # Removed language_sorting field
    # Removed quality_filter field
    mediaflow_config: MediaFlowConfig | None = Field(default=None, alias="mfc")
    rpdb_config: RPDBConfig | None = Field(default=None, alias="rpc")
    # Removed live_search_streams field
    # Removed contribution_streams field
    # Removed show_language_country_flag field
    mdblist_config: MDBListConfig | None = Field(default=None, alias="mdb")

    # Removed validate_selected_resolutions validator
    # Removed parse_max_size validator
    # Removed validate_torrent_sorting_priority validator

    @field_validator("nudity_filter", mode="after")
    def validate_nudity_filter(cls, v):
        return v or ["Severe"]

    @field_validator("certification_filter", mode="after")
    def validate_certification_filter(cls, v):
        return v or ["Adults+"]

    # Removed validate_quality_filter validator
    # Removed validate_language_sorting validator
    # Removed is_sorting_option_present method
    # Removed get_sorting_direction method

    class Config:
        extra = "ignore"
        populate_by_name = True


class AuthorizeData(BaseModel):
    device_code: str


class MetaIdProjection(BaseModel):
    id: str = Field(alias="_id")
    type: str


class MetaSearchProjection(BaseModel):
    id: str = Field(alias="_id")
    title: str
    aka_titles: Optional[list[str]] = Field(default_factory=list)


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
    genres: list[str] = Field(default_factory=list)
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
        "tamilmv",
        "tamil_blasters",
        "dlhd",
        "motogp_tgx",
        "arab_torrents",
        "wwe_tgx",
        "ufc_tgx",
        "movies_tv_tgx",
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
    total_pages: int | None = None
    api_password: str = None


class TVMetaDataUpload(BaseModel):
    api_password: str = None
    tv_metadata: TVMetaData


class KodiConfig(BaseModel):
    code: str = Field(max_length=6)
    manifest_url: HttpUrl


class BlockTorrent(BaseModel):
    info_hash: str
    action: Literal["block", "delete"]
    api_password: str


class CacheStatusRequest(BaseModel):
    """Request model for checking cache status"""

    service: Literal[
        "realdebrid",
        "premiumize",
        "alldebrid",
        "debridlink",
        "offcloud",
        "seedr",
        "pikpak",
        "torbox",
        # Removed easydebrid
    ]
    info_hashes: list[str]


class CacheStatusResponse(BaseModel):
    """Response model for cache status"""

    cached_status: dict[str, bool]


class CacheSubmitRequest(BaseModel):
    """Request model for submitting cached info hashes"""

    service: Literal[
        "realdebrid",
        "premiumize",
        "alldebrid",
        "debridlink",
        "offcloud",
        "seedr",
        "pikpak",
        "torbox",
        # Removed easydebrid
    ]
    info_hashes: list[str]


class CacheSubmitResponse(BaseModel):
    """Response model for cache submission"""

    success: bool
    message: str


class MigrateID(BaseModel):
    mediafusion_id: str
    imdb_id: str
    media_type: Literal["movie", "series"]
