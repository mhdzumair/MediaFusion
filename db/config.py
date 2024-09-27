from pydantic import model_validator, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database and cache settings
    mongo_uri: str
    db_max_connections: int = 50
    redis_url: str = "redis://redis-service:6379"

    # API and service URLs
    host_url: str
    poster_host_url: str | None = None
    scraper_proxy_url: str | None = None
    torrentio_url: str = "https://torrentio.strem.fun"
    prowlarr_url: str = "http://prowlarr-service:9696"
    zilean_url: str = "http://zilean.zilean:8181"
    playwright_cdp_url: str = "ws://browserless:3000?blockAds=true&stealth=true"
    flaresolverr_url: str = "http://flaresolverr:8191/v1"

    # External API keys and secrets
    secret_key: str = Field(..., max_length=32, min_length=32)
    prowlarr_api_key: str | None = None
    premiumize_oauth_client_id: str | None = None
    premiumize_oauth_client_secret: str | None = None

    # Common settings
    logging_level: str = "INFO"
    git_rev: str = "stable"
    addon_name: str = "MediaFusion"
    logo_url: str = (
        "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/images/mediafusion_logo.png"
    )
    remote_config_source: str = (
        "https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/scraper_config.json"
    )
    local_config_path: str = "resources/json/scraper_config.json"

    # Feature toggles
    is_scrap_from_torrentio: bool = False
    is_scrap_from_zilean: bool = False
    enable_rate_limit: bool = True
    is_public_instance: bool = False
    validate_m3u8_urls_liveness: bool = True
    adult_content_regex_keywords: str = (
        r"(^|\b|\s|$|[\[._-])"
        r"(18\s*\+|adults?|porn|sex|xxx|nude|boobs?|pussy|ass|bigass|bigtits?|blowjob|hardfuck|onlyfans?|naked|hot|milf|slut|doggy|anal|threesome|foursome|erotic|sexy|18\s*plus|trailer)"
        r"(\b|\s|$|[\]._-])"
    )
    prowlarr_live_title_search: bool = False
    prowlarr_background_title_search: bool = True
    prowlarr_search_query_timeout: int = 120
    disable_download_via_browser: bool = False

    # Scheduler settings
    disable_all_scheduler: bool = False
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
    streamed_scheduler_crontab: str = "*/30 * * * *"
    disable_streamed_scheduler: bool = False
    mrgamingstreams_scheduler_crontab: str = "*/15 * * * *"
    disable_mrgamingstreams_scheduler: bool = True  # Disabled due to site being down.
    crictime_scheduler_crontab: str = "*/15 * * * *"
    disable_crictime_scheduler: bool = False
    streambtw_scheduler_crontab: str = "*/15 * * * *"
    disable_streambtw_scheduler: bool = False
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
    prowlarr_feed_scrape_interval: int = 3
    prowlarr_feed_scraper_crontab: str = "0 */3 * * *"
    disable_prowlarr_feed_scraper: bool = False

    # Time-related settings
    torrentio_search_interval_days: int = 3
    prowlarr_search_interval_hour: int = 24
    prowlarr_immediate_max_process: int = 10
    prowlarr_immediate_max_process_time: int = 15
    meta_cache_ttl: int = 1800  # 30 minutes in seconds
    worker_max_tasks_per_child: int = 20

    # Optional security settings
    api_password: str | None = None

    @model_validator(mode="after")
    def default_poster_host_url(self) -> "Settings":
        if not self.poster_host_url:
            self.poster_host_url = self.host_url
        return self

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
