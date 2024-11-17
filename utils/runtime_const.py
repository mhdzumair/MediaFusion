import re
from datetime import timedelta

from fastapi.templating import Jinja2Templates
import redis

from db import schemas
from db.config import settings
from utils import get_json_data, const

ADULT_CONTENT_KEYWORDS = re.compile(
    settings.adult_content_regex_keywords,
    re.IGNORECASE,
)

SPORTS_ARTIFACTS = get_json_data("resources/json/sports_artifacts.json")

PRIVATE_CIDR = re.compile(
    r"^(10\.|127\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)",
)

TEMPLATES = Jinja2Templates(directory="resources")
MANIFEST_TEMPLATE = TEMPLATES.get_template("templates/manifest.json.j2")

DELETE_ALL_META = schemas.Meta(
    **const.DELETE_ALL_WATCHLIST_META,
    poster=f"{settings.poster_host_url}/static/images/delete_all_poster.jpg",
    background=f"{settings.poster_host_url}/static/images/delete_all_background.png",
)

DELETE_ALL_META_ITEM = {
    "meta": DELETE_ALL_META.model_dump(by_alias=True, exclude_none=True)
}
TRACKERS = get_json_data("resources/json/trackers.json")

SECRET_KEY = settings.secret_key.encode("utf-8")


REDIS_SYNC_CLIENT: redis.Redis = redis.Redis(
    connection_pool=redis.ConnectionPool.from_url(settings.redis_url)
)

REDIS_ASYNC_CLIENT: redis.asyncio.Redis = redis.asyncio.Redis(
    connection_pool=redis.asyncio.ConnectionPool.from_url(settings.redis_url)
)


PROWLARR_SEARCH_TTL = int(
    timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds()
)
TORRENTIO_SEARCH_TTL = int(
    timedelta(days=settings.torrentio_search_interval_days).total_seconds()
)
MEDIAFUSION_SEARCH_TTL = int(
    timedelta(days=settings.mediafusion_search_interval_days).total_seconds()
)
ZILEAN_SEARCH_TTL = int(
    timedelta(hours=settings.zilean_search_interval_hour).total_seconds()
)
