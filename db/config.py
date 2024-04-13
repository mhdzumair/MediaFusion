from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    db_max_connections: int = 50
    redis_url: str = "redis://redis-service:6379"
    git_rev: str = "stable"
    secret_key: str
    host_url: str = "https://mediafusion.fun"
    logging_level: str = "INFO"
    scraper_proxy_url: str | None = None
    torrentio_url: str = "https://torrentio.strem.fun"
    is_scrap_from_torrentio: bool = False
    torrentio_search_interval_days: int = 3
    premiumize_oauth_client_id: str | None = None
    premiumize_oauth_client_secret: str | None = None
    prowlarr_url: str = "http://prowlarr-service:9696"
    prowlarr_api_key: str | None = None
    prowlarr_search_interval_hour: int = 24
    prowlarr_immediate_max_process: int = 10
    prowlarr_immediate_max_process_time: int = 15
    prowlarr_live_title_search: bool = False
    adult_content_regex_keywords: str = r"(^|\b|\s)(18\+|adult|porn|sex|xxx|nude|naked|erotic|sexy|18\s*plus)(\b|\s|$|[._-])"
    enable_rate_limit: bool = True
    api_password: str | None = None
    is_public_instance: bool = False
    meta_cache_ttl: int = 1800  # 30 minutes
    validate_m3u8_urls_liveness: bool = True

    # Scheduler settings
    tamilmv_scheduler_crontab: str = "0 */3 * * *"
    disable_tamilmv_scheduler: bool = False
    tamil_blasters_scheduler_crontab: str = "0 */6 * * *"
    disable_tamil_blasters_scheduler: bool = False
    formula_tgx_scheduler_crontab: str = "0 */12 * * *"
    disable_formula_tgx_scheduler: bool = False
    mhdtvworld_scheduler_crontab: str = "0 0 * * 5"
    disable_mhdtvworld_scheduler: bool = False
    mhdtvsports_scheduler_crontab: str = "0 10 * * *"
    disable_mhdtvsports_scheduler: bool = False
    tamilultra_scheduler_crontab: str = "0 8 * * *"
    disable_tamilultra_scheduler: bool = False
    validate_tv_streams_in_db_crontab: str = "0 */6 * * *"
    disable_validate_tv_streams_in_db: bool = False
    sport_video_scheduler_crontab: str = "20 * * * *"
    disable_sport_video_scheduler: bool = False
    streamed_scheduler_crontab: str = "*/15 * * * *"
    disable_streamed_scheduler: bool = False
    mrgamingstreams_scheduler_crontab: str = "*/15 * * * *"
    disable_mrgamingstreams_scheduler: bool = (
        True  # Disabled it due to the site being down.
    )
    disable_all_scheduler: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
