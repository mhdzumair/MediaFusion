"""
Streaming playback routes - handles video streaming via debrid providers.
"""

import asyncio
import logging
from datetime import datetime
from os import path
from os.path import basename
from typing import Annotated

import pytz
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlmodel import select

from api.services.sync.manager import IntegrationManager

from db import crud, schemas
from db.models import User
from db.config import settings
from db.database import get_async_session_context, get_read_session
from db.models import (
    Media,
    MediaExternalID,
    PlaybackTracking,
    Stream,
    StreamFile,
    StreamMediaLink,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    WatchHistory,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import TorrentStreamData
from db.schemas.media import TelegramStreamData, UsenetStreamData
from streaming_providers import mapper
from streaming_providers.cache_helpers import store_cached_info_hashes
from streaming_providers.exceptions import ProviderException
from utils import const, crypto, torrent, wrappers
from utils.const import CONTENT_TYPE_HEADERS_MAPPING
from utils.lock import acquire_redis_lock, release_redis_lock
from utils.network import encode_mediaflow_proxy_url, get_user_data, get_user_public_ip
from utils.nzb_storage import generate_signed_nzb_url
from utils.telegram_bot import telegram_content_bot

# Seconds until when the Video URLs are cached
URL_CACHE_EXP = 3600

router = APIRouter()


def generate_cache_key(
    user_ip: str,
    secret_str: str,
    info_hash: str,
    season: int | None,
    episode: int | None,
) -> str:
    """
    Generates a cache key based on user IP, secret string, info hash, season, and episode.
    """
    return "streaming_provider_" + crypto.get_text_hash(
        f"{user_ip}_{secret_str}_{info_hash}_{season}_{episode}", full_hash=True
    )


async def get_cached_stream_url(cached_stream_url_key: str) -> str | None:
    """Get cached stream URL from Redis."""
    if cached_stream_url := await REDIS_ASYNC_CLIENT.getex(cached_stream_url_key, ex=URL_CACHE_EXP):
        return cached_stream_url.decode("utf-8")
    return None


async def get_cached_stream_url_and_redirect(
    cached_stream_url_key: str,
    user_data: schemas.UserData,
    response: Response,
    streaming_provider: schemas.StreamingProvider | None = None,
) -> RedirectResponse | None:
    """
    Checks for cached stream URL and returns a RedirectResponse if available.

    For Stremio/Kodi playback, uses per-provider use_mediaflow setting.

    Args:
        cached_stream_url_key: Redis cache key for the stream URL
        user_data: User's configuration data
        response: FastAPI response object for headers
        streaming_provider: Provider for per-provider MediaFlow check

    Returns:
        RedirectResponse if cached URL exists, None otherwise
    """
    if cached_stream_url := await get_cached_stream_url(cached_stream_url_key):
        # Check if MediaFlow is configured and per-provider setting is enabled
        should_proxy = (
            user_data.mediaflow_config
            and user_data.mediaflow_config.proxy_url
            and user_data.mediaflow_config.api_password
            and (streaming_provider is None or streaming_provider.use_mediaflow)
        )
        if should_proxy:
            response_headers = {}
            file_extension = path.splitext(cached_stream_url)[-1]
            content_type = CONTENT_TYPE_HEADERS_MAPPING.get(file_extension)
            if content_type:
                response_headers["Content-Type"] = content_type

            cached_stream_url = encode_mediaflow_proxy_url(
                user_data.mediaflow_config.proxy_url,
                "/proxy/stream",
                cached_stream_url,
                query_params={"api_password": user_data.mediaflow_config.api_password},
                response_headers=response_headers,
            )
        return RedirectResponse(url=cached_stream_url, headers=response.headers, status_code=302)
    return None


async def fetch_stream_or_404(info_hash: str) -> TorrentStreamData:
    """
    Fetches stream by info hash, raises a 404 error if not found.
    Returns TorrentStreamData (Pydantic model) to avoid lazy loading issues.
    """
    async for session in get_read_session():
        torrent_stream = await crud.get_stream_by_info_hash(session, info_hash, load_relations=True)
        if torrent_stream:
            # Convert to Pydantic model to detach from session
            return TorrentStreamData.from_db(torrent_stream)

    raise HTTPException(status_code=400, detail="Stream not found.")


async def get_or_create_video_url(
    stream: TorrentStreamData,
    streaming_provider: schemas.StreamingProvider,
    info_hash: str,
    season: int | None,
    episode: int | None,
    filename: str | None,
    user_ip: str,
    background_tasks: BackgroundTasks,
) -> str:
    """
    Retrieves or generates the video URL based on stream data and provider info.
    """
    # stream is TorrentStreamData (Pydantic model) with announce_list
    magnet_link = torrent.convert_info_hash_to_magnet(info_hash, stream.announce_list or [])
    episodes = stream.get_episodes(season, episode)

    # Get main file for fallback (largest video file)
    main_file = stream.get_main_file()

    if not filename:
        episode_data = episodes[0] if episodes else None
        filename = episode_data.filename if episode_data else (main_file.filename if main_file else None)
        filename = basename(filename) if filename else None
    else:
        episode_data = next((ep for ep in episodes if ep.filename == filename), None)

    file_index = episode_data.file_index if episode_data else (main_file.file_index if main_file else 0)

    get_video_url = mapper.GET_VIDEO_URL_FUNCTIONS.get(streaming_provider.service)
    kwargs = dict(
        info_hash=info_hash,
        magnet_link=magnet_link,
        streaming_provider=streaming_provider,
        filename=filename,
        file_index=file_index,
        user_ip=user_ip,
        season=season,
        episode=episode,
        max_retries=1,
        retry_interval=0,
        stream=stream,
        name=stream.name,
        background_tasks=background_tasks,
    )

    return await get_video_url(**kwargs)


async def cache_stream_url(cached_stream_url_key: str, video_url: str) -> None:
    """
    Caches the streaming URL in Redis for future use.
    """
    await REDIS_ASYNC_CLIENT.set(cached_stream_url_key, video_url.encode("utf-8"), ex=URL_CACHE_EXP)


def apply_mediaflow_proxy_if_needed(
    video_url: str,
    user_data: schemas.UserData,
    streaming_provider: schemas.StreamingProvider | None = None,
) -> str:
    """
    Applies mediaflow proxy to the video URL for Stremio/Kodi playback.

    Uses the per-provider use_mediaflow setting to determine if proxy should be applied.
    Each provider can independently enable/disable MediaFlow proxy.

    Args:
        video_url: The original video URL to proxy
        user_data: User's configuration data
        streaming_provider: The streaming provider to check for MediaFlow setting

    Returns:
        Proxied URL if MediaFlow should be applied, original URL otherwise
    """
    if not user_data.mediaflow_config:
        return video_url
    if not user_data.mediaflow_config.proxy_url:
        return video_url
    if not user_data.mediaflow_config.api_password:
        return video_url

    # Check per-provider MediaFlow setting (defaults to True if not set)
    if streaming_provider and not streaming_provider.use_mediaflow:
        return video_url

    response_headers = {}
    file_extension = path.splitext(video_url)[-1]
    content_type = CONTENT_TYPE_HEADERS_MAPPING.get(file_extension)
    if content_type:
        response_headers["Content-Type"] = content_type

    return encode_mediaflow_proxy_url(
        user_data.mediaflow_config.proxy_url,
        "/proxy/stream",
        video_url,
        query_params={"api_password": user_data.mediaflow_config.api_password},
        response_headers=response_headers,
    )


def handle_provider_exception(error: ProviderException, usage: str) -> str:
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


def handle_generic_exception(exception: Exception, info_hash: str) -> str:
    """
    Handles generic exceptions and logs them.
    """
    logging.error("Generic exception occurred for %s: %s", info_hash, exception, exc_info=True)
    return f"{settings.host_url}/static/exceptions/api_error.mp4"


async def track_playback(
    info_hash: str,
    meta_id: str | None,
    season: int | None,
    episode: int | None,
    provider_name: str | None,
    provider_service: str | None,
    user_id: int | None = None,
    profile_id: int | None = None,
    stream_type: str = "torrent",  # "torrent", "usenet", or "telegram"
) -> None:
    """
    Track playback in the background.
    For authenticated users: creates/updates PlaybackTracking entry.
    For anonymous users: increments playback_count on Stream.
    Also triggers scrobbling to external platforms via IntegrationManager
    (looks up integrations from DB by profile_id).

    Args:
        info_hash: For torrents, the info_hash. For usenet, the nzb_guid.
                   For telegram, the "chat_id:message_id" format.
        stream_type: "torrent", "usenet", or "telegram" to determine how to look up the stream.
    """
    media_id = None
    media_title = None
    media_type = None
    stream_id = None
    # External IDs for scrobbling
    external_ids: dict[str, str | int | None] = {}

    try:
        async with get_async_session_context() as session:
            now = datetime.now(pytz.UTC)

            # Look up stream based on type
            if stream_type == "usenet":
                # Get UsenetStream by nzb_guid
                usenet_result = await session.exec(
                    select(UsenetStream)
                    .options(
                        selectinload(UsenetStream.stream)
                        .selectinload(Stream.files)
                        .selectinload(StreamFile.media_links)
                    )
                    .where(UsenetStream.nzb_guid == info_hash)
                )
                us = usenet_result.first()
                if not us:
                    logging.warning(f"UsenetStream not found for nzb_guid: {info_hash}")
                    return
                stream_id = us.stream_id
                stream_obj = us.stream
            elif stream_type == "telegram":
                # Get TelegramStream by chat_id:message_id
                # Parse chat_id and message_id from info_hash format "chat_id:message_id"
                try:
                    chat_id, message_id_str = info_hash.split(":", 1)
                    message_id = int(message_id_str)
                except (ValueError, AttributeError):
                    logging.warning(f"Invalid Telegram identifier format: {info_hash}")
                    return

                telegram_result = await session.exec(
                    select(TelegramStream)
                    .options(
                        selectinload(TelegramStream.stream)
                        .selectinload(Stream.files)
                        .selectinload(StreamFile.media_links)
                    )
                    .where(
                        TelegramStream.chat_id == chat_id,
                        TelegramStream.message_id == message_id,
                    )
                )
                tg = telegram_result.first()
                if not tg:
                    logging.warning(f"TelegramStream not found for {info_hash}")
                    return
                stream_id = tg.stream_id
                stream_obj = tg.stream
            else:
                # Get TorrentStream by info_hash
                torrent_result = await session.exec(
                    select(TorrentStream)
                    .options(
                        selectinload(TorrentStream.stream)
                        .selectinload(Stream.files)
                        .selectinload(StreamFile.media_links)
                    )
                    .where(TorrentStream.info_hash == info_hash)
                )
                ts = torrent_result.first()
                if not ts:
                    logging.warning(f"TorrentStream not found for info_hash: {info_hash}")
                    return
                stream_id = ts.stream_id
                stream_obj = ts.stream

            # Resolve media_id: prefer StreamMediaLink (stream-level), fall back to FileMediaLink (file-level)
            if stream_id:
                sml_result = await session.exec(
                    select(StreamMediaLink.media_id)
                    .where(StreamMediaLink.stream_id == stream_id, StreamMediaLink.is_primary == True)
                    .limit(1)
                )
                media_id = sml_result.first()

            if not media_id and stream_obj and stream_obj.files:
                for f in stream_obj.files:
                    if f.media_links:
                        media_id = f.media_links[0].media_id
                        break

            if media_id:
                media = await session.get(Media, media_id)
                if media:
                    media_title = media.title
                    media_type = media.type

                    # Get ALL external IDs for scrobbling (IMDb, TMDb, TVDB, MAL)
                    ext_ids_result = await session.exec(
                        select(MediaExternalID).where(MediaExternalID.media_id == media_id)
                    )
                    for ext_id in ext_ids_result.all():
                        if ext_id.provider == "imdb":
                            external_ids["imdb"] = ext_id.external_id
                        elif ext_id.provider == "tmdb":
                            external_ids["tmdb"] = int(ext_id.external_id) if ext_id.external_id else None
                        elif ext_id.provider == "tvdb":
                            external_ids["tvdb"] = int(ext_id.external_id) if ext_id.external_id else None
                        elif ext_id.provider == "mal":
                            external_ids["mal"] = int(ext_id.external_id) if ext_id.external_id else None

            if user_id:
                if media_id:
                    # Track in PlaybackTracking table (requires media_id)
                    existing = await session.exec(
                        select(PlaybackTracking).where(
                            PlaybackTracking.user_id == user_id,
                            PlaybackTracking.stream_id == stream_id,
                            PlaybackTracking.season == season,
                            PlaybackTracking.episode == episode,
                        )
                    )
                    tracking = existing.first()

                    if tracking:
                        tracking.last_played_at = now
                        tracking.play_count += 1
                        tracking.provider_name = provider_name
                        tracking.provider_service = provider_service
                    else:
                        tracking = PlaybackTracking(
                            user_id=user_id,
                            profile_id=profile_id,
                            stream_id=stream_id,
                            media_id=media_id,
                            season=season,
                            episode=episode,
                            provider_name=provider_name,
                            provider_service=provider_service,
                            first_played_at=now,
                            last_played_at=now,
                            play_count=1,
                        )
                        session.add(tracking)

                    # Also update watch history
                    existing_history = await session.exec(
                        select(WatchHistory).where(
                            WatchHistory.user_id == user_id,
                            WatchHistory.media_id == media_id,
                            WatchHistory.season == season,
                            WatchHistory.episode == episode,
                        )
                    )
                    watch_entry = existing_history.first()

                    if watch_entry:
                        watch_entry.watched_at = now
                    elif profile_id:
                        watch_entry = WatchHistory(
                            user_id=user_id,
                            profile_id=profile_id,
                            media_id=media_id,
                            title=media_title,
                            media_type=media_type,
                            season=season,
                            episode=episode,
                            watched_at=now,
                        )
                        session.add(watch_entry)

                await session.commit()
            else:
                # Anonymous user - increment playback count on Stream
                stream = await session.get(Stream, stream_id)
                if stream:
                    stream.playback_count = (stream.playback_count or 0) + 1
                    session.add(stream)
                    await session.commit()
    except Exception as e:
        logging.warning(f"Failed to track playback for {info_hash}: {e}")

    # Scrobble to external platforms via IntegrationManager
    # Only for authenticated users with a profile and at least one external ID
    if profile_id and external_ids:
        await IntegrationManager.scrobble_playback_start(
            profile_id=profile_id,
            imdb_id=external_ids.get("imdb"),
            tmdb_id=external_ids.get("tmdb"),
            title=media_title or "",
            media_type=media_type or "movie",
            season=season,
            episode=episode,
        )


async def extract_user_info_from_secret(
    user_data: schemas.UserData,
) -> tuple[int | None, int | None]:
    """
    Extract user_id and profile_id from decrypted user_data.
    Returns (user_id, profile_id) or (None, None) for anonymous users.
    """
    return user_data.user_id, user_data.profile_id


# New URL format with provider_name for multi-debrid support
@router.head("/{secret_str}/playback/{provider_name}/{info_hash}")
@router.get("/{secret_str}/playback/{provider_name}/{info_hash}")
@router.head("/{secret_str}/playback/{provider_name}/{info_hash}/{filename}")
@router.get("/{secret_str}/playback/{provider_name}/{info_hash}/{filename}")
@router.head("/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}")
@router.get("/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}")
@router.head("/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}/{filename}")
@router.get("/{secret_str}/playback/{provider_name}/{info_hash}/{season}/{episode}/{filename}")
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def streaming_provider_endpoint(
    secret_str: str,
    provider_name: str,
    info_hash: str,
    response: Response,
    request: Request,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    season: int = None,
    episode: int = None,
    filename: str = None,
):
    """
    Handles streaming provider requests with multi-debrid support.
    Uses caching for performance and locking mechanisms to prevent duplicate tasks.
    Tracks playback for authenticated users and anonymous users separately.
    """
    response.headers.update(const.NO_CACHE_HEADERS)
    info_hash = info_hash.lower()

    # Get the specific provider by name (for multi-debrid support)
    streaming_provider = user_data.get_provider_by_name(provider_name)
    if not streaming_provider:
        # Fall back to primary provider if named provider not found
        streaming_provider = user_data.get_primary_provider()
        logging.debug(
            f"Provider '{provider_name}' not found, using primary: {streaming_provider.service if streaming_provider else 'None'}"
        )

    if not streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    user_ip = await get_user_public_ip(request, user_data)
    cached_stream_url_key = generate_cache_key(user_ip, secret_str, info_hash, season, episode)

    # Check for cached stream URL (pass streaming_provider for per-provider MediaFlow check)
    cached_stream_url = await get_cached_stream_url_and_redirect(
        cached_stream_url_key, user_data, response, streaming_provider
    )
    if cached_stream_url:
        return cached_stream_url

    # Fetch stream from DB
    stream = await fetch_stream_or_404(info_hash)

    # Acquire Redis lock to prevent duplicate download tasks
    acquired, lock = await acquire_redis_lock(f"{cached_stream_url_key}_locked", timeout=60, block=True)
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many requests.")

    redirect_status_code = 307

    try:
        video_url = await get_or_create_video_url(
            stream,
            streaming_provider,
            info_hash,
            season,
            episode,
            filename,
            user_ip,
            background_tasks,
        )

        await store_cached_info_hashes(streaming_provider, [info_hash])
        await cache_stream_url(cached_stream_url_key, video_url)
        video_url = apply_mediaflow_proxy_if_needed(video_url, user_data, streaming_provider)
        redirect_status_code = 302

        # Track playback in background using asyncio.create_task to preserve async context
        # (BackgroundTasks runs in thread pool which breaks SQLAlchemy's async greenlet)
        user_id, profile_id = await extract_user_info_from_secret(user_data)
        asyncio.create_task(
            track_playback(
                info_hash=info_hash,
                meta_id=stream.meta_id,
                season=season,
                episode=episode,
                provider_name=provider_name,
                provider_service=streaming_provider.service,
                user_id=user_id,
                profile_id=profile_id,
            )
        )
    except ProviderException as error:
        video_url = handle_provider_exception(error, info_hash)
    except Exception as e:
        video_url = handle_generic_exception(e, info_hash)
    finally:
        await release_redis_lock(lock)

    return RedirectResponse(url=video_url, headers=response.headers, status_code=redirect_status_code)


# =========================================================================
# Usenet/NZB Playback Endpoints
# =========================================================================


def generate_usenet_cache_key(
    user_ip: str,
    secret_str: str,
    nzb_guid: str,
    season: int | None,
    episode: int | None,
) -> str:
    """
    Generates a cache key for Usenet streams.
    """
    return "usenet_provider_" + crypto.get_text_hash(
        f"{user_ip}_{secret_str}_{nzb_guid}_{season}_{episode}", full_hash=True
    )


async def fetch_usenet_stream_or_404(nzb_guid: str) -> UsenetStreamData:
    """
    Fetches Usenet stream by NZB GUID, raises a 404 error if not found.
    """
    async for session in get_read_session():
        usenet_stream = await crud.get_usenet_stream_by_guid(session, nzb_guid, load_relations=True)
        if usenet_stream:
            return UsenetStreamData.from_db(usenet_stream)

    raise HTTPException(status_code=400, detail="Usenet stream not found.")


async def get_or_create_usenet_video_url(
    stream: UsenetStreamData,
    streaming_provider: schemas.StreamingProvider,
    nzb_guid: str,
    season: int | None,
    episode: int | None,
    filename: str | None,
    user_ip: str,
    background_tasks: BackgroundTasks,
) -> str:
    """
    Retrieves or generates the video URL for Usenet content.
    """
    # Get main file for fallback
    main_file = stream.get_main_file()

    if not filename:
        episode_data = stream.get_file_for_episode(season, episode) if season and episode else None
        filename = episode_data.filename if episode_data else (main_file.filename if main_file else None)
        filename = basename(filename) if filename else None

    get_video_url = mapper.USENET_GET_VIDEO_URL_FUNCTIONS.get(streaming_provider.service)
    if not get_video_url:
        raise ProviderException(
            f"Provider {streaming_provider.service} does not support Usenet",
            "provider_error.mp4",
        )

    # For file-imported NZBs (nzb_url is None), generate a signed download URL
    # so providers can fetch the NZB from our storage.
    if not stream.nzb_url:
        stream.nzb_url = generate_signed_nzb_url(nzb_guid)

    kwargs = dict(
        nzb_hash=nzb_guid,
        streaming_provider=streaming_provider,
        filename=filename,
        user_ip=user_ip,
        season=season,
        episode=episode,
        stream=stream,
        background_tasks=background_tasks,
    )

    return await get_video_url(**kwargs)


# Usenet playback endpoints
@router.head("/{secret_str}/usenet/{provider_name}/{nzb_guid}")
@router.get("/{secret_str}/usenet/{provider_name}/{nzb_guid}")
@router.head("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{filename}")
@router.get("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{filename}")
@router.head("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}")
@router.get("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}")
@router.head("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}/{filename}")
@router.get("/{secret_str}/usenet/{provider_name}/{nzb_guid}/{season}/{episode}/{filename}")
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def usenet_playback_endpoint(
    secret_str: str,
    provider_name: str,
    nzb_guid: str,
    response: Response,
    request: Request,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    season: int = None,
    episode: int = None,
    filename: str = None,
):
    """
    Handles Usenet/NZB playback requests.
    Similar to torrent playback but uses Usenet-specific providers.
    """
    response.headers.update(const.NO_CACHE_HEADERS)

    # Get the specific provider by name
    streaming_provider = user_data.get_provider_by_name(provider_name)
    if not streaming_provider:
        streaming_provider = user_data.get_primary_provider()
        logging.debug(
            f"Provider '{provider_name}' not found, using primary: {streaming_provider.service if streaming_provider else 'None'}"
        )

    if not streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    # Verify provider supports Usenet
    if streaming_provider.service not in mapper.USENET_CAPABLE_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider {streaming_provider.service} does not support Usenet streams.",
        )

    user_ip = await get_user_public_ip(request, user_data)
    cached_stream_url_key = generate_usenet_cache_key(user_ip, secret_str, nzb_guid, season, episode)

    # Check for cached stream URL
    cached_stream_url = await get_cached_stream_url_and_redirect(
        cached_stream_url_key, user_data, response, streaming_provider
    )
    if cached_stream_url:
        return cached_stream_url

    # Fetch Usenet stream from DB
    stream = await fetch_usenet_stream_or_404(nzb_guid)

    # Acquire Redis lock to prevent duplicate download tasks
    acquired, lock = await acquire_redis_lock(f"{cached_stream_url_key}_locked", timeout=60, block=True)
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many requests.")

    redirect_status_code = 307

    try:
        video_url = await get_or_create_usenet_video_url(
            stream,
            streaming_provider,
            nzb_guid,
            season,
            episode,
            filename,
            user_ip,
            background_tasks,
        )
        await cache_stream_url(cached_stream_url_key, video_url)
        video_url = apply_mediaflow_proxy_if_needed(video_url, user_data, streaming_provider)
        redirect_status_code = 302

        # Track playback in background
        user_id, profile_id = await extract_user_info_from_secret(user_data)
        asyncio.create_task(
            track_playback(
                info_hash=nzb_guid,  # Use NZB GUID as identifier
                meta_id=stream.meta_id,
                season=season,
                episode=episode,
                provider_name=provider_name,
                provider_service=streaming_provider.service,
                user_id=user_id,
                profile_id=profile_id,
                stream_type="usenet",  # Indicate this is a usenet stream
            )
        )
    except ProviderException as error:
        video_url = handle_provider_exception(error, nzb_guid)
    except Exception as e:
        video_url = handle_generic_exception(e, nzb_guid)
    finally:
        await release_redis_lock(lock)

    return RedirectResponse(url=video_url, headers=response.headers, status_code=redirect_status_code)


# =========================================================================
# Telegram Playback Endpoints
# =========================================================================


# Telegram playback endpoint - routes through MediaFlow Proxy with per-user forwarding
# ===================================================================================
# Architecture:
# 1. User 1 (contributor) forwards video to bot -> stored with file_id in TelegramStream
# 2. User 2 (viewer) requests playback -> endpoint uses sendVideo with file_id to send
#    the video to User 2's DM (file_id can be reused across chats)
# 3. User 2's MediaFlow (with their own Telegram session) streams from their DM
# ===================================================================================


async def _get_telegram_stream_by_chat_message(chat_id: str, message_id: int):
    """Fetch TelegramStream from database by chat_id and message_id."""
    async for session in get_read_session():
        telegram_stream = await crud.get_telegram_stream_by_chat_message(
            session, chat_id, message_id, load_relations=True
        )
        if telegram_stream:
            return telegram_stream
    return None


async def _get_or_create_user_forward(
    telegram_stream_id: int,
    file_id: str,
    user_id: int,
    stream_name: str | None = None,
):
    """
    Get existing forward or create one by sending video to user's DM using file_id.

    Uses Telegram's sendVideo API with the stored file_id to send the video
    to the user's DM. The file_id can be reused across chats.

    Args:
        telegram_stream_id: ID of the TelegramStream record
        file_id: Telegram file_id of the video
        user_id: MediaFusion user ID
        stream_name: Optional stream name for caption

    Returns:
        TelegramUserForward record with forwarded_chat_id and forwarded_message_id

    Raises:
        HTTPException if sending fails
    """

    # Check if forward already exists
    async with get_async_session_context() as session:
        existing = await crud.get_telegram_user_forward(session, telegram_stream_id, user_id)
        if existing:
            logging.debug(f"Found existing forward for stream {telegram_stream_id}, user {user_id}")
            return existing

    # Get user's Telegram ID from their profile
    async with get_async_session_context() as session:
        query = select(User).where(User.id == user_id)
        result = await session.exec(query)
        user = result.first()

        if not user or not user.telegram_user_id:
            raise HTTPException(
                status_code=400, detail="Link your Telegram account first. Send /login to the MediaFusion bot."
            )

        telegram_user_id = int(user.telegram_user_id)

    # Send the video to user's DM using file_id
    logging.info(f"Sending Telegram video to user {user_id} (tg:{telegram_user_id}) using file_id")

    result = await telegram_content_bot.send_video_to_user(
        chat_id=telegram_user_id,
        file_id=file_id,
        caption=f"🎬 {stream_name}" if stream_name else None,
    )

    if not result or "message_id" not in result:
        raise HTTPException(
            status_code=502,
            detail="Failed to send video to your Telegram. Make sure you've started the bot with /start.",
        )

    forwarded_message_id = result["message_id"]
    forwarded_chat_id = str(telegram_user_id)  # Bot DM chat_id is the user's Telegram ID

    # Store the forward record (handle race: concurrent HEAD/GET may both create)
    try:
        async with get_async_session_context() as session:
            forward = await crud.create_telegram_user_forward(
                session,
                telegram_stream_id=telegram_stream_id,
                user_id=user_id,
                telegram_user_id=telegram_user_id,
                forwarded_chat_id=forwarded_chat_id,
                forwarded_message_id=forwarded_message_id,
            )
            logging.info(
                f"Created forward record: stream {telegram_stream_id} -> user {user_id}, "
                f"new chat_id={forwarded_chat_id}, message_id={forwarded_message_id}"
            )
            return forward
    except IntegrityError:
        # Race: another request created the record first; fetch it
        async with get_async_session_context() as session:
            existing = await crud.get_telegram_user_forward(session, telegram_stream_id, user_id)
            if existing:
                logging.debug(f"Forward already exists (race): stream {telegram_stream_id}, user {user_id}")
                return existing
        raise


def _build_mediaflow_telegram_url(
    mediaflow_config,
    forward,
    stream: TelegramStream,
    transcode: bool = False,
) -> str:
    """
    Build a MediaFlow Proxy URL for Telegram streaming.

    Constructs the endpoint path with optional filename for content-type hints,
    and query params including optional transcode support.
    """
    query_params = {
        "api_password": mediaflow_config.api_password,
        "chat_id": forward.forwarded_chat_id,
        "message_id": str(forward.forwarded_message_id),
    }

    # Include file metadata as hints for MediaFlow
    if stream.file_id:
        query_params["file_id"] = stream.file_id
    if stream.size:
        query_params["file_size"] = str(stream.size)

    # Enable transcoding for browser playback (converts unsupported codecs to HLS)
    if transcode:
        query_params["transcode"] = "true"

    # Build endpoint path with optional filename for content-type detection
    endpoint = "/proxy/telegram/stream"
    if stream.file_name:
        # Use the filename path parameter for better content-type detection
        endpoint = f"/proxy/telegram/stream/{stream.file_name}"

    return encode_mediaflow_proxy_url(
        mediaflow_config.proxy_url,
        endpoint,
        destination_url=None,  # Telegram streams don't use destination_url
        query_params=query_params,
    )


# Telegram playback endpoints
@router.head("/{secret_str}/telegram/{chat_id}/{message_id}")
@router.get("/{secret_str}/telegram/{chat_id}/{message_id}")
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def telegram_playback_endpoint(
    secret_str: str,
    chat_id: str,
    message_id: int,
    response: Response,
    request: Request,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    transcode: bool = False,
):
    """
    Handle Telegram stream playback via MediaFlow Proxy with per-user forwarding.

    Flow:
    1. Require MediaFlow Proxy configuration
    2. Look up the TelegramStream from DB (contains file_id)
    3. Get or create a per-user forwarded copy (bot sends video using file_id to viewer's DM)
    4. Generate MediaFlow Telegram streaming URL with user's forwarded chat_id + message_id
    5. Redirect to MediaFlow

    User's MediaFlow must be configured with their own Telegram session (API ID, hash,
    session string) to access their DMs with the bot.

    Query params:
        transcode: Enable transcoding for browser playback (converts unsupported codecs to HLS).
    """
    response.headers.update(const.NO_CACHE_HEADERS)

    # 1. Require MediaFlow Proxy with Telegram support
    if not (
        user_data.mediaflow_config and user_data.mediaflow_config.proxy_url and user_data.mediaflow_config.api_password
    ):
        raise HTTPException(
            status_code=502,
            detail="MediaFlow Proxy with Telegram support is required to stream Telegram content. "
            "Configure MediaFlow in your profile settings with your Telegram session.",
        )

    # 2. Look up TelegramStream from DB
    stream = await _get_telegram_stream_by_chat_message(chat_id, message_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Telegram stream not found.")

    # Require file_id for sendVideo
    if not stream.file_id:
        raise HTTPException(status_code=400, detail="Telegram stream has no file_id. Cannot forward to user.")

    # 3. Get user info and create/retrieve per-user forward
    user_id, profile_id = await extract_user_info_from_secret(user_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required for Telegram streaming.")

    # Get stream name for caption
    stream_name = stream.stream.name if stream.stream else None

    forward = await _get_or_create_user_forward(
        telegram_stream_id=stream.id,
        file_id=stream.file_id,
        user_id=user_id,
        stream_name=stream_name,
    )

    # 4. Build MediaFlow Telegram stream URL
    video_url = _build_mediaflow_telegram_url(
        mediaflow_config=user_data.mediaflow_config,
        forward=forward,
        stream=stream,
        transcode=transcode,
    )

    # 5. Track playback in background
    stream_data = TelegramStreamData.from_db(stream)
    asyncio.create_task(
        track_playback(
            info_hash=f"{chat_id}:{message_id}",  # Use original chat_id:message_id as identifier
            meta_id=stream_data.meta_id,
            season=stream_data.season_number,
            episode=stream_data.episode_number,
            provider_name="telegram",
            provider_service="telegram",
            user_id=user_id,
            profile_id=profile_id,
            stream_type="telegram",
        )
    )

    logging.info(
        f"Telegram playback: user {user_id} -> forwarded {forward.forwarded_chat_id}/{forward.forwarded_message_id}"
    )
    return RedirectResponse(url=video_url, headers=response.headers, status_code=302)


# Alternative Telegram endpoint using stream ID (preferred for bot contributions)
@router.head("/{secret_str}/telegram/stream/{telegram_stream_id}")
@router.get("/{secret_str}/telegram/stream/{telegram_stream_id}")
@wrappers.exclude_rate_limit
@wrappers.auth_required
async def telegram_playback_by_id_endpoint(
    secret_str: str,
    telegram_stream_id: int,
    response: Response,
    request: Request,
    user_data: Annotated[schemas.UserData, Depends(get_user_data)],
    background_tasks: BackgroundTasks,
    transcode: bool = False,
):
    """
    Handle Telegram stream playback by stream ID.

    This endpoint is preferred for bot-contributed content where chat_id/message_id
    may be placeholder values. Uses the stream ID to look up the TelegramStream directly.

    Flow is identical to the chat_id/message_id endpoint.

    Query params:
        transcode: Enable transcoding for browser playback (converts unsupported codecs to HLS).
    """
    response.headers.update(const.NO_CACHE_HEADERS)

    # 1. Require MediaFlow Proxy with Telegram support
    if not (
        user_data.mediaflow_config and user_data.mediaflow_config.proxy_url and user_data.mediaflow_config.api_password
    ):
        raise HTTPException(
            status_code=502,
            detail="MediaFlow Proxy with Telegram support is required to stream Telegram content. "
            "Configure MediaFlow in your profile settings with your Telegram session.",
        )

    # 2. Look up TelegramStream by ID
    async with get_async_session_context() as session:
        stream = await session.get(TelegramStream, telegram_stream_id)

    if not stream:
        raise HTTPException(status_code=404, detail="Telegram stream not found.")

    # Require file_id for sendVideo
    if not stream.file_id:
        raise HTTPException(status_code=400, detail="Telegram stream has no file_id. Cannot forward to user.")

    # 3. Get user info and create/retrieve per-user forward
    user_id, profile_id = await extract_user_info_from_secret(user_data)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required for Telegram streaming.")

    # Get stream name for caption
    stream_name = stream.stream.name if stream.stream else None

    forward = await _get_or_create_user_forward(
        telegram_stream_id=stream.id,
        file_id=stream.file_id,
        user_id=user_id,
        stream_name=stream_name,
    )

    # 4. Build MediaFlow Telegram stream URL
    video_url = _build_mediaflow_telegram_url(
        mediaflow_config=user_data.mediaflow_config,
        forward=forward,
        stream=stream,
        transcode=transcode,
    )

    # 5. Track playback in background
    stream_data = TelegramStreamData.from_db(stream)
    asyncio.create_task(
        track_playback(
            info_hash=f"tg_stream:{telegram_stream_id}",
            meta_id=stream_data.meta_id,
            season=stream_data.season_number,
            episode=stream_data.episode_number,
            provider_name="telegram",
            provider_service="telegram",
            user_id=user_id,
            profile_id=profile_id,
            stream_type="telegram",
        )
    )

    logging.info(f"Telegram playback (by ID): user {user_id} -> stream {telegram_stream_id}")
    return RedirectResponse(url=video_url, headers=response.headers, status_code=302)


@router.get("/{secret_str}/delete_all_watchlist")
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

    streaming_provider = user_data.get_primary_provider()
    if not streaming_provider:
        raise HTTPException(status_code=400, detail="No streaming provider set.")

    user_ip = await get_user_public_ip(request, user_data)

    # Get the delete watchlist function for the user's streaming provider
    delete_all_watchlist_function = mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(streaming_provider.service)

    if not delete_all_watchlist_function:
        raise HTTPException(status_code=400, detail="Provider does not support this action.")

    try:
        await delete_all_watchlist_function(streaming_provider=streaming_provider, user_ip=user_ip)
        video_url = f"{settings.host_url}/static/exceptions/watchlist_deleted.mp4"

    except ProviderException as error:
        # Handle provider-specific exceptions
        video_url = handle_provider_exception(error, "delete_watchlist")

    except Exception as e:
        # Handle generic exceptions
        video_url = handle_generic_exception(e, "delete_watchlist")

    return RedirectResponse(url=video_url, headers=response.headers)
