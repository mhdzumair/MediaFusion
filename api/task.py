import asyncio

from db import database

from utils import torrent

# import background actors
# noqa: F401
from mediafusion_scrapy import task
from scrapers import tv, imdb_data, trackers, helpers, prowlarr
from utils import validation_helper


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()


asyncio.run(async_setup())
