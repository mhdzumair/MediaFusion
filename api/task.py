# import background actors
import asyncio

from scrappers.helpers import update_torrent_movie_streams_metadata  # noqa: F401
from scrappers.prowlarr import parse_and_store_movie_stream_data  # noqa: F401
from utils import torrent


async def async_setup():
    # Your async initialization code here
    await torrent.init_best_trackers()


asyncio.run(async_setup())
