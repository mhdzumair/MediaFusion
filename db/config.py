from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    redis_url: str = "redis://localhost:6379"
    git_rev: str = "stable"
    secret_key: str
    host_url: str = "https://mediafusion.fun"
    logging_level: str = "INFO"
    enable_scrapper: bool = False
    enable_search_scrapper: bool = False
    torrentio_url: str = "https://torrentio.strem.fun"
    premiumize_oauth_client_id: str | None = None
    premiumize_oauth_client_secret: str | None = None

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
