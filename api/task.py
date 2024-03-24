import asyncio

from db import database

# import background actors
from scrapers import helpers  # noqa: F401
from scrapers import prowlarr  # noqa: F401
from mediafusion_scrapy import task  # noqa: F401
from utils import torrent
from utils import validation_helper  # noqa: F401
from scrapers import tamil_blasters, tamilmv, tv  # noqa: F401


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()


asyncio.run(async_setup())
