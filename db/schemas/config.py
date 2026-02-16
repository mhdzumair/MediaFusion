"""Configuration schemas for streaming providers and user settings."""

import math
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from db.config import settings
from db.enums import IntegrationType, NudityStatus, SyncDirection
from utils import const


class QBittorrentConfig(BaseModel):
    """qBittorrent WebDAV configuration."""

    qbittorrent_url: str = Field(alias="qur")
    qbittorrent_username: str = Field(alias="qus")
    qbittorrent_password: str = Field(alias="qpw")
    seeding_time_limit: int = Field(default=1440, alias="stl")  # 24 hours
    seeding_ratio_limit: float = Field(default=1.0, alias="srl")
    play_video_after: int = Field(default=100, le=100, ge=0, alias="pva")
    category: str = Field(default="MediaFusion", alias="cat")
    webdav_url: str = Field(alias="wur")
    webdav_username: str = Field(alias="wus")
    webdav_password: str = Field(alias="wpw")
    webdav_downloads_path: str = Field(default="/", alias="wdp")

    class Config:
        extra = "ignore"
        populate_by_name = True


class MediaFlowConfig(BaseModel):
    """MediaFlow proxy configuration.

    MediaFlow is used to proxy streams for different purposes:
    - Live streams: Proxies IPTV/live TV streams
    - Web playback: Required for playing debrid streams in the browser (CORS restriction)

    For Stremio/Kodi debrid proxy, use the per-provider `use_mediaflow` setting instead.
    """

    proxy_url: str | None = Field(default=None, alias="pu")
    api_password: str | None = Field(default=None, alias="ap")
    public_ip: str | None = Field(default=None, alias="pip")
    proxy_live_streams: bool = Field(default=False, alias="pls")
    # Enable web browser playback - required for playing debrid streams in MediaFusion web UI
    enable_web_playback: bool = Field(default=False, alias="ewp")

    class Config:
        extra = "ignore"
        populate_by_name = True


class RPDBConfig(BaseModel):
    """Rating Poster DB configuration."""

    api_key: str = Field(alias="ak")

    class Config:
        extra = "ignore"
        populate_by_name = True


# ============================================
# Usenet Provider Configurations
# ============================================


class SABnzbdConfig(BaseModel):
    """SABnzbd downloader configuration."""

    url: str = Field(alias="u")
    api_key: str = Field(alias="ak")
    category: str = Field(default="MediaFusion", alias="cat")
    # WebDAV or file path for serving completed downloads
    webdav_url: str | None = Field(default=None, alias="wur")
    webdav_username: str | None = Field(default=None, alias="wus")
    webdav_password: str | None = Field(default=None, alias="wpw")
    webdav_downloads_path: str = Field(default="/", alias="wdp")

    class Config:
        extra = "ignore"
        populate_by_name = True


class NZBGetConfig(BaseModel):
    """NZBGet downloader configuration (JSON-RPC API)."""

    url: str = Field(alias="u")
    username: str = Field(alias="un")
    password: str = Field(alias="pw")
    category: str = Field(default="MediaFusion", alias="cat")
    # WebDAV or file path for serving completed downloads
    webdav_url: str | None = Field(default=None, alias="wur")
    webdav_username: str | None = Field(default=None, alias="wus")
    webdav_password: str | None = Field(default=None, alias="wpw")
    webdav_downloads_path: str = Field(default="/", alias="wdp")

    class Config:
        extra = "ignore"
        populate_by_name = True


class NzbDAVConfig(BaseModel):
    """NzbDAV configuration - SABnzbd-compatible API with built-in WebDAV."""

    url: str = Field(alias="u")
    api_key: str = Field(alias="ak")
    category: str = Field(default="MediaFusion", alias="cat")

    class Config:
        extra = "ignore"
        populate_by_name = True


class EasynewsConfig(BaseModel):
    """Easynews streaming service configuration."""

    username: str = Field(alias="un")
    password: str = Field(alias="pw")

    class Config:
        extra = "ignore"
        populate_by_name = True


class StreamingProvider(BaseModel):
    """Streaming/debrid provider configuration."""

    # Unique name for multi-provider identification
    name: str = Field(default="default", alias="n")
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
        "p2p",
        # Usenet-only providers
        "sabnzbd",
        "nzbget",
        "nzbdav",
        "easynews",
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
    only_show_cached_streams: bool = Field(default=False, alias="oscs")
    priority: int = Field(default=0, alias="pr")  # Lower = higher priority
    enabled: bool = Field(default=True, alias="en")
    use_mediaflow: bool = Field(default=True, alias="umf")  # Per-provider MediaFlow toggle
    # Usenet-specific provider configs
    sabnzbd_config: SABnzbdConfig | None = Field(default=None, alias="sbc")
    nzbget_config: NZBGetConfig | None = Field(default=None, alias="ngc")
    nzbdav_config: NzbDAVConfig | None = Field(default=None, alias="ndc")
    easynews_config: EasynewsConfig | None = Field(default=None, alias="enc")

    @model_validator(mode="after")
    def validate_token_or_username_password(self) -> "StreamingProvider":
        if self.service in settings.disabled_providers:
            raise ValueError(f"The streaming provider '{self.service}' has been disabled by the administrator")
        required_fields = const.STREAMING_SERVICE_REQUIREMENTS.get(
            self.service, const.STREAMING_SERVICE_REQUIREMENTS["default"]
        )
        for field in required_fields:
            if getattr(self, field, None) is None:
                raise ValueError(f"{field} is required")
        return self

    class Config:
        extra = "ignore"
        populate_by_name = True


class SortingOption(BaseModel):
    """Sorting option for torrent results."""

    key: str = Field(alias="k")
    direction: Literal["asc", "desc"] = Field(default="desc", alias="d")

    class Config:
        extra = "ignore"
        populate_by_name = True


class MDBListItem(BaseModel):
    """MDBList catalog item configuration."""

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
    """MDBList integration configuration."""

    api_key: str = Field(alias="ak")
    lists: list[MDBListItem] = Field(default_factory=list, alias="l")

    class Config:
        extra = "ignore"
        populate_by_name = True


class CatalogConfig(BaseModel):
    """Per-catalog configuration including selection and sorting.

    Combines catalog selection (enabled/disabled) with sorting preferences.
    This replaces the legacy `selected_catalogs` list with a more flexible
    per-catalog configuration, similar to how MDBList catalogs support sorting.

    The order of catalogs in the list determines their display order in Stremio.
    """

    catalog_id: str = Field(alias="ci")
    enabled: bool = Field(default=True, alias="en")
    sort: Literal["latest", "popular", "rating", "year", "title", "release_date"] | None = Field(
        default=None, alias="s"
    )
    order: Literal["asc", "desc"] = Field(default="desc", alias="o")

    class Config:
        extra = "ignore"
        populate_by_name = True


class StreamTemplate(BaseModel):
    """Stream display template configuration.

    MediaFusion Template Syntax:
    - {variable.path} - Direct field access
    - {variable|modifier} - Apply modifier
    - {variable|mod1|mod2} - Chained modifiers
    - {if condition}...{else}...{/if} - Conditionals
    - {if condition}...{elif condition}...{else}...{/if} - Multiple conditions

    Modifiers:
    - |bytes - Format bytes as human-readable (e.g., "4.5 GB")
    - |time - Format duration as HH:MM:SS
    - |join(', ') - Join array with separator
    - |upper, |lower, |title - Case transformation
    - |first, |last - First/last array element
    - |truncate(50) - Truncate to N characters
    - |escape - HTML escape (for web output)

    Conditions:
    - {if stream.cached}...{/if} - Truthy check
    - {if stream.type = torrent}...{/if} - Equality
    - {if stream.size > 0}...{/if} - Comparison (>, <, >=, <=, !=)
    - {if stream.name ~ 720}...{/if} - Contains
    - {if cond1 and cond2}...{/if} - Logical AND
    - {if cond1 or cond2}...{/if} - Logical OR
    - {if not stream.cached}...{/if} - Negation

    Example:
        {addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{else}🔗{/if} {stream.resolution}

    Available fields:
    - Stream: name, type, resolution, quality, codec, bit_depth
    - Stream type indicators: 🧲 torrent, 📰 usenet, 🔗 http, 📺 tv
    - Stream arrays: audio_formats, channels, hdr_formats, languages
    - Stream info: size, seeders, source, release_group, uploader, cached
    - Service: service.name, service.shortName, service.cached
    - Addon: addon.name
    """

    title: str = Field(
        default="{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}",
        alias="t",
        description="Title template for stream display",
    )
    description: str = Field(
        default="{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}\n"
        "{if stream.size > 0}📦 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}\n"
        "{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}\n"
        "🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}",
        alias="d",
        description="Description template for stream display",
    )

    class Config:
        extra = "ignore"
        populate_by_name = True


class IndexerInstanceConfig(BaseModel):
    """Configuration for Prowlarr/Jackett instance."""

    enabled: bool = Field(default=False, alias="en")
    url: str | None = Field(default=None, alias="u")
    api_key: str | None = Field(default=None, alias="ak")
    use_global: bool = Field(default=True, alias="ug")  # Use global instance if True

    class Config:
        extra = "ignore"
        populate_by_name = True


class TorznabEndpointConfig(BaseModel):
    """Configuration for a custom Torznab endpoint."""

    id: str = Field(alias="i")
    name: str = Field(alias="n")
    url: str = Field(alias="u")
    headers: dict[str, str] | None = Field(default=None, alias="h")
    enabled: bool = Field(default=True, alias="en")
    categories: list[int] = Field(default_factory=list, alias="c")
    priority: int = Field(default=1, alias="p")

    class Config:
        extra = "ignore"
        populate_by_name = True


# ============================================
# Newznab Indexer Configuration
# ============================================


class NewznabIndexerConfig(BaseModel):
    """Configuration for a Newznab-compatible NZB indexer."""

    id: str = Field(alias="i")
    name: str = Field(alias="n")
    url: str = Field(alias="u")
    api_key: str = Field(alias="ak")
    enabled: bool = Field(default=True, alias="en")
    priority: int = Field(default=1, alias="p")
    # Optional category overrides (use defaults if empty)
    movie_categories: list[int] = Field(default_factory=list, alias="mc")
    tv_categories: list[int] = Field(default_factory=list, alias="tc")

    class Config:
        extra = "ignore"
        populate_by_name = True


class IndexerConfig(BaseModel):
    """User's indexer configuration for Prowlarr/Jackett/Torznab/Newznab."""

    prowlarr: IndexerInstanceConfig | None = Field(default=None, alias="pr")
    jackett: IndexerInstanceConfig | None = Field(default=None, alias="jk")
    torznab_endpoints: list[TorznabEndpointConfig] = Field(default_factory=list, alias="tz")
    # Newznab indexers for Usenet NZB scraping
    newznab_indexers: list[NewznabIndexerConfig] = Field(default_factory=list, alias="nz")

    class Config:
        extra = "ignore"
        populate_by_name = True


# ============================================
# Telegram Channel Configuration
# ============================================


class TelegramChannelConfig(BaseModel):
    """Configuration for a Telegram channel to scrape."""

    id: str = Field(alias="i")  # Unique identifier (channel username or chat_id)
    name: str = Field(alias="n")  # Display name for the channel
    username: str | None = Field(default=None, alias="u")  # @username (without @)
    chat_id: str | None = Field(default=None, alias="cid")  # Numeric chat ID
    enabled: bool = Field(default=True, alias="en")
    priority: int = Field(default=1, alias="p")  # Lower = higher priority

    class Config:
        extra = "ignore"
        populate_by_name = True


class TelegramConfig(BaseModel):
    """User's Telegram scraping configuration."""

    enabled: bool = Field(default=False, alias="en")
    channels: list[TelegramChannelConfig] = Field(default_factory=list, alias="ch")
    # Option to also use admin-configured global channels
    use_global_channels: bool = Field(default=True, alias="ugc")

    class Config:
        extra = "ignore"
        populate_by_name = True


# ============================================
# External Platform Integration Configs
# ============================================


class TraktConfig(BaseModel):
    """Trakt integration configuration."""

    access_token: str = Field(alias="at")
    refresh_token: str | None = Field(default=None, alias="rt")
    expires_at: int | None = Field(default=None, alias="ea")  # Unix timestamp
    # For custom apps - both ID and secret needed for token exchange
    client_id: str | None = Field(default=None, alias="ci")
    client_secret: str | None = Field(default=None, alias="cs")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.BIDIRECTIONAL, alias="sd")
    scrobble_enabled: bool = Field(default=True, alias="sc")  # Real-time sync while watching
    min_watch_percent: int = Field(default=80, alias="mwp")  # % watched before marking complete

    class Config:
        extra = "ignore"
        populate_by_name = True


class SimklConfig(BaseModel):
    """Simkl integration configuration."""

    access_token: str = Field(alias="at")
    refresh_token: str | None = Field(default=None, alias="rt")
    expires_at: int | None = Field(default=None, alias="ea")
    # For custom apps - both ID and secret needed for token exchange
    client_id: str | None = Field(default=None, alias="ci")
    client_secret: str | None = Field(default=None, alias="cs")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.BIDIRECTIONAL, alias="sd")

    class Config:
        extra = "ignore"
        populate_by_name = True


class MALConfig(BaseModel):
    """MyAnimeList integration configuration."""

    access_token: str = Field(alias="at")
    refresh_token: str | None = Field(default=None, alias="rt")
    expires_at: int | None = Field(default=None, alias="ea")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.BIDIRECTIONAL, alias="sd")
    sync_anime_only: bool = Field(default=True, alias="sao")  # Only sync anime content

    class Config:
        extra = "ignore"
        populate_by_name = True


class AniListConfig(BaseModel):
    """AniList integration configuration."""

    access_token: str = Field(alias="at")
    expires_at: int | None = Field(default=None, alias="ea")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.BIDIRECTIONAL, alias="sd")
    sync_anime_only: bool = Field(default=True, alias="sao")

    class Config:
        extra = "ignore"
        populate_by_name = True


class LetterboxdConfig(BaseModel):
    """Letterboxd integration configuration (RSS-based, no OAuth)."""

    username: str = Field(alias="un")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.IMPORT, alias="sd")  # Letterboxd is import-only via RSS

    class Config:
        extra = "ignore"
        populate_by_name = True


class TVTimeConfig(BaseModel):
    """TV Time integration configuration."""

    access_token: str = Field(alias="at")
    refresh_token: str | None = Field(default=None, alias="rt")
    expires_at: int | None = Field(default=None, alias="ea")
    sync_enabled: bool = Field(default=True, alias="se")
    sync_direction: SyncDirection = Field(default=SyncDirection.BIDIRECTIONAL, alias="sd")

    class Config:
        extra = "ignore"
        populate_by_name = True


class IntegrationConfigs(BaseModel):
    """Container for all external platform integration configs."""

    trakt: TraktConfig | None = Field(default=None, alias="trk")
    simkl: SimklConfig | None = Field(default=None, alias="smk")
    mal: MALConfig | None = Field(default=None, alias="mal")
    anilist: AniListConfig | None = Field(default=None, alias="ani")
    letterboxd: LetterboxdConfig | None = Field(default=None, alias="lbx")
    tvtime: TVTimeConfig | None = Field(default=None, alias="tvt")

    class Config:
        extra = "ignore"
        populate_by_name = True

    def get_enabled_platforms(self) -> list[IntegrationType]:
        """Get list of enabled integration platforms."""
        enabled = []
        if self.trakt and self.trakt.sync_enabled:
            enabled.append(IntegrationType.TRAKT)
        if self.simkl and self.simkl.sync_enabled:
            enabled.append(IntegrationType.SIMKL)
        if self.mal and self.mal.sync_enabled:
            enabled.append(IntegrationType.MAL)
        if self.anilist and self.anilist.sync_enabled:
            enabled.append(IntegrationType.ANILIST)
        if self.letterboxd and self.letterboxd.sync_enabled:
            enabled.append(IntegrationType.LETTERBOXD)
        if self.tvtime and self.tvtime.sync_enabled:
            enabled.append(IntegrationType.TVTIME)
        return enabled


class UserData(BaseModel):
    """User configuration data stored in profile."""

    # User identification (for authenticated users - optional for anonymous)
    user_id: int | None = Field(default=None, alias="uid")
    profile_id: int | None = Field(default=None, alias="pid")
    # UUID-based identification for security (prevents enumeration attacks)
    user_uuid: str | None = Field(default=None, alias="uuuid")
    profile_uuid: str | None = Field(default=None, alias="puuid")

    # Multi-debrid support: list of streaming providers
    streaming_providers: list[StreamingProvider] = Field(default_factory=list, alias="sps")
    # Legacy single provider field for backward compatibility
    streaming_provider: StreamingProvider | None = Field(default=None, alias="sp")
    # Per-catalog configuration (selection + sorting)
    catalog_configs: list[CatalogConfig] = Field(default_factory=list, alias="cc")
    # Legacy: selected_catalogs (deprecated, use catalog_configs instead)
    selected_catalogs: list[str] = Field(alias="sc", default_factory=list)
    selected_resolutions: list[str | None] = Field(default=const.RESOLUTIONS, alias="sr")
    enable_catalogs: bool = Field(default=True, alias="ec")
    enable_imdb_metadata: bool = Field(default=True, alias="eim")
    max_size: int | str | float = Field(default=math.inf, alias="ms")
    min_size: int = Field(default=0, alias="mns")
    max_streams_per_resolution: int = Field(default=10, alias="mspr")
    torrent_sorting_priority: list[SortingOption] = Field(
        default_factory=lambda: [SortingOption(key=k) for k in const.TORRENT_SORTING_PRIORITY],
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
    language_sorting: list[str | None] = Field(default=const.LANGUAGES_FILTERS, alias="ls")
    quality_filter: list[str] = Field(default=list[str](const.QUALITY_GROUPS.keys()), alias="qf")
    mediaflow_config: MediaFlowConfig | None = Field(default=None, alias="mfc")
    rpdb_config: RPDBConfig | None = Field(default=None, alias="rpc")
    live_search_streams: bool = Field(default=False, alias="lss")
    mdblist_config: MDBListConfig | None = Field(default=None, alias="mdb")
    stream_template: StreamTemplate | None = Field(default=None, alias="st")
    # Indexer configuration for user-level scraping (Prowlarr/Jackett/Torznab/Newznab)
    indexer_config: IndexerConfig | None = Field(default=None, alias="ic")
    # Telegram channel configuration for user-level Telegram scraping
    telegram_config: TelegramConfig | None = Field(default=None, alias="tgc")

    # Usenet settings
    enable_usenet_streams: bool = Field(default=True, alias="eus")
    prefer_usenet_over_torrent: bool = Field(default=False, alias="puot")
    # Telegram settings (opt-in - requires MediaFlow Proxy with Telegram session)
    enable_telegram_streams: bool = Field(default=False, alias="ets")
    # AceStream settings (opt-in - requires MediaFlow Proxy with AceEngine)
    enable_acestream_streams: bool = Field(default=False, alias="eas")

    # Stream display settings
    max_streams: int = Field(default=25, alias="mxs")
    stream_type_grouping: Literal["mixed", "separate"] = Field(default="separate", alias="stg")
    stream_type_order: list[str] = Field(
        default_factory=lambda: ["torrent", "usenet", "telegram", "http", "acestream"],
        alias="sto",
    )
    provider_grouping: Literal["mixed", "separate"] = Field(default="separate", alias="pg")
    # Stream name filter (include or exclude mode with keyword/regex patterns)
    stream_name_filter_mode: Literal["disabled", "include", "exclude"] = Field(default="disabled", alias="snfm")
    stream_name_filter_patterns: list[str] = Field(default_factory=list, alias="snfp")
    stream_name_filter_use_regex: bool = Field(default=False, alias="snfr")

    @field_validator("selected_resolutions", mode="after")
    def validate_selected_resolutions(cls, v):
        for resolution in v:
            if resolution not in const.RESOLUTIONS:
                raise ValueError("Invalid resolution")
        return v

    @field_validator("max_size", mode="before")
    def parse_max_size(cls, v):
        if isinstance(v, float):
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            if v == "inf":
                return math.inf
            if v.isdigit():
                return int(v)
        raise ValueError("Invalid max_size")

    @field_validator("min_size", mode="before")
    def parse_min_size(cls, v):
        if isinstance(v, (int, float)):
            return max(0, int(v))
        if isinstance(v, str) and v.isdigit():
            return max(0, int(v))
        return 0

    @field_validator("torrent_sorting_priority", mode="before")
    def validate_torrent_sorting_priority(cls, v):
        for priority in v:
            if isinstance(priority, dict):
                key = priority.get("k", priority.get("key"))
                if key not in const.TORRENT_SORTING_PRIORITY_OPTIONS:
                    raise ValueError(f"Invalid priority {key}")
            elif isinstance(priority, str):
                if priority not in const.TORRENT_SORTING_PRIORITY_OPTIONS:
                    raise ValueError(f"Invalid priority {priority}")
        if isinstance(v, list) and v and isinstance(v[0], str):
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

    @field_validator("max_streams", mode="after")
    def validate_max_streams(cls, v):
        return max(1, min(v, 100))

    @field_validator("stream_type_order", mode="after")
    def validate_stream_type_order(cls, v):
        valid_types = {"torrent", "usenet", "telegram", "http", "acestream"}
        for stream_type in v:
            if stream_type not in valid_types:
                raise ValueError(f"Invalid stream type: {stream_type}")
        return v

    def is_sorting_option_present(self, key: str) -> bool:
        return any(sort.key == key for sort in self.torrent_sorting_priority)

    def get_sorting_direction(self, key: str) -> str:
        for sort in self.torrent_sorting_priority:
            if sort.key == key:
                return sort.direction
        return "desc"

    def get_active_providers(self) -> list[StreamingProvider]:
        """Get all active streaming providers, sorted by priority."""
        providers = []
        if self.streaming_providers:
            providers = [p for p in self.streaming_providers if p.enabled]
        elif self.streaming_provider:
            providers = [self.streaming_provider]
        return sorted(providers, key=lambda p: p.priority)

    def get_primary_provider(self) -> StreamingProvider | None:
        """Get the highest priority active provider."""
        providers = self.get_active_providers()
        return providers[0] if providers else None

    def get_provider_by_name(self, name: str) -> StreamingProvider | None:
        """Get a streaming provider by its unique name."""
        service_shorthands = {
            "rd": "realdebrid",
            "ad": "alldebrid",
            "pm": "premiumize",
            "dl": "debridlink",
            "tb": "torbox",
            "pk": "pikpak",
            "oc": "offcloud",
            "st": "stremthru",
            "ed": "easydebrid",
            "qb": "qbittorrent",
        }
        for provider in self.streaming_providers:
            if provider.name == name and provider.enabled:
                return provider
        if name in service_shorthands:
            target_service = service_shorthands[name]
            for provider in self.streaming_providers:
                if provider.service == target_service and provider.enabled:
                    return provider
        if self.streaming_provider and name == "default":
            return self.streaming_provider
        if self.streaming_provider and name in service_shorthands:
            if self.streaming_provider.service == service_shorthands[name]:
                return self.streaming_provider
        return None

    def has_any_provider(self) -> bool:
        """Check if any streaming provider is configured."""
        return bool(self.streaming_providers) or bool(self.streaming_provider)

    def model_post_init(self, __context) -> None:
        """Post-initialization hook to perform automatic migrations."""
        self.migrate_to_multi_provider()
        self.migrate_to_catalog_configs()

    def migrate_to_multi_provider(self) -> None:
        """Migrate from legacy single provider to multi-provider format."""
        if self.streaming_provider and not self.streaming_providers:
            self.streaming_providers = [self.streaming_provider]

    def migrate_to_catalog_configs(self) -> None:
        """Migrate from legacy selected_catalogs to catalog_configs format.

        This converts the simple list of catalog IDs to the new format
        with per-catalog configuration (enabled status and sorting).
        After migration, selected_catalogs is cleared.
        """
        if self.selected_catalogs and not self.catalog_configs:
            self.catalog_configs = [
                CatalogConfig(catalog_id=catalog_id, enabled=True) for catalog_id in self.selected_catalogs
            ]
            # Clear legacy field after migration
            self.selected_catalogs = []

    def get_catalog_config(self, catalog_id: str) -> CatalogConfig | None:
        """Get the configuration for a specific catalog.

        Args:
            catalog_id: The catalog identifier (e.g., "tmdb_popular_movies")

        Returns:
            CatalogConfig if found, None otherwise
        """
        for config in self.catalog_configs:
            if config.catalog_id == catalog_id:
                return config
        return None

    def get_enabled_catalog_ids(self) -> list[str]:
        """Get list of enabled catalog IDs in order.

        Returns:
            List of enabled catalog IDs in display order
        """
        return [c.catalog_id for c in self.catalog_configs if c.enabled]

    def is_catalog_enabled(self, catalog_id: str) -> bool:
        """Check if a catalog is enabled.

        Args:
            catalog_id: The catalog identifier

        Returns:
            True if catalog is enabled, False otherwise
        """
        config = self.get_catalog_config(catalog_id)
        return config.enabled if config else False

    class Config:
        extra = "ignore"
        populate_by_name = True


class AuthorizeData(BaseModel):
    """Device code authorization data."""

    device_code: str
