# Desc: Setup the dramatiq broker and middleware.
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from db.config import settings

# Setup the broker and the middleware
redis_broker = RedisBroker(url=settings.redis_url)
redis_broker.add_middleware(AsyncIO())
dramatiq.set_broker(redis_broker)
