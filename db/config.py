from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    git_rev: str = "beta"
    secret_key: str
    host_url: str = "https://882b9915d0fe-stremio-tamilblasters.baby-beamup.club"
    logging_level: str = "INFO"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
