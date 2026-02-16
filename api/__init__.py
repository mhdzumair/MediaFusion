# Desc: Setup the dramatiq broker and middleware.
import logging

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    AsyncIO,
    Callbacks,
    CurrentMessage,
    Pipelines,
    ShutdownNotifications,
    TimeLimit,
)
from dramatiq_abort import Abortable
from dramatiq_abort.backends import RedisBackend

from api.middleware import MaxTasksPerChild, Retries, TaskManager
from db.config import settings
from utils.exception_tracker import install_exception_handler

# Configure logging for the worker process (matches api/app.py format)
logging.basicConfig(
    format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=settings.logging_level,
)

# Install Redis exception handler so Dramatiq worker exceptions are tracked
install_exception_handler()

# Setup the broker and the middleware
redis_broker = RedisBroker(url=settings.redis_url)
asyncio_middleware = AsyncIO()
redis_broker.middleware = [
    asyncio_middleware,
    AgeLimit(),
    TimeLimit(),
    ShutdownNotifications(),
    Callbacks(),
    Pipelines(),
    Retries(),
    MaxTasksPerChild(settings.worker_max_tasks_per_child),
    TaskManager(),
    CurrentMessage(),
    Abortable(backend=RedisBackend.from_url(settings.redis_url)),
]
dramatiq.set_broker(redis_broker)
asyncio_middleware.before_worker_boot(redis_broker, None)
