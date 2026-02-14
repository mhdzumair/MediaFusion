"""
Stream service operations for Stremio API.

These functions query streams and format them for Stremio addon responses.
They use:
- db/crud/streams.py for raw database queries
- db/schemas/media.py TorrentStreamData.from_db() for model conversion
- utils/parser.py parse_stream_data() for Stremio formatting
"""

import logging

from fastapi import BackgroundTasks
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings
from db.crud.media import get_media_by_external_id
from db.enums import MediaType
from db.models import (
    AceStreamStream,
    FileMediaLink,
    HTTPStream,
    Stream,
    StreamFile,
    StreamMediaLink,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    YouTubeStream,
)
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
USENET_CAPABLE_PROVIDERS = {"torbox", "debrider", "sabnzbd", "nzbget", "easynews"}

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

    Query flow:
    1. Resolve video_id to media
    2. Query torrent and HTTP streams via StreamMediaLink
    3. Convert to stream data objects
    4. Format for Stremio via parse_stream_data

    Args:
        user_id: Current user ID for visibility filtering (shows public + user's own streams)
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    # Get media by external_id
    media = await get_media_by_external_id(session, video_id, MediaType.MOVIE)
    if not media:
        logger.warning(f"Movie not found for video_id: {video_id}")
        return []

    # Visibility filter - public streams + user's own streams
    visibility_filter = _get_visibility_filter(user_id)

    # Query torrent streams linked to this media
    torrent_query = (
        select(TorrentStream)
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(StreamMediaLink.media_id == media.id)
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

    # Convert torrent streams to TorrentStreamData
    stream_data_list = [TorrentStreamData.from_db(torrent, torrent.stream, media) for torrent in torrents]

    # Query Usenet streams linked to this media (if user has Usenet enabled AND has a capable provider)
    usenet_stream_data_list = []
    # Check if user has any Usenet-capable provider
    has_usenet_provider = any(sp.service in USENET_CAPABLE_PROVIDERS for sp in user_data.get_active_providers())
    if user_data.enable_usenet_streams and has_usenet_provider:
        usenet_query = (
            select(UsenetStream)
            .join(Stream, Stream.id == UsenetStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
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

        # Convert Usenet streams to UsenetStreamData
        usenet_stream_data_list = [UsenetStreamData.from_db(usenet) for usenet in usenet_streams]

    # Query Telegram streams (requires both enable_telegram_streams AND MediaFlow config)
    # Users must have MediaFlow configured with their own Telegram session to access streams
    telegram_stream_data_list = []
    show_telegram = (
        user_data.enable_telegram_streams
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_url
        and user_data.mediaflow_config.api_password
    )
    if show_telegram:
        telegram_query = (
            select(TelegramStream)
            .join(Stream, Stream.id == TelegramStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
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

        # Convert Telegram streams to TelegramStreamData
        telegram_stream_data_list = [TelegramStreamData.from_db(tg) for tg in telegram_streams]

    # Query HTTP streams (from M3U imports)
    http_query = (
        select(HTTPStream)
        .join(Stream, Stream.id == HTTPStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(StreamMediaLink.media_id == media.id)
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

    # Format HTTP streams directly for Stremio
    formatted_http_streams = []
    for http_stream in http_streams:
        stream = http_stream.stream
        formatted_http_streams.append(
            StremioStream(
                name=f"{settings.addon_name}\n{stream.name}",
                description=f"🎬 {stream.source}" if stream.source else "🎬 Direct",
                url=http_stream.url,
            )
        )

    # Query AceStream streams (requires enable_acestream_streams AND MediaFlow config)
    formatted_acestream_streams = []
    if user_data.enable_acestream_streams and _has_mediaflow_config(user_data):
        acestream_query = (
            select(AceStreamStream)
            .join(Stream, Stream.id == AceStreamStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
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

        formatted_acestream_streams = _format_acestream_streams(acestream_streams, user_data, user_ip)

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not formatted_http_streams
        and not formatted_acestream_streams
    ):
        return []

    # Format torrent streams for Stremio
    torrent_formatted = []
    if stream_data_list:
        torrent_formatted = await parse_stream_data(
            streams=stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            user_ip=user_ip,
            is_series=False,
        )

    # Format Usenet streams for Stremio
    usenet_formatted = []
    if usenet_stream_data_list:
        usenet_formatted = await parse_stream_data(
            streams=usenet_stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            user_ip=user_ip,
            is_series=False,
            is_usenet=True,
        )

    # Format Telegram streams for Stremio
    telegram_formatted = []
    if telegram_stream_data_list:
        telegram_formatted = await parse_stream_data(
            streams=telegram_stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            user_ip=user_ip,
            is_series=False,
            is_telegram=True,
        )

    # Combine streams based on user's type grouping and ordering preferences
    stream_groups = {
        "torrent": torrent_formatted,
        "usenet": usenet_formatted,
        "telegram": telegram_formatted,
        "http": formatted_http_streams,
        "acestream": formatted_acestream_streams,
    }
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

    Query flow:
    1. Resolve video_id to media
    2. Query torrent and HTTP streams via StreamFile + FileMediaLink
    3. Convert to stream data objects
    4. Format for Stremio via parse_stream_data

    Args:
        user_id: Current user ID for visibility filtering (shows public + user's own streams)
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    # Get media by external_id
    media = await get_media_by_external_id(session, video_id, MediaType.SERIES)
    if not media:
        logger.warning(f"Series not found for video_id: {video_id}")
        return []

    # Visibility filter - public streams + user's own streams
    visibility_filter = _get_visibility_filter(user_id)

    # Query torrent streams with files linked to this episode
    torrent_query = (
        select(TorrentStream)
        .join(Stream, Stream.id == TorrentStream.stream_id)
        .join(StreamFile, StreamFile.stream_id == Stream.id)
        .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
        .where(
            FileMediaLink.media_id == media.id,
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

    # Convert torrent streams to TorrentStreamData
    stream_data_list = [TorrentStreamData.from_db(torrent, torrent.stream, media) for torrent in torrents]

    # Query Usenet streams with files linked to this episode (if user has Usenet enabled AND has a capable provider)
    usenet_stream_data_list = []
    # Check if user has any Usenet-capable provider
    has_usenet_provider = any(sp.service in USENET_CAPABLE_PROVIDERS for sp in user_data.get_active_providers())
    if user_data.enable_usenet_streams and has_usenet_provider:
        usenet_query = (
            select(UsenetStream)
            .join(Stream, Stream.id == UsenetStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media.id,
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

        # Convert Usenet streams to UsenetStreamData
        usenet_stream_data_list = [UsenetStreamData.from_db(usenet) for usenet in usenet_streams]

    # Query Telegram streams with files linked to this episode (requires MediaFlow config)
    telegram_stream_data_list = []
    show_telegram = (
        user_data.enable_telegram_streams
        and user_data.mediaflow_config
        and user_data.mediaflow_config.proxy_url
        and user_data.mediaflow_config.api_password
    )
    if show_telegram:
        telegram_query = (
            select(TelegramStream)
            .join(Stream, Stream.id == TelegramStream.stream_id)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == media.id,
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

        # Convert Telegram streams to TelegramStreamData
        telegram_stream_data_list = [TelegramStreamData.from_db(tg) for tg in telegram_streams]

    # Query HTTP streams with file-level linking for series
    http_query = (
        select(HTTPStream)
        .join(Stream, Stream.id == HTTPStream.stream_id)
        .join(StreamFile, StreamFile.stream_id == Stream.id)
        .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
        .where(
            FileMediaLink.media_id == media.id,
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

    # Format HTTP streams directly for Stremio
    formatted_http_streams = []
    for http_stream in http_streams:
        stream = http_stream.stream
        formatted_http_streams.append(
            StremioStream(
                name=f"{settings.addon_name}\n{stream.name}",
                description=f"📺 S{season}E{episode} | {stream.source}" if stream.source else f"📺 S{season}E{episode}",
                url=http_stream.url,
            )
        )

    # Query AceStream streams linked to this episode (requires enable_acestream_streams AND MediaFlow config)
    formatted_acestream_streams = []
    if user_data.enable_acestream_streams and _has_mediaflow_config(user_data):
        acestream_query = (
            select(AceStreamStream)
            .join(Stream, Stream.id == AceStreamStream.stream_id)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == media.id)
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

        formatted_acestream_streams = _format_acestream_streams(acestream_streams, user_data, user_ip)

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not formatted_http_streams
        and not formatted_acestream_streams
    ):
        return []

    # Format torrent streams for Stremio
    torrent_formatted = []
    if stream_data_list:
        torrent_formatted = await parse_stream_data(
            streams=stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            season=season,
            episode=episode,
            user_ip=user_ip,
            is_series=True,
        )

    # Format Usenet streams for Stremio
    usenet_formatted = []
    if usenet_stream_data_list:
        usenet_formatted = await parse_stream_data(
            streams=usenet_stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            season=season,
            episode=episode,
            user_ip=user_ip,
            is_series=True,
            is_usenet=True,
        )

    # Format Telegram streams for Stremio
    telegram_formatted = []
    if telegram_stream_data_list:
        telegram_formatted = await parse_stream_data(
            streams=telegram_stream_data_list,
            user_data=user_data,
            secret_str=secret_str,
            season=season,
            episode=episode,
            user_ip=user_ip,
            is_series=True,
            is_telegram=True,
        )

    # Combine streams based on user's type grouping and ordering preferences
    stream_groups = {
        "torrent": torrent_formatted,
        "usenet": usenet_formatted,
        "telegram": telegram_formatted,
        "http": formatted_http_streams,
        "acestream": formatted_acestream_streams,
    }
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

    # Query HTTP streams
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

    # Query YouTube streams
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

    # Format streams for Stremio
    formatted_streams = []

    for http_stream in http_streams:
        stream = http_stream.stream
        formatted_streams.append(
            StremioStream(
                name=f"{settings.addon_name}\n{stream.name}",
                description=f"📺 {stream.source}" if stream.source else "📺 Live",
                url=http_stream.url,
            )
        )

    for yt_stream in yt_streams:
        stream = yt_stream.stream
        formatted_streams.append(
            StremioStream(
                name=f"{settings.addon_name} YouTube\n{stream.name}",
                description="▶️ YouTube Stream",
                externalUrl=f"https://www.youtube.com/watch?v={yt_stream.video_id}",
            )
        )

    # Query AceStream streams (requires enable_acestream_streams AND MediaFlow config)
    if user_data.enable_acestream_streams and _has_mediaflow_config(user_data):
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
