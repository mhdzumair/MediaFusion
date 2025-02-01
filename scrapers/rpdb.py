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


async def check_rpdb_poster_availability(rpdb_poster_url: str) -> bool:
    try:
        async with httpx.AsyncClient(
            timeout=10.0, proxy=settings.requests_proxy_url
        ) as client:
            response = await client.head(rpdb_poster_url)
            return response.status_code == 200
    except httpx.TimeoutException as exc:
        logging.error(f"Timeout for {rpdb_poster_url} - {exc}")
        return False
    except httpx.HTTPError as exc:
        logging.error(f"HTTP Exception for {exc.request.url} - {exc}")
        return False
    except Exception as exc:
        logging.error(f"Exception for {rpdb_poster_url} - {exc}")
        return False


async def update_single_rpdb_poster(imdb_id: str, rpdb_poster_base: str) -> str | None:
    if not imdb_id.startswith("tt"):
        return None

    rpdb_poster_url = f"{rpdb_poster_base}{imdb_id}.jpg"
    current_time = int(time.time())

    # Check if the IMDB ID is in the supported set
    if await REDIS_ASYNC_CLIENT.sismember(RPDB_SUPPORTED_SET, imdb_id):
        return rpdb_poster_url

    # Check if the IMDB ID is in the unsupported hash
    expiry_time = await REDIS_ASYNC_CLIENT.hget(RPDB_UNSUPPORTED_HASH, imdb_id)

    if expiry_time:
        expiry_time = int(expiry_time)
        if expiry_time > current_time:
            # Still within expiry period, return None
            return None
        else:
            # Expired, remove from unsupported hash
            await REDIS_ASYNC_CLIENT.hdel(RPDB_UNSUPPORTED_HASH, imdb_id)

    # Check availability (either not in unsupported hash or expired)
    if await check_rpdb_poster_availability(rpdb_poster_url):
        await REDIS_ASYNC_CLIENT.sadd(RPDB_SUPPORTED_SET, imdb_id)
        return rpdb_poster_url
    else:
        new_expiry_time = current_time + RPDB_UNSUPPORTED_EXPIRY
        await REDIS_ASYNC_CLIENT.hset(
            RPDB_UNSUPPORTED_HASH, imdb_id, str(new_expiry_time)
        )
        return None


async def update_rpdb_poster(
    meta_item: schemas.MetaItem, user_data: schemas.UserData, catalog_type: str
) -> schemas.MetaItem:
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return meta_item

    rpdb_poster_base = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/"
    updated_poster = await update_single_rpdb_poster(
        meta_item.meta.id, rpdb_poster_base
    )

    if updated_poster:
        meta_item.meta.poster = updated_poster

    return meta_item


async def update_rpdb_posters(
    metas: schemas.Metas, user_data: schemas.UserData, catalog_type: str
) -> schemas.Metas:
    if not user_data.rpdb_config or catalog_type not in ["movie", "series"]:
        return metas

    rpdb_poster_base = f"https://api.ratingposterdb.com/{user_data.rpdb_config.api_key}/imdb/poster-default/"

    async def update_poster(meta):
        updated_poster = await update_single_rpdb_poster(meta.id, rpdb_poster_base)
        if updated_poster:
            meta.poster = updated_poster

    await asyncio.gather(
        *(update_poster(meta) for meta in metas.metas), return_exceptions=True
    )

    return metas
