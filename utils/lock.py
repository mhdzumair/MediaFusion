import aiofiles
from aiofiles import os


async def acquire_lock():
    try:
        async with aiofiles.open("/tmp/mediafusion.lock", "x") as f:
            return True
    except FileExistsError:
        return False


async def release_lock():
    try:
        await os.remove("/tmp/mediafusion.lock")
    except FileNotFoundError:
        pass
