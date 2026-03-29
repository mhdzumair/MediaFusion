import json
from copy import deepcopy
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


DEFAULT_PROVIDER_SIGNUP_LINKS: dict[str, list[str]] = {
    "realdebrid": [
        "https://real-debrid.com/?id=9490816",
        "https://real-debrid.com/?id=3351376",
    ],
    "alldebrid": ["https://alldebrid.com/?uid=3ndha&lang=en"],
    "premiumize": ["https://www.premiumize.me"],
    "debridlink": ["https://debrid-link.com/id/kHgZs"],
    "torbox": [
        "https://torbox.app/subscription?referral=38f1c266-8a6c-40b2-a6d2-2148e77dafc9",
        "https://torbox.app/subscription?referral=339b923e-fb23-40e7-8031-4af39c212e3c",
        "https://torbox.app/subscription?referral=e2a28977-99ed-43cd-ba2c-e90dc398c49c",
    ],
    "seedr": ["https://www.seedr.cc/?r=2726511"],
    "offcloud": ["https://offcloud.com/?=9932cd9f"],
    "pikpak": ["https://mypikpak.com/drive/activity/invited?invitation-code=52875535"],
    "easydebrid": ["https://paradise-cloud.com/products/easydebrid"],
    "debrider": ["https://debrider.app/pricing"],
    "qbittorrent": [
        "https://github.com/mhdzumair/MediaFusion/tree/main/streaming_providers/qbittorrent#qbittorrent-webdav-setup-options-with-mediafusion"
    ],
    "stremthru": ["https://github.com/MunifTanjim/stremthru?tab=readme-ov-file#configuration"],
}


def _normalize_provider_signup_links(raw_value: object) -> dict[str, list[str]]:
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}

    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for provider, raw_links in raw_value.items():
        if not isinstance(provider, str):
            continue

        if isinstance(raw_links, str):
            links = [raw_links]
        elif isinstance(raw_links, list):
            links = [link for link in raw_links if isinstance(link, str) and link]
        else:
            continue

        if links:
            normalized[provider] = _dedupe_links(links)

    return normalized


def _dedupe_links(links: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append(link)
    return deduped


# Valid ids for requests_proxy_exclude_debrid_providers (same names as streaming provider service).
REQUESTS_PROXY_EXCLUDE_DEBRID_IDS: frozenset[str] = frozenset(
    {
        "realdebrid",
        "seedr",
        "debridlink",
        "alldebrid",
        "offcloud",
        "pikpak",
        "torbox",
        "premiumize",
        "stremthru",
        "easydebrid",
        "debrider",
    }
)


class Settings(BaseSettings):
    # Core Application Settings
    addon_name: str = "MediaFusion"
    version: str = "1.0.0"
    description: str = "Open-source streaming platform for Movies, Series, and Live TV. Source: https://github.com/mhdzumair/MediaFusion"
    branding_description: str = ""
    branding_svg: str | None = None  # Optional partner/host SVG logo URL
    contact_email: str
    host_url: str
    secret_key: str = Field(..., max_length=32, min_length=32)
    api_password: str
    logging_level: str = "INFO"
    logo_url: str = "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png"
    # Default frontend color scheme for users without a saved local preference.
    default_color_scheme: Literal[
        "mediafusion",
        "cinematic",
        "ocean",
        "forest",
        "emeraldnight",
        "midnight",
        "arctic",
        "slate",
        "rose",
        "purple",
        "sunset",
        "youtube",
    ] = "mediafusion"
    is_public_instance: bool = False
    poster_host_url: str | None = None
    min_scraping_video_size: int = 26214400  # 25 MB in bytes
    metadata_primary_source: Literal["imdb", "tmdb"] = "imdb"
    # When True, failed cinemagoerng IMDb title fetch falls back to v3-cinemeta.strem.io.
    imdb_cinemeta_fallback_enabled: bool = True
    startup_migrate_only: bool = False  # Skip startup DB bootstrap checks; run Alembic + Gunicorn only
    gunicorn_workers: int = Field(default=3, ge=1)  # Gunicorn worker process count
    gunicorn_timeout: int = Field(default=120, ge=1)  # Gunicorn worker timeout in seconds
    gunicorn_max_requests: int = Field(default=5000, ge=1)  # Max requests before Gunicorn worker restart
    gunicorn_max_requests_jitter: int = Field(default=2000, ge=0)  # Randomized restart spread for workers

    # Streaming Provider Toggles
    disabled_providers: list[
        Literal[
            "p2p",
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
        ]
    ] = Field(default_factory=list)
    max_streaming_providers_per_profile: int = Field(default=5, ge=1)
    provider_signup_links: dict[str, list[str]] = Field(default_factory=lambda: deepcopy(DEFAULT_PROVIDER_SIGNUP_LINKS))

    # Content Type Toggles
    # Globally disable specific content types. Affects imports, stream delivery, and UI visibility.
    # "iptv" disables both M3U and Xtream tabs, hides the IPTV Sources page, and filters IPTV streams.
    disabled_content_types: list[
        Literal["magnet", "torrent", "nzb", "iptv", "youtube", "http", "acestream", "telegram"]
    ] = Field(default_factory=list)

    # Database and Cache Settings
    postgres_uri: str  # Primary read-write PostgreSQL URI
    postgres_read_uri: str | None = None  # Optional read replica URI (if None, uses primary)
    db_max_connections: int = Field(default=50, ge=1)  # Total SQLAlchemy connection budget per app instance
    redis_url: str = "redis://redis-service:6379"
    redis_max_connections: int = 100
    redis_retry_attempts: int = 3
    redis_retry_delay: float = 0.1
    redis_connection_timeout: int = 10
    redis_enable_circuit_breaker: bool = True

    # External Service URLs
    requests_proxy_url: str | None = None
    # Do not route these debrid/streaming API clients through requests_proxy_url (direct egress).
    requests_proxy_exclude_debrid_providers: list[str] = Field(default_factory=list)
    scrapling_proxy_url: str | None = None
    scrapling_cdp_url: str | None = None
    scrapling_headless: bool = True
    scrapling_disable_resources: bool = False
    scrapling_network_idle: bool = True
    scrapling_wait_time_ms: int = 3000
    scrapling_timeout_ms: int = 60000
    scrapling_google_search_referer: bool = True
    scrapling_fetcher_mode: Literal["dynamic", "stealthy"] = "stealthy"
    scrapling_solve_cloudflare: bool = True
    scrapling_real_chrome: bool = False
    scrapling_cloudflare_cache_duration: int = 3600
    scrapling_cloudflare_max_attempts: int = 3

    # External Service API Keys
    tmdb_api_key: str | None = None
    tvdb_api_key: str | None = None
    youtube_api_key: str | None = None
    is_scrap_from_youtube_background: bool = False
    is_scrap_from_acestream_background: bool = True
    acestream_background_search_api_key: str | None = None
    is_scrap_from_telegram_background: bool = False
    telegram_background_use_indexers: bool = False
    telegram_background_indexer_api_key: str | None = None

    # Prowlarr Settings
    is_scrap_from_prowlarr: bool = True
    prowlarr_url: str = "http://prowlarr-service:9696"
    prowlarr_api_key: str | None = None
    prowlarr_live_title_search: bool = True
    prowlarr_background_title_search: bool = True
    prowlarr_search_query_timeout: int = 30
    prowlarr_search_interval_hour: int = 72
    prowlarr_immediate_max_process: int = 10
    prowlarr_immediate_max_process_time: int = 15
    prowlarr_feed_scrape_interval_hour: int = 3

    # Torrentio Settings
    is_scrap_from_torrentio: bool = False
    torrentio_search_interval_days: int = 3
    torrentio_url: str = "https://torrentio.strem.fun"

    # Mediafusion Settings
    is_scrap_from_mediafusion: bool = False
    mediafusion_search_interval_days: int = 3
    mediafusion_url: str = "https://mediafusion.elfhosted.com"
    mediafusion_api_password: str | None = None
    sync_debrid_cache_streams: bool = False
    rss_feed_scrape_interval_hour: int = 3

    # Zilean Settings
    is_scrap_from_zilean: bool = False
    zilean_search_interval_hour: int = 24
    zilean_url: str = "https://zilean.elfhosted.com"

    # DMM Hashlist Settings
    is_scrap_from_dmm_hashlist: bool = False
    dmm_hashlist_repo_owner: str = "debridmediamanager"
    dmm_hashlist_repo_name: str = "hashlists"
    dmm_hashlist_branch: str = "main"
    dmm_hashlist_sync_interval_hour: int = 6
    dmm_hashlist_commits_per_run: int = 20
    dmm_hashlist_backfill_commits_per_run: int = 20

    # Native Public Indexers (Scrapling-backed)
    is_scrap_from_public_indexers: bool = True
    public_indexers_search_interval_hour: int = 48
    # Optional global allowlist for live-search indexers. When set, this list applies
    # to movie/series/anime (comma-separated ids, e.g. "uindex,rutor,thepiratebay").
    public_indexers_live_search_sites: str = "all"
    # Type-specific live-search allowlists (used when the global allowlist is empty).
    public_indexers_movie_live_search_sites: str = "all"
    public_indexers_series_live_search_sites: str = "all"
    public_indexers_anime_live_search_sites: str = "all"
    # When False, live search skips indexers that require browser-based Cloudflare solving.
    # When True, those indexers are allowed and use Scrapling solver on demand.
    public_indexers_live_search_enable_cloudflare_solver: bool = False
    public_indexers_anime_include_series_fallback: bool = True
    public_indexers_live_search_parallelism: int = Field(default=16, ge=1, le=32)
    public_indexers_max_rows_per_page: int = Field(default=12, ge=1, le=100)
    public_indexers_source_health_metrics_ttl_seconds: int = Field(default=60 * 60 * 24, ge=60)
    public_indexers_source_health_gates_enabled: bool = True
    public_indexers_source_health_scope_mode: Literal["global", "pod", "custom"] = "pod"
    public_indexers_source_health_scope: str = ""
    public_indexers_source_health_min_samples: int = 10
    public_indexers_source_min_success_rate: float = 0.35
    public_indexers_source_max_timeout_rate: float = 0.35
    public_indexers_source_health_counter_soft_cap: int = Field(default=120, ge=20)
    public_indexers_source_health_decay_factor: float = Field(default=0.5, ge=0.1, le=0.95)
    public_indexers_source_health_recovery_success_streak: int = Field(default=2, ge=0)
    public_indexers_source_bootstrap_demote_enabled: bool = True
    public_indexers_source_bootstrap_min_samples: int = 2
    public_indexers_source_bootstrap_timeout_threshold: int = 2
    public_indexers_source_health_probation_enabled: bool = True
    public_indexers_source_health_probation_ratio: float = Field(default=0.3, ge=0.0, le=1.0)
    public_indexers_source_health_probation_max_sources_per_query: int = Field(default=2, ge=0)

    # Native public Usenet indexers (HTML search; not Newznab — e.g. Binsearch).
    is_scrap_from_public_usenet_indexers: bool = True
    public_usenet_indexers_search_interval_hour: int = 48
    public_usenet_indexers_live_search_sites: str = "all"
    public_usenet_indexers_movie_live_search_sites: str = "all"
    public_usenet_indexers_series_live_search_sites: str = "all"
    public_usenet_indexers_anime_live_search_sites: str = "all"

    # Jackett Settings
    is_scrap_from_jackett: bool = False
    jackett_url: str = "http://jackett-service:9117"
    jackett_api_key: str | None = None
    jackett_search_interval_hour: int = 72
    jackett_search_query_timeout: int = 30
    jackett_immediate_max_process: int = 10
    jackett_immediate_max_process_time: int = 15
    jackett_live_title_search: bool = True
    jackett_background_title_search: bool = True
    jackett_feed_scrape_interval_hour: int = 3

    # Torznab Scraping Settings
    # List of Torznab endpoint objects (JSON array) used for global scraping.
    # Expected keys per endpoint: name, url, optional headers, categories, priority, enabled, id.
    is_scrap_from_torznab: bool = True
    torznab_endpoints: list[dict] = Field(default_factory=list)

    background_search_interval_hours: int = 72
    background_search_crontab: str = "*/3 * * * *"

    # Premiumize Settings
    premiumize_oauth_client_id: str | None = None
    premiumize_oauth_client_secret: str | None = None

    # Telegram Settings (Notifications)
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None  # Bot @username (without @), shown in the frontend import guide
    telegram_chat_id: str | None = None
    telegram_webhook_secret_token: str | None = None  # Secret token for webhook security (optional but recommended)

    # Telegram Scraper Settings
    is_scrap_from_telegram: bool = False  # Master toggle for Telegram scraping
    # Telethon credentials for channel scraping (get from https://my.telegram.org)
    telegram_api_id: int | None = None  # API ID from my.telegram.org
    telegram_api_hash: str | None = None  # API Hash from my.telegram.org
    telegram_session_string: str | None = None  # Telethon session string (StringSession)
    telegram_scraping_channels: list[str] = []  # Admin-configured channel list (@username or chat_id)
    telegram_scrape_interval_hour: int = 6  # How often to scrape channels
    telegram_scrape_message_limit: int = 100  # Max messages to fetch per channel per scrape
    telegram_file_url_ttl: int = 1800  # 30 minutes - Telegram file URLs expire

    # Telegram Backup Channel (for content redundancy)
    # Private channel where contributed content is forwarded for backup
    # If the bot gets suspended, content can be recovered via new bot using file_unique_id
    telegram_backup_channel_id: str | None = None  # e.g., "-1001234567890"

    # Configuration Sources
    use_config_source: str = "remote"
    remote_config_source: str = (
        "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/scraper_config.json"
    )
    local_config_path: str = "resources/json/scraper_config.json"

    # Feature Toggles
    enable_rate_limit: bool = False
    validate_m3u8_urls_liveness: bool = True
    store_stremthru_magnet_cache: bool = False
    scrape_with_aka_titles: bool = True
    scrape_max_aka_titles_per_query: int = Field(default=8, ge=0)
    scrape_degraded_mode_enabled: bool = True
    scrape_degraded_mode_duration_seconds: int = Field(default=180, ge=30)
    scrape_degraded_mode_open_breakers_threshold: int = Field(default=3, ge=1)
    scrape_degraded_mode_min_attempts: int = Field(default=12, ge=1)
    scrape_degraded_mode_error_ratio_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    taskiq_single_worker_mode: bool = True
    enable_fetching_torrent_metadata_from_p2p: bool = True
    # Anime metadata providers used by search fallback chain.
    # Ordered preference: first provider is queried first, next providers are used as fallback.
    anime_metadata_source_order: list[Literal["kitsu", "anilist"]] = Field(default_factory=lambda: ["kitsu", "anilist"])

    # Poster Fetch Failure Tracking
    poster_failure_ttl: int = 3600  # TTL in seconds for a single failure record (1 hour)
    poster_failure_threshold: int = 3  # Number of failures before marking a poster URL as dead
    poster_dead_ttl: int = 86400  # TTL in seconds for a dead poster URL marker (24 hours)
    # Rendered Stremio poster JPEG bytes in Redis (api/routers/stremio/poster.py)
    poster_jpeg_cache_ttl_seconds: int = Field(default=259200, ge=60)  # default 3 days
    # Downscaled JPEG source bytes in Redis under poster_src:{sha256(url)} (utils/poster.fetch_poster_image)
    poster_source_image_cache_ttl_seconds: int = Field(default=3600, ge=60)
    poster_source_cache_max_edge: int = Field(default=480, ge=32)
    poster_source_cache_jpeg_quality: int = Field(default=82, ge=60, le=95)
    # Max members per scraper cooldown zset after time trim; 0 = unlimited
    scraper_cooldown_zset_max_members: int = Field(default=0, ge=0)

    # Raw stream list Redis cache (keys stream_data:* in db/crud/stream_services.py)
    stream_raw_redis_cache_enabled: bool = True
    stream_raw_redis_cache_ttl_seconds: int = Field(default=900, ge=60)
    stream_raw_redis_cache_zlib_compress: bool = True
    # If > 0, skip caching when the stored blob (after compression) would exceed this size
    stream_raw_redis_cache_max_stored_bytes: int = Field(default=0, ge=0)

    # Exception Tracking
    enable_exception_tracking: bool = False
    exception_tracking_ttl: int = 259200  # 3 days in seconds
    exception_tracking_max_entries: int = 500

    # Request Metrics Tracking
    enable_request_metrics: bool = False
    request_metrics_ttl: int = 86400  # 1 day for aggregated stats
    request_metrics_recent_ttl: int = 3600  # 1 hour for individual request logs
    request_metrics_max_recent: int = 1000  # max individual requests to keep
    request_metrics_latency_window: int = 1000  # samples per endpoint for percentiles

    # IPTV Import Settings
    enable_iptv_import: bool = True  # Master toggle for M3U/Xtream import feature
    allow_public_iptv_sharing: bool = True  # If False, all imported streams are private to user profile only

    # NZB File Import Settings
    enable_nzb_file_import: bool = False  # Opt-in for NZB file uploads (NZB URL import is always available)
    nzb_file_storage_backend: Literal["local", "s3"] = "local"  # Where to store uploaded NZB files

    # S3/R2 Storage Settings (shared, usable by NZB file storage + future features)
    s3_endpoint_url: str | None = None  # e.g., https://xxx.r2.cloudflarestorage.com
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str | None = None
    s3_region: str = "auto"

    # Upload Size Limits
    max_torrent_file_size: int = 5_242_880  # 5 MB (torrent files are typically <1MB)
    max_nzb_file_size: int = 104_857_600  # 100 MB
    max_image_upload_size: int = 5_242_880  # 5 MB for poster/background/logo uploads

    # Zyclops NZB Health API Integration (optional)
    # When set, every NZB the system processes is forwarded to Zyclops for ingestion.
    # Failures are silent — never blocks or affects user-facing operations.
    zyclops_health_api_url: str | None = None  # e.g., "https://zyclops.example.com"
    # NZB Download URL Expiry (seconds) — applies to signed NZB download links
    # exposed to stremio_nntp clients. Other providers consume NZBs server-side.
    nzb_download_url_expiry: int = 3600  # 1 hour

    # Torznab API Settings
    enable_torznab_api: bool = True  # Master toggle for Torznab API endpoint

    # External Platform Integration Settings (Trakt, Simkl, etc.)
    # Get Trakt credentials from: https://trakt.tv/oauth/applications
    trakt_client_id: str | None = None
    trakt_client_secret: str | None = None
    # Get Simkl credentials from: https://simkl.com/settings/developer/
    # and register callback URL: <HOST_URL>/api/v1/integrations/simkl/callback.
    simkl_client_id: str | None = None
    # Client secret for Simkl app that uses callback URL <HOST_URL>/api/v1/integrations/simkl/callback.
    simkl_client_secret: str | None = None

    # Email / SMTP Settings (required for email verification and password reset)
    # When smtp_host is None, email verification is skipped and users are auto-verified.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None  # Defaults to contact_email if not set
    smtp_from_name: str | None = None  # Defaults to addon_name if not set
    smtp_use_tls: bool = True  # STARTTLS on port 587
    smtp_use_ssl: bool = False  # Implicit SSL on port 465

    # ConvertKit Newsletter Integration (optional)
    # When convertkit_api_key and convertkit_form_id are both set,
    # a newsletter opt-in checkbox is shown during registration.
    convertkit_api_key: str | None = None
    convertkit_form_id: str | None = None
    convertkit_newsletter_label: str = "Subscribe to our newsletter"
    convertkit_newsletter_default_checked: bool = False

    # Operator-Configured NzbDAV Defaults
    # When both are set, all users automatically get NzbDAV as a streaming provider
    # without needing to configure it manually. Useful for hosted instances with
    # NzbDAV running as a sidecar service.
    default_nzbdav_url: str | None = None
    default_nzbdav_api_key: str | None = None

    # Content Filtering
    adult_content_regex_keywords: str = (
        r"(^|\b|\s|$|[\[._-])"
        r"(18\s*\+|adults?|porn|sex|xxx|nude|boobs?|pussy|ass|bigass|bigtits?|blowjob|hardfuck|onlyfans?|naked|hot|milf|slut|doggy|anal|threesome|foursome|erotic|sexy|18\s*plus|trailer|RiffTrax|zipx)"
        r"(\b|\s|$|[\]._-])"
    )
    adult_content_filter_in_torrent_title: bool = True
    max_upload_contributions_per_hour: int = 10000
    upload_warning_email_cooldown_minutes: int = 180

    # Time-related Settings
    meta_cache_ttl: int = 1800  # 30 minutes in seconds
    enable_worker_memory_metrics: bool = True
    worker_memory_metrics_history_size: int = 1000
    enable_worker_max_tasks_per_child: bool = False
    worker_max_tasks_per_child: int = 20

    # Global Scheduler Settings
    disable_all_scheduler: bool = False

    # Individual Scheduler Settings
    tamilmv_scheduler_crontab: str = "0 */3 * * *"
    disable_tamilmv_scheduler: bool = False
    tamil_blasters_scheduler_crontab: str = "0 */6 * * *"
    disable_tamil_blasters_scheduler: bool = False
    formula_ext_scheduler_crontab: str = "*/30 * * * *"
    disable_formula_ext_scheduler: bool = False
    motogp_ext_scheduler_crontab: str = "0 5 * * *"
    disable_motogp_ext_scheduler: bool = False
    wwe_ext_scheduler_crontab: str = "10 */3 * * *"
    disable_wwe_ext_scheduler: bool = False
    ufc_ext_scheduler_crontab: str = "30 */3 * * *"
    disable_ufc_ext_scheduler: bool = False
    movies_tv_ext_scheduler_crontab: str = "0 * * * *"
    disable_movies_tv_ext_scheduler: bool = False
    nowmetv_scheduler_crontab: str = "0 0 * * 5"
    disable_nowmetv_scheduler: bool = True
    nowsports_scheduler_crontab: str = "0 10 * * 5"
    disable_nowsports_scheduler: bool = True
    tamilultra_scheduler_crontab: str = "0 8 * * 5"
    disable_tamilultra_scheduler: bool = True
    validate_tv_streams_in_db_crontab: str = "0 0 * * 4"
    disable_validate_tv_streams_in_db: bool = False
    sport_video_scheduler_crontab: str = "*/20 * * * *"
    disable_sport_video_scheduler: bool = False
    dlhd_scheduler_crontab: str = "0 0 * * 1"
    disable_dlhd_scheduler: bool = True
    update_seeders_crontab: str = "0 0 * * 3"
    disable_update_seeders: bool = True
    arab_torrents_scheduler_crontab: str = "0 0 * * *"
    disable_arab_torrents_scheduler: bool = True
    x1337_scheduler_crontab: str = "0 */6 * * *"
    disable_x1337_scheduler: bool = True
    thepiratebay_scheduler_crontab: str = "30 */6 * * *"
    disable_thepiratebay_scheduler: bool = True
    rutor_scheduler_crontab: str = "45 */6 * * *"
    disable_rutor_scheduler: bool = True
    limetorrents_scheduler_crontab: str = "0 */8 * * *"
    disable_limetorrents_scheduler: bool = True
    yts_scheduler_crontab: str = "0 */12 * * *"
    disable_yts_scheduler: bool = True
    bt4g_scheduler_crontab: str = "15 */8 * * *"
    disable_bt4g_scheduler: bool = True
    nyaa_scheduler_crontab: str = "15 */3 * * *"
    disable_nyaa_scheduler: bool = False
    animetosho_scheduler_crontab: str = "30 */4 * * *"
    disable_animetosho_scheduler: bool = False
    subsplease_scheduler_crontab: str = "45 */4 * * *"
    disable_subsplease_scheduler: bool = False
    animepahe_scheduler_crontab: str = "0 */6 * * *"
    disable_animepahe_scheduler: bool = True
    bt52_scheduler_crontab: str = "30 */6 * * *"
    disable_bt52_scheduler: bool = True
    uindex_scheduler_crontab: str = "0 */4 * * *"
    disable_uindex_scheduler: bool = True
    eztv_rss_scheduler_crontab: str = "0 */2 * * *"
    disable_eztv_rss_scheduler: bool = False
    prowlarr_feed_scraper_crontab: str = "0 */3 * * *"
    disable_prowlarr_feed_scraper: bool = False
    jackett_feed_scraper_crontab: str = "0 */3 * * *"
    disable_jackett_feed_scraper: bool = False
    rss_feed_scraper_crontab: str = "0 */3 * * *"
    disable_rss_feed_scraper: bool = False
    dmm_hashlist_scraper_crontab: str = "0 * * * *"
    disable_dmm_hashlist_scraper: bool = False
    youtube_background_scraper_crontab: str = "20 */6 * * *"
    disable_youtube_background_scraper: bool = True
    acestream_background_scraper_crontab: str = "40 */6 * * *"
    disable_acestream_background_scraper: bool = False
    telegram_background_scraper_crontab: str = "10 */6 * * *"
    disable_telegram_background_scraper: bool = True
    cleanup_expired_scraper_task_crontab: str = "0 * * * *"
    cleanup_expired_cache_task_crontab: str = "0 0 * * *"
    pending_moderation_reminder_crontab: str = "0 */6 * * *"
    disable_pending_moderation_reminder_scheduler: bool = False

    @model_validator(mode="before")
    @classmethod
    def merge_provider_signup_links(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        user_links = _normalize_provider_signup_links(data.get("provider_signup_links"))
        merged_links = deepcopy(DEFAULT_PROVIDER_SIGNUP_LINKS)
        for provider, links in user_links.items():
            merged_links.setdefault(provider, []).extend(links)
        for provider, links in merged_links.items():
            merged_links[provider] = _dedupe_links(links)

        data["provider_signup_links"] = merged_links
        return data

    @field_validator("requests_proxy_exclude_debrid_providers", mode="before")
    @classmethod
    def coerce_requests_proxy_exclude_debrid_providers(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        items: list[str] = []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                items = [p.strip() for p in value.split(",") if p.strip()]
            else:
                if not isinstance(parsed, list):
                    raise ValueError(
                        "requests_proxy_exclude_debrid_providers must be a JSON array or comma-separated ids"
                    )
                items = [str(x).strip() for x in parsed if str(x).strip()]
        elif isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
        else:
            raise ValueError("Invalid requests_proxy_exclude_debrid_providers")
        normalized: list[str] = []
        for raw in items:
            key = raw.casefold()
            if key not in REQUESTS_PROXY_EXCLUDE_DEBRID_IDS:
                raise ValueError(
                    f"Unknown provider {raw!r} for requests_proxy_exclude_debrid_providers; "
                    f"allowed: {sorted(REQUESTS_PROXY_EXCLUDE_DEBRID_IDS)}"
                )
            normalized.append(key)
        deduped: list[str] = []
        seen: set[str] = set()
        for k in normalized:
            if k in seen:
                continue
            seen.add(k)
            deduped.append(k)
        return deduped

    @model_validator(mode="after")
    def default_poster_host_url(self) -> "Settings":
        if not self.poster_host_url:
            self.poster_host_url = self.host_url
        return self

    @property
    def image_upload_enabled(self) -> bool:
        """Whether S3-backed image uploads are available."""
        return all(
            [
                self.s3_endpoint_url,
                self.s3_access_key_id,
                self.s3_secret_access_key,
                self.s3_bucket_name,
            ]
        )

    def requests_proxy_url_for_debrid_provider(self, provider_id: str) -> str | None:
        """Global requests proxy unless this streaming/debrid service id is excluded."""
        if not self.requests_proxy_url:
            return None
        if provider_id.casefold() in self.requests_proxy_exclude_debrid_providers:
            return None
        return self.requests_proxy_url

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
