"""
Admin API endpoints for metrics and monitoring.
Migrated from metrics/routes.py - now with proper authentication.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import humanize
from fastapi import APIRouter, Depends, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db import crud
from db.database import get_read_session
from db.enums import ContributionStatus, UserRole
from db.enums import WatchAction
from db.models import (
    Contribution,
    MetadataVote,
    PlaybackTracking,
    RSSFeed,
    StreamVote,
    User,
    UserLibraryItem,
    UserProfile,
    WatchHistory,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.base_scraper import (
    SCRAPER_METRICS_AGGREGATED_KEY,
    SCRAPER_METRICS_HISTORY_KEY,
    SCRAPER_METRICS_LATEST_KEY,
)
from utils import const

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/metrics", tags=["Admin Metrics"])


# ============================================
# Scraper Metrics Pydantic Models
# ============================================


class ScraperMetricsSummary(BaseModel):
    """Summary of a single scraper run"""

    scraper_name: str
    timestamp: str
    end_timestamp: str
    duration_seconds: float
    meta_id: str | None = None
    meta_title: str | None = None
    season: int | None = None
    episode: int | None = None
    skip_scraping: bool = False
    total_items: dict[str, int]
    error_counts: dict[str, int]
    skip_reasons: dict[str, int]
    quality_distribution: dict[str, int]
    source_distribution: dict[str, int]
    indexer_stats: dict[str, Any] | None = None


class ScraperAggregatedStats(BaseModel):
    """Aggregated statistics for a scraper over time"""

    scraper_name: str
    total_runs: int
    total_items_found: int
    total_items_processed: int
    total_items_skipped: int
    total_errors: int
    total_duration_seconds: float
    successful_runs: int
    failed_runs: int
    skipped_runs: int
    error_distribution: dict[str, int]
    skip_reason_distribution: dict[str, int]
    quality_distribution: dict[str, int]
    source_distribution: dict[str, int]
    last_run: str | None = None
    last_successful_run: str | None = None
    # Computed fields
    success_rate: float | None = None
    avg_duration_seconds: float | None = None
    avg_items_per_run: float | None = None


class ScraperMetricsResponse(BaseModel):
    """Response for scraper metrics overview"""

    timestamp: str
    scrapers: list[dict[str, Any]]
    total_scrapers: int


class ScraperHistoryResponse(BaseModel):
    """Response for scraper run history"""

    scraper_name: str
    history: list[ScraperMetricsSummary]
    total: int


# Prometheus gauges
total_torrents_gauge = Gauge("total_torrents", "Total number of torrents")
torrent_sources_gauge = Gauge("torrent_sources", "Total number of torrents by source", labelnames=["source"])
metadata_count_gauge = Gauge(
    "metadata_count",
    "Total number of metadata in the database",
    labelnames=["metadata_type"],
)
spider_last_run_gauge = Gauge(
    "spider_last_run_time",
    "Seconds since the last run of each spider, labeled by spider name",
    labelnames=["spider_name"],
)


# ============================================
# Helper Functions (from metrics/redis_metrics.py)
# ============================================


async def get_redis_metrics() -> dict[str, Any]:
    """Get comprehensive Redis metrics."""
    async_pool = REDIS_ASYNC_CLIENT.connection_pool

    pool_stats = {
        "app_connections": {
            "async": {
                "in_use": len(async_pool._in_use_connections),
                "available": len(async_pool._available_connections),
                "max": async_pool.max_connections,
            },
        }
    }

    try:
        info_stats = await REDIS_ASYNC_CLIENT.info()

        return {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "app_pool_stats": pool_stats,
            "memory": {
                "used_memory_human": info_stats.get("used_memory_human"),
                "used_memory_peak_human": info_stats.get("used_memory_peak_human"),
                "maxmemory_human": info_stats.get("maxmemory_human"),
                "mem_fragmentation_ratio": info_stats.get("mem_fragmentation_ratio"),
            },
            "connections": {
                "connected_clients": info_stats.get("connected_clients"),
                "blocked_clients": info_stats.get("blocked_clients"),
                "maxclients": info_stats.get("maxclients"),
            },
            "performance": {
                "instantaneous_ops_per_sec": info_stats.get("instantaneous_ops_per_sec"),
                "total_commands_processed": info_stats.get("total_commands_processed"),
            },
            "cache": {
                "keyspace_hits": info_stats.get("keyspace_hits"),
                "keyspace_misses": info_stats.get("keyspace_misses"),
                "hit_rate": (
                    info_stats.get("keyspace_hits", 0)
                    / (info_stats.get("keyspace_hits", 0) + info_stats.get("keyspace_misses", 1))
                    * 100
                    if info_stats.get("keyspace_hits") is not None and info_stats.get("keyspace_misses") is not None
                    else 0
                ),
            },
        }
    except Exception as e:
        logger.error(f"Error getting Redis metrics: {e}")
        return {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "error": str(e),
            "app_pool_stats": pool_stats,
        }


async def get_debrid_cache_metrics() -> dict[str, Any]:
    """Get debrid cache metrics per service."""
    metrics = {"timestamp": datetime.now(tz=UTC).isoformat(), "services": {}}

    debrid_services = [
        "alldebrid",
        "debridlink",
        "offcloud",
        "pikpak",
        "premiumize",
        "qbittorrent",
        "realdebrid",
        "seedr",
        "torbox",
        "easydebrid",
        "debrider",
    ]

    try:
        for service in debrid_services:
            cache_key = f"debrid_cache:{service}"
            cache_size = await REDIS_ASYNC_CLIENT.hlen(cache_key)
            if cache_size > 0:
                metrics["services"][service] = {"cached_torrents": cache_size}

        metrics["services"] = dict(
            sorted(
                metrics["services"].items(),
                key=lambda x: x[1]["cached_torrents"],
                reverse=True,
            )
        )
        return metrics
    except Exception as e:
        logger.error(f"Error getting debrid cache metrics: {e}")
        return {"timestamp": datetime.now(tz=UTC).isoformat(), "error": str(e)}


# ============================================
# Metrics Endpoints (Admin only)
# ============================================


@router.get("/torrents")
async def get_torrents_count(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get total torrent count (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)
    count = await crud.streams.get_torrent_count(session)
    return {
        "total_torrents": count,
        "total_torrents_readable": humanize.intword(count),
    }


@router.get("/torrents/sources")
async def get_torrents_by_sources(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get torrents grouped by source (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    cache_key = "admin:torrents:sources"
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_data:
        return json.loads(cached_data)

    torrent_sources = await crud.streams.get_torrents_by_source(session, limit=20)
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(torrent_sources), ex=1800)
    return torrent_sources


@router.get("/metadata")
async def get_total_metadata(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get metadata counts by type (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)
    return await crud.media.get_metadata_counts(session)


@router.get("/scrapy-schedulers")
async def get_schedulers_last_run(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get last run time for all scrapy schedulers (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)
    tasks = [
        crud.scraper_helpers.fetch_last_run(spider_id, spider_name)
        for spider_id, spider_name in const.SCRAPY_SPIDERS.items()
    ]
    results = await asyncio.gather(*tasks)
    return results


async def update_prometheus_metrics(session: AsyncSession, response: Response):
    """Update all Prometheus gauges."""
    count = await crud.streams.get_torrent_count(session)
    total_torrents_gauge.set(count)

    torrent_sources = await crud.streams.get_torrents_by_source(session, limit=20)
    for source in torrent_sources:
        torrent_sources_gauge.labels(source=source["name"]).set(source["count"])

    results = await crud.media.get_metadata_counts(session)
    metadata_count_gauge.labels(metadata_type="movies").set(results.get("movies", 0))
    metadata_count_gauge.labels(metadata_type="series").set(results.get("series", 0))
    metadata_count_gauge.labels(metadata_type="tv_channels").set(results.get("tv_channels", 0))


@router.get("/prometheus")
async def prometheus_metrics(
    request: Request,
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Export Prometheus metrics (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)
    await update_prometheus_metrics(session, response)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/redis")
async def redis_metrics(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get Redis server metrics (Admin only)."""
    return await get_redis_metrics()


@router.get("/debrid-cache")
async def debrid_cache_metrics_endpoint(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get debrid cache metrics per service (Admin only)."""
    return await get_debrid_cache_metrics()


@router.get("/torrents/uploaders")
async def get_torrents_by_uploaders(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get torrents grouped by uploader (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)
    return await crud.streams.get_torrents_by_uploader(session, limit=20)


@router.get("/torrents/uploaders/weekly/{week_date}")
async def get_weekly_top_uploaders_endpoint(
    week_date: str,
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get weekly top uploaders (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        selected_date = datetime.strptime(week_date, "%Y-%m-%d").replace(tzinfo=UTC)
        start_of_week = selected_date - timedelta(days=selected_date.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_week = start_of_week + timedelta(days=7)

        raw_results = await crud.streams.get_weekly_top_uploaders(session, start_of_week, end_of_week)

        # Process results - latest_upload is already ISO string from crud function
        processed_results = []
        anonymous_total = 0
        anonymous_latest = None

        for stat in raw_results:
            uploader_name = stat.get("name")
            count = stat.get("count", 0)
            latest_upload = stat.get("latest_upload")  # Already ISO string

            if count <= 0:
                continue

            if not uploader_name or uploader_name.strip() == "" or uploader_name.lower() == "anonymous":
                anonymous_total += count
                if anonymous_latest is None or (latest_upload and latest_upload > anonymous_latest):
                    anonymous_latest = latest_upload
            else:
                processed_results.append(
                    {
                        "name": uploader_name.strip(),
                        "count": count,
                        "latest_upload": latest_upload,  # Already formatted
                    }
                )

        if anonymous_total > 0:
            processed_results.append(
                {
                    "name": "Anonymous",
                    "count": anonymous_total,
                    "latest_upload": anonymous_latest,  # Already formatted
                }
            )

        final_results = sorted(processed_results, key=lambda x: x["count"], reverse=True)[:20]

        return {
            "week_start": start_of_week.strftime("%Y-%m-%d"),
            "week_end": end_of_week.strftime("%Y-%m-%d"),
            "uploaders": final_results,
        }

    except ValueError as e:
        return {"error": f"Invalid date format. Please use YYYY-MM-DD format. Details: {str(e)}"}
    except Exception as e:
        return {"error": f"An error occurred: {str(e)}"}


# ============================================
# User & System Statistics Endpoints
# ============================================


@router.get("/users/stats")
async def get_user_stats(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get comprehensive user statistics (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        now = datetime.now(tz=UTC)

        # Total users
        total_users_result = await session.exec(select(func.count(User.id)))
        total_users = total_users_result.first() or 0

        # Active users (logged in within last 7 days)
        week_ago = now - timedelta(days=7)
        active_weekly_result = await session.exec(select(func.count(User.id)).where(User.last_login >= week_ago))
        active_weekly = active_weekly_result.first() or 0

        # Active users (logged in within last 30 days)
        month_ago = now - timedelta(days=30)
        active_monthly_result = await session.exec(select(func.count(User.id)).where(User.last_login >= month_ago))
        active_monthly = active_monthly_result.first() or 0

        # Active users (logged in within last 24 hours)
        day_ago = now - timedelta(hours=24)
        active_daily_result = await session.exec(select(func.count(User.id)).where(User.last_login >= day_ago))
        active_daily = active_daily_result.first() or 0

        # Users by role
        role_counts_result = await session.exec(
            select(User.role, func.count(User.id).label("count")).group_by(User.role)
        )
        users_by_role = {row.role.value: row.count for row in role_counts_result.all()}

        # Verified vs unverified
        verified_result = await session.exec(select(func.count(User.id)).where(User.is_verified.is_(True)))
        verified_count = verified_result.first() or 0

        # Users by contribution level
        contribution_level_result = await session.exec(
            select(User.contribution_level, func.count(User.id).label("count")).group_by(User.contribution_level)
        )
        users_by_contribution_level = {row.contribution_level: row.count for row in contribution_level_result.all()}

        # New users (registered in last 7 days)
        new_users_result = await session.exec(select(func.count(User.id)).where(User.created_at >= week_ago))
        new_users_weekly = new_users_result.first() or 0

        # Total profiles
        total_profiles_result = await session.exec(select(func.count(UserProfile.id)))
        total_profiles = total_profiles_result.first() or 0

        return {
            "timestamp": now.isoformat(),
            "total_users": total_users,
            "active_users": {
                "daily": active_daily,
                "weekly": active_weekly,
                "monthly": active_monthly,
            },
            "new_users_this_week": new_users_weekly,
            "verified_users": verified_count,
            "unverified_users": total_users - verified_count,
            "users_by_role": users_by_role,
            "users_by_contribution_level": users_by_contribution_level,
            "total_profiles": total_profiles,
            "avg_profiles_per_user": round(total_profiles / total_users, 2) if total_users > 0 else 0,
        }
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        return {"error": str(e)}


@router.get("/contributions/stats")
async def get_contribution_stats(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get contribution statistics (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        now = datetime.now(tz=UTC)
        week_ago = now - timedelta(days=7)

        # Total contributions
        total_contributions_result = await session.exec(select(func.count(Contribution.id)))
        total_contributions = total_contributions_result.first() or 0

        # Contributions by status
        status_counts_result = await session.exec(
            select(Contribution.status, func.count(Contribution.id).label("count")).group_by(Contribution.status)
        )
        contributions_by_status = {row.status.value: row.count for row in status_counts_result.all()}

        # Pending contributions count
        pending_count = contributions_by_status.get(ContributionStatus.PENDING.value, 0)

        # Recent contributions (last 7 days)
        recent_contributions_result = await session.exec(
            select(func.count(Contribution.id)).where(Contribution.created_at >= week_ago)
        )
        recent_contributions = recent_contributions_result.first() or 0

        # Total stream votes
        total_votes_result = await session.exec(select(func.count(StreamVote.id)))
        total_stream_votes = total_votes_result.first() or 0

        # Total metadata votes
        metadata_votes_result = await session.exec(select(func.count(MetadataVote.id)))
        total_metadata_votes = metadata_votes_result.first() or 0

        # Unique contributors
        unique_contributors_result = await session.exec(select(func.count(func.distinct(Contribution.user_id))))
        unique_contributors = unique_contributors_result.first() or 0

        return {
            "timestamp": now.isoformat(),
            "total_contributions": total_contributions,
            "contributions_by_status": contributions_by_status,
            "pending_review": pending_count,
            "recent_contributions_week": recent_contributions,
            "total_stream_votes": total_stream_votes,
            "total_metadata_votes": total_metadata_votes,
            "unique_contributors": unique_contributors,
        }
    except Exception as e:
        logger.error(f"Error getting contribution stats: {e}")
        return {"error": str(e)}


@router.get("/activity/stats")
async def get_activity_stats(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get user activity statistics (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        now = datetime.now(tz=UTC)
        week_ago = now - timedelta(days=7)

        # Total watch history entries
        total_watch_history_result = await session.exec(select(func.count(WatchHistory.id)))
        total_watch_history = total_watch_history_result.first() or 0

        # Recent watch history (last 7 days)
        recent_watch_result = await session.exec(
            select(func.count(WatchHistory.id)).where(WatchHistory.created_at >= week_ago)
        )
        recent_watch_history = recent_watch_result.first() or 0

        # Total downloads (watch history entries with action=downloaded)
        total_downloads_result = await session.exec(
            select(func.count(WatchHistory.id)).where(WatchHistory.action == WatchAction.DOWNLOADED)
        )
        total_downloads = total_downloads_result.first() or 0

        # Total library items
        total_library_result = await session.exec(select(func.count(UserLibraryItem.id)))
        total_library_items = total_library_result.first() or 0

        # Total playback tracking entries
        total_playback_result = await session.exec(select(func.count(PlaybackTracking.id)))
        total_playback_entries = total_playback_result.first() or 0

        # Total play count
        total_plays_result = await session.exec(select(func.sum(PlaybackTracking.play_count)))
        total_plays = total_plays_result.first() or 0

        # Unique users with watch history
        unique_watchers_result = await session.exec(select(func.count(func.distinct(WatchHistory.user_id))))
        unique_watchers = unique_watchers_result.first() or 0

        # RSS feeds stats
        total_rss_feeds_result = await session.exec(select(func.count(RSSFeed.id)))
        total_rss_feeds = total_rss_feeds_result.first() or 0

        active_rss_feeds_result = await session.exec(select(func.count(RSSFeed.id)).where(RSSFeed.is_active.is_(True)))
        active_rss_feeds = active_rss_feeds_result.first() or 0

        return {
            "timestamp": now.isoformat(),
            "watch_history": {
                "total_entries": total_watch_history,
                "recent_week": recent_watch_history,
                "unique_users": unique_watchers,
            },
            "downloads": {
                "total": total_downloads,
            },
            "library": {
                "total_items": total_library_items,
            },
            "playback": {
                "total_entries": total_playback_entries,
                "total_plays": total_plays or 0,
            },
            "rss_feeds": {
                "total": total_rss_feeds,
                "active": active_rss_feeds,
            },
        }
    except Exception as e:
        logger.error(f"Error getting activity stats: {e}")
        return {"error": str(e)}


@router.get("/system/overview")
async def get_system_overview(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get comprehensive system overview (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        now = datetime.now(tz=UTC)

        # Gather all metrics in parallel
        torrent_count_task = crud.streams.get_torrent_count(session)
        metadata_task = crud.media.get_metadata_counts(session)

        torrent_count = await torrent_count_task
        metadata_counts = await metadata_task

        # User stats
        total_users_result = await session.exec(select(func.count(User.id)))
        total_users = total_users_result.first() or 0

        day_ago = now - timedelta(hours=24)
        active_daily_result = await session.exec(select(func.count(User.id)).where(User.last_login >= day_ago))
        active_daily = active_daily_result.first() or 0

        # Total content
        total_content = (
            metadata_counts.get("movies", 0) + metadata_counts.get("series", 0) + metadata_counts.get("tv_channels", 0)
        )

        # Pending moderation
        pending_contributions_result = await session.exec(
            select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.PENDING)
        )
        pending_contributions = pending_contributions_result.first() or 0

        return {
            "timestamp": now.isoformat(),
            "torrents": {
                "total": torrent_count,
                "formatted": humanize.intword(torrent_count),
            },
            "content": {
                "total": total_content,
                "movies": metadata_counts.get("movies", 0),
                "series": metadata_counts.get("series", 0),
                "tv_channels": metadata_counts.get("tv_channels", 0),
            },
            "users": {
                "total": total_users,
                "active_today": active_daily,
            },
            "moderation": {
                "pending_contributions": pending_contributions,
            },
        }
    except Exception as e:
        logger.error(f"Error getting system overview: {e}")
        return {"error": str(e)}


# ============================================
# Scraper Metrics Endpoints
# ============================================


async def get_all_scraper_names() -> list[str]:
    """Get all scraper names that have metrics stored in Redis"""
    scraper_names = set()

    # Scan for latest metrics keys
    try:
        async for key in REDIS_ASYNC_CLIENT.scan_iter(match=f"{SCRAPER_METRICS_LATEST_KEY}*"):
            if isinstance(key, bytes):
                key = key.decode()
            scraper_name = key.replace(SCRAPER_METRICS_LATEST_KEY, "")
            if scraper_name:
                scraper_names.add(scraper_name)
    except Exception as e:
        logger.warning(f"Error scanning for scraper metrics keys: {e}")

    return sorted(list(scraper_names))


@router.get("/scrapers", response_model=ScraperMetricsResponse)
async def get_scraper_metrics_overview(
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get an overview of all scraper metrics including latest run and aggregated stats.
    """
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        scraper_names = await get_all_scraper_names()
        scrapers = []

        for scraper_name in scraper_names:
            # Get latest metrics
            latest_key = f"{SCRAPER_METRICS_LATEST_KEY}{scraper_name}"
            latest_raw = await REDIS_ASYNC_CLIENT.get(latest_key)

            # Get aggregated stats
            agg_key = f"{SCRAPER_METRICS_AGGREGATED_KEY}{scraper_name}"
            agg_raw = await REDIS_ASYNC_CLIENT.get(agg_key)

            scraper_data = {
                "scraper_name": scraper_name,
                "latest": None,
                "aggregated": None,
            }

            if latest_raw:
                try:
                    scraper_data["latest"] = json.loads(latest_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            if agg_raw:
                try:
                    agg_stats = json.loads(agg_raw)
                    # Compute derived fields
                    if agg_stats.get("total_runs", 0) > 0:
                        total_runs = agg_stats["total_runs"]
                        agg_stats["success_rate"] = round((agg_stats.get("successful_runs", 0) / total_runs) * 100, 2)
                        agg_stats["avg_duration_seconds"] = round(
                            agg_stats.get("total_duration_seconds", 0) / total_runs, 2
                        )
                        agg_stats["avg_items_per_run"] = round(
                            agg_stats.get("total_items_processed", 0) / total_runs, 2
                        )
                    scraper_data["aggregated"] = agg_stats
                except (json.JSONDecodeError, TypeError):
                    pass

            scrapers.append(scraper_data)

        return ScraperMetricsResponse(
            timestamp=datetime.now(tz=UTC).isoformat(),
            scrapers=scrapers,
            total_scrapers=len(scrapers),
        )
    except Exception as e:
        logger.error(f"Error getting scraper metrics overview: {e}")
        return ScraperMetricsResponse(
            timestamp=datetime.now(tz=UTC).isoformat(),
            scrapers=[],
            total_scrapers=0,
        )


@router.get("/scrapers/{scraper_name}", response_model=ScraperAggregatedStats)
async def get_scraper_aggregated_stats(
    scraper_name: str,
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get aggregated statistics for a specific scraper."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        agg_key = f"{SCRAPER_METRICS_AGGREGATED_KEY}{scraper_name}"
        agg_raw = await REDIS_ASYNC_CLIENT.get(agg_key)

        if not agg_raw:
            return ScraperAggregatedStats(
                scraper_name=scraper_name,
                total_runs=0,
                total_items_found=0,
                total_items_processed=0,
                total_items_skipped=0,
                total_errors=0,
                total_duration_seconds=0,
                successful_runs=0,
                failed_runs=0,
                skipped_runs=0,
                error_distribution={},
                skip_reason_distribution={},
                quality_distribution={},
                source_distribution={},
            )

        agg_stats = json.loads(agg_raw)

        # Compute derived fields
        if agg_stats.get("total_runs", 0) > 0:
            total_runs = agg_stats["total_runs"]
            agg_stats["success_rate"] = round((agg_stats.get("successful_runs", 0) / total_runs) * 100, 2)
            agg_stats["avg_duration_seconds"] = round(agg_stats.get("total_duration_seconds", 0) / total_runs, 2)
            agg_stats["avg_items_per_run"] = round(agg_stats.get("total_items_processed", 0) / total_runs, 2)

        return ScraperAggregatedStats(**agg_stats)
    except Exception as e:
        logger.error(f"Error getting scraper aggregated stats: {e}")
        return ScraperAggregatedStats(
            scraper_name=scraper_name,
            total_runs=0,
            total_items_found=0,
            total_items_processed=0,
            total_items_skipped=0,
            total_errors=0,
            total_duration_seconds=0,
            successful_runs=0,
            failed_runs=0,
            skipped_runs=0,
            error_distribution={},
            skip_reason_distribution={},
            quality_distribution={},
            source_distribution={},
        )


@router.get("/scrapers/{scraper_name}/history", response_model=ScraperHistoryResponse)
async def get_scraper_history(
    scraper_name: str,
    response: Response,
    limit: int = Query(20, ge=1, le=100, description="Number of history entries to return"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get run history for a specific scraper."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        history_key = f"{SCRAPER_METRICS_HISTORY_KEY}{scraper_name}"
        history_raw = await REDIS_ASYNC_CLIENT.lrange(history_key, 0, limit - 1)

        history = []
        for entry_raw in history_raw:
            try:
                if isinstance(entry_raw, bytes):
                    entry_raw = entry_raw.decode()
                entry = json.loads(entry_raw)
                history.append(ScraperMetricsSummary(**entry))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Error parsing scraper history entry: {e}")
                continue

        return ScraperHistoryResponse(
            scraper_name=scraper_name,
            history=history,
            total=len(history),
        )
    except Exception as e:
        logger.error(f"Error getting scraper history: {e}")
        return ScraperHistoryResponse(
            scraper_name=scraper_name,
            history=[],
            total=0,
        )


@router.get("/scrapers/{scraper_name}/latest")
async def get_scraper_latest_metrics(
    scraper_name: str,
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get the latest metrics for a specific scraper."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        latest_key = f"{SCRAPER_METRICS_LATEST_KEY}{scraper_name}"
        latest_raw = await REDIS_ASYNC_CLIENT.get(latest_key)

        if not latest_raw:
            return {"error": f"No metrics found for scraper '{scraper_name}'"}

        return json.loads(latest_raw)
    except Exception as e:
        logger.error(f"Error getting latest scraper metrics: {e}")
        return {"error": str(e)}


@router.delete("/scrapers/{scraper_name}/metrics")
async def clear_scraper_metrics(
    scraper_name: str,
    response: Response,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Clear all stored metrics for a specific scraper (Admin only)."""
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        keys_to_delete = [
            f"{SCRAPER_METRICS_LATEST_KEY}{scraper_name}",
            f"{SCRAPER_METRICS_HISTORY_KEY}{scraper_name}",
            f"{SCRAPER_METRICS_AGGREGATED_KEY}{scraper_name}",
        ]

        deleted_count = 0
        for key in keys_to_delete:
            result = await REDIS_ASYNC_CLIENT.delete(key)
            deleted_count += result if result else 0

        return {
            "message": f"Cleared metrics for scraper '{scraper_name}'",
            "keys_deleted": deleted_count,
        }
    except Exception as e:
        logger.error(f"Error clearing scraper metrics: {e}")
        return {"error": str(e)}
