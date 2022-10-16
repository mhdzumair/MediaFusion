import motor.motor_asyncio
from beanie import init_beanie

from db.config import settings
from db.models import TamilBlasterMovie


async def init():
    # Create Motor client
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongo_uri)

    # Init beanie with the Product document class
    await init_beanie(database=client.streamio, document_models=[TamilBlasterMovie])
