import motor.motor_asyncio
from beanie import init_beanie

from db.config import settings
from db.models import (
    MediaFusionSeriesMetaData,
    MediaFusionMovieMetaData,
    TorrentStreams,
    TVStreams,
    MediaFusionTVMetaData,
    SearchHistory,
)


async def init():
    # Create Motor client
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)
    # Init beanie with the Product document class
    await init_beanie(
        database=client.get_default_database(),  # Note that the database needs to be passed as part of the URI
        document_models=[
            MediaFusionMovieMetaData,
            MediaFusionSeriesMetaData,
            TorrentStreams,
            TVStreams,
            MediaFusionTVMetaData,
            SearchHistory,
        ],
    )
