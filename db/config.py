from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    git_rev: str = "stable"
    secret_key: str
    host_url: str = "https://mediafusion.fun"
    logging_level: str = "INFO"
    enable_scrapper: bool = False
    poster_cache_path: str = "resources/poster_cache"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
