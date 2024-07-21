import re

from fastapi.templating import Jinja2Templates

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

DELETE_ALL_META = schemas.Meta(
    **const.DELETE_ALL_WATCHLIST_META,
    poster=f"{settings.poster_host_url}/static/images/delete_all_poster.jpg",
    background=f"{settings.poster_host_url}/static/images/delete_all_background.png",
)

DELETE_ALL_META_ITEM = {
    "meta": DELETE_ALL_META.model_dump(by_alias=True, exclude_none=True)
}
TRACKERS = get_json_data("resources/json/trackers.json")
