import asyncio
import logging

from fastapi import (
    Request,
    Response,
    HTTPException,
    APIRouter,
    Depends,
)
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

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

# Seconds until when the Video URLs are cached
URL_CACHE_EXP = 3600

router = APIRouter()


async def get_cached_stream_url(redis: Redis, cached_stream_url_key):
    if cached_stream_url := await redis.getex(cached_stream_url_key, ex=URL_CACHE_EXP):
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
    season: int = None,
    episode: int = None,
    user_data: schemas.UserData = Depends(get_user_data),
):
    response.headers.update(const.NO_CACHE_HEADERS)

    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    user_ip = await get_user_public_ip(request, user_data)
    redirect_status_code = 302
    cached_stream_url_key = "streaming_provider_" + crypto.get_text_hash(
        f"{user_ip}_{secret_str}_{info_hash}_{season}_{episode}",
        full_hash=True,
    )

    # Check for cached URL before any database operations
    if cached_stream_url := await get_cached_stream_url(
        request.app.state.redis, cached_stream_url_key
    ):
        if (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_debrid_streams
        ):
            cached_stream_url = encode_mediaflow_proxy_url(
                user_data.mediaflow_config.proxy_url,
                "/proxy/stream",
                cached_stream_url,
                query_params={"api_password": user_data.mediaflow_config.api_password},
            )
        return RedirectResponse(
            url=cached_stream_url,
            headers=response.headers,
            status_code=redirect_status_code,
        )

    # Fetch stream data after checking cache
    stream = await crud.get_stream_by_info_hash(info_hash)
    if not stream:
        raise HTTPException(status_code=400, detail="Stream not found.")

    magnet_link = await torrent.convert_info_hash_to_magnet(
        info_hash, stream.announce_list
    )

    episode_data = stream.get_episode(season, episode)
    filename = episode_data.filename if episode_data else stream.filename

    # Create a Redis lock to prevent multiple requests from initiating a download task.
    acquired, lock = await acquire_redis_lock(
        request.app.state.redis,
        f"{cached_stream_url_key}_locked",
        timeout=60,
        block=True,
    )
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many requests.")

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
        episode=episode,
        max_retries=1,
        retry_interval=0,
        stream=stream,
        torrent_name=stream.torrent_name,
    )

    try:
        if asyncio.iscoroutinefunction(get_video_url):
            video_url = await get_video_url(**kwargs)
        else:
            # video_url = get_video_url(**kwargs)  # Use this, In case of debugging api calls
            video_url = await asyncio.to_thread(get_video_url, **kwargs)

        # Cache the streaming URL for URL_CACHE_EXP
        await request.app.state.redis.set(
            cached_stream_url_key, video_url.encode("utf-8"), ex=URL_CACHE_EXP
        )
        if (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_debrid_streams
        ):
            video_url = encode_mediaflow_proxy_url(
                user_data.mediaflow_config.proxy_url,
                "/proxy/stream",
                video_url,
                query_params={"api_password": user_data.mediaflow_config.api_password},
            )

    except ProviderException as error:
        logging.error(
            "Exception occurred for %s: %s",
            info_hash,
            error.message,
            exc_info=True if error.video_file_name == "api_error.mp4" else False,
        )
        video_url = f"{settings.host_url}/static/exceptions/{error.video_file_name}"
        redirect_status_code = 307
    except Exception as e:
        logging.error("Exception occurred for %s: %s", info_hash, e, exc_info=True)
        video_url = f"{settings.host_url}/static/exceptions/api_error.mp4"
        redirect_status_code = 307

    # Release the lock
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
    response.headers.update(const.NO_CACHE_HEADERS)

    user_ip = get_user_public_ip(request, user_data)

    if not user_data.streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    kwargs = dict(user_data=user_data, user_ip=user_ip)

    if delete_all_watchlist_function := mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(
        user_data.streaming_provider.service
    ):
        try:
            if asyncio.iscoroutinefunction(delete_all_watchlist_function):
                await delete_all_watchlist_function(**kwargs)
            else:
                await asyncio.to_thread(delete_all_watchlist_function, **kwargs)
            video_url = f"{settings.host_url}/static/exceptions/watchlist_deleted.mp4"
        except ProviderException as error:
            logging.error(
                "Exception occurred while deleting watchlist: %s",
                error.message,
                exc_info=True,
            )
            video_url = f"{settings.host_url}/static/exceptions/{error.video_file_name}"
        except Exception as e:
            logging.error(
                "Exception occurred while deleting watchlist: %s", e, exc_info=True
            )
            video_url = f"{settings.host_url}/static/exceptions/api_error.mp4"
    else:
        raise HTTPException(
            status_code=400, detail="Provider does not support this action."
        )

    return RedirectResponse(url=video_url, headers=response.headers)


router.include_router(seedr_router, prefix="/seedr", tags=["seedr"])
router.include_router(realdebrid_router, prefix="/realdebrid", tags=["realdebrid"])
router.include_router(debridlink_router, prefix="/debridlink", tags=["debridlink"])
router.include_router(premiumize_router, prefix="/premiumize", tags=["premiumize"])
