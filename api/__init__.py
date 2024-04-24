# Desc: Setup the dramatiq broker and middleware.
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from api.middleware import MaxTasksPerChild
from db.config import settings

# Setup the broker and the middleware
redis_broker = RedisBroker(url=settings.redis_url)
redis_broker.add_middleware(AsyncIO())
redis_broker.add_middleware(MaxTasksPerChild(settings.worker_max_tasks_per_child))
dramatiq.set_broker(redis_broker)
