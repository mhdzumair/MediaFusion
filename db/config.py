from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    db_max_connections: int = 50
    redis_url: str = "redis://redis-service:6379"
    git_rev: str = "stable"
    secret_key: str
    host_url: str = "https://mediafusion.fun"
    logging_level: str = "INFO"
    enable_tamilmv_search_scraper: bool = False
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
    adult_content_regex_keywords: str = r"(^|\b|\s)(18\+|adult|porn|sex|xxx|nude|naked|erotic|sexy|18\s*plus)(\b|\s|$|[._-])"
    enable_rate_limit: bool = True
    api_password: str | None = None
    tamilmv_scheduler_crontab: str = "0 */3 * * *"
    tamil_blasters_scheduler_crontab: str = "0 */6 * * *"
    formula_tgx_scheduler_crontab: str = "0 */12 * * *"
    mhdtvworld_scheduler_crontab: str = "0 0 * * 5"
    mhdtvsports_scheduler_crontab: str = "0 10 * * *"
    tamilultra_scheduler_crontab: str = "0 8 * * *"
    validate_tv_streams_in_db_crontab: str = "0 */2 * * *"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
