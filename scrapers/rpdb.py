import asyncio
import logging
import time

import httpx

from db import schemas
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT

RPDB_SUPPORTED_SET = "rpdb_supported_ids"
RPDB_UNSUPPORTED_HASH = "rpdb_unsupported_ids"
RPDB_UNSUPPORTED_EXPIRY = 60 * 60 * 24 * 7  # 7 days in seconds


async def check_rpdb_posters_availability(urls: list[str]) -> dict[str, bool]:
    """Batch check multiple poster URLs availability."""
    results = {}
    async with httpx.AsyncClient(
        timeout=10.0, proxy=settings.requests_proxy_url
    ) as client:

        async def check_url(url: str):
            try:
                response = await client.head(url)
                results[url] = response.status_code == 200
            except Exception as exc:
                logging.error(f"Exception for {url} - {exc}")
                results[url] = False

        await asyncio.gather(*(check_url(url) for url in urls), return_exceptions=True)
    return results


async def safe_redis_operation(pipe, operation: str, *args):
    """Execute Redis operation safely, logging errors but not raising them."""
    try:
        if operation == "sadd":
            await pipe.sadd(*args)
        elif operation == "hset":
            await pipe.hset(*args)
    except Exception as e:
        logging.error(f"Redis {operation} failed for {args}: {e}")


async def batch_update_rpdb_posters(
    imdb_ids: list[str], rpdb_poster_base: str, batch_size: int = 50
) -> dict[str, str]:
    """Process multiple IMDB IDs in batches to update poster URLs."""
    current_time = int(time.time())
    result_urls = {}

    # Filter valid IMDB IDs
    valid_imdb_ids = [id for id in imdb_ids if id.startswith("tt")]
    if not valid_imdb_ids:
        return result_urls

    try:
        # Check supported set
        supported = await REDIS_ASYNC_CLIENT.smembers(RPDB_SUPPORTED_SET)
    except Exception as e:
        logging.error(f"Failed to get supported IDs from Redis: {e}")
        supported = set()

    try:
        # Check unsupported hash
        unsupported = await REDIS_ASYNC_CLIENT.hgetall(RPDB_UNSUPPORTED_HASH)
    except Exception as e:
        logging.error(f"Failed to get unsupported IDs from Redis: {e}")
        unsupported = {}

    # Process supported IDs
    for imdb_id in valid_imdb_ids:
        if imdb_id in supported:
            result_urls[imdb_id] = f"{rpdb_poster_base}{imdb_id}.jpg"

    # Filter out IDs that need checking
    to_check = []
    for imdb_id in valid_imdb_ids:
        if imdb_id in result_urls:
            continue

        expiry_time = unsupported.get(imdb_id)
        if expiry_time and int(expiry_time) > current_time:
            continue

        to_check.append(imdb_id)

    # Process remaining IDs in batches
    for i in range(0, len(to_check), batch_size):
        batch = to_check[i : i + batch_size]
        urls_to_check = [f"{rpdb_poster_base}{id}.jpg" for id in batch]

        # Check availability in parallel
        availability = await check_rpdb_posters_availability(urls_to_check)

        # Update Redis and results for each ID independently
        for imdb_id, url in zip(batch, urls_to_check):
            try:
                if availability.get(url, False):
                    # Try to add to supported set
                    try:
                        await REDIS_ASYNC_CLIENT.sadd(RPDB_SUPPORTED_SET, imdb_id)
                        result_urls[imdb_id] = url
                    except Exception as e:
                        logging.error(f"Failed to add {imdb_id} to supported set: {e}")
                        # Still include in results even if Redis fails
                        result_urls[imdb_id] = url
                else:
                    # Try to add to unsupported hash
                    try:
                        new_expiry_time = current_time + RPDB_UNSUPPORTED_EXPIRY
                        await REDIS_ASYNC_CLIENT.hset(
                            RPDB_UNSUPPORTED_HASH, imdb_id, str(new_expiry_time)
                        )
                    except Exception as e:
                        logging.error(
                            f"Failed to add {imdb_id} to unsupported hash: {e}"
                        )
            except Exception as e:
                logging.error(f"Failed to process {imdb_id}: {e}")
                continue

    return result_urls


async def update_rpdb_posters(
    metas: schemas.Metas, user_data: schemas.UserData, catalog_type: str
) -> schemas.Metas:
    """Update multiple meta items with RPDB posters in an optimized way."""
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return metas

    rpdb_poster_base = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/"

    try:
        # Get all IMDB IDs
        imdb_ids = [meta.id for meta in metas.metas]

        # Batch process all posters
        poster_urls = await batch_update_rpdb_posters(imdb_ids, rpdb_poster_base)

        # Update meta items with new poster URLs
        for meta in metas.metas:
            if meta.id in poster_urls:
                meta.poster = poster_urls[meta.id]
    except Exception as e:
        logging.error(f"Failed to update RPDB posters: {e}")
        # Return original metas if update fails
        return metas

    return metas


async def update_rpdb_poster(
    meta_item: schemas.MetaItem, user_data: schemas.UserData, catalog_type: str
) -> schemas.MetaItem:
    """Update single meta item with RPDB poster."""
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return meta_item

    rpdb_poster_base = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/"

    try:
        poster_urls = await batch_update_rpdb_posters(
            [meta_item.meta.id], rpdb_poster_base
        )

        if meta_item.meta.id in poster_urls:
            meta_item.meta.poster = poster_urls[meta_item.meta.id]
    except Exception as e:
        logging.error(f"Failed to update RPDB poster for {meta_item.meta.id}: {e}")
        # Return original meta_item if update fails
        return meta_item

    return meta_item
