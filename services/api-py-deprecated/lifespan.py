"""Application lifecycle management."""

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from workers.scheduler import setup_scheduler
from db import database
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from utils import torrent
from utils.lock import (
    acquire_redis_lock,
    acquire_scheduler_lock,
    maintain_heartbeat,
    release_redis_lock,
    release_scheduler_lock,
)
from utils.telegram_bot import telegram_content_bot

TELEGRAM_COMMANDS_REGISTERED_KEY = "mediafusion:telegram:commands:registered:v1"
TELEGRAM_COMMANDS_REGISTER_LOCK_KEY = "mediafusion:telegram:commands:register-lock"
TELEGRAM_COMMANDS_REGISTER_TTL_SECONDS = 60 * 60 * 24
TELEGRAM_COMMANDS_REGISTER_RETRIES = 5
TELEGRAM_COMMANDS_REGISTER_RETRY_DELAY_SECONDS = 1


async def ensure_redis_available() -> None:
    """Validate Redis connectivity during startup."""
    try:
        await REDIS_ASYNC_CLIENT.ping()
    except Exception as error:
        raise RuntimeError(
            f"Cannot connect to Redis at {settings.redis_url}. "
            "Ensure Redis is running and REDIS_URL is configured correctly."
        ) from error


async def register_telegram_commands_once() -> None:
    """Register Telegram bot commands once across all workers/pods."""
    for _ in range(TELEGRAM_COMMANDS_REGISTER_RETRIES):
        if await REDIS_ASYNC_CLIENT.exists(TELEGRAM_COMMANDS_REGISTERED_KEY):
            logging.debug("Telegram bot commands already registered recently, skipping")
            return

        acquired, lock = await acquire_redis_lock(
            TELEGRAM_COMMANDS_REGISTER_LOCK_KEY,
            timeout=60,
            block=False,
        )
        if not acquired:
            await asyncio.sleep(TELEGRAM_COMMANDS_REGISTER_RETRY_DELAY_SECONDS)
            continue

        try:
            if await REDIS_ASYNC_CLIENT.exists(TELEGRAM_COMMANDS_REGISTERED_KEY):
                logging.debug("Telegram bot commands already registered recently, skipping")
                return

            registered = await telegram_content_bot.register_bot_commands()
            if registered:
                await REDIS_ASYNC_CLIENT.set(
                    TELEGRAM_COMMANDS_REGISTERED_KEY,
                    "1",
                    ex=TELEGRAM_COMMANDS_REGISTER_TTL_SECONDS,
                )
                logging.info("Telegram bot commands registered")
            else:
                logging.warning("Failed to register Telegram bot commands")
            return
        finally:
            await release_redis_lock(lock)

    logging.debug("Skipped bot command registration; another worker is handling it")


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
    await ensure_redis_available()

    await torrent.init_best_trackers()

    # Register Telegram bot commands if enabled
    if settings.telegram_bot_token:
        try:
            await register_telegram_commands_once()
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
