import asyncio

from db import database
from utils import torrent

# import background actors
# noqa: F401


async def async_setup():
    await torrent.init_best_trackers()
    await database.init()


asyncio.run(async_setup())
