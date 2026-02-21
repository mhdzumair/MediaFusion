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

from api.middleware import MaxTasksPerChild, Retries, TaskManager, WorkerMemoryTelemetry
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
worker_middlewares = [
    asyncio_middleware,
    AgeLimit(),
    TimeLimit(),
    ShutdownNotifications(),
    Callbacks(),
    Pipelines(),
    Retries(),
]
if settings.enable_worker_memory_metrics and settings.worker_memory_metrics_history_size > 0:
    worker_middlewares.append(WorkerMemoryTelemetry(settings.worker_memory_metrics_history_size))
    logging.info(
        "Worker memory telemetry enabled (history_size=%s).",
        settings.worker_memory_metrics_history_size,
    )
else:
    logging.info("Worker memory telemetry disabled.")

if settings.enable_worker_max_tasks_per_child and settings.worker_max_tasks_per_child > 0:
    worker_middlewares.append(MaxTasksPerChild(settings.worker_max_tasks_per_child))
    logging.info(
        "MaxTasksPerChild enabled: worker recycles every %s tasks.",
        settings.worker_max_tasks_per_child,
    )
else:
    logging.info("MaxTasksPerChild disabled: worker runs continuously.")

worker_middlewares.extend(
    [
        TaskManager(),
        CurrentMessage(),
        Abortable(backend=RedisBackend.from_url(settings.redis_url)),
    ]
)
redis_broker.middleware = worker_middlewares
dramatiq.set_broker(redis_broker)
asyncio_middleware.before_worker_boot(redis_broker, None)
