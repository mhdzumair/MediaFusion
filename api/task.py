import asyncio

from db import database

from utils import torrent

# import background actors
# noqa: F401
from mediafusion_scrapy import task
from scrapers import (
    tv,
    imdb_data,
    trackers,
    helpers,
    prowlarr,
    feed_scraper,
    background_scraper,
    scraper_tasks,
)
from streaming_providers import cache_helpers
from utils import validation_helper


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()


asyncio.run(async_setup())
