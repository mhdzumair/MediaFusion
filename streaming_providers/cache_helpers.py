import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

from utils.runtime_const import REDIS_ASYNC_CLIENT

# Constants
CACHE_KEY_PREFIX = "debrid_cache:"
EXPIRY_DAYS = 7


async def store_cached_info_hashes(service: str, info_hashes: List[str]) -> None:
    """
    Store multiple cached info hashes efficiently.
    Only stores info hashes that are confirmed to be cached.

    Args:
        service: The debrid service name (e.g., 'realdebrid', 'alldebrid')
        info_hashes: List of info hashes that are confirmed to be cached
    """
    if not info_hashes:
        return

    try:
        cache_key = f"{CACHE_KEY_PREFIX}{service}"
        timestamp = int(
            (datetime.now(tz=timezone.utc) + timedelta(days=EXPIRY_DAYS)).timestamp()
        )

        # Create mapping of info_hash to expiry timestamp
        cache_data = {hash_: timestamp for hash_ in info_hashes}

        # Store all hashes with their expiry timestamps in one operation
        await REDIS_ASYNC_CLIENT.hset(cache_key, mapping=cache_data)
    except Exception as e:
        logging.error(f"Error storing cached info hashes for {service}: {e}")


async def get_cached_status(service: str, info_hashes: List[str]) -> Dict[str, bool]:
    """
    Get cached status for multiple info hashes.
    If a hash isn't found in Redis or is expired, it's considered not cached.

    Args:
        service: The debrid service name
        info_hashes: List of info hashes to check

    Returns:
        Dict[str, bool]: Maps info_hash to cached status
    """
    if not info_hashes:
        return {}

    try:
        cache_key = f"{CACHE_KEY_PREFIX}{service}"
        current_time = int(datetime.now(tz=timezone.utc).timestamp())

        # Get all timestamps in one operation
        timestamps = await REDIS_ASYNC_CLIENT.hmget(cache_key, info_hashes)

        # Process results and handle expired entries
        result = {}
        expired_hashes = []

        for info_hash, timestamp_bytes in zip(info_hashes, timestamps):
            if timestamp_bytes is None:
                result[info_hash] = False
                continue

            try:
                expiry_time = int(timestamp_bytes)
                if expiry_time > current_time:
                    result[info_hash] = True
                else:
                    result[info_hash] = False
                    expired_hashes.append(info_hash)
            except (ValueError, TypeError):
                result[info_hash] = False
                expired_hashes.append(info_hash)

        # Clean up expired entries if any found
        if expired_hashes:
            await REDIS_ASYNC_CLIENT.hdel(cache_key, *expired_hashes)

        return result
    except Exception as e:
        logging.error(f"Error getting cached status for {service}: {e}")
        return {hash_: False for hash_ in info_hashes}


async def cleanup_service_cache(service: str) -> None:
    """
    Cleanup expired entries for a service.
    Uses efficient HSCAN to handle large hash maps.

    Args:
        service: The debrid service name
    """
    try:
        cache_key = f"{CACHE_KEY_PREFIX}{service}"
        current_time = int(datetime.now(tz=timezone.utc).timestamp())
        cursor = 0
        expired_hashes = []

        while True:
            cursor, data = await REDIS_ASYNC_CLIENT.hscan(
                cache_key, cursor, count=1000  # Process in chunks of 1000
            )

            # Check for expired entries in this chunk
            for hash_, timestamp_bytes in data.items():
                try:
                    if int(timestamp_bytes) <= current_time:
                        expired_hashes.append(hash_)
                except (ValueError, TypeError):
                    expired_hashes.append(hash_)

            # Delete expired entries if we have accumulated enough or reached the end
            if expired_hashes and (len(expired_hashes) >= 1000 or cursor == 0):
                await REDIS_ASYNC_CLIENT.hdel(cache_key, *expired_hashes)
                expired_count = len(expired_hashes)
                expired_hashes = []
                logging.info(
                    f"Cleaned up {expired_count} expired entries for {service}"
                )

            # Exit if we've processed all entries
            if cursor == 0:
                break

    except Exception as e:
        logging.error(f"Error during cache cleanup for {service}: {e}")
