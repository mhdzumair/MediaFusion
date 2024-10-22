import asyncio
import logging
from os import path
from typing import Annotated

from fastapi import (
    Request,
    Response,
    HTTPException,
    APIRouter,
    Depends,
    BackgroundTasks,
)
from fastapi.responses import RedirectResponse

from db import crud, schemas
from db.config import settings
from streaming_providers import mapper
from streaming_providers.debridlink.api import router as debridlink_router
from streaming_providers.exceptions import ProviderException
from streaming_providers.premiumize.api import router as premiumize_router
from streaming_providers.realdebrid.api import router as realdebrid_router
from streaming_providers.seedr.api import router as seedr_router
from utils import crypto, torrent, wrappers, const
from utils.lock import acquire_redis_lock, release_redis_lock
from utils.network import get_user_public_ip, get_user_data, encode_mediaflow_proxy_url
from utils.runtime_const import REDIS_ASYNC_CLIENT

# Seconds until when the Video URLs are cached
URL_CACHE_EXP = 3600

router = APIRouter()


def generate_cache_key(user_ip, secret_str, info_hash, season, episode):
    """
    Generates a cache key based on user IP, secret string, info hash, season, and episode.
    """
    return "streaming_provider_" + crypto.get_text_hash(
        f"{user_ip}_{secret_str}_{info_hash}_{season}_{episode}", full_hash=True
    )


async def get_cached_stream_url_and_redirect(
    cached_stream_url_key, user_data, response
):
    """
    Checks for cached stream URL and returns a RedirectResponse if available.
    """
    if cached_stream_url := await get_cached_stream_url(cached_stream_url_key):
        if (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_debrid_streams
        ):
            cached_stream_url = encode_mediaflow_proxy_url(
                user_data.mediaflow_config.proxy_url,
                "/proxy/stream",
                cached_stream_url,
                query_params={"api_password": user_data.mediaflow_config.api_password},
                response_headers={
                    "Content-Disposition": "attachment, filename={}".format(
                        path.basename(cached_stream_url)
                    )
                },
            )
        return RedirectResponse(
            url=cached_stream_url, headers=response.headers, status_code=302
        )
    return None


async def fetch_stream_or_404(info_hash):
    """
    Fetches stream by info hash, raises a 404 error if not found.
    """
    stream = await crud.get_stream_by_info_hash(info_hash)
    if stream:
        return stream

    # TODO: added for backwards compatibility, remove in the future
    stream = await crud.get_stream_by_info_hash(info_hash.upper())
    if stream:
        return stream

    raise HTTPException(status_code=400, detail="Stream not found.")


async def get_or_create_video_url(
    stream, user_data, info_hash, season, episode, user_ip, background_tasks
):
    """
    Retrieves or generates the video URL based on stream data and user info.
    """
    magnet_link = await torrent.convert_info_hash_to_magnet(
        info_hash, stream.announce_list
    )
    episode_data = stream.get_episode(season, episode)
    filename = episode_data.filename if episode_data else stream.filename

    get_video_url = mapper.GET_VIDEO_URL_FUNCTIONS.get(
        user_data.streaming_provider.service
    )
    kwargs = dict(
        info_hash=info_hash,
        magnet_link=magnet_link,
        user_data=user_data,
        filename=filename,
        file_index=stream.file_index,
        user_ip=user_ip,
        season=season,
        episode=episode,
        max_retries=1,
        retry_interval=0,
        stream=stream,
        torrent_name=stream.torrent_name,
        background_tasks=background_tasks,
    )

    if asyncio.iscoroutinefunction(get_video_url):
        return await get_video_url(**kwargs)
    else:
        return await asyncio.to_thread(get_video_url, **kwargs)


async def cache_stream_url(cached_stream_url_key, video_url):
    """
    Caches the streaming URL in Redis for future use.
    """
    await REDIS_ASYNC_CLIENT.set(
        cached_stream_url_key, video_url.encode("utf-8"), ex=URL_CACHE_EXP
    )


def apply_mediaflow_proxy_if_needed(video_url, user_data):
    """
    Applies mediaflow proxy to the video URL if user config requires it.
    """
    if user_data.mediaflow_config and user_data.mediaflow_config.proxy_debrid_streams:
        return encode_mediaflow_proxy_url(
            user_data.mediaflow_config.proxy_url,
            "/proxy/stream",
            video_url,
            query_params={"api_password": user_data.mediaflow_config.api_password},
            response_headers={
                "Content-Disposition": "attachment, filename={}".format(
                    path.basename(video_url)
                )
            },
        )
    return video_url


def handle_provider_exception(error, usage) -> str:
    """
    Handles exceptions raised by the provider and logs them.
    """
    logging.error(
        "Provider exception occurred for %s: %s",
        usage,
        error.message,
        exc_info=error.video_file_name == "api_error.mp4",
    )
    return f"{settings.host_url}/static/exceptions/{error.video_file_name}"


def handle_generic_exception(exception, info_hash) -> str:
    """
    Handles generic exceptions and logs them.
    """
    logging.error(
        "Generic exception occurred for %s: %s", info_hash, exception, exc_info=True
    )
    return f"{settings.host_url}/static/exceptions/api_error.mp4"


async def get_cached_stream_url(cached_stream_url_key):
    if cached_stream_url := await REDIS_ASYNC_CLIENT.getex(
        cached_stream_url_key, ex=URL_CACHE_EXP
    ):
        cached_stream_url = cached_stream_url.decode("utf-8")
        return cached_stream_url
    return None


@router.head("/{secret_str}/stream", tags=["streaming_provider"])
@router.get("/{secret_str}/stream", tags=["streaming_provider"])
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def streaming_provider_endpoint(
    secret_str: str,
    info_hash: str,
    response: Response,
    request: Request,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    season: int = None,
    episode: int = None,
):
    """
    Handles streaming provider requests, using caching for performance and
    locking mechanisms to prevent duplicate tasks.
    """
    response.headers.update(const.NO_CACHE_HEADERS)
    info_hash = info_hash.lower()

    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    user_ip = await get_user_public_ip(request, user_data)
    cached_stream_url_key = generate_cache_key(
        user_ip, secret_str, info_hash, season, episode
    )

    # Check for cached stream URL
    cached_stream_url = await get_cached_stream_url_and_redirect(
        cached_stream_url_key, user_data, response
    )
    if cached_stream_url:
        return cached_stream_url

    # Fetch stream from DB
    stream = await fetch_stream_or_404(info_hash)

    # Acquire Redis lock to prevent duplicate download tasks
    acquired, lock = await acquire_redis_lock(
        f"{cached_stream_url_key}_locked", timeout=60, block=True
    )
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many requests.")

    redirect_status_code = 307

    try:
        video_url = await get_or_create_video_url(
            stream, user_data, info_hash, season, episode, user_ip, background_tasks
        )
        await cache_stream_url(cached_stream_url_key, video_url)
        video_url = apply_mediaflow_proxy_if_needed(video_url, user_data)
        redirect_status_code = 302
    except ProviderException as error:
        video_url = handle_provider_exception(error, info_hash)
    except Exception as e:
        video_url = handle_generic_exception(e, info_hash)
    finally:
        await release_redis_lock(lock)

    return RedirectResponse(
        url=video_url, headers=response.headers, status_code=redirect_status_code
    )


@router.get("/{secret_str}/delete_all_watchlist", tags=["streaming_provider"])
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def delete_all_watchlist(
    request: Request,
    response: Response,
    user_data: schemas.UserData = Depends(get_user_data),
):
    """
    Deletes the entire watchlist for the given user, based on the streaming provider.
    """
    response.headers.update(const.NO_CACHE_HEADERS)

    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    user_ip = await get_user_public_ip(request, user_data)
    kwargs = dict(user_data=user_data, user_ip=user_ip)

    # Get the delete watchlist function for the user's streaming provider
    delete_all_watchlist_function = mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(
        user_data.streaming_provider.service
    )

    if not delete_all_watchlist_function:
        raise HTTPException(
            status_code=400, detail="Provider does not support this action."
        )

    try:
        # Call the appropriate delete function asynchronously
        if asyncio.iscoroutinefunction(delete_all_watchlist_function):
            await delete_all_watchlist_function(**kwargs)
        else:
            await asyncio.to_thread(delete_all_watchlist_function, **kwargs)
        video_url = f"{settings.host_url}/static/exceptions/watchlist_deleted.mp4"

    except ProviderException as error:
        # Handle provider-specific exceptions
        video_url = handle_provider_exception(error, "delete_watchlist")

    except Exception as e:
        # Handle generic exceptions
        video_url = handle_generic_exception(e, "delete_watchlist")

    return RedirectResponse(url=video_url, headers=response.headers)


router.include_router(seedr_router, prefix="/seedr", tags=["seedr"])
router.include_router(realdebrid_router, prefix="/realdebrid", tags=["realdebrid"])
router.include_router(debridlink_router, prefix="/debridlink", tags=["debridlink"])
router.include_router(premiumize_router, prefix="/premiumize", tags=["premiumize"])
