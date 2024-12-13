from typing import Literal

from pydantic import model_validator, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Core Application Settings
    addon_name: str = "MediaFusion"
    version: str = "1.0.0"
    description: str = (
        "Universal Stremio Add-on for Movies, Series, Live TV & Sports Events. Source: https://github.com/mhdzumair/MediaFusion"
    )
    branding_description: str = ""
    contact_email: str = "mhdzumair@gmail.com"
    host_url: str
    secret_key: str = Field(..., max_length=32, min_length=32)
    api_password: str
    logging_level: str = "INFO"
    logo_url: str = (
        "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png"
    )
    is_public_instance: bool = False
    poster_host_url: str | None = None

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
        ]
    ] = Field(default_factory=list)

    # Database and Cache Settings
    mongo_uri: str
    db_max_connections: int = 50
    redis_url: str = "redis://redis-service:6379"
    redis_max_connections: int = 100

    # External Service URLs
    requests_proxy_url: str | None = None
    playwright_cdp_url: str = "ws://browserless:3000?blockAds=true&stealth=true"
    flaresolverr_url: str = "http://flaresolverr:8191/v1"

    # External Service API Keys
    tmdb_api_key: str

    # Prowlarr Settings
    prowlarr_url: str = "http://prowlarr-service:9696"
    prowlarr_api_key: str | None = None
    prowlarr_live_title_search: bool = False
    prowlarr_background_title_search: bool = True
    prowlarr_search_query_timeout: int = 15
    prowlarr_search_interval_hour: int = 24
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

    # Zilean Settings
    is_scrap_from_zilean: bool = False
    zilean_search_interval_hour: int = 24
    zilean_url: str = "https://zilean.elfhosted.com"

    # Premiumize Settings
    premiumize_oauth_client_id: str | None = None
    premiumize_oauth_client_secret: str | None = None

    # Configuration Sources
    remote_config_source: str = (
        "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/scraper_config.json"
    )
    local_config_path: str = "resources/json/scraper_config.json"

    # Feature Toggles
    enable_rate_limit: bool = False
    validate_m3u8_urls_liveness: bool = True
    store_stremthru_magnet_cache: bool = False

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
    disable_formula_tgx_scheduler: bool = False
    nowmetv_scheduler_crontab: str = "0 0 * * *"
    disable_nowmetv_scheduler: bool = False
    nowsports_scheduler_crontab: str = "0 10 * * *"
    disable_nowsports_scheduler: bool = False
    tamilultra_scheduler_crontab: str = "0 8 * * *"
    disable_tamilultra_scheduler: bool = False
    validate_tv_streams_in_db_crontab: str = "0 */6 * * *"
    disable_validate_tv_streams_in_db: bool = False
    sport_video_scheduler_crontab: str = "*/20 * * * *"
    disable_sport_video_scheduler: bool = False
    dlhd_scheduler_crontab: str = "25 * * * *"
    disable_dlhd_scheduler: bool = False
    update_imdb_data_crontab: str = "0 2 * * *"
    motogp_tgx_scheduler_crontab: str = "0 5 * * *"
    disable_motogp_tgx_scheduler: bool = False
    update_seeders_crontab: str = "0 0 * * *"
    arab_torrents_scheduler_crontab: str = "0 0 * * *"
    disable_arab_torrents_scheduler: bool = False
    wwe_tgx_scheduler_crontab: str = "10 */3 * * *"
    disable_wwe_tgx_scheduler: bool = False
    ufc_tgx_scheduler_crontab: str = "30 */3 * * *"
    disable_ufc_tgx_scheduler: bool = False
    prowlarr_feed_scraper_crontab: str = "0 */3 * * *"
    disable_prowlarr_feed_scraper: bool = False
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
