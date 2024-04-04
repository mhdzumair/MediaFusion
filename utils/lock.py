import aiofiles
from aiofiles import os


async def acquire_lock():
    try:
        async with aiofiles.open(".lock", "x") as f:
            return True
    except FileExistsError:
        return False


async def release_lock():
    try:
        await os.remove(".lock")
    except FileNotFoundError:
        pass
