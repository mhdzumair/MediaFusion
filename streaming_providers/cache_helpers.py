import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

import dramatiq
import httpx

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import StreamingProvider

# Constants
CACHE_KEY_PREFIX = "debrid_cache:"
EXPIRY_DAYS = 7


def get_cache_service_name(streaming_provider: StreamingProvider):
    """
    get service name to use for redis cache retrieval
    """
    if (
        streaming_provider.service == "stremthru"
        and streaming_provider.stremthru_store_name
    ):
        return streaming_provider.stremthru_store_name
    return streaming_provider.service


async def store_cached_info_hashes(
    streaming_provider: StreamingProvider,
    info_hashes: List[str],
    service_override: str | None = None,
) -> None:
    """
    Store multiple cached info hashes efficiently and sync with MediaFusion Public Host.
    Only stores info hashes that are confirmed to be cached.

    Args:
        streaming_provider: The streaming provider object
        info_hashes: List of info hashes that are confirmed to be cached
        service_override: Optional service name override
    """
    if not info_hashes:
        return

    if (
        streaming_provider.service == "stremthru"
        and not settings.store_stremthru_magnet_cache
    ):
        # Don't cache info hashes for StremThru
        return

    service = service_override or get_cache_service_name(streaming_provider)

    try:
        # Store in local Redis
        cache_key = f"{CACHE_KEY_PREFIX}{service}"
        timestamp = int(
            (datetime.now(tz=timezone.utc) + timedelta(days=EXPIRY_DAYS)).timestamp()
        )

        # Create mapping of info_hash to expiry timestamp
        cache_data = {hash_: timestamp for hash_ in info_hashes}

        # Store all hashes with their expiry timestamps in one operation
        await REDIS_ASYNC_CLIENT.hset(cache_key, mapping=cache_data)

        # Submit to MediaFusion
        if settings.sync_debrid_cache_streams:
            await mediafusion_client.submit_cached_hashes(
                streaming_provider, info_hashes
            )

    except Exception as e:
        logging.error(f"Error storing cached info hashes for {service}: {e}")


async def get_cached_status(
    streaming_provider: StreamingProvider, info_hashes: List[str]
) -> Dict[str, bool]:
    """
    Get cached status for multiple info hashes from both local Redis and MediaFusion.
    If a hash isn't found in Redis or is expired, checks MediaFusion Public Host.

    Args:
        streaming_provider: The streaming provider object
        info_hashes: List of info hashes to check

    Returns:
        Dict[str, bool]: Maps info_hash to cached status
    """
    if not info_hashes:
        return {}

    service = get_cache_service_name(streaming_provider)

    try:
        # First check local Redis cache
        cache_key = f"{CACHE_KEY_PREFIX}{service}"
        current_time = int(datetime.now(tz=timezone.utc).timestamp())

        # Get all timestamps in one operation
        timestamps = await REDIS_ASYNC_CLIENT.hmget(cache_key, info_hashes)

        # Process results and identify which hashes need MediaFusion check
        result = {}
        expired_hashes = []
        mediafusion_check_needed = []

        for info_hash, timestamp_bytes in zip(info_hashes, timestamps):
            if timestamp_bytes is None:
                mediafusion_check_needed.append(info_hash)
                continue

            try:
                expiry_time = int(timestamp_bytes)
                if expiry_time > current_time:
                    result[info_hash] = True
                else:
                    expired_hashes.append(info_hash)
                    mediafusion_check_needed.append(info_hash)
            except (ValueError, TypeError):
                expired_hashes.append(info_hash)
                mediafusion_check_needed.append(info_hash)

        # Clean up expired entries if any found
        if expired_hashes:
            await REDIS_ASYNC_CLIENT.hdel(cache_key, *expired_hashes)

        # Check MediaFusion for any hashes not found in Redis or expired
        if mediafusion_check_needed and settings.sync_debrid_cache_streams:
            mediafusion_results = await mediafusion_client.fetch_cache_status(
                streaming_provider, mediafusion_check_needed
            )
            result.update(mediafusion_results)
        else:
            # If MediaFusion isn't available, mark all uncached hashes as False
            for hash_ in mediafusion_check_needed:
                result[hash_] = False

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


@dramatiq.actor(
    time_limit=5 * 60 * 1000,  # 5 minutes
    priority=2,
)
async def cleanup_expired_cache(**kwargs):
    """
    Cleanup expired entries for all services.
    """
    try:
        services = await REDIS_ASYNC_CLIENT.keys(f"{CACHE_KEY_PREFIX}*")
        for service in services:
            service_name = service.decode("utf-8").replace(CACHE_KEY_PREFIX, "")
            logging.info(f"Cleaning up cache for {service_name}")
            await cleanup_service_cache(service_name)
    except Exception as e:
        logging.error(f"Error during cache cleanup: {e}")


class MediaFusionCacheClient:
    """Client for interacting with MediaFusion cache service"""

    def __init__(self):
        self.base_url = settings.mediafusion_url.rstrip("/")
        self.timeout = httpx.Timeout(30.0)  # 30 second timeout

    async def fetch_cache_status(
        self, provider: StreamingProvider, info_hashes: List[str]
    ) -> Dict[str, bool]:
        """
        Fetch cache status from MediaFusion for given info hashes.

        Args:
            provider: Streaming provider details
            info_hashes: List of info hashes to check

        Returns:
            Dict mapping info hashes to their cache status
        """
        if not info_hashes or not settings.mediafusion_url:
            return {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/streaming_provider/cache/status",
                    json={
                        "service": provider.service,
                        "info_hashes": info_hashes,
                    },
                )
                response.raise_for_status()
                data = response.json()

                # Store any cached hashes we learn about
                cached_hashes = [
                    hash_
                    for hash_, is_cached in data["cached_status"].items()
                    if is_cached
                ]
                if cached_hashes:
                    await store_cached_info_hashes(provider, cached_hashes)

                return data["cached_status"]

        except Exception as e:
            logging.error(f"Error fetching cache status from MediaFusion: {str(e)}")
            return {}

    async def submit_cached_hashes(
        self, provider: StreamingProvider, info_hashes: List[str]
    ) -> bool:
        """
        Submit cached info hashes to MediaFusion.

        Args:
            provider: Streaming provider details
            info_hashes: List of cached info hashes to submit

        Returns:
            bool indicating success
        """
        if not info_hashes or not settings.mediafusion_url:
            return True

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/streaming_provider/cache/submit",
                    json={
                        "service": provider.service,
                        "info_hashes": info_hashes,
                    },
                )
                response.raise_for_status()
                return True

        except Exception as e:
            logging.error(f"Error submitting cache status to MediaFusion: {str(e)}")
            return False


# Global client instance
mediafusion_client = MediaFusionCacheClient()
