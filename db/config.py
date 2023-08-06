from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    git_rev: str = "beta"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
