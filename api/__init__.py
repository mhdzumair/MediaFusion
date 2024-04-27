# Desc: Setup the dramatiq broker and middleware.
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AsyncIO,
    AgeLimit,
    TimeLimit,
    ShutdownNotifications,
    Callbacks,
    Pipelines,
)

from api.middleware import MaxTasksPerChild, Retries, TaskManager
from db.config import settings

# Setup the broker and the middleware
redis_broker = RedisBroker(url=settings.redis_url)
redis_broker.middleware = [
    AgeLimit(),
    TimeLimit(),
    ShutdownNotifications(),
    Callbacks(),
    Pipelines(),
    Retries(),
    AsyncIO(),
    MaxTasksPerChild(settings.worker_max_tasks_per_child),
    TaskManager(),
]
dramatiq.set_broker(redis_broker)
