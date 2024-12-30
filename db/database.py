import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from db.config import settings
from db.models import (
    MediaFusionSeriesMetaData,
    MediaFusionMovieMetaData,
    TorrentStreams,
    TVStreams,
    MediaFusionTVMetaData,
)

logging.getLogger("pymongo").setLevel(logging.WARNING)


async def init(allow_index_dropping: bool = False):
    retries = 5
    for i in range(retries):
        try:
            # Create a Motor client with maxPoolSize
            client = AsyncIOMotorClient(
                settings.mongo_uri, maxPoolSize=settings.db_max_connections
            )
            # Init beanie with the Product document class
            await init_beanie(
                database=client.get_default_database(),  # Note that the database needs to be passed as part of the URI
                document_models=[
                    MediaFusionMovieMetaData,
                    MediaFusionSeriesMetaData,
                    MediaFusionTVMetaData,
                    TorrentStreams,
                    TVStreams,
                ],
                multiprocessing_mode=True,
                allow_index_dropping=allow_index_dropping,
            )
            logging.info("Database initialized successfully.")
            break
        except Exception as e:
            if i < retries - 1:  # i is zero indexed
                wait_time = 2**i  # exponential backoff
                logging.exception(
                    f"Error initializing database: {e}, retrying in {wait_time} seconds..."
                )
                await asyncio.sleep(wait_time)
            else:
                logging.error("Failed to initialize database after several attempts.")
                raise e
