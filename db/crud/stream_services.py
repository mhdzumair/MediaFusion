"""
Stream service operations for Stremio API.

These functions query streams and format them for Stremio addon responses.
They use:
- db/crud/streams.py for raw database queries
- db/schemas/media.py TorrentStreamData.from_db() for model conversion
- utils/parser.py parse_stream_data() for Stremio formatting
"""

import asyncio
import gzip
import importlib
import json
import logging
import time
import zlib
from collections.abc import Awaitable
from typing import Any, cast

from fastapi import BackgroundTasks
from sqlalchemy import or_
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings
from db.crud.scraper_helpers import (
    get_or_create_metadata,
    store_new_torrent_streams,
    store_new_usenet_streams,
)
from db.crud.media import get_media_by_external_id, get_related_media_ids_by_external_id
from db.database import get_async_session_context, get_read_session_context
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
from db.retry_utils import run_db_operation_with_retry, run_db_read_with_primary_fallback
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import (
    MetadataData,
    RichStream,
    RichStreamMetadata,
    Stream as StremioStream,
)
from db.schemas import (
    TorrentStreamData,
    UserData,
    YouTubeStreamData,
)
from db.schemas.media import HTTPStreamData, TelegramStreamData, UsenetStreamData
from utils.network import encode_mediaflow_acestream_url
from utils.usenet_url_resolver import apply_user_scoped_nzb_urls
from utils.youtube import format_geo_restriction_label

# Providers that support Usenet content - defined here to avoid circular import
# This should be kept in sync with streaming_providers.mapper.USENET_CAPABLE_PROVIDERS
USENET_CAPABLE_PROVIDERS = {"torbox", "debrider", "sabnzbd", "nzbget", "nzbdav", "easynews", "stremio_nntp"}
# Providers that can play torrent streams directly.
# Keep in sync with streaming_providers.mapper.GET_VIDEO_URL_FUNCTIONS (+ "p2p").
TORRENT_CAPABLE_PROVIDERS = {
    "alldebrid",
    "debridlink",
    "offcloud",
    "pikpak",
    "premiumize",
    "qbittorrent",
    "realdebrid",
    "seedr",
    "torbox",
    "stremthru",
    "easydebrid",
    "debrider",
    "p2p",
}

# Redis cache for raw stream payloads (see settings.stream_raw_redis_cache_*)
STREAM_CACHE_PREFIX = "stream_data:"
_STREAM_CACHE_MAGIC = b"\x01MFsc1"  # zlib-compressed JSON blob prefix
LIVE_TORRENT_FALLBACK_CACHE_TTL = 180  # 3 minutes
LIVE_TORRENT_FALLBACK_CACHE_PREFIX = "live_torrent_fallback:"

logger = logging.getLogger(__name__)


def _encode_stream_cache_blob(data: dict) -> bytes | None:
    """Serialize stream cache payload for Redis. Returns None if over max_stored_bytes."""
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    if settings.stream_raw_redis_cache_zlib_compress:
        body = _STREAM_CACHE_MAGIC + zlib.compress(raw, 6)
    else:
        body = raw
    max_b = settings.stream_raw_redis_cache_max_stored_bytes
    if max_b > 0 and len(body) > max_b:
        return None
    return body


# Zlib wrappers typically start with CMF 0x78; FLG is commonly 0x01 / 0x5E / 0x9C / 0xDA (RFC 1950).
_ZLIB_FLG_BYTES = frozenset((0x01, 0x5E, 0x9C, 0xDA))


def _decode_stream_cache_blob(blob: bytes | memoryview | str | None) -> dict | None:
    """Parse stream cache payload from Redis (compressed or legacy JSON string/bytes).

    Never raises: returns None if the blob is missing, empty, or not decodable JSON.
    Handles: MF magic+zlib, raw zlib-wrapped JSON, gzip, and plain UTF-8 JSON bytes.
    """
    if blob is None:
        return None
    if isinstance(blob, str):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return None
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if not blob:
        return None

    def _loads_utf8_json(raw: bytes) -> dict | None:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    if blob.startswith(_STREAM_CACHE_MAGIC):
        try:
            raw = zlib.decompress(blob[len(_STREAM_CACHE_MAGIC) :])
        except zlib.error:
            return None
        return _loads_utf8_json(raw)

    if len(blob) >= 2 and blob[0] == 0x78 and blob[1] in _ZLIB_FLG_BYTES:
        try:
            raw = zlib.decompress(blob)
        except zlib.error:
            raw = None
        if raw is not None:
            parsed = _loads_utf8_json(raw)
            if parsed is not None:
                return parsed

    parsed = _loads_utf8_json(blob)
    if parsed is not None:
        return parsed

    try:
        raw = zlib.decompress(blob)
    except zlib.error:
        raw = None
    if raw is not None:
        parsed = _loads_utf8_json(raw)
        if parsed is not None:
            return parsed

    try:
        raw = gzip.decompress(blob)
    except (OSError, EOFError):
        raw = None
    if raw is not None:
        return _loads_utf8_json(raw)

    return None


async def _store_stream_cache(cache_key: str, data: dict) -> None:
    if not settings.stream_raw_redis_cache_enabled:
        return
    try:
        blob = _encode_stream_cache_blob(data)
        if blob is None:
            logger.debug("Stream cache skip SET (oversize): %s", cache_key)
            return
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            blob,
            ex=settings.stream_raw_redis_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.warning("Stream cache SET failed for %s: %s", cache_key, exc)


_scraper_tasks_module = None
LIVE_SEARCH_EXTERNAL_PROVIDERS = {"tmdb", "tvdb", "mal", "kitsu"}


def _log_db_retry_attempt(operation_name: str):
    def _handler(attempt: int, max_attempts: int, exc: Exception):
        logger.warning(
            "Retryable DB error during %s (attempt %d/%d): %s",
            operation_name,
            attempt,
            max_attempts,
            exc,
        )

    return _handler


async def _get_media_by_external_id_with_retry(
    video_id: str,
    media_type: MediaType,
    operation_name: str,
) -> Media | None:
    async def _lookup_media() -> Media | None:
        async with get_read_session_context() as read_session:
            return await get_media_by_external_id(read_session, video_id, media_type)

    return await run_db_operation_with_retry(
        operation=_lookup_media,
        operation_name=operation_name,
        on_retry=_log_db_retry_attempt(operation_name),
    )


async def _get_related_media_ids_by_external_id_with_retry(
    video_id: str,
    media_type: MediaType,
    operation_name: str,
) -> list[int]:
    async def _lookup_media_ids() -> list[int]:
        async with get_read_session_context() as read_session:
            return await get_related_media_ids_by_external_id(read_session, video_id, media_type)

    return await run_db_operation_with_retry(
        operation=_lookup_media_ids,
        operation_name=operation_name,
        on_retry=_log_db_retry_attempt(operation_name),
    )


async def _get_media_and_related_media_ids_by_external_id_with_retry(
    video_id: str,
    media_type: MediaType,
    operation_name: str,
) -> tuple[Media | None, list[int]]:
    """Single read session for media + related IDs (avoids two pool checkouts per request)."""

    async def _lookup() -> tuple[Media | None, list[int]]:
        async with get_read_session_context() as read_session:
            media = await get_media_by_external_id(read_session, video_id, media_type)
            if not media:
                return None, []
            related = await get_related_media_ids_by_external_id(read_session, video_id, media_type)
            return media, related

    return await run_db_operation_with_retry(
        operation=_lookup,
        operation_name=operation_name,
        on_retry=_log_db_retry_attempt(operation_name),
    )


def _get_scraper_tasks_module():
    """Lazy-load scraper_tasks to avoid import cycles during app startup."""
    global _scraper_tasks_module
    if _scraper_tasks_module is None:
        _scraper_tasks_module = importlib.import_module("scrapers.scraper_tasks")
    return _scraper_tasks_module


def _build_scraper_metadata(
    media: Media | None,
    video_id: str,
    media_type: MediaType,
    fetched_metadata: dict | None = None,
) -> MetadataData | None:
    """Build MetadataData for scraper execution from DB media or fetched metadata."""
    if media:
        return MetadataData(
            id=media.id,
            external_id=video_id,
            type=media_type.value,
            title=media.title,
            original_title=media.original_title,
            year=media.year,
            release_date=media.release_date,
            aka_titles=[],
            external_ids=None,
        )

    if fetched_metadata:
        external_id = fetched_metadata.get("imdb_id") or fetched_metadata.get("id") or video_id
        title = fetched_metadata.get("title")
        if title:
            return MetadataData(
                id=0,
                external_id=external_id,
                type=media_type.value,
                title=title,
                original_title=fetched_metadata.get("original_title"),
                year=fetched_metadata.get("year"),
                aka_titles=fetched_metadata.get("aka_titles") or [],
                external_ids=None,
            )
    return None


def _parse_live_search_id(video_id: str) -> tuple[str, str] | None:
    """Parse a supported live-search external ID into (provider, provider_id)."""
    normalized_video_id = str(video_id).strip()
    if not normalized_video_id:
        return None

    if normalized_video_id.startswith("tt"):
        return "imdb", normalized_video_id

    if ":" not in normalized_video_id:
        return None

    provider, provider_id = normalized_video_id.split(":", 1)
    provider = provider.lower()
    if provider not in LIVE_SEARCH_EXTERNAL_PROVIDERS:
        return None
    provider_id = provider_id.strip()
    if not provider_id:
        return None
    return provider, provider_id


async def _persist_live_search_streams(
    torrent_streams: list[TorrentStreamData],
    usenet_streams: list[UsenetStreamData],
) -> None:
    """Persist live-scraped streams using a write session."""
    if not torrent_streams and not usenet_streams:
        return

    try:
        async with get_async_session_context() as write_session:
            if torrent_streams:
                await store_new_torrent_streams(
                    write_session,
                    [stream.model_dump(by_alias=True) for stream in torrent_streams],
                )
            if usenet_streams:
                await store_new_usenet_streams(
                    write_session,
                    [stream.model_dump(by_alias=True) for stream in usenet_streams],
                )
            await write_session.commit()
    except Exception as exc:
        logger.warning("Failed to persist live search streams: %s", exc)


async def _fetch_missing_media_for_live_search(
    video_id: str,
    media_type: MediaType,
    source_provider: str,
    source_provider_id: str,
) -> tuple[Media | None, MetadataData | None]:
    """Fetch and persist missing metadata for supported external IDs during live search."""
    scraper_tasks = _get_scraper_tasks_module()
    if source_provider in {"imdb", "tmdb"}:
        fetched_metadata = await scraper_tasks.meta_fetcher.get_metadata(
            source_provider_id,
            media_type=media_type.value,
            source_type=source_provider,
        )
    else:
        fetched_metadata = await scraper_tasks.meta_fetcher.get_metadata_from_provider(
            source_provider,
            source_provider_id,
            media_type.value,
        )
    if not fetched_metadata:
        return None, None

    scraper_metadata = _build_scraper_metadata(None, video_id, media_type, fetched_metadata)
    if not scraper_metadata:
        return None, None

    media = None
    try:
        async with get_async_session_context() as write_session:
            media = await get_or_create_metadata(write_session, fetched_metadata, media_type.value)
            await write_session.commit()
    except Exception as exc:
        logger.warning("Failed to persist metadata for %s: %s", video_id, exc)

    return media, scraper_metadata


async def _run_live_search_scrapers(
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> tuple[list[TorrentStreamData], list[UsenetStreamData]]:
    """Run enabled live scrapers and return in-memory stream results."""
    scraper_tasks = _get_scraper_tasks_module()
    torrent_streams: list[TorrentStreamData] = []
    usenet_streams: list[UsenetStreamData] = []
    active_providers = user_data.get_active_providers()
    has_torrent_provider = (
        True if not active_providers else any(sp.service in TORRENT_CAPABLE_PROVIDERS for sp in active_providers)
    )
    has_usenet_provider = any(sp.service in USENET_CAPABLE_PROVIDERS for sp in active_providers)
    has_newznab_indexers = bool(user_data.indexer_config and user_data.indexer_config.newznab_indexers)
    has_public_usenet = settings.is_scrap_from_public_usenet_indexers

    if has_torrent_provider:
        try:
            torrent_streams = list(
                await scraper_tasks.run_scrapers(
                    user_data=user_data,
                    metadata=metadata,
                    catalog_type=catalog_type,
                    season=season,
                    episode=episode,
                )
            )
        except Exception as exc:
            logger.warning("Live torrent scraping failed for %s: %s", metadata.external_id, exc)
    else:
        logger.info(
            "Skipping live torrent scraping for %s: no torrent-capable provider configured",
            metadata.external_id,
        )

    if user_data.enable_usenet_streams and (has_usenet_provider or has_newznab_indexers or has_public_usenet):
        try:
            usenet_streams = await scraper_tasks.run_usenet_scrapers(
                user_data=user_data,
                metadata=metadata,
                catalog_type=catalog_type,
                season=season,
                episode=episode,
            )
        except Exception as exc:
            logger.warning("Live usenet scraping failed for %s: %s", metadata.external_id, exc)

    return torrent_streams, usenet_streams


async def _gather_with_concurrency_limit(coros: list[Awaitable[Any]], limit: int) -> list[Any]:
    """Run awaitables with bounded concurrency and stable result ordering."""
    if not coros:
        return []
    if limit <= 0:
        return await asyncio.gather(*coros)

    semaphore = asyncio.Semaphore(limit)
    results: list[Any] = [None] * len(coros)

    async def _run_one(index: int, coro: Awaitable[Any]) -> None:
        async with semaphore:
            results[index] = await coro

    await asyncio.gather(*(_run_one(index, coro) for index, coro in enumerate(coros)))
    return results


def _merge_unique_torrent_streams(
    existing_streams: list[TorrentStreamData],
    live_streams: list[TorrentStreamData],
) -> list[TorrentStreamData]:
    """Merge live torrent streams while keeping info_hash values unique."""
    if not live_streams:
        return []

    existing_info_hashes = {stream.info_hash.lower() for stream in existing_streams}
    unique_live_streams: list[TorrentStreamData] = []

    for stream in live_streams:
        info_hash = stream.info_hash.lower()
        if info_hash in existing_info_hashes:
            continue
        existing_info_hashes.add(info_hash)
        unique_live_streams.append(stream)

    existing_streams.extend(unique_live_streams)
    return unique_live_streams


def _merge_unique_usenet_streams(
    existing_streams: list[UsenetStreamData],
    live_streams: list[UsenetStreamData],
) -> list[UsenetStreamData]:
    """Merge live usenet streams while keeping nzb_guid values unique."""
    if not live_streams:
        return []

    existing_nzb_guids = {stream.nzb_guid for stream in existing_streams}
    unique_live_streams: list[UsenetStreamData] = []

    for stream in live_streams:
        if stream.nzb_guid in existing_nzb_guids:
            continue
        existing_nzb_guids.add(stream.nzb_guid)
        unique_live_streams.append(stream)

    existing_streams.extend(unique_live_streams)
    return unique_live_streams


def _get_live_torrent_fallback_cache_key(info_hash: str) -> str:
    return f"{LIVE_TORRENT_FALLBACK_CACHE_PREFIX}{info_hash.lower()}"


async def cache_live_torrent_fallback_streams(streams: list[TorrentStreamData]) -> None:
    """Cache live torrent streams by info_hash for playback fallback recovery."""
    if not streams:
        return

    try:
        pipeline = REDIS_ASYNC_CLIENT.pipeline(transaction=False)
        if pipeline is not None:
            for stream in streams:
                # Exclude binary payloads to keep cache compact and JSON-safe.
                stream_payload = stream.model_dump(mode="json", exclude={"torrent_file"})
                payload = json.dumps(stream_payload).encode("utf-8")
                key = _get_live_torrent_fallback_cache_key(stream.info_hash)
                pipeline.set(key, payload, ex=LIVE_TORRENT_FALLBACK_CACHE_TTL)
            await pipeline.execute()
            return

        for stream in streams:
            stream_payload = stream.model_dump(mode="json", exclude={"torrent_file"})
            payload = json.dumps(stream_payload).encode("utf-8")
            key = _get_live_torrent_fallback_cache_key(stream.info_hash)
            await REDIS_ASYNC_CLIENT.set(key, payload, ex=LIVE_TORRENT_FALLBACK_CACHE_TTL)
    except Exception as exc:
        logger.warning("Failed to cache live torrent fallback streams: %s", exc)


async def get_cached_live_torrent_fallback_stream(info_hash: str) -> TorrentStreamData | None:
    """Read a live torrent stream fallback payload from Redis by info_hash."""
    try:
        key = _get_live_torrent_fallback_cache_key(info_hash)
        payload = await REDIS_ASYNC_CLIENT.get(key)
        if not payload:
            return None

        stream_payload = json.loads(payload.decode("utf-8"))
        return TorrentStreamData.model_validate(stream_payload)
    except Exception as exc:
        logger.warning("Failed to read live torrent fallback stream for %s: %s", info_hash, exc)
        return None


def _dedupe_raw_streams(
    streams: list[dict[str, Any]],
    key_builder,
) -> list[dict[str, Any]]:
    """Deduplicate raw stream dicts while preserving order."""
    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for stream in streams:
        dedupe_key = key_builder(stream)
        if dedupe_key is None:
            dedupe_key = f"json:{json.dumps(stream, sort_keys=True, default=str)}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped.append(stream)

    return deduped


def _merge_raw_stream_payloads(payloads: list[dict[str, list[dict[str, Any]]]]) -> dict[str, list[dict[str, Any]]]:
    """Merge raw stream payloads across media IDs and deduplicate by stable keys."""
    merged: dict[str, list[dict[str, Any]]] = {
        "torrents": [],
        "usenet": [],
        "telegram": [],
        "http": [],
        "acestream": [],
        "youtube": [],
    }

    for payload in payloads:
        for stream_type in merged:
            merged[stream_type].extend(payload.get(stream_type, []))

    def _norm(value: Any) -> str | None:
        if value is None:
            return None
        return str(value).lower()

    merged["torrents"] = _dedupe_raw_streams(
        merged["torrents"],
        lambda s: f"info_hash:{_norm(s.get('info_hash'))}" if s.get("info_hash") else None,
    )
    merged["usenet"] = _dedupe_raw_streams(
        merged["usenet"],
        lambda s: f"nzb_guid:{_norm(s.get('nzb_guid'))}" if s.get("nzb_guid") else None,
    )
    merged["telegram"] = _dedupe_raw_streams(
        merged["telegram"],
        lambda s: (
            f"telegram_stream_id:{_norm(s.get('telegram_stream_id'))}"
            if s.get("telegram_stream_id")
            else (
                f"chat_message:{_norm(s.get('chat_id'))}:{_norm(s.get('message_id'))}"
                if s.get("chat_id") is not None and s.get("message_id") is not None
                else (f"file_unique_id:{_norm(s.get('file_unique_id'))}" if s.get("file_unique_id") else None)
            )
        ),
    )
    merged["http"] = _dedupe_raw_streams(
        merged["http"],
        lambda s: (
            f"stream_id:{_norm(s.get('stream_id'))}"
            if s.get("stream_id")
            else (f"url:{_norm(s.get('url'))}" if s.get("url") else None)
        ),
    )
    merged["acestream"] = _dedupe_raw_streams(
        merged["acestream"],
        lambda s: (
            f"info_hash:{_norm(s.get('info_hash'))}"
            if s.get("info_hash")
            else (
                f"content_id:{_norm(s.get('content_id'))}"
                if s.get("content_id")
                else (f"name:{_norm(s.get('stream_name'))}" if s.get("stream_name") else None)
            )
        ),
    )
    merged["youtube"] = _dedupe_raw_streams(
        merged["youtube"],
        lambda s: (
            f"stream_id:{_norm(s.get('stream_id'))}"
            if s.get("stream_id")
            else (f"video_id:{_norm(s.get('video_id'))}" if s.get("video_id") else None)
        ),
    )

    return merged


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
    stream_groups: dict[str, list[Any]],
    disable_stream_cap: bool = False,
) -> list[Any]:
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
        combined: list[Any] = []
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

    if disable_stream_cap:
        return combined

    # Apply total stream cap
    return combined[: user_data.max_streams]


async def invalidate_media_stream_cache(media_id: int) -> None:
    """Delete all cached stream data for a media.

    Called when streams are added to or removed from a media entry.
    Clears both movie and series cache keys for the given media_id.
    """
    try:
        # Delete legacy movie cache key (backward compatibility)
        legacy_movie_key = f"{STREAM_CACHE_PREFIX}movie:{media_id}"
        await REDIS_ASYNC_CLIENT.delete(legacy_movie_key)

        # Delete visibility-scoped movie cache keys
        movie_pattern = f"{STREAM_CACHE_PREFIX}movie:{media_id}:*"
        movie_keys = []
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=movie_pattern, count=100):
            movie_keys.append(key)
        if movie_keys:
            await REDIS_ASYNC_CLIENT.delete(*movie_keys)

        # For series, scan and delete all season:episode combos
        pattern = f"{STREAM_CACHE_PREFIX}series:{media_id}:*"
        keys = []
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            await REDIS_ASYNC_CLIENT.delete(*keys)
    except Exception as e:
        logger.warning(f"Error invalidating stream cache for media_id={media_id}: {e}")


async def _fetch_movie_raw_streams_in_session(
    session: AsyncSession,
    media_id: int,
    visibility_filter,
) -> dict:
    """Fetch all raw stream data for one movie using an existing read session."""
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

    # Exclude binary fields that can't be JSON-serialized (torrent_file)
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
                selectinload(Stream.uploader_user),
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
    usenet_data = [UsenetStreamData.from_db(u).model_dump(mode="json") for u in usenet_streams]

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
    telegram_data = [
        TelegramStreamData.from_db(tg, tg.stream, media).model_dump(mode="json") for tg in telegram_streams
    ]

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
    http_data = [HTTPStreamData.from_db(hs, hs.stream, media).model_dump(mode="json") for hs in http_streams]

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
            "languages": [lang.name for lang in (ace.stream.languages or [])],
        }
        for ace in acestream_streams
    ]

    # Query YouTube streams
    youtube_query = (
        select(YouTubeStream)
        .join(Stream, Stream.id == YouTubeStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(StreamMediaLink.media_id == media_id)
        .where(Stream.is_active.is_(True))
        .where(Stream.is_blocked.is_(False))
        .where(visibility_filter)
        .options(
            joinedload(YouTubeStream.stream).options(
                selectinload(Stream.languages),
            )
        )
        .limit(100)
    )
    youtube_result = await session.exec(youtube_query)
    youtube_streams = youtube_result.unique().all()
    youtube_data = [YouTubeStreamData.from_db(yt, yt.stream, media).model_dump(mode="json") for yt in youtube_streams]

    return {
        "torrents": torrent_data,
        "usenet": usenet_data,
        "telegram": telegram_data,
        "http": http_data,
        "acestream": acestream_data,
        "youtube": youtube_data,
    }


async def _fetch_movie_raw_streams(media_id: int, visibility_filter) -> dict:
    """Fetch all raw stream data for a movie from DB (read replica) and cache as JSON.

    Returns a dict with serialized stream data lists keyed by type.
    """
    async with get_read_session_context() as session:
        return await _fetch_movie_raw_streams_in_session(session, media_id, visibility_filter)


async def _fetch_movie_raw_streams_batch(media_ids: list[int], visibility_filter) -> dict[int, dict]:
    """Load several movies using a fresh read session per movie.

    A fresh session per iteration ensures a connection left in a bad state by a
    replica WAL-replay cancel on one movie does not poison subsequent fetches.
    """
    out: dict[int, dict] = {}
    for media_id in media_ids:
        async with get_read_session_context() as session:
            out[media_id] = await _fetch_movie_raw_streams_in_session(session, media_id, visibility_filter)
    return out


async def _get_cached_movie_streams_bulk(
    media_ids: list[int],
    visibility_filter,
    user_id: int | None = None,
) -> list[dict]:
    """Get raw movie stream payloads with Redis caching; DB misses run in one read session."""
    n = len(media_ids)
    if n == 0:
        return []
    visibility_scope = f"user:{user_id}" if user_id else "public"
    cache_keys = [f"{STREAM_CACHE_PREFIX}movie:{mid}:{visibility_scope}" for mid in media_ids]
    out: list[dict | None] = [None] * n
    miss_indices: list[int] = []

    for i, cache_key in enumerate(cache_keys):
        if settings.stream_raw_redis_cache_enabled:
            cached = await REDIS_ASYNC_CLIENT.get(cache_key)
            if cached:
                parsed = _decode_stream_cache_blob(cached)
                if parsed is not None:
                    logger.debug("Stream cache HIT for movie media_id=%s", media_ids[i])
                    out[i] = parsed
                    continue
                logger.warning(
                    "Stream cache unreadable for movie media_id=%s; evicting key %s",
                    media_ids[i],
                    cache_key,
                )
                try:
                    await REDIS_ASYNC_CLIENT.delete(cache_key)
                except Exception as exc:
                    logger.debug("Stream cache evict failed for %s: %s", cache_key, exc)
        miss_indices.append(i)

    if miss_indices:
        ids_to_fetch = [media_ids[i] for i in miss_indices]
        batch_op_name = f"movie stream batch fetch media_ids={ids_to_fetch}"
        logger.debug("Stream cache MISS for movie media_ids=%s", ids_to_fetch)
        t0 = time.monotonic()

        async def _run_movie_batch_read():
            return await _fetch_movie_raw_streams_batch(ids_to_fetch, visibility_filter)

        async def _run_movie_batch_primary():
            out_primary: dict[int, dict] = {}
            for mid in ids_to_fetch:
                async with get_async_session_context() as s:
                    out_primary[mid] = await _fetch_movie_raw_streams_in_session(s, mid, visibility_filter)
            return out_primary

        batch = await run_db_read_with_primary_fallback(
            _run_movie_batch_read,
            _run_movie_batch_primary,
            operation_name=batch_op_name,
            on_fallback=lambda exc: logger.warning(
                "Falling back to primary for %s after replica error: %s", batch_op_name, exc
            ),
        )
        elapsed = time.monotonic() - t0
        logger.info("DB batch fetch for movie media_ids=%s took %.3fs", ids_to_fetch, elapsed)
        for idx in miss_indices:
            mid = media_ids[idx]
            data = batch[mid]
            out[idx] = data
            await _store_stream_cache(cache_keys[idx], data)

    if any(x is None for x in out):
        raise RuntimeError("incomplete movie stream cache bulk fetch")
    return cast(list[dict], out)


async def _get_cached_movie_streams(media_id: int, visibility_filter, user_id: int | None = None) -> dict:
    """Get raw movie stream data with Redis caching."""
    rows = await _get_cached_movie_streams_bulk([media_id], visibility_filter, user_id)
    return rows[0]


async def _fetch_series_raw_streams_in_session(
    session: AsyncSession,
    media_id: int,
    season: int,
    episode: int,
    visibility_filter,
) -> dict:
    """Fetch all raw stream data for one series episode using an existing read session."""
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
                selectinload(Stream.uploader_user),
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
    usenet_data = [UsenetStreamData.from_db(u).model_dump(mode="json") for u in usenet_streams]

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
    telegram_data = [
        TelegramStreamData.from_db(tg, tg.stream, media).model_dump(mode="json") for tg in telegram_streams
    ]

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
    http_data = [
        HTTPStreamData.from_db(hs, hs.stream, media, season, episode).model_dump(mode="json") for hs in http_streams
    ]

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
            "languages": [lang.name for lang in (ace.stream.languages or [])],
        }
        for ace in acestream_streams
    ]

    # YouTube streams (uses StreamMediaLink, not FileMediaLink)
    youtube_query = (
        select(YouTubeStream)
        .join(Stream, Stream.id == YouTubeStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(StreamMediaLink.media_id == media_id)
        .where(Stream.is_active.is_(True))
        .where(Stream.is_blocked.is_(False))
        .where(visibility_filter)
        .options(
            joinedload(YouTubeStream.stream).options(
                selectinload(Stream.languages),
            )
        )
        .limit(100)
    )
    youtube_result = await session.exec(youtube_query)
    youtube_streams = youtube_result.unique().all()
    youtube_data = [YouTubeStreamData.from_db(yt, yt.stream, media).model_dump(mode="json") for yt in youtube_streams]

    return {
        "torrents": torrent_data,
        "usenet": usenet_data,
        "telegram": telegram_data,
        "http": http_data,
        "acestream": acestream_data,
        "youtube": youtube_data,
    }


async def _fetch_series_raw_streams(media_id: int, season: int, episode: int, visibility_filter) -> dict:
    """Fetch all raw stream data for a series episode from DB (read replica)."""
    async with get_read_session_context() as session:
        return await _fetch_series_raw_streams_in_session(session, media_id, season, episode, visibility_filter)


async def _fetch_series_raw_streams_batch(
    media_ids: list[int],
    season: int,
    episode: int,
    visibility_filter,
) -> dict[int, dict]:
    """Load several series episodes (same S/E) using a fresh read session per media_id.

    A fresh session per iteration prevents a connection poisoned by a replica
    WAL-replay cancel (or mid-selectin cancellation) from contaminating the
    remaining media_ids in the batch.
    """
    out: dict[int, dict] = {}
    for media_id in media_ids:
        async with get_read_session_context() as session:
            out[media_id] = await _fetch_series_raw_streams_in_session(
                session, media_id, season, episode, visibility_filter
            )
    return out


async def _get_cached_series_streams_bulk(
    media_ids: list[int],
    season: int,
    episode: int,
    visibility_filter,
    user_id: int | None = None,
) -> list[dict]:
    """Get raw series stream payloads with Redis caching; DB misses run in one read session."""
    n = len(media_ids)
    if n == 0:
        return []
    visibility_scope = f"user:{user_id}" if user_id else "public"
    cache_keys = [f"{STREAM_CACHE_PREFIX}series:{mid}:{season}:{episode}:{visibility_scope}" for mid in media_ids]
    out: list[dict | None] = [None] * n
    miss_indices: list[int] = []

    for i, cache_key in enumerate(cache_keys):
        if settings.stream_raw_redis_cache_enabled:
            cached = await REDIS_ASYNC_CLIENT.get(cache_key)
            if cached:
                parsed = _decode_stream_cache_blob(cached)
                if parsed is not None:
                    logger.debug(
                        "Stream cache HIT for series media_id=%s S%sE%s",
                        media_ids[i],
                        season,
                        episode,
                    )
                    out[i] = parsed
                    continue
                logger.warning(
                    "Stream cache unreadable for series media_id=%s S%sE%s; evicting key %s",
                    media_ids[i],
                    season,
                    episode,
                    cache_key,
                )
                try:
                    await REDIS_ASYNC_CLIENT.delete(cache_key)
                except Exception as exc:
                    logger.debug("Stream cache evict failed for %s: %s", cache_key, exc)
        miss_indices.append(i)

    if miss_indices:
        ids_to_fetch = [media_ids[i] for i in miss_indices]
        batch_op_name = f"series stream batch fetch S{season}E{episode} media_ids={ids_to_fetch}"
        logger.debug("Stream cache MISS for series media_ids=%s S%sE%s", ids_to_fetch, season, episode)
        t0 = time.monotonic()

        async def _run_series_batch_read():
            return await _fetch_series_raw_streams_batch(ids_to_fetch, season, episode, visibility_filter)

        async def _run_series_batch_primary():
            out_primary: dict[int, dict] = {}
            for mid in ids_to_fetch:
                async with get_async_session_context() as s:
                    out_primary[mid] = await _fetch_series_raw_streams_in_session(
                        s, mid, season, episode, visibility_filter
                    )
            return out_primary

        batch = await run_db_read_with_primary_fallback(
            _run_series_batch_read,
            _run_series_batch_primary,
            operation_name=batch_op_name,
            on_fallback=lambda exc: logger.warning(
                "Falling back to primary for %s after replica error: %s", batch_op_name, exc
            ),
        )
        elapsed = time.monotonic() - t0
        logger.info("DB batch fetch for series media_ids=%s S%sE%s took %.3fs", ids_to_fetch, season, episode, elapsed)
        for idx in miss_indices:
            mid = media_ids[idx]
            data = batch[mid]
            out[idx] = data
            await _store_stream_cache(cache_keys[idx], data)

    if any(x is None for x in out):
        raise RuntimeError("incomplete series stream cache bulk fetch")
    return cast(list[dict], out)


async def _get_cached_series_streams(
    media_id: int,
    season: int,
    episode: int,
    visibility_filter,
    user_id: int | None = None,
) -> dict:
    """Get raw series stream data with Redis caching."""
    rows = await _get_cached_series_streams_bulk([media_id], season, episode, visibility_filter, user_id)
    return rows[0]


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


def _extract_resolution_dimensions(resolution: str | None) -> tuple[int | None, int | None]:
    """Map a resolution label to height/width values for Kodi stream details."""
    if not resolution:
        return None, None

    normalized = resolution.lower()
    if normalized in {"4k", "2160p"}:
        return 2160, 3840
    if normalized == "1440p":
        return 1440, 2560
    if normalized == "1080p":
        return 1080, 1920
    if normalized == "720p":
        return 720, 1280
    if normalized == "576p":
        return 576, 720
    if normalized == "480p":
        return 480, 640
    if normalized == "360p":
        return 360, 480
    if normalized == "240p":
        return 240, 320
    return None, None


def _deserialize_acestream_rich_streams(
    acestream_data: list[dict], user_data: UserData, user_ip: str | None = None
) -> list[RichStream]:
    """Deserialize cached AceStream data into rich stream payloads for Kodi."""
    if not acestream_data or not _has_mediaflow_config(user_data):
        return []

    mediaflow_config = user_data.mediaflow_config
    rich_streams = []
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

        stream_name = ace.get("stream_name") or "AceStream"
        stremio_stream = StremioStream(
            name=f"{settings.addon_name}\n{stream_name}",
            description=" ".join(desc_parts),
            url=url,
            behaviorHints={"filename": stream_name},
        )
        video_height, video_width = _extract_resolution_dimensions(ace.get("resolution"))
        stream_id = str(ace.get("content_id") or ace.get("info_hash") or stream_name)
        rich_streams.append(
            RichStream(
                stream=stremio_stream,
                metadata=RichStreamMetadata(
                    id=stream_id,
                    info_hash=str(ace.get("info_hash") or stream_id),
                    name=stream_name,
                    resolution=ace.get("resolution"),
                    quality=ace.get("quality"),
                    codec=ace.get("codec"),
                    source=ace.get("source") or "AceStream",
                    languages=ace.get("languages") or [],
                    cached=False,
                    stream_type="acestream",
                    provider_name="acestream",
                    provider_short_name="Ace",
                    filename=stream_name,
                    video_width=video_width,
                    video_height=video_height,
                ),
            )
        )
    return rich_streams


async def get_movie_streams(
    video_id: str,
    user_data: UserData,
    secret_str: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
    user_id: int | None = None,
    return_rich: bool = False,
    disable_stream_cap: bool = False,
) -> list[StremioStream] | list[RichStream]:
    """
    Get formatted streams for a movie.

    Uses Redis caching for raw stream data (user-independent) and applies
    per-user filtering/sorting/debrid-cache-checking on top.
    Parallelizes parse_stream_data calls across stream types.
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    live_search_id = _parse_live_search_id(video_id)
    live_search_enabled = user_data.live_search_streams and live_search_id is not None

    # Resolve video_id to media + related IDs (single read session)
    media_lookup_name = f"movie media lookup video_id={video_id}"
    media, related_media_ids = await _get_media_and_related_media_ids_by_external_id_with_retry(
        video_id,
        MediaType.MOVIE,
        media_lookup_name,
    )
    scraper_metadata = _build_scraper_metadata(media, video_id, MediaType.MOVIE)

    if not media and live_search_enabled and live_search_id:
        media, scraper_metadata = await _fetch_missing_media_for_live_search(
            video_id,
            MediaType.MOVIE,
            live_search_id[0],
            live_search_id[1],
        )
        if media:
            related_media_ids = await _get_related_media_ids_by_external_id_with_retry(
                video_id,
                MediaType.MOVIE,
                f"movie related media lookup video_id={video_id}",
            )

    if not media:
        logger.warning(f"Movie not found for video_id: {video_id}")
        if not scraper_metadata:
            return []

    visibility_filter = _get_visibility_filter(user_id)

    if media and media.id not in related_media_ids:
        related_media_ids.insert(0, media.id)

    # Get cached or fresh raw stream data (one DB session for all cache misses)
    if related_media_ids:
        raw_payloads = await _get_cached_movie_streams_bulk(related_media_ids, visibility_filter, user_id)
        raw_data = _merge_raw_stream_payloads(raw_payloads)
    else:
        raw_data = {
            "torrents": [],
            "usenet": [],
            "telegram": [],
            "http": [],
            "acestream": [],
            "youtube": [],
        }

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

    # Deserialize stream data objects from cache
    http_stream_data_list = [HTTPStreamData.model_validate(h) for h in raw_data.get("http", [])]
    youtube_stream_data_list = [YouTubeStreamData.model_validate(yt) for yt in raw_data.get("youtube", [])]

    # Live search: run scrapers on-demand and merge in-memory results.
    if live_search_enabled and scraper_metadata:
        live_torrent_streams, live_usenet_streams = await _run_live_search_scrapers(
            user_data=user_data,
            metadata=scraper_metadata,
            catalog_type="movie",
        )

        unique_live_torrent_streams = _merge_unique_torrent_streams(stream_data_list, live_torrent_streams)
        unique_live_usenet_streams = _merge_unique_usenet_streams(usenet_stream_data_list, live_usenet_streams)

        if unique_live_torrent_streams or unique_live_usenet_streams:
            await cache_live_torrent_fallback_streams(unique_live_torrent_streams)
            background_tasks.add_task(
                _persist_live_search_streams, unique_live_torrent_streams, unique_live_usenet_streams
            )

    # AceStream is formatted directly (requires MediaFlow config, not a parse_stream_data stream type)
    formatted_acestream_streams = (
        _deserialize_acestream_rich_streams(raw_data["acestream"], user_data, user_ip)
        if return_rich and user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
        else (
            _deserialize_acestream_streams(raw_data["acestream"], user_data, user_ip)
            if user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
            else []
        )
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
        http_stream_data_list = []
    if "acestream" in disabled:
        formatted_acestream_streams = []
    if "youtube" in disabled:
        youtube_stream_data_list = []

    if usenet_stream_data_list:
        apply_user_scoped_nzb_urls(usenet_stream_data_list, user_data)

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not http_stream_data_list
        and not formatted_acestream_streams
        and not youtube_stream_data_list
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("telegram")

    if http_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=http_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                user_ip=user_ip,
                is_series=False,
                is_http=True,
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("http")

    if youtube_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=youtube_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                user_ip=user_ip,
                is_series=False,
                is_youtube=True,
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("youtube")

    # Run all parse_stream_data calls in parallel
    results = await asyncio.gather(*coros) if coros else []

    # Map results back to stream groups
    stream_groups: dict[str, list[Any]] = {
        "torrent": [],
        "usenet": [],
        "telegram": [],
        "http": [],
        "acestream": formatted_acestream_streams,
        "youtube": [],
    }
    for key, result in zip(coro_keys, results):
        stream_groups[key] = result

    return _combine_streams_by_type(
        user_data,
        stream_groups,
        disable_stream_cap=disable_stream_cap,
    )


async def get_series_streams(
    video_id: str,
    season: int,
    episode: int,
    user_data: UserData,
    secret_str: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
    user_id: int | None = None,
    return_rich: bool = False,
    disable_stream_cap: bool = False,
) -> list[StremioStream] | list[RichStream]:
    """
    Get formatted streams for a series episode.

    Uses Redis caching for raw stream data (user-independent) and applies
    per-user filtering/sorting/debrid-cache-checking on top.
    Parallelizes parse_stream_data calls across stream types.
    """
    # Lazy import to avoid circular dependency
    from utils.parser import parse_stream_data

    live_search_id = _parse_live_search_id(video_id)
    live_search_enabled = user_data.live_search_streams and live_search_id is not None

    # Resolve video_id to media + related IDs (single read session)
    media_lookup_name = f"series media lookup video_id={video_id}"
    media, related_media_ids = await _get_media_and_related_media_ids_by_external_id_with_retry(
        video_id,
        MediaType.SERIES,
        media_lookup_name,
    )
    scraper_metadata = _build_scraper_metadata(media, video_id, MediaType.SERIES)

    if not media and live_search_enabled and live_search_id:
        media, scraper_metadata = await _fetch_missing_media_for_live_search(
            video_id,
            MediaType.SERIES,
            live_search_id[0],
            live_search_id[1],
        )
        if media:
            related_media_ids = await _get_related_media_ids_by_external_id_with_retry(
                video_id,
                MediaType.SERIES,
                f"series related media lookup video_id={video_id}",
            )

    if not media:
        logger.warning(f"Series not found for video_id: {video_id}")
        if not scraper_metadata:
            return []

    visibility_filter = _get_visibility_filter(user_id)

    if media and media.id not in related_media_ids:
        related_media_ids.insert(0, media.id)

    # Get cached or fresh raw stream data (one DB session for all cache misses)
    if related_media_ids:
        raw_payloads = await _get_cached_series_streams_bulk(
            related_media_ids, season, episode, visibility_filter, user_id
        )
        raw_data = _merge_raw_stream_payloads(raw_payloads)
    else:
        raw_data = {
            "torrents": [],
            "usenet": [],
            "telegram": [],
            "http": [],
            "acestream": [],
            "youtube": [],
        }

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

    http_stream_data_list = [HTTPStreamData.model_validate(h) for h in raw_data.get("http", [])]
    youtube_stream_data_list = [YouTubeStreamData.model_validate(yt) for yt in raw_data.get("youtube", [])]

    if live_search_enabled and scraper_metadata:
        live_torrent_streams, live_usenet_streams = await _run_live_search_scrapers(
            user_data=user_data,
            metadata=scraper_metadata,
            catalog_type="series",
            season=season,
            episode=episode,
        )

        unique_live_torrent_streams = _merge_unique_torrent_streams(stream_data_list, live_torrent_streams)
        unique_live_usenet_streams = _merge_unique_usenet_streams(usenet_stream_data_list, live_usenet_streams)

        if unique_live_torrent_streams or unique_live_usenet_streams:
            await cache_live_torrent_fallback_streams(unique_live_torrent_streams)
            background_tasks.add_task(
                _persist_live_search_streams, unique_live_torrent_streams, unique_live_usenet_streams
            )

    formatted_acestream_streams = (
        _deserialize_acestream_rich_streams(raw_data["acestream"], user_data, user_ip)
        if return_rich and user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
        else (
            _deserialize_acestream_streams(raw_data["acestream"], user_data, user_ip)
            if user_data.enable_acestream_streams and _has_mediaflow_config(user_data)
            else []
        )
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
        http_stream_data_list = []
    if "acestream" in disabled:
        formatted_acestream_streams = []
    if "youtube" in disabled:
        youtube_stream_data_list = []

    if usenet_stream_data_list:
        apply_user_scoped_nzb_urls(usenet_stream_data_list, user_data)

    if (
        not stream_data_list
        and not usenet_stream_data_list
        and not telegram_stream_data_list
        and not http_stream_data_list
        and not formatted_acestream_streams
        and not youtube_stream_data_list
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
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
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("telegram")

    if http_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=http_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                season=season,
                episode=episode,
                user_ip=user_ip,
                is_series=True,
                is_http=True,
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("http")

    if youtube_stream_data_list:
        coros.append(
            parse_stream_data(
                streams=youtube_stream_data_list,
                user_data=user_data,
                secret_str=secret_str,
                season=season,
                episode=episode,
                user_ip=user_ip,
                is_series=True,
                is_youtube=True,
                return_rich=return_rich,
                disable_total_stream_cap=disable_stream_cap,
            )
        )
        coro_keys.append("youtube")

    results = await asyncio.gather(*coros) if coros else []

    stream_groups: dict[str, list[Any]] = {
        "torrent": [],
        "usenet": [],
        "telegram": [],
        "http": [],
        "acestream": formatted_acestream_streams,
        "youtube": [],
    }
    for key, result in zip(coro_keys, results):
        stream_groups[key] = result

    return _combine_streams_by_type(
        user_data,
        stream_groups,
        disable_stream_cap=disable_stream_cap,
    )


async def get_tv_streams_formatted(
    video_id: str,
    namespace: str | None,
    user_data: UserData,
) -> list[StremioStream]:
    """
    Get formatted streams for a TV channel.

    TV channels use HTTPStream or YouTubeStream, not TorrentStream.
    """
    # Get media by external_id
    media_lookup_name = f"tv media lookup video_id={video_id}"
    media = await _get_media_by_external_id_with_retry(video_id, MediaType.TV, media_lookup_name)
    if not media:
        logger.warning(f"TV channel not found for video_id: {video_id}")
        return []

    visibility_filter = _get_visibility_filter(user_data.user_id)
    disabled = set(settings.disabled_content_types)
    formatted_streams = []

    async with get_read_session_context() as session:
        # Query HTTP streams (skip if iptv/http disabled)
        if "iptv" not in disabled and "http" not in disabled:
            http_query = (
                select(HTTPStream)
                .join(Stream, Stream.id == HTTPStream.stream_id)
                .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
                .where(StreamMediaLink.media_id == media.id)
                .where(Stream.is_active.is_(True))
                .where(Stream.is_blocked.is_(False))
                .where(visibility_filter)
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
                .where(visibility_filter)
                .options(
                    joinedload(YouTubeStream.stream).options(
                        selectinload(Stream.languages),
                    )
                )
                .limit(100)
            )

            yt_result = await session.exec(yt_query)
            yt_streams = yt_result.unique().all()

            for yt_stream in yt_streams:
                stream = yt_stream.stream
                geo_label = format_geo_restriction_label(
                    yt_stream.geo_restriction_type,
                    yt_stream.geo_restriction_countries,
                )
                display_name = stream.name
                if geo_label:
                    display_name = f"{display_name} [{geo_label}]"
                description = f"▶️ {stream.source}" if stream.source else "▶️ YouTube"
                if geo_label:
                    description = f"{geo_label}\n{description}"
                formatted_streams.append(
                    StremioStream(
                        name=f"{settings.addon_name}\n{display_name}",
                        description=description,
                        ytId=yt_stream.video_id,
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
