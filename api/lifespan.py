"""Application lifecycle management."""

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from api.scheduler import setup_scheduler
from db import database
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import torrent
from utils.lock import (
    acquire_scheduler_lock,
    maintain_heartbeat,
    release_scheduler_lock,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Application lifespan context manager.

    Handles:
    - Database initialization
    - Tracker initialization
    - Telegram bot commands registration
    - Scheduler setup with distributed locking
    - Graceful shutdown
    """
    # Startup logic
    await database.init()

    await torrent.init_best_trackers()

    # Register Telegram bot commands if enabled
    if settings.telegram_bot_token:
        try:
            from utils.telegram_bot import telegram_content_bot

            await telegram_content_bot.register_bot_commands()
            logging.info("Telegram bot commands registered")
        except Exception as e:
            logging.warning(f"Failed to register Telegram bot commands: {e}")

    scheduler = None
    scheduler_lock = None
    heartbeat_task = None

    if not settings.disable_all_scheduler:
        acquired, scheduler_lock = await acquire_scheduler_lock()
        if acquired:
            try:
                scheduler = AsyncIOScheduler()
                setup_scheduler(scheduler)
                scheduler.start()
                heartbeat_task = asyncio.create_task(maintain_heartbeat())
            except Exception as e:
                await release_scheduler_lock(scheduler_lock)
                raise e

    yield

    # Shutdown logic
    if heartbeat_task:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            logging.info("Heartbeat task cancelled")

    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:
            logging.exception("Error shutting down scheduler, %s", e)
        finally:
            await release_scheduler_lock(scheduler_lock)

    await REDIS_ASYNC_CLIENT.aclose()
