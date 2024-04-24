import os

import aiofiles
import aiofiles.os
from redis.asyncio import Redis


async def is_server_running(pid: int):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def acquire_lock():
    lock_file_path = "/tmp/mediafusion.lock"
    if os.path.exists(lock_file_path):
        async with aiofiles.open(lock_file_path, "r") as f:
            pid = await f.read()
        if not await is_server_running(int(pid)):
            await release_lock()
    try:
        async with aiofiles.open(lock_file_path, "x") as f:
            await f.write(str(os.getpid()))  # Write the PID to the lock file
        return True
    except FileExistsError:
        return False


async def release_lock():
    try:
        await aiofiles.os.remove("/tmp/mediafusion.lock")
    except FileNotFoundError:
        pass


async def acquire_redis_lock(
    redis: Redis, key: str, timeout: int = 60, block: bool = False
):
    lock = redis.lock(key, timeout=timeout)
    acquired = await lock.acquire(blocking=block)
    return acquired, lock


async def release_redis_lock(lock):
    await lock.release()
