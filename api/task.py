import asyncio

from db import database

from utils import torrent

# import background actors
# noqa: F401
from scrapers import helpers
from scrapers import prowlarr
from mediafusion_scrapy import task
from utils import validation_helper
from scrapers import tv
from scrapers import imdb_data


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()


asyncio.run(async_setup())
