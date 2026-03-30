import logging

from taskiq import TaskiqEvents

from db import database
from db.config import settings
from utils import torrent
from utils.exception_tracker import install_exception_handler

from mediafusion_scrapy import task as scrapy_task  # noqa: F401
from scrapers import (  # noqa: F401
    background_scraper,
    dmm_hashlist,
    feed_scraper,
    import_tasks,
    non_torrent_background_scraper,
    rss_scraper,
    scraper_tasks,
    trackers,
    tv,
)
from streaming_providers import cache_helpers  # noqa: F401

from api.task_queue import get_worker_broker

logging.basicConfig(
    format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=settings.logging_level,
)

install_exception_handler()


async def worker_startup() -> None:
    await torrent.init_best_trackers()
    await database.init()


async def worker_shutdown() -> None:
    await database.close()


def _attach_lifecycle_handlers(broker) -> None:
    @broker.on_event(TaskiqEvents.WORKER_STARTUP)
    async def _on_worker_startup(state):
        del state
        await worker_startup()

    @broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
    async def _on_worker_shutdown(state):
        del state
        await worker_shutdown()


broker_default = get_worker_broker("default")
broker_scrapy = get_worker_broker("scrapy")
broker_import = get_worker_broker("import")
broker_priority = get_worker_broker("priority")

_attach_lifecycle_handlers(broker_default)
_attach_lifecycle_handlers(broker_scrapy)
_attach_lifecycle_handlers(broker_import)
_attach_lifecycle_handlers(broker_priority)
