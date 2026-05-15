"""Standalone APScheduler process — runs background cron jobs without the HTTP server."""

import asyncio
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import database
from db.config import settings
from utils.exception_tracker import install_exception_handler
from utils.torrent import init_best_trackers
from workers.scheduler import setup_scheduler

logging.basicConfig(
    format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    level=settings.logging_level,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    install_exception_handler()
    await database.init()
    await init_best_trackers()

    scheduler = AsyncIOScheduler()
    setup_scheduler(scheduler)
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set_result, None)

    await stop
    scheduler.shutdown(wait=False)
    await database.close()
    logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
