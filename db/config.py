from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_uri: str
    git_rev: str = "beta"
    secret_key: str

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
