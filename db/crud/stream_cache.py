"""
Stream cache invalidation helpers.

Extracted here to avoid circular imports between stream_services.py and streams.py.
"""

import logging

from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)

STREAM_CACHE_PREFIX = "stream_data:"


async def invalidate_media_stream_cache(media_id: int) -> None:
    """Delete all cached stream data for a media.

    Called when streams are added to or removed from a media entry.
    Clears both movie and series cache keys for the given media_id.
    """
    try:
        legacy_movie_key = f"{STREAM_CACHE_PREFIX}movie:{media_id}"
        await REDIS_ASYNC_CLIENT.delete(legacy_movie_key)

        movie_pattern = f"{STREAM_CACHE_PREFIX}movie:{media_id}:*"
        movie_keys = []
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=movie_pattern, count=100):
            movie_keys.append(key)
        if movie_keys:
            await REDIS_ASYNC_CLIENT.delete(*movie_keys)

        pattern = f"{STREAM_CACHE_PREFIX}series:{media_id}:*"
        keys = []
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            await REDIS_ASYNC_CLIENT.delete(*keys)
    except Exception as e:
        logger.warning(f"Error invalidating stream cache for media_id={media_id}: {e}")
