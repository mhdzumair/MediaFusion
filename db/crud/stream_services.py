"""
Stream service operations for Stremio API.

These functions query streams and format them for Stremio addon responses.
They use:
- db/crud/streams.py for raw database queries
- db/schemas/media.py TorrentStreamData.from_db() for model conversion
- utils/parser.py parse_stream_data() for Stremio formatting
"""

import asyncio
import json
import logging
import time

from fastapi import BackgroundTasks
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings
from db.crud.media import get_media_by_external_id
from db.database import get_read_session_context
from db.enums import MediaType
from db.models import (
    AceStreamStream,
    FileMediaLink,
    HTTPStream,
    Media,
    Stream,
    StreamFile,
    StreamMediaLink,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    YouTubeStream,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import (
    Stream as StremioStream,
)
from db.schemas import (
    TorrentStreamData,
    UserData,
)
from db.schemas.media import TelegramStreamData, UsenetStreamData
from utils.network import encode_mediaflow_acestream_url

# Providers that support Usenet content - defined here to avoid circular import
# This should be kept in sync with streaming_providers.mapper.USENET_CAPABLE_PROVIDERS
USENET_CAPABLE_PROVIDERS = {"torbox", "debrider", "sabnzbd", "nzbget", "nzbdav", "easynews"}

# Redis cache settings for raw stream data
STREAM_CACHE_TTL = 1800  # 30 minutes
STREAM_CACHE_PREFIX = "stream_data:"

logger = logging.getLogger(__name__)


def _get_visibility_filter(user_id: int | None = None):
    """Get visibility filter for streams.

    Returns streams that are either:
    - Public (is_public=True)
    - Owned by the current user (uploader_user_id matches)
    """
    if user_id:
        return or_(Stream.is_public.is_(True), Stream.uploader_user_id == user_id)
    return Stream.is_public.is_(True)


def _has_mediaflow_config(user_data: UserData) -> bool:
    """Check if user has MediaFlow proxy configured."""
    return bool(
        user_data.mediaflow_config and user_data.mediaflow_config.proxy_url and user_data.mediaflow_config.api_password
    )


def _format_acestream_streams(
    acestream_streams: list[AceStreamStream],
    user_data: UserData,
    user_ip: str | None = None,
) -> list[StremioStream]:
    """Format AceStream streams for Stremio using MediaFlow proxy URLs.

    AceStream playback requires MediaFlow proxy. Each stream is converted to a
    Stremio stream with the appropriate MediaFlow proxy URL.
    """
    if not acestream_streams or not _has_mediaflow_config(user_data):
        return []

    mediaflow_config = user_data.mediaflow_config
    formatted = []
    for ace_stream in acestream_streams:
        stream = ace_stream.stream
        try:
            url = encode_mediaflow_acestream_url(
                mediaflow_proxy_url=mediaflow_config.proxy_url,
                content_id=ace_stream.content_id,
                info_hash=ace_stream.info_hash,
                api_password=mediaflow_config.api_password,
            )
        except ValueError:
            logger.warning(
                f"Skipping AceStream stream_id={ace_stream.stream_id}: missing both content_id and info_hash"
            )
            continue

        # Build description with available metadata
        desc_parts = ["📡 AceStream"]
        if stream.resolution:
            desc_parts.append(stream.resolution)
        if stream.quality:
            desc_parts.append(stream.quality)
        if stream.codec:
            desc_parts.append(stream.codec)
        if stream.source and stream.source != "acestream":
            desc_parts.append(f"| {stream.source}")

        formatted.append(
            StremioStream(
                name=f"{settings.addon_name}\n{stream.name}",
                description=" ".join(desc_parts),
                url=url,
            )
        )

    return formatted


def _combine_streams_by_type(
    user_data: UserData,
    stream_groups: dict[str, list[StremioStream]],
) -> list[StremioStream]:
    """Combine stream groups based on user's type grouping and ordering preferences.

    For "separate" mode: concatenate groups in the user's preferred stream_type_order.
    For "mixed" mode: interleave streams round-robin from each group in the preferred order.
    Finally, apply the total max_streams cap.
    """
    type_order = user_data.stream_type_order

    if user_data.stream_type_grouping == "mixed":
        # Interleave round-robin from each type in preferred order
        ordered_lists = [stream_groups.get(t, []) for t in type_order]
        # Filter out empty lists
        ordered_lists = [lst for lst in ordered_lists if lst]
        combined: list[StremioStream] = []
        iterators = [iter(lst) for lst in ordered_lists]
        while iterators:
            exhausted = []
            for i, it in enumerate(iterators):
                val = next(it, None)
                if val is not None:
                    combined.append(val)
                else:
                    exhausted.append(i)
            # Remove exhausted iterators in reverse to preserve indices
            for i in reversed(exhausted):
                iterators.pop(i)
    else:
        # "separate" mode: concatenate in user's preferred type order
        combined = []
        for stream_type in type_order:
            combined.extend(stream_groups.get(stream_type, []))

    # Apply total stream cap
    return combined[: user_data.max_streams]


async def invalidate_media_stream_cache(media_id: int) -> None:
    """Delete all cached stream data for a media.

    Called when streams are added to or removed from a media entry.
    Clears both movie and series cache keys for the given media_id.
    """
    try:
        # Delete movie cache key directly
        movie_key = f"{STREAM_CACHE_PREFIX}movie:{media_id}"
        await REDIS_ASYNC_CLIENT.delete(movie_key)

        # For series, scan and delete all season:episode combos
        pattern = f"{STREAM_CACHE_PREFIX}series:{media_id}:*"
        keys = []
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            await REDIS_ASYNC_CLIENT.delete(*keys)
    except Exception as e:
        logger.warning(f"Error invalidating stream cache for media_id={media_id}: {e}")


async def _fetch_movie_raw_streams(media_id: int, visibility_filter) -> dict:
    """Fetch all raw stream data for a movie from DB (read replica) and cache as JSON.

    Returns a dict with serialized stream data lists keyed by type.
    """
    async with get_read_session_context() as session:
        # Query torrent streams
        torrent_query = (
            select(TorrentStream)
            .join(Stream, Stream.id == TorrentStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(TorrentStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
                selectinload(TorrentStream.trackers),
            )
            .limit(500)
        )
        result = await session.exec(torrent_query)
        torrents = result.unique().all()

        # We need a Media object for from_db; fetch it
        media = await session.get(Media, media_id)

        # Exclude binary fields that can't be JSON-serialized (torrent_file, nzb_content)
        _torrent_exclude = {"torrent_file"}
        torrent_data = [
            TorrentStreamData.from_db(t, t.stream, media).model_dump(mode="json", exclude=_torrent_exclude)
            for t in torrents
        ]

        # Query usenet streams
        usenet_query = (
            select(UsenetStream)
            .join(Stream, Stream.id == UsenetStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(UsenetStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
            )
            .limit(200)
        )
        usenet_result = await session.exec(usenet_query)
        usenet_streams = usenet_result.unique().all()
        _usenet_exclude = {"nzb_content"}
        usenet_data = [
            UsenetStreamData.from_db(u).model_dump(mode="json", exclude=_usenet_exclude) for u in usenet_streams
        ]

        # Query telegram streams
        telegram_query = (
            select(TelegramStream)
            .join(Stream, Stream.id == TelegramStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(TelegramStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
            )
            .limit(100)
        )
        telegram_result = await session.exec(telegram_query)
        telegram_streams = telegram_result.unique().all()
        telegram_data = [TelegramStreamData.from_db(tg).model_dump(mode="json") for tg in telegram_streams]

        # Query HTTP streams
        http_query = (
            select(HTTPStream)
            .join(Stream, Stream.id == HTTPStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(HTTPStream.stream).options(
                    selectinload(Stream.languages),
                ),
            )
            .limit(100)
        )
        http_result = await session.exec(http_query)
        http_streams = http_result.unique().all()
        http_data = [{"name": hs.stream.name, "source": hs.stream.source, "url": hs.url} for hs in http_streams]

        # Query AceStream streams
        acestream_query = (
            select(AceStreamStream)
            .join(Stream, Stream.id == AceStreamStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(AceStreamStream.stream).options(
                    selectinload(Stream.languages),
                ),
            )
            .limit(100)
        )
        acestream_result = await session.exec(acestream_query)
        acestream_streams = acestream_result.unique().all()
        acestream_data = [
            {
                "content_id": ace.content_id,
                "info_hash": ace.info_hash,
                "stream_name": ace.stream.name,
                "resolution": ace.stream.resolution,
                "quality": ace.stream.quality,
                "codec": ace.stream.codec,
                "source": ace.stream.source,
                "languages": [lang.lang for lang in (ace.stream.languages or [])],
            }
            for ace in acestream_streams
        ]

    return {
        "torrents": torrent_data,
        "usenet": usenet_data,
        "telegram": telegram_data,
        "http": http_data,
        "acestream": acestream_data,
    }


async def _get_cached_movie_streams(media_id: int, visibility_filter) -> dict:
    """Get raw movie stream data with Redis caching."""
    cache_key = f"{STREAM_CACHE_PREFIX}movie:{media_id}"

    # Try Redis cache first
    cached = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached:
        logger.debug(f"Stream cache HIT for movie media_id={media_id}")
        return json.loads(cached)

    logger.debug(f"Stream cache MISS for movie media_id={media_id}")
    t0 = time.monotonic()
    data = await _fetch_movie_raw_streams(media_id, visibility_filter)
    elapsed = time.monotonic() - t0
    logger.info(f"DB fetch for movie media_id={media_id} took {elapsed:.3f}s")

    # Store in Redis
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(data), ex=STREAM_CACHE_TTL)
    return data


async def _fetch_series_raw_streams(media_id: int, season: int, episode: int, visibility_filter) -> dict:
    """Fetch all raw stream data for a series episode from DB (read replica)."""
    async with get_read_session_context() as session:
        media = await session.get(Media, media_id)

        # Torrent streams
        torrent_query = (
            select(TorrentStream)
            .join(Stream, Stream.id == TorrentStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media_id,
                FileMediaLink.season_number == season,
                FileMediaLink.episode_number == episode,
            )
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(TorrentStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
                selectinload(TorrentStream.trackers),
            )
            .limit(500)
        )
        result = await session.exec(torrent_query)
        torrents = result.unique().all()
        _torrent_exclude = {"torrent_file"}
        torrent_data = [
            TorrentStreamData.from_db(t, t.stream, media).model_dump(mode="json", exclude=_torrent_exclude)
            for t in torrents
        ]

        # Usenet streams
        usenet_query = (
            select(UsenetStream)
            .join(Stream, Stream.id == UsenetStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media_id,
                FileMediaLink.season_number == season,
                FileMediaLink.episode_number == episode,
            )
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(UsenetStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
            )
            .limit(200)
        )
        usenet_result = await session.exec(usenet_query)
        usenet_streams = usenet_result.unique().all()
        _usenet_exclude = {"nzb_content"}
        usenet_data = [
            UsenetStreamData.from_db(u).model_dump(mode="json", exclude=_usenet_exclude) for u in usenet_streams
        ]

        # Telegram streams
        telegram_query = (
            select(TelegramStream)
            .join(Stream, Stream.id == TelegramStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media_id,
                FileMediaLink.season_number == season,
                FileMediaLink.episode_number == episode,
            )
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(TelegramStream.stream).options(
                    selectinload(Stream.languages),
                    selectinload(Stream.audio_formats),
                    selectinload(Stream.channels),
                    selectinload(Stream.hdr_formats),
                    selectinload(Stream.files).options(selectinload(StreamFile.media_links)),
                ),
            )
            .limit(100)
        )
        telegram_result = await session.exec(telegram_query)
        telegram_streams = telegram_result.unique().all()
        telegram_data = [TelegramStreamData.from_db(tg).model_dump(mode="json") for tg in telegram_streams]

        # HTTP streams
        http_query = (
            select(HTTPStream)
            .join(Stream, Stream.id == HTTPStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media_id,
                FileMediaLink.season_number == season,
                FileMediaLink.episode_number == episode,
            )
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(HTTPStream.stream).options(
                    selectinload(Stream.languages),
                ),
            )
            .limit(100)
        )
        http_result = await session.exec(http_query)
        http_streams = http_result.unique().all()
        http_data = [{"name": hs.stream.name, "source": hs.stream.source, "url": hs.url} for hs in http_streams]

        # AceStream streams (uses StreamMediaLink, not FileMediaLink)
        acestream_query = (
            select(AceStreamStream)
            .join(Stream, Stream.id == AceStreamStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media_id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .where(visibility_filter)
            .options(
                joinedload(AceStreamStream.stream).options(
                    selectinload(Stream.languages),
                ),
            )
            .limit(100)
        )
        acestream_result = await session.exec(acestream_query)
        acestream_streams = acestream_result.unique().all()
        acestream_data = [
            {
                "content_id": ace.content_id,
                "info_hash": ace.info_hash,
                "stream_name": ace.stream.name,
                "resolution": ace.stream.resolution,
                "quality": ace.stream.quality,
                "codec": ace.stream.codec,
                "source": ace.stream.source,
                "languages": [lang.lang for lang in (ace.stream.languages or [])],
            }
            for ace in acestream_streams
        ]

    return {
        "torrents": torrent_data,
        "usenet": usenet_data,
        "telegram": telegram_data,
        "http": http_data,
        "acestream": acestream_data,
    }


async def _get_cached_series_streams(media_id: int, season: int, episode: int, visibility_filter) -> dict:
    """Get raw series stream data with Redis caching."""
    cache_key = f"{STREAM_CACHE_PREFIX}series:{media_id}:{season}:{episode}"

    cached = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached:
        logger.debug(f"Stream cache HIT for series media_id={media_id} S{season}E{episode}")
        return json.loads(cached)

    logger.debug(f"Stream cache MISS for series media_id={media_id} S{season}E{episode}")
    t0 = time.monotonic()
    data = await _fetch_series_raw_streams(media_id, season, episode, visibility_filter)
    elapsed = time.monotonic() - t0
    logger.info(f"DB fetch for series media_id={media_id} S{season}E{episode} took {elapsed:.3f}s")

    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(data), ex=STREAM_CACHE_TTL)
    return data


def _deserialize_http_streams(http_data: list[dict]) -> list[StremioStream]:
    """Deserialize cached HTTP stream data into Stremio streams."""
    return [
        StremioStream(
            name=f"{settings.addon_name}\n{h['name']}",
            description=f"🎬 {h['source']}" if h.get("source") else "🎬 Direct",
            url=h["url"],
        )
        for h in http_data
    ]


def _deserialize_http_streams_series(http_data: list[dict], season: int, episode: int) -> list[StremioStream]:
    """Deserialize cached HTTP stream data for series into Stremio streams."""
    return [
        StremioStream(
            name=f"{settings.addon_name}\n{h['name']}",
            description=f"📺 S{season}E{episode} | {h['source']}" if h.get("source") else f"📺 S{season}E{episode}",
            url=h["url"],
        )
        for h in http_data
    ]


def _deserialize_acestream_streams(
    acestream_data: list[dict], user_data: UserData, user_ip: str | None = None
) -> list[StremioStream]:
    """Deserialize cached AceStream data into Stremio streams."""
    if not acestream_data or not _has_mediaflow_config(user_data):
        return []

    mediaflow_config = user_data.mediaflow_config
    formatted = []
    for ace in acestream_data:
        try:
            url = encode_mediaflow_acestream_url(
                mediaflow_proxy_url=mediaflow_config.proxy_url,
                content_id=ace.get("content_id"),
                info_hash=ace.get("info_hash"),
                api_password=mediaflow_config.api_password,
            )
        except ValueError:
            continue

        desc_parts = ["📡 AceStream"]
        if ace.get("resolution"):
            desc_parts.append(ace["resolution"])
        if ace.get("quality"):
            desc_parts.append(ace["quality"])
        if ace.get("codec"):
            desc_parts.append(ace["codec"])
        if ace.get("source") and ace["source"] != "acestream":
            desc_parts.append(f"| {ace['source']}")

        formatted.append(
            StremioStream(
                name=f"{settings.addon_name}\n{ace['stream_name']}",
                description=" ".join(desc_parts),
                url=url,
            )
        )
    return formatted


async def get_movie_streams(
    session: AsyncSession,
    video_id: str,
    user_data: UserData,
    secret_str: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
    user_id: int | None = None,
) -> list[StremioStream]:
    """
    Get formatted streams for a movie.

    Uses Redis caching for raw stream data (user-independent) and applies
    per-user filtering/sorting/debrid-cache-checking on top.
    Parallelizes parse_stream_data calls across stream types.
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    # Resolve video_id to media
    media = await get_media_by_external_id(session, video_id, MediaType.MOVIE)
    if not media:
        logger.warning(f"Movie not found for video_id: {video_id}")
        return []

    visibility_filter = _get_visibility_filter(user_id)

    # Get cached or fresh raw stream data
    raw_data = await _get_cached_movie_streams(media.id, visibility_filter)

    # Deserialize stream data objects from cache
    stream_data_list = [TorrentStreamData.model_validate(t) for t in raw_data["torrents"]]

    # Check user flags to determine which stream types to include
    has_usenet_provider = any(sp.service in USENET_CAPABLE_PROVIDERS for sp in user_data.get_active_providers())
    usenet_stream_data_list = (
        [UsenetStreamData.model_validate(u) for u in raw_data["usenet"]]
        if user_data.enable_usenet_streams and has_usenet_provider and raw_data["usenet"]
        else []
    )

    show_telegram = (
        user_data.enable_telegram_streams
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_url
        and user_data.mediaflow_config.api_password
    )
    telegram_stream_data_list = (
        [TelegramStreamData.model_validate(tg) for tg in raw_data["telegram"]]
        if show_telegram and raw_data["telegram"]
        else []
    )

    # Deserialize HTTP and AceStream directly to Stremio format
    formatted_http_streams = _deserialize_http_streams(raw_data["http"])
    formatted_acestream_streams = (
        _deserialize_acestream_streams(raw_data["acestream"], user_data, user_ip)
        if user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
        else []
    )

    # Apply disabled content type filtering
    disabled = set(settings.disabled_content_types)
    if "torrent" in disabled or "magnet" in disabled:
        stream_data_list = []
    if "nzb" in disabled:
        usenet_stream_data_list = []
    if "telegram" in disabled:
        telegram_stream_data_list = []
    if "iptv" in disabled or "http" in disabled:
        formatted_http_streams = []
    if "acestream" in disabled:
        formatted_acestream_streams = []

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not formatted_http_streams
        and not formatted_acestream_streams
    ):
        return []

    # Parallelize parse_stream_data calls across stream types
    coros = []
    coro_keys = []

    if stream_data_list:
        coros.append(
            parse_stream_data(
                streams=stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                user_ip=user_ip,
                is_series=False,
            )
        )
        coro_keys.append("torrent")

    if usenet_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=usenet_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                user_ip=user_ip,
                is_series=False,
                is_usenet=True,
            )
        )
        coro_keys.append("usenet")

    if telegram_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=telegram_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                user_ip=user_ip,
                is_series=False,
                is_telegram=True,
            )
        )
        coro_keys.append("telegram")

    # Run all parse_stream_data calls in parallel
    results = await asyncio.gather(*coros) if coros else []

    # Map results back to stream groups
    stream_groups: dict[str, list[StremioStream]] = {
        "torrent": [],
        "usenet": [],
        "telegram": [],
        "http": formatted_http_streams,
        "acestream": formatted_acestream_streams,
    }
    for key, result in zip(coro_keys, results):
        stream_groups[key] = result

    return _combine_streams_by_type(user_data, stream_groups)


async def get_series_streams(
    session: AsyncSession,
    video_id: str,
    season: int,
    episode: int,
    user_data: UserData,
    secret_str: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
    user_id: int | None = None,
) -> list[StremioStream]:
    """
    Get formatted streams for a series episode.

    Uses Redis caching for raw stream data (user-independent) and applies
    per-user filtering/sorting/debrid-cache-checking on top.
    Parallelizes parse_stream_data calls across stream types.
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    # Resolve video_id to media
    media = await get_media_by_external_id(session, video_id, MediaType.SERIES)
    if not media:
        logger.warning(f"Series not found for video_id: {video_id}")
        return []

    visibility_filter = _get_visibility_filter(user_id)

    # Get cached or fresh raw stream data
    raw_data = await _get_cached_series_streams(media.id, season, episode, visibility_filter)

    # Deserialize stream data objects from cache
    stream_data_list = [TorrentStreamData.model_validate(t) for t in raw_data["torrents"]]

    has_usenet_provider = any(sp.service in USENET_CAPABLE_PROVIDERS for sp in user_data.get_active_providers())
    usenet_stream_data_list = (
        [UsenetStreamData.model_validate(u) for u in raw_data["usenet"]]
        if user_data.enable_usenet_streams and has_usenet_provider and raw_data["usenet"]
        else []
    )

    show_telegram = (
        user_data.enable_telegram_streams
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_url
        and user_data.mediaflow_config.api_password
    )
    telegram_stream_data_list = (
        [TelegramStreamData.model_validate(tg) for tg in raw_data["telegram"]]
        if show_telegram and raw_data["telegram"]
        else []
    )

    formatted_http_streams = _deserialize_http_streams_series(raw_data["http"], season, episode)
    formatted_acestream_streams = (
        _deserialize_acestream_streams(raw_data["acestream"], user_data, user_ip)
        if user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
        else []
    )

    # Apply disabled content type filtering
    disabled = set(settings.disabled_content_types)
    if "torrent" in disabled or "magnet" in disabled:
        stream_data_list = []
    if "nzb" in disabled:
        usenet_stream_data_list = []
    if "telegram" in disabled:
        telegram_stream_data_list = []
    if "iptv" in disabled or "http" in disabled:
        formatted_http_streams = []
    if "acestream" in disabled:
        formatted_acestream_streams = []

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not formatted_http_streams
        and not formatted_acestream_streams
    ):
        return []

    # Parallelize parse_stream_data calls across stream types
    coros = []
    coro_keys = []

    if stream_data_list:
        coros.append(
            parse_stream_data(
                streams=stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                season=season,
                episode=episode,
                user_ip=user_ip,
                is_series=True,
            )
        )
        coro_keys.append("torrent")

    if usenet_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=usenet_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                season=season,
                episode=episode,
                user_ip=user_ip,
                is_series=True,
                is_usenet=True,
            )
        )
        coro_keys.append("usenet")

    if telegram_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=telegram_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                season=season,
                episode=episode,
                user_ip=user_ip,
                is_series=True,
                is_telegram=True,
            )
        )
        coro_keys.append("telegram")

    results = await asyncio.gather(*coros) if coros else []

    stream_groups: dict[str, list[StremioStream]] = {
        "torrent": [],
        "usenet": [],
        "telegram": [],
        "http": formatted_http_streams,
        "acestream": formatted_acestream_streams,
    }
    for key, result in zip(coro_keys, results):
        stream_groups[key] = result

    return _combine_streams_by_type(user_data, stream_groups)


async def get_tv_streams_formatted(
    session: AsyncSession,
    video_id: str,
    namespace: str | None,
    user_data: UserData,
) -> list[StremioStream]:
    """
    Get formatted streams for a TV channel.

    TV channels use HTTPStream or YouTubeStream, not TorrentStream.
    """
    # Get media by external_id
    media = await get_media_by_external_id(session, video_id, MediaType.TV)
    if not media:
        logger.warning(f"TV channel not found for video_id: {video_id}")
        return []

    disabled = set(settings.disabled_content_types)
    formatted_streams = []

    # Query HTTP streams (skip if iptv/http disabled)
    if "iptv" not in disabled and "http" not in disabled:
        http_query = (
            select(HTTPStream)
            .join(Stream, Stream.id == HTTPStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .options(joinedload(HTTPStream.stream))
            .limit(100)
        )

        result = await session.exec(http_query)
        http_streams = result.unique().all()

        for http_stream in http_streams:
            stream = http_stream.stream
            formatted_streams.append(
                StremioStream(
                    name=f"{settings.addon_name}\n{stream.name}",
                    description=f"📺 {stream.source}" if stream.source else "📺 Live",
                    url=http_stream.url,
                )
            )

    # Query YouTube streams (skip if youtube disabled)
    if "youtube" not in disabled:
        yt_query = (
            select(YouTubeStream)
            .join(Stream, Stream.id == YouTubeStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .options(joinedload(YouTubeStream.stream))
            .limit(100)
        )

        yt_result = await session.exec(yt_query)
        yt_streams = yt_result.unique().all()

        for yt_stream in yt_streams:
            stream = yt_stream.stream
            formatted_streams.append(
                StremioStream(
                    name=f"{settings.addon_name} YouTube\n{stream.name}",
                    description="▶️ YouTube Stream",
                    externalUrl=f"https://www.youtube.com/watch?v={yt_stream.video_id}",
                )
            )

    # Query AceStream streams (skip if acestream disabled; requires enable_acestream_streams AND MediaFlow config)
    if "acestream" not in disabled and user_data.enable_acestream_streams and _has_mediaflow_config(user_data):
        acestream_query = (
            select(AceStreamStream)
            .join(Stream, Stream.id == AceStreamStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
            .options(
                joinedload(AceStreamStream.stream).options(
                    selectinload(Stream.languages),
                ),
            )
            .limit(100)
        )

        acestream_result = await session.exec(acestream_query)
        acestream_streams = acestream_result.unique().all()

        formatted_streams.extend(_format_acestream_streams(acestream_streams, user_data))

    return formatted_streams


async def get_event_streams(
    video_id: str,
    user_data: UserData,
) -> list[StremioStream]:
    """
    Get formatted streams for an event (e.g., sports).

    Events are typically live streams fetched dynamically.
    This is a placeholder for future implementation.
    """
    # TODO: Implement event stream fetching
    # Events might be stored differently or fetched from external APIs
    return []
