"""
Background import tasks for M3U and Xtream content.

These tasks handle large imports asynchronously to avoid HTTP timeouts.
Progress and status are tracked in Redis for client polling.
"""

import asyncio
import json
import logging
from datetime import datetime
from urllib.parse import urlparse

import dramatiq
import pytz

from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)

# Redis key patterns for import jobs
IMPORT_JOB_KEY = "import_job:{job_id}"
IMPORT_JOB_TTL = 3600 * 24  # 24 hours


class ImportJobStatus:
    """Import job status constants."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


async def get_import_job_status(job_id: str) -> dict | None:
    """Get the current status of an import job."""
    key = IMPORT_JOB_KEY.format(job_id=job_id)
    data = await REDIS_ASYNC_CLIENT.get(key)
    if data:
        return json.loads(data)
    return None


async def update_import_job_status(
    job_id: str,
    status: str,
    progress: int = 0,
    total: int = 0,
    stats: dict | None = None,
    error: str | None = None,
    **extra,
):
    """Update the status of an import job in Redis."""
    key = IMPORT_JOB_KEY.format(job_id=job_id)

    # Get existing data to preserve fields
    existing = await get_import_job_status(job_id) or {}

    data = {
        **existing,
        "job_id": job_id,
        "status": status,
        "progress": progress,
        "total": total,
        "stats": stats or existing.get("stats", {}),
        "error": error,
        "updated_at": datetime.now(pytz.UTC).isoformat(),
        **extra,
    }

    await REDIS_ASYNC_CLIENT.set(key, json.dumps(data), ex=IMPORT_JOB_TTL)
    return data


async def create_import_job(
    job_id: str,
    user_id: int,
    source_type: str,
    total_items: int,
    **extra,
) -> dict:
    """Create a new import job entry in Redis."""
    return await update_import_job_status(
        job_id=job_id,
        status=ImportJobStatus.QUEUED,
        progress=0,
        total=total_items,
        stats={"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0},
        user_id=user_id,
        source_type=source_type,
        created_at=datetime.now(pytz.UTC).isoformat(),
        **extra,
    )


async def _process_m3u_import(
    job_id: str,
    user_id: int,
    entries: list[dict],
    source: str,
    is_public: bool,
    override_map: dict[int, dict],
    save_source: bool,
    source_name: str | None,
    m3u_url: str | None,
):
    """Process M3U import in background."""
    from db import database
    from db.enums import IPTVSourceType
    from db.models import IPTVSource
    from api.routers.content.m3u_import import (
        M3UContentType,
        _import_tv_entry,
        _import_movie_entry,
        _import_series_entry,
    )

    await database.init()

    total = len(entries)
    stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}

    await update_import_job_status(
        job_id=job_id,
        status=ImportJobStatus.PROCESSING,
        progress=0,
        total=total,
    )

    try:
        async with database.get_background_session() as session:
            for i, entry in enumerate(entries):
                try:
                    idx = entry["index"]

                    # Apply override if exists
                    if idx in override_map:
                        override = override_map[idx]
                        entry["detected_type"] = override.get("type", entry["detected_type"])
                        if "media_id" in override and override["media_id"]:
                            entry["matched_media_id"] = override["media_id"]

                    content_type = M3UContentType(entry["detected_type"])

                    if content_type == M3UContentType.TV:
                        import_result = await _import_tv_entry(
                            session=session,
                            entry=entry,
                            source=source,
                            user_id=user_id,
                            is_public=is_public,
                        )
                        if import_result["stream_created"]:
                            stats["tv"] += 1
                        elif import_result["stream_existed"]:
                            stats["skipped"] += 1

                    elif content_type == M3UContentType.MOVIE:
                        await _import_movie_entry(
                            session=session,
                            entry=entry,
                            source=source,
                            user_id=user_id,
                            is_public=is_public,
                        )
                        stats["movie"] += 1

                    elif content_type == M3UContentType.SERIES:
                        await _import_series_entry(
                            session=session,
                            entry=entry,
                            source=source,
                            user_id=user_id,
                            is_public=is_public,
                        )
                        stats["series"] += 1

                    else:
                        stats["skipped"] += 1

                except Exception as e:
                    logger.warning(f"Failed to import entry {entry.get('name', 'unknown')}: {e}")
                    stats["failed"] += 1

                # Update progress every 10 items or at the end
                if (i + 1) % 10 == 0 or i == total - 1:
                    await update_import_job_status(
                        job_id=job_id,
                        status=ImportJobStatus.PROCESSING,
                        progress=i + 1,
                        total=total,
                        stats=stats,
                    )

            await session.commit()

            # Save IPTV source if requested
            source_id = None
            if save_source and m3u_url:
                if not source_name:
                    parsed = urlparse(m3u_url)
                    source_name = f"M3U - {parsed.netloc or 'playlist'}"

                iptv_source = IPTVSource(
                    user_id=user_id,
                    source_type=IPTVSourceType.M3U,
                    name=source_name,
                    m3u_url=m3u_url,
                    is_public=is_public,
                    import_live=True,
                    import_vod=True,
                    import_series=True,
                    last_synced_at=datetime.now(pytz.UTC),
                    last_sync_stats=stats,
                    is_active=True,
                )
                session.add(iptv_source)
                await session.commit()
                await session.refresh(iptv_source)
                source_id = iptv_source.id

        # Mark as completed
        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.COMPLETED,
            progress=total,
            total=total,
            stats=stats,
            source_id=source_id,
        )

        logger.info(f"M3U import job {job_id} completed: {stats}")

    except Exception as e:
        logger.exception(f"M3U import job {job_id} failed: {e}")
        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.FAILED,
            stats=stats,
            error=str(e),
        )


async def _process_xtream_import(
    job_id: str,
    user_id: int,
    server_url: str,
    username: str,
    password: str,
    source_name: str,
    is_public: bool,
    save_source: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    live_category_ids: list[str] | None,
    vod_category_ids: list[str] | None,
    series_category_ids: list[str] | None,
):
    """Process Xtream import in background."""
    from db import database
    from db.enums import IPTVSourceType
    from db.models import IPTVSource
    from utils.xtream_client import XtreamClient
    from utils.crypto import ProfileCrypto
    from api.routers.content.m3u_import import (
        _import_tv_entry,
        _import_movie_entry,
        _import_series_entry,
    )

    await database.init()

    stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}
    total_items = 0
    processed = 0

    await update_import_job_status(
        job_id=job_id,
        status=ImportJobStatus.PROCESSING,
        progress=0,
        total=0,
    )

    try:
        client = XtreamClient(server_url, username, password)

        # Count total items to import
        if import_live and live_category_ids:
            for cat_id in live_category_ids:
                streams = await client.get_live_streams(cat_id)
                total_items += len(streams)

        if import_vod and vod_category_ids:
            for cat_id in vod_category_ids:
                streams = await client.get_vod_streams(cat_id)
                total_items += len(streams)

        if import_series and series_category_ids:
            for cat_id in series_category_ids:
                series_list = await client.get_series(cat_id)
                total_items += len(series_list)

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.PROCESSING,
            progress=0,
            total=total_items,
        )

        async with database.get_background_session() as session:
            # Import Live TV
            if import_live and live_category_ids:
                for cat_id in live_category_ids:
                    streams = await client.get_live_streams(cat_id)
                    for stream in streams:
                        try:
                            stream_url = client.build_stream_url("live", str(stream.get("stream_id", "")))
                            entry = {
                                "index": processed,
                                "name": stream.get("name", "Unknown"),
                                "url": stream_url,
                                "logo": stream.get("stream_icon"),
                                "genres": [stream.get("category_name", "")],
                                "detected_type": "tv",
                            }

                            result = await _import_tv_entry(
                                session=session,
                                entry=entry,
                                source="xtream",
                                user_id=user_id,
                                is_public=is_public,
                            )
                            if result["stream_created"]:
                                stats["tv"] += 1
                            else:
                                stats["skipped"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to import Xtream live stream: {e}")
                            stats["failed"] += 1

                        processed += 1
                        if processed % 50 == 0:
                            await update_import_job_status(
                                job_id=job_id,
                                status=ImportJobStatus.PROCESSING,
                                progress=processed,
                                total=total_items,
                                stats=stats,
                            )

                    # Commit after each category
                    await session.commit()

            # Import VOD (Movies)
            if import_vod and vod_category_ids:
                for cat_id in vod_category_ids:
                    streams = await client.get_vod_streams(cat_id)
                    for stream in streams:
                        try:
                            stream_url = client.build_stream_url("movie", str(stream.get("stream_id", "")))
                            entry = {
                                "index": processed,
                                "name": stream.get("name", "Unknown"),
                                "url": stream_url,
                                "logo": stream.get("stream_icon"),
                                "genres": [stream.get("category_name", "")],
                                "detected_type": "movie",
                                "parsed_title": stream.get("name"),
                                "parsed_year": stream.get("year"),
                            }

                            await _import_movie_entry(
                                session=session,
                                entry=entry,
                                source="xtream",
                                user_id=user_id,
                                is_public=is_public,
                            )
                            stats["movie"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to import Xtream VOD: {e}")
                            stats["failed"] += 1

                        processed += 1
                        if processed % 50 == 0:
                            await update_import_job_status(
                                job_id=job_id,
                                status=ImportJobStatus.PROCESSING,
                                progress=processed,
                                total=total_items,
                                stats=stats,
                            )

                    await session.commit()

            # Import Series
            if import_series and series_category_ids:
                for cat_id in series_category_ids:
                    series_list = await client.get_series(cat_id)
                    for series in series_list:
                        try:
                            series_id = str(series.get("series_id", ""))
                            series_info = await client.get_series_info(series_id)

                            episodes = series_info.get("episodes", {})
                            for season_num, season_episodes in episodes.items():
                                for ep in season_episodes:
                                    ep_id = ep.get("id", "")
                                    stream_url = client.build_stream_url("series", str(ep_id))

                                    entry = {
                                        "index": processed,
                                        "name": f"{series.get('name', 'Unknown')} S{season_num}E{ep.get('episode_num', 1)}",
                                        "url": stream_url,
                                        "logo": series.get("cover"),
                                        "genres": [series.get("category_name", "")],
                                        "detected_type": "series",
                                        "parsed_title": series.get("name"),
                                        "season": int(season_num),
                                        "episode": int(ep.get("episode_num", 1)),
                                    }

                                    await _import_series_entry(
                                        session=session,
                                        entry=entry,
                                        source="xtream",
                                        user_id=user_id,
                                        is_public=is_public,
                                    )
                                    stats["series"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to import Xtream series: {e}")
                            stats["failed"] += 1

                        processed += 1
                        if processed % 10 == 0:
                            await update_import_job_status(
                                job_id=job_id,
                                status=ImportJobStatus.PROCESSING,
                                progress=processed,
                                total=total_items,
                                stats=stats,
                            )

                    await session.commit()

            # Save IPTV source if requested
            source_id = None
            if save_source:
                crypto = ProfileCrypto()
                encrypted_creds = crypto.encrypt(
                    json.dumps(
                        {
                            "username": username,
                            "password": password,
                        }
                    )
                )

                iptv_source = IPTVSource(
                    user_id=user_id,
                    source_type=IPTVSourceType.XTREAM,
                    name=source_name,
                    server_url=server_url,
                    encrypted_credentials=encrypted_creds,
                    is_public=is_public,
                    import_live=import_live,
                    import_vod=import_vod,
                    import_series=import_series,
                    live_category_ids=live_category_ids,
                    vod_category_ids=vod_category_ids,
                    series_category_ids=series_category_ids,
                    last_synced_at=datetime.now(pytz.UTC),
                    last_sync_stats=stats,
                    is_active=True,
                )
                session.add(iptv_source)
                await session.commit()
                await session.refresh(iptv_source)
                source_id = iptv_source.id

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.COMPLETED,
            progress=processed,
            total=total_items,
            stats=stats,
            source_id=source_id,
        )

        logger.info(f"Xtream import job {job_id} completed: {stats}")

    except Exception as e:
        logger.exception(f"Xtream import job {job_id} failed: {e}")
        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.FAILED,
            stats=stats,
            error=str(e),
        )


# Dramatiq actors for background processing
@dramatiq.actor(
    priority=5,
    max_retries=1,
    time_limit=3600000,  # 1 hour
    queue_name="import",
)
def run_m3u_import(**kwargs):
    """Dramatiq actor for M3U import."""
    asyncio.run(_process_m3u_import(**kwargs))


@dramatiq.actor(
    priority=5,
    max_retries=1,
    time_limit=3600000,  # 1 hour
    queue_name="import",
)
def run_xtream_import(**kwargs):
    """Dramatiq actor for Xtream import."""
    asyncio.run(_process_xtream_import(**kwargs))


# ============================================
# Sync Background Tasks
# ============================================


async def _process_m3u_sync(
    job_id: str,
    source_id: int,
    user_id: int,
    m3u_url: str,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
):
    """Process M3U sync in background."""
    from db import database
    from db.models import IPTVSource
    from api.routers.content.m3u_import import (
        M3UContentType,
        _import_movie_entry,
        _import_series_entry,
        _import_tv_entry,
    )
    from utils.m3u_parser import parse_m3u_playlist_for_preview

    stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}

    try:
        # Parse M3U
        entries, _, total = await parse_m3u_playlist_for_preview(
            playlist_url=m3u_url,
            preview_limit=100000,
        )

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.PROCESSING,
            progress=0,
            total=len(entries),
        )

        async with database.get_background_session() as session:
            for i, entry in enumerate(entries):
                try:
                    content_type = M3UContentType(entry.get("detected_type", "unknown"))

                    if content_type == M3UContentType.TV and import_live:
                        result = await _import_tv_entry(
                            session=session,
                            entry=entry,
                            source="m3u",
                            user_id=user_id,
                            is_public=is_public,
                        )
                        if result.get("stream_created"):
                            stats["tv"] += 1
                        elif result.get("stream_existed"):
                            stats["skipped"] += 1

                    elif content_type == M3UContentType.MOVIE and import_vod:
                        await _import_movie_entry(
                            session=session,
                            entry=entry,
                            source="m3u",
                            user_id=user_id,
                            is_public=is_public,
                        )
                        stats["movie"] += 1

                    elif content_type == M3UContentType.SERIES and import_series:
                        await _import_series_entry(
                            session=session,
                            entry=entry,
                            source="m3u",
                            user_id=user_id,
                            is_public=is_public,
                        )
                        stats["series"] += 1

                except Exception as e:
                    logger.warning(f"Failed to import entry during sync: {e}")
                    stats["failed"] += 1

                # Update progress periodically
                if (i + 1) % 50 == 0:
                    await update_import_job_status(
                        job_id=job_id,
                        status=ImportJobStatus.PROCESSING,
                        progress=i + 1,
                        total=len(entries),
                        stats=stats,
                    )

            await session.commit()

            # Update source sync metadata
            from sqlmodel import select

            query = select(IPTVSource).where(IPTVSource.id == source_id)
            result = await session.exec(query)
            source = result.first()
            if source:
                source.last_synced_at = datetime.now(pytz.UTC)
                source.last_sync_stats = stats
                session.add(source)
                await session.commit()

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.COMPLETED,
            progress=len(entries),
            total=len(entries),
            stats=stats,
            source_id=source_id,
        )

        logger.info(f"M3U sync job {job_id} completed: {stats}")

    except Exception as e:
        logger.exception(f"M3U sync job {job_id} failed: {e}")
        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.FAILED,
            stats=stats,
            error=str(e),
        )


async def _process_xtream_sync(
    job_id: str,
    source_id: int,
    user_id: int,
    server_url: str,
    encrypted_credentials: str,
    is_public: bool,
    import_live: bool,
    import_vod: bool,
    import_series: bool,
    live_category_ids: list[str] | None,
    vod_category_ids: list[str] | None,
    series_category_ids: list[str] | None,
):
    """Process Xtream sync in background."""
    from db import database
    from db.models import IPTVSource
    from api.routers.content.m3u_import import (
        _import_movie_entry,
        _import_tv_entry,
    )
    from utils.profile_crypto import profile_crypto
    from utils.xtream_client import XtreamClient

    stats = {"tv": 0, "movie": 0, "series": 0, "failed": 0, "skipped": 0}

    try:
        # Decrypt credentials
        creds = profile_crypto.decrypt_secrets(encrypted_credentials)

        client = XtreamClient(
            server_url=server_url,
            username=creds.get("username", ""),
            password=creds.get("password", ""),
        )

        await client.authenticate()

        # Count total items
        total_items = 0
        if import_live:
            live_streams = await client.get_live_streams()
            if live_category_ids:
                live_streams = [s for s in live_streams if str(s.get("category_id", "")) in live_category_ids]
            total_items += len(live_streams)

        if import_vod:
            vod_streams = await client.get_vod_streams()
            if vod_category_ids:
                vod_streams = [s for s in vod_streams if str(s.get("category_id", "")) in vod_category_ids]
            total_items += len(vod_streams)

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.PROCESSING,
            progress=0,
            total=total_items,
        )

        processed = 0
        async with database.get_background_session() as session:
            # Import live streams
            if import_live:
                for stream in live_streams:
                    try:
                        stream_url = client.build_live_url(str(stream.get("stream_id", "")))
                        entry = {
                            "name": stream.get("name", "Unknown"),
                            "url": stream_url,
                            "logo": stream.get("stream_icon"),
                            "genres": [],
                            "index": 0,
                        }

                        result = await _import_tv_entry(
                            session=session,
                            entry=entry,
                            source="xtream",
                            user_id=user_id,
                            is_public=is_public,
                        )

                        if result.get("stream_created"):
                            stats["tv"] += 1
                        elif result.get("stream_existed"):
                            stats["skipped"] += 1

                    except Exception as e:
                        logger.warning(f"Failed to sync live stream: {e}")
                        stats["failed"] += 1

                    processed += 1
                    if processed % 50 == 0:
                        await update_import_job_status(
                            job_id=job_id,
                            status=ImportJobStatus.PROCESSING,
                            progress=processed,
                            total=total_items,
                            stats=stats,
                        )

                await session.commit()

            # Import VOD
            if import_vod:
                for stream in vod_streams:
                    try:
                        ext = stream.get("container_extension", "mkv")
                        stream_url = client.build_vod_url(str(stream.get("stream_id", "")), ext)
                        entry = {
                            "name": stream.get("name", "Unknown"),
                            "url": stream_url,
                            "logo": stream.get("stream_icon"),
                            "parsed_title": stream.get("name"),
                            "parsed_year": None,
                            "index": 0,
                        }

                        await _import_movie_entry(
                            session=session,
                            entry=entry,
                            source="xtream",
                            user_id=user_id,
                            is_public=is_public,
                        )
                        stats["movie"] += 1

                    except Exception as e:
                        logger.warning(f"Failed to sync VOD: {e}")
                        stats["failed"] += 1

                    processed += 1
                    if processed % 50 == 0:
                        await update_import_job_status(
                            job_id=job_id,
                            status=ImportJobStatus.PROCESSING,
                            progress=processed,
                            total=total_items,
                            stats=stats,
                        )

                await session.commit()

            # Update source sync metadata
            from sqlmodel import select

            query = select(IPTVSource).where(IPTVSource.id == source_id)
            result = await session.exec(query)
            source = result.first()
            if source:
                source.last_synced_at = datetime.now(pytz.UTC)
                source.last_sync_stats = stats
                session.add(source)
                await session.commit()

        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.COMPLETED,
            progress=total_items,
            total=total_items,
            stats=stats,
            source_id=source_id,
        )

        logger.info(f"Xtream sync job {job_id} completed: {stats}")

    except Exception as e:
        logger.exception(f"Xtream sync job {job_id} failed: {e}")
        await update_import_job_status(
            job_id=job_id,
            status=ImportJobStatus.FAILED,
            stats=stats,
            error=str(e),
        )


@dramatiq.actor(
    priority=5,
    max_retries=1,
    time_limit=3600000,  # 1 hour
    queue_name="import",
)
def run_m3u_sync(**kwargs):
    """Dramatiq actor for M3U sync."""
    asyncio.run(_process_m3u_sync(**kwargs))


@dramatiq.actor(
    priority=5,
    max_retries=1,
    time_limit=3600000,  # 1 hour
    queue_name="import",
)
def run_xtream_sync(**kwargs):
    """Dramatiq actor for Xtream sync."""
    asyncio.run(_process_xtream_sync(**kwargs))
