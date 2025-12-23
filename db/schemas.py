import math
from datetime import datetime
from typing import Literal, Optional, List

from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl

from db.config import settings
from db.enums import NudityStatus
from utils import const


class PosterData(BaseModel):
    """Data required for poster generation"""
    id: str
    poster: str
    title: str
    imdb_rating: Optional[float] = None
    is_add_title_to_poster: bool = False


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
        "stremthru",
        "easydebrid",
        "debrider",
    ] = Field(alias="sv")
    stremthru_store_name: (
        Literal[
            "realdebrid",
            "debridlink",
            "alldebrid",
            "torbox",
            "premiumize",
        ]
        | None
    ) = Field(default=None, alias="stsn")
    url: HttpUrl | None = Field(default=None, alias="u")
    token: str | None = Field(default=None, alias="tk")
    email: str | None = Field(default=None, alias="em")
    password: str | None = Field(default=None, alias="pw")
    enable_watchlist_catalogs: bool = Field(default=True, alias="ewc")
    qbittorrent_config: QBittorrentConfig | None = Field(default=None, alias="qbc")
    download_via_browser: bool = Field(default=False, alias="dvb")
    only_show_cached_streams: bool = Field(default=False, alias="oscs")

    @model_validator(mode="after")
    def validate_token_or_username_password(self) -> "StreamingProvider":
        if self.service in settings.disabled_providers:
            raise ValueError(
                f"The streaming provider '{self.service}' has been disabled by the administrator"
            )

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


class SortingOption(BaseModel):
    key: str = Field(alias="k")
    direction: Literal["asc", "desc"] = Field(default="desc", alias="d")

    class Config:
        extra = "ignore"
        populate_by_name = True


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
    streaming_provider: StreamingProvider | None = Field(default=None, alias="sp")
    selected_catalogs: list[str] = Field(alias="sc", default_factory=list)
    selected_resolutions: list[str | None] = Field(
        default=const.RESOLUTIONS, alias="sr"
    )
    enable_catalogs: bool = Field(default=True, alias="ec")
    enable_imdb_metadata: bool = Field(default=False, alias="eim")
    max_size: int | str | float = Field(default=math.inf, alias="ms")
    max_streams_per_resolution: int = Field(default=10, alias="mspr")
    show_full_torrent_name: bool = Field(default=True, alias="sftn")
    torrent_sorting_priority: list[SortingOption] = Field(
        default_factory=lambda: [
            SortingOption(key=k) for k in const.TORRENT_SORTING_PRIORITY
        ],
        alias="tsp",
    )
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
    language_sorting: list[str | None] = Field(
        default=const.LANGUAGES_FILTERS, alias="ls"
    )
    quality_filter: list[str] = Field(
        default=list(const.QUALITY_GROUPS.keys()), alias="qf"
    )
    mediaflow_config: MediaFlowConfig | None = Field(default=None, alias="mfc")
    rpdb_config: RPDBConfig | None = Field(default=None, alias="rpc")
    live_search_streams: bool = Field(default=False, alias="lss")
    contribution_streams: bool = Field(default=False, alias="cs")
    show_language_country_flag: bool = Field(default=False, alias="slcf")
    mdblist_config: MDBListConfig | None = Field(default=None, alias="mdb")

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

    @field_validator("torrent_sorting_priority", mode="before")
    def validate_torrent_sorting_priority(cls, v):
        # Validate the sorting priority
        for priority in v:
            if isinstance(priority, dict):
                key = priority.get("k", priority.get("key"))
                if key not in const.TORRENT_SORTING_PRIORITY_OPTIONS:
                    raise ValueError(f"Invalid priority {key}")
            elif isinstance(priority, str):
                if priority not in const.TORRENT_SORTING_PRIORITY_OPTIONS:
                    raise ValueError(f"Invalid priority {priority}")

        if isinstance(v, list):
            # Handle string items (old format)
            if v and isinstance(v[0], str):
                return [SortingOption(key=item) for item in v]
        return v

    @field_validator("nudity_filter", mode="after")
    def validate_nudity_filter(cls, v):
        return v or ["Severe"]

    @field_validator("certification_filter", mode="after")
    def validate_certification_filter(cls, v):
        return v or ["Adults+"]

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

    def is_sorting_option_present(self, key: str) -> bool:
        return any(sort.key == key for sort in self.torrent_sorting_priority)

    def get_sorting_direction(self, key: str) -> str:
        for sort in self.torrent_sorting_priority:
            if sort.key == key:
                return sort.direction
        return "desc"

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
    streams: list["TorrentStreamData"]


class KnownFile(BaseModel):
    """File information for known files in a torrent"""
    size: int
    filename: str


class SeriesEpisodeData(BaseModel):
    """Series episode metadata from IMDb"""
    season_number: int
    episode_number: int
    title: Optional[str] = None
    overview: Optional[str] = None
    released: Optional[datetime] = None
    imdb_rating: Optional[float] = None
    tmdb_rating: Optional[float] = None
    thumbnail: Optional[str] = None

    @model_validator(mode="after")
    def validate_title(self):
        if not self.title:
            self.title = f"Episode {self.episode_number}"
        return self


class CatalogStats(BaseModel):
    """Catalog statistics for metadata"""
    catalog: str
    total_streams: int = 0
    last_stream_added: Optional[datetime] = None


class EpisodeFileData(BaseModel):
    """Database-agnostic episode file representation"""
    season_number: int
    episode_number: int
    file_index: Optional[int] = None
    filename: Optional[str] = None
    size: Optional[int] = None
    title: Optional[str] = None
    released: Optional[datetime] = None
    thumbnail: Optional[str] = None
    overview: Optional[str] = None


class TorrentStreamData(BaseModel):
    """Database-agnostic torrent stream representation for parser compatibility.
    
    Note: The `id` field stores the torrent info_hash (40-character hex string).
    Use `info_hash` property as an alias for clarity when needed.
    """
    model_config = {"extra": "allow"}
    
    # id is the torrent info_hash (40-char hex string), used as primary key
    id: str = Field(description="Torrent info_hash (40-character hex string)")
    meta_id: str
    torrent_name: str
    size: int
    source: str
    resolution: Optional[str] = None
    codec: Optional[str] = None
    quality: Optional[str] = None
    audio: Optional[List[str]] = None
    seeders: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    uploaded_at: Optional[datetime] = None
    uploader: Optional[str] = None
    is_blocked: bool = False
    filename: Optional[str] = None
    file_index: Optional[int] = None
    hdr: Optional[List[str]] = None
    torrent_file: Optional[bytes] = None
    torrent_type: Optional[str] = "public"  # TorrentType enum value
    languages: List[str] = Field(default_factory=list)
    announce_list: List[str] = Field(default_factory=list)
    episode_files: List[EpisodeFileData] = Field(default_factory=list)
    catalog: List[str] = Field(default_factory=list)
    # Store file details from debrid service for later metadata fixing
    known_file_details: Optional[List["KnownFile"]] = None
    
    @property
    def info_hash(self) -> str:
        """Alias for id - returns the torrent info_hash."""
        return self.id
    
    @field_validator("audio", mode="before")
    @classmethod
    def parse_audio(cls, v):
        """Convert string audio to list, handling both comma and pipe separators"""
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            # Handle both comma and pipe separators
            if "|" in v:
                return [a.strip() for a in v.split("|") if a.strip()]
            elif "," in v:
                return [a.strip() for a in v.split(",") if a.strip()]
            return [v] if v.strip() else None
        return None
    
    def __hash__(self):
        """Make TorrentStreamData hashable by its info_hash (id field)."""
        return hash(self.id)
    
    def __eq__(self, other):
        """Equality based on info_hash (id field) for deduplication."""
        if isinstance(other, TorrentStreamData):
            return self.id == other.id
        return False
    
    def get_episodes(self, season_number: int, episode_number: int) -> List[EpisodeFileData]:
        """Returns episode files for the given season and episode, sorted by size descending"""
        episodes = [
            ep for ep in self.episode_files
            if ep.season_number == season_number and ep.episode_number == episode_number
        ]
        return sorted(episodes, key=lambda ep: ep.size or 0, reverse=True)
    
    @classmethod
    def from_pg_model(cls, pg_stream) -> "TorrentStreamData":
        """Create from PostgreSQL TorrentStream model"""
        # audio is stored as comma-separated string in DB, validator will convert to list
        return cls(
            id=pg_stream.id,
            meta_id=pg_stream.meta_id,
            torrent_name=pg_stream.torrent_name,
            size=pg_stream.size,
            source=pg_stream.source,
            resolution=pg_stream.resolution,
            codec=pg_stream.codec,
            quality=pg_stream.quality,
            audio=pg_stream.audio,  # Validator converts string to list
            seeders=pg_stream.seeders,
            created_at=pg_stream.created_at,
            updated_at=pg_stream.updated_at,
            uploaded_at=pg_stream.uploaded_at,
            uploader=pg_stream.uploader,
            is_blocked=pg_stream.is_blocked,
            filename=pg_stream.filename,
            file_index=pg_stream.file_index,
            hdr=pg_stream.hdr,
            torrent_file=pg_stream.torrent_file,
            torrent_type=pg_stream.torrent_type,
            languages=[lang.name for lang in pg_stream.languages] if pg_stream.languages else [],
            announce_list=[url.name for url in pg_stream.announce_urls] if pg_stream.announce_urls else [],
            episode_files=[
                EpisodeFileData(
                    season_number=ef.season_number,
                    episode_number=ef.episode_number,
                    file_index=ef.file_index,
                    filename=ef.filename,
                    size=ef.size,
                )
                for ef in pg_stream.episode_files
            ] if pg_stream.episode_files else [],
            catalog=[],
        )


class MetadataData(BaseModel):
    """Database-agnostic metadata representation for scraper compatibility"""
    model_config = {"extra": "allow"}
    
    id: str
    title: str
    year: Optional[int] = None
    end_year: Optional[int] = None  # For series
    poster: Optional[str] = None
    background: Optional[str] = None
    description: Optional[str] = None
    runtime: Optional[str] = None
    imdb_rating: Optional[float] = None
    aka_titles: List[str] = Field(default_factory=list)
    genres: List[str] = Field(default_factory=list)
    parent_guide_nudity_status: Optional[str] = None
    type: Optional[str] = None  # 'movie' or 'series'
    
    @classmethod
    def from_pg_movie(cls, pg_movie) -> "MetadataData":
        """Convert PostgreSQL MovieMetadata to adapter model"""
        base = pg_movie.base_metadata
        return cls(
            id=pg_movie.id,
            title=base.title if base else "",
            year=base.year if base else None,
            end_year=None,  # Movies don't have end_year
            poster=base.poster if base else None,
            background=base.background if base else None,
            description=base.description if base else None,
            runtime=base.runtime if base else None,
            imdb_rating=pg_movie.imdb_rating,
            aka_titles=[aka.title for aka in base.aka_titles] if base and base.aka_titles else [],
            genres=[g.name for g in base.genres] if base and base.genres else [],
            parent_guide_nudity_status=pg_movie.parent_guide_nudity_status,
            type="movie",
        )
    
    @classmethod
    def from_pg_series(cls, pg_series) -> "MetadataData":
        """Convert PostgreSQL SeriesMetadata to adapter model"""
        base = pg_series.base_metadata
        return cls(
            id=pg_series.id,
            title=base.title if base else "",
            year=base.year if base else None,
            end_year=pg_series.end_year,  # Series have end_year
            poster=base.poster if base else None,
            background=base.background if base else None,
            description=base.description if base else None,
            runtime=base.runtime if base else None,
            imdb_rating=pg_series.imdb_rating,
            aka_titles=[aka.title for aka in base.aka_titles] if base and base.aka_titles else [],
            genres=[g.name for g in base.genres] if base and base.genres else [],
            parent_guide_nudity_status=pg_series.parent_guide_nudity_status,
            type="series",
        )


class MediaFusionEventsMetaData(BaseModel):
    """Events metadata stored in Redis"""
    id: str
    title: str
    description: Optional[str] = None
    poster: Optional[str] = None
    background: Optional[str] = None
    logo: Optional[str] = None
    website: Optional[str] = None
    country: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    is_add_title_to_poster: bool = False
    event_start_timestamp: Optional[int] = None
    streams: List["TVStreams"] = Field(default_factory=list)


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
        "easydebrid",
        "debrider",
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
        "easydebrid",
        "debrider",
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


class ParsingPattern(BaseModel):
    field: str
    path: str
    regex: Optional[str] = None
    regex_group: Optional[int] = 0


class RSSFeedParsingPatterns(BaseModel):
    title: Optional[str] = "title"
    description: Optional[str] = "description"
    pubDate: Optional[str] = "pubDate"
    poster: Optional[str] = None
    background: Optional[str] = None
    logo: Optional[str] = None
    category: Optional[str] = "category"

    # Advanced patterns with regex support
    magnet: Optional[str] = None
    magnet_regex: Optional[str] = None
    torrent: Optional[str] = None
    torrent_regex: Optional[str] = None
    size: Optional[str] = None
    size_regex: Optional[str] = None
    seeders: Optional[str] = None
    seeders_regex: Optional[str] = None
    category_regex: Optional[str] = None
    episode_name_parser: Optional[str] = None  # New field for episode name parsing

    # Regex group numbers (0 = full match, 1+ = capture groups)
    magnet_regex_group: int = 1
    torrent_regex_group: int = 1
    size_regex_group: int = 1
    seeders_regex_group: int = 1
    category_regex_group: int = 1


class CatalogPattern(BaseModel):
    name: str  # Descriptive name for the pattern
    regex: str  # Regex pattern to match content
    case_sensitive: bool = False  # Whether the regex is case sensitive
    enabled: bool = True  # Whether this pattern is active
    target_catalogs: List[str]  # List of catalog IDs to assign when matched


class RSSFeedFilters(BaseModel):
    title_filter: Optional[str] = None  # Regex pattern for title inclusion
    title_exclude_filter: Optional[str] = None  # Regex pattern for title exclusion
    min_size_mb: Optional[int] = None  # Minimum size in MB
    max_size_mb: Optional[int] = None  # Maximum size in MB
    min_seeders: Optional[int] = None  # Minimum number of seeders
    category_filter: Optional[List[str]] = None  # List of allowed categories


class RSSFeedMetrics(BaseModel):
    """RSS Feed scraping metrics"""
    total_items_found: int = 0
    total_items_processed: int = 0
    total_items_skipped: int = 0
    total_errors: int = 0
    last_scrape_duration: Optional[float] = None  # in seconds
    items_processed_last_run: int = 0
    items_skipped_last_run: int = 0
    errors_last_run: int = 0
    skip_reasons: dict[str, int] = Field(default_factory=dict)  # reason -> count


class RSSFeed(BaseModel):
    id: Optional[str] = Field(alias="_id")
    name: str
    url: str
    parsing_patterns: RSSFeedParsingPatterns = Field(default_factory=RSSFeedParsingPatterns)
    filters: RSSFeedFilters = Field(default_factory=RSSFeedFilters)
    active: bool = True
    last_scraped: Optional[datetime] = None
    source: Optional[str] = None  # New field for source name
    torrent_type: Optional[str] = "public"  # New field for torrent type (public/private/webseed)
    auto_detect_catalog: Optional[bool] = False
    catalog_patterns: Optional[List[CatalogPattern]] = Field(default_factory=list)
    metrics: Optional[RSSFeedMetrics] = Field(default_factory=RSSFeedMetrics)


class RSSFeedCreate(BaseModel):
    name: str
    url: str
    auto_detect_catalog: Optional[bool] = False
    catalog_patterns: Optional[List[CatalogPattern]] = Field(default_factory=list)
    parsing_patterns: RSSFeedParsingPatterns = RSSFeedParsingPatterns()
    filters: RSSFeedFilters = RSSFeedFilters()
    active: bool = True
    api_password: Optional[str] = None
    source: Optional[str] = None  # New field for source name
    torrent_type: Optional[str] = "public"  # New field for torrent type


class RSSFeedUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    parsing_patterns: Optional[RSSFeedParsingPatterns] = None
    filters: Optional[RSSFeedFilters] = None
    active: Optional[bool] = None
    auto_detect_catalog: Optional[bool] = None
    catalog_patterns: Optional[List[CatalogPattern]] = None
    source: Optional[str] = None
    torrent_type: Optional[str] = None


class RSSFeedBulkImport(BaseModel):
    feeds: List[RSSFeedCreate]
    api_password: str


class RSSFeedExamine(BaseModel):
    url: str
    api_password: str


class RSSFeedCatalogPatternSchema(BaseModel):
    """Schema for RSS feed catalog pattern from SQL model"""
    id: int
    name: Optional[str] = None
    regex: str
    enabled: bool = True
    case_sensitive: bool = False
    target_catalogs: List[str] = Field(default_factory=list)
    
    model_config = {"from_attributes": True}


class RSSFeedSchema(BaseModel):
    """Schema for RSS feed response from SQL model"""
    id: int
    name: str
    url: str
    parsing_patterns: Optional[RSSFeedParsingPatterns] = None
    filters: Optional[RSSFeedFilters] = None
    active: bool = True
    last_scraped: Optional[datetime] = None
    source: Optional[str] = None
    torrent_type: str = "public"
    auto_detect_catalog: bool = False
    catalog_patterns: List[RSSFeedCatalogPatternSchema] = Field(default_factory=list)
    metrics: Optional[RSSFeedMetrics] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    model_config = {"from_attributes": True}
    
    @classmethod
    def from_sql_model(cls, feed) -> "RSSFeedSchema":
        """Create from SQL model RSSFeed"""
        return cls(
            id=feed.id,
            name=feed.name,
            url=feed.url,
            parsing_patterns=RSSFeedParsingPatterns(**feed.parsing_patterns) if feed.parsing_patterns else None,
            filters=RSSFeedFilters(**feed.filters) if feed.filters else None,
            active=feed.active,
            last_scraped=feed.last_scraped,
            source=feed.source,
            torrent_type=feed.torrent_type,
            auto_detect_catalog=feed.auto_detect_catalog,
            catalog_patterns=[
                RSSFeedCatalogPatternSchema(
                    id=p.id,
                    name=p.name,
                    regex=p.regex,
                    enabled=p.enabled,
                    case_sensitive=p.case_sensitive,
                    target_catalogs=p.target_catalogs,
                )
                for p in (feed.catalog_patterns or [])
            ],
            metrics=RSSFeedMetrics(**feed.metrics) if feed.metrics else None,
            created_at=feed.created_at,
            updated_at=feed.updated_at,
        )
