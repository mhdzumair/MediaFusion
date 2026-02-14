"""Admin and task-related schemas."""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from db.schemas.media import TVMetaData


class ScraperTask(BaseModel):
    """Scraper task configuration."""

    spider_name: Literal[
        "formula_tgx",
        "nowmetv",
        "nowsports",
        "tamilultra",
        "sport_video",
        "tamilmv",
        "tamil_blasters",
        "dlhd",
        "motogp_tgx",
        "arab_torrents",
        "wwe_tgx",
        "ufc_tgx",
        "movies_tv_tgx",
    ]
    pages: int | None = 1
    start_page: int | None = 1
    search_keyword: str | None = None
    scrape_all: bool = False
    scrap_catalog_id: Literal[
        "all",
        "tamil_hdrip",
        "tamil_tcrip",
        "tamil_dubbed",
        "tamil_series",
        "malayalam_hdrip",
        "malayalam_tcrip",
        "malayalam_dubbed",
        "malayalam_series",
        "telugu_tcrip",
        "telugu_hdrip",
        "telugu_dubbed",
        "telugu_series",
        "hindi_tcrip",
        "hindi_hdrip",
        "hindi_dubbed",
        "hindi_series",
        "kannada_tcrip",
        "kannada_hdrip",
        "kannada_series",
        "english_tcrip",
        "english_hdrip",
        "english_series",
    ] = "all"
    total_pages: int | None = None
    api_password: str = None


class TVMetaDataUpload(BaseModel):
    """TV metadata upload request."""

    api_password: str = None
    tv_metadata: TVMetaData


class KodiConfig(BaseModel):
    """Kodi configuration for device linking."""

    code: str = Field(max_length=6)
    manifest_url: HttpUrl


class BlockTorrent(BaseModel):
    """Block torrent request."""

    info_hash: str
    action: Literal["block", "delete"]
    api_password: str


class MigrateID(BaseModel):
    """Migration ID mapping."""

    mediafusion_id: str
    imdb_id: str
    media_type: Literal["movie", "series"]
