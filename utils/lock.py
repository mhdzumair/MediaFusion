import asyncio
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

from db.redis_database import REDIS_ASYNC_CLIENT

scheduler_lock_key = "mediafusion_scheduler_lock"
heartbeat_key = "mediafusion_scheduler_heartbeat"
heartbeat_timeout = 300  # 5 minutes


async def acquire_scheduler_lock():
    current_time = int(time.time())
    # Check if the current scheduler is active
    last_heartbeat = await REDIS_ASYNC_CLIENT.get(heartbeat_key)
    if last_heartbeat and (current_time - int(last_heartbeat) <= heartbeat_timeout):
        logging.info("Scheduler is still active, not acquiring lock")
        return False, None  # Scheduler is still active, do not acquire lock

    # Attempt to acquire the lock
    acquired, lock = await acquire_redis_lock(
        scheduler_lock_key, timeout=heartbeat_timeout, block=False
    )
    if acquired:
        logging.info("Acquired scheduler lock")
        await REDIS_ASYNC_CLIENT.set(heartbeat_key, current_time)
        return True, lock
    logging.info("Failed to acquire scheduler lock")
    return False, None


async def release_scheduler_lock(lock):
    logging.info("Releasing scheduler lock")
    await release_redis_lock(lock)
    await REDIS_ASYNC_CLIENT.delete(heartbeat_key)


async def maintain_heartbeat():
    while True:
        await asyncio.sleep(heartbeat_timeout // 2)
        await REDIS_ASYNC_CLIENT.set(heartbeat_key, int(time.time()))


async def acquire_redis_lock(key: str, timeout: int = 60, block: bool = False):
    lock = REDIS_ASYNC_CLIENT.lock(key, timeout=timeout)
    acquired = await lock.acquire(blocking=block)
    return acquired, lock


async def release_redis_lock(lock):
    try:
        await lock.release()
    except LockNotOwnedError:
        logging.error("Failed to release lock, lock not owned")
        pass
