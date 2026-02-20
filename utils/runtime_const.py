import re
from datetime import timedelta

import PTT
from PTT.adult import is_adult_content
from fastapi.templating import Jinja2Templates
from regex import regex

from db import schemas
from db.config import settings
from utils import const, get_json_data

ADULT_PARSER = PTT.Parser()
ADULT_PARSER.add_handler("adult", is_adult_content)
ADULT_PARSER.add_handler(
    "adult",
    regex.compile(settings.adult_content_regex_keywords, regex.IGNORECASE),
    PTT.transformers.boolean,
)

SPORTS_ARTIFACTS = get_json_data("resources/json/sports_artifacts.json")

TEMPLATES = Jinja2Templates(directory="resources")
MANIFEST_TEMPLATE = TEMPLATES.get_template("templates/manifest.json.j2")

DELETE_ALL_META = schemas.Meta(
    **const.DELETE_ALL_WATCHLIST_META,
    poster=f"{settings.poster_host_url}/static/images/delete_all_poster.jpg",
    background=f"{settings.poster_host_url}/static/images/delete_all_background.png",
)

DELETE_ALL_META_ITEM = {"meta": DELETE_ALL_META.model_dump(by_alias=True, exclude_none=True)}
TRACKERS = get_json_data("resources/json/trackers.json")

SECRET_KEY = settings.secret_key.encode("utf-8")

PROWLARR_SEARCH_TTL = int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds())
TORRENTIO_SEARCH_TTL = int(timedelta(days=settings.torrentio_search_interval_days).total_seconds())
MEDIAFUSION_SEARCH_TTL = int(timedelta(days=settings.mediafusion_search_interval_days).total_seconds())
ZILEAN_SEARCH_TTL = int(timedelta(hours=settings.zilean_search_interval_hour).total_seconds())

SERVER_NAMESPACE = None
YTS_SEARCH_TTL = 259200  # 3 days in seconds
BT4G_SEARCH_TTL = int(timedelta(hours=settings.bt4g_search_interval_hour).total_seconds())
JACKETT_SEARCH_TTL = int(timedelta(hours=settings.jackett_search_interval_hour).total_seconds())
# Torznab TTL - same as Jackett since they use the same protocol
TORZNAB_SEARCH_TTL = JACKETT_SEARCH_TTL
# TorBox Search API TTL - same as Prowlarr
TORBOX_SEARCH_TTL = PROWLARR_SEARCH_TTL
# Telegram search TTL
TELEGRAM_SEARCH_TTL = int(timedelta(hours=settings.telegram_scrape_interval_hour).total_seconds())

DATE_STR_REGEX = re.compile(
    r"\d{4}\.\d{2}\.\d{2}|\d{4}-\d{2}-\d{2}|\d{4}_\d{2}_\d{2}|\d{2}\.\d{2}\.\d{4}|\d{2}-\d{2}-\d{4}|\d{2}_\d{2}_\d{4}",
)
