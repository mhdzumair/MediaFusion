import asyncio

from db import database
from utils import torrent

# Import all modules containing Dramatiq actors so the worker discovers them.
from mediafusion_scrapy import task  # noqa: F401
from scrapers import (  # noqa: F401
    background_scraper,
    feed_scraper,
    import_tasks,
    rss_scraper,
    scraper_tasks,
    trackers,
    tv,
)
from streaming_providers import cache_helpers  # noqa: F401


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()
    # async_setup runs in a short-lived asyncio.run() loop during worker boot.
    # Dispose pooled connections here so later worker loops don't inherit
    # connections bound to this closed setup loop.
    await database.close()


asyncio.run(async_setup())
