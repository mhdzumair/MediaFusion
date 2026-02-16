from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Core Application Settings
    addon_name: str = "MediaFusion"
    version: str = "1.0.0"
    description: str = "The Ultimate Open-source Streaming Platform for Movies, Series, Live TV. Source: https://github.com/mhdzumair/MediaFusion"
    branding_description: str = ""
    branding_svg: str | None = None  # Optional partner/host SVG logo URL
    contact_email: str = "mhdzumair@gmail.com"
    host_url: str
    secret_key: str = Field(..., max_length=32, min_length=32)
    api_password: str
    logging_level: str = "INFO"
    logo_url: str = "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png"
    is_public_instance: bool = False
    poster_host_url: str | None = None
    min_scraping_video_size: int = 26214400  # 25 MB in bytes
    metadata_primary_source: Literal["imdb", "tmdb"] = "imdb"

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

    # Content Import Toggles
    # Disable specific content import types in the frontend UI.
    # "iptv" disables both M3U and Xtream tabs, and hides the IPTV Sources page.
    disabled_content_imports: list[
        Literal["magnet", "torrent", "nzb", "iptv", "youtube", "http", "acestream", "telegram"]
    ] = Field(default_factory=list)

    # Database and Cache Settings
    mongo_uri: str
    postgres_uri: str  # Primary read-write PostgreSQL URI
    postgres_read_uri: str | None = None  # Optional read replica URI (if None, uses primary)
    db_max_connections: int = 50
    redis_url: str = "redis://redis-service:6379"
    redis_max_connections: int = 100
    redis_retry_attempts: int = 3
    redis_retry_delay: float = 0.1
    redis_connection_timeout: int = 10
    redis_enable_circuit_breaker: bool = True

    # External Service URLs
    requests_proxy_url: str | None = None
    playwright_cdp_url: str = "ws://browserless:3000?blockAds=true&stealth=true"
    flaresolverr_url: str = "http://flaresolverr:8191/v1"

    # External Service API Keys
    tmdb_api_key: str | None = None
    tvdb_api_key: str | None = None

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

    # BT4G Settings
    is_scrap_from_bt4g: bool = True
    bt4g_url: str = "https://bt4gprx.com"
    bt4g_search_interval_hour: int = 72
    bt4g_search_timeout: int = 10
    bt4g_immediate_max_process: int = 15
    bt4g_immediate_max_process_time: int = 15

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
    is_scrap_from_yts: bool = True
    scrape_with_aka_titles: bool = True
    enable_fetching_torrent_metadata_from_p2p: bool = True

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

    # Torznab API Settings
    enable_torznab_api: bool = True  # Master toggle for Torznab API endpoint

    # External Platform Integration Settings (Trakt, Simkl, etc.)
    # Get Trakt credentials from: https://trakt.tv/oauth/applications
    trakt_client_id: str | None = None
    trakt_client_secret: str | None = None
    # Get Simkl credentials from: https://simkl.com/settings/developer/
    simkl_client_id: str | None = None
    simkl_client_secret: str | None = None

    # Content Filtering
    adult_content_regex_keywords: str = (
        r"(^|\b|\s|$|[\[._-])"
        r"(18\s*\+|adults?|porn|sex|xxx|nude|boobs?|pussy|ass|bigass|bigtits?|blowjob|hardfuck|onlyfans?|naked|hot|milf|slut|doggy|anal|threesome|foursome|erotic|sexy|18\s*plus|trailer|RiffTrax|zipx)"
        r"(\b|\s|$|[\]._-])"
    )
    adult_content_filter_in_torrent_title: bool = True

    # Time-related Settings
    meta_cache_ttl: int = 1800  # 30 minutes in seconds
    worker_max_tasks_per_child: int = 20

    # Global Scheduler Settings
    disable_all_scheduler: bool = False

    # Individual Scheduler Settings
    tamilmv_scheduler_crontab: str = "0 */3 * * *"
    disable_tamilmv_scheduler: bool = False
    tamil_blasters_scheduler_crontab: str = "0 */6 * * *"
    disable_tamil_blasters_scheduler: bool = False
    formula_tgx_scheduler_crontab: str = "*/30 * * * *"
    disable_formula_tgx_scheduler: bool = True
    nowmetv_scheduler_crontab: str = "0 0 * * 5"
    disable_nowmetv_scheduler: bool = True
    nowsports_scheduler_crontab: str = "0 10 * * 5"
    disable_nowsports_scheduler: bool = True
    tamilultra_scheduler_crontab: str = "0 8 * * 5"
    disable_tamilultra_scheduler: bool = False
    validate_tv_streams_in_db_crontab: str = "0 0 * * 4"
    disable_validate_tv_streams_in_db: bool = False
    sport_video_scheduler_crontab: str = "*/20 * * * *"
    disable_sport_video_scheduler: bool = False
    dlhd_scheduler_crontab: str = "0 0 * * 1"
    disable_dlhd_scheduler: bool = False
    motogp_tgx_scheduler_crontab: str = "0 5 * * *"
    disable_motogp_tgx_scheduler: bool = True
    update_seeders_crontab: str = "0 0 * * 3"
    disable_update_seeders: bool = True
    arab_torrents_scheduler_crontab: str = "0 0 * * *"
    disable_arab_torrents_scheduler: bool = False
    wwe_tgx_scheduler_crontab: str = "10 */3 * * *"
    disable_wwe_tgx_scheduler: bool = True
    ufc_tgx_scheduler_crontab: str = "30 */3 * * *"
    disable_ufc_tgx_scheduler: bool = True
    movies_tv_tgx_scheduler_crontab: str = "0 * * * *"
    disable_movies_tv_tgx_scheduler: bool = True
    prowlarr_feed_scraper_crontab: str = "0 */3 * * *"
    disable_prowlarr_feed_scraper: bool = False
    jackett_feed_scraper_crontab: str = "0 */3 * * *"
    disable_jackett_feed_scraper: bool = False
    rss_feed_scraper_crontab: str = "0 */3 * * *"
    disable_rss_feed_scraper: bool = False
    cleanup_expired_scraper_task_crontab: str = "0 * * * *"
    cleanup_expired_cache_task_crontab: str = "0 0 * * *"

    @model_validator(mode="after")
    def default_poster_host_url(self) -> "Settings":
        if not self.poster_host_url:
            self.poster_host_url = self.host_url
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
