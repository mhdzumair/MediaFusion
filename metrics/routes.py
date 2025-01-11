import asyncio
import json
from datetime import datetime, timezone, timedelta

import humanize
from fastapi import APIRouter, Request, Response
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from db.config import settings
from db.crud import fetch_last_run
from db.models import (
    MediaFusionMetaData,
    TorrentStreams,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from metrics.redis_metrics import get_redis_metrics, get_debrid_cache_metrics
from utils import const
from utils.runtime_const import TEMPLATES

metrics_router = APIRouter()
total_torrents_gauge = Gauge("total_torrents", "Total number of torrents")
torrent_sources_gauge = Gauge(
    "torrent_sources", "Total number of torrents by source", labelnames=["source"]
)
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


@metrics_router.get("/", tags=["metrics"])
async def render_dashboard(
    request: Request,
    response: Response,
):
    response.headers.update(const.NO_CACHE_HEADERS)
    return TEMPLATES.TemplateResponse(
        "html/metrics.html",
        {
            "request": request,
            "logo_url": settings.logo_url,
            "addon_name": settings.addon_name,
        },
    )


@metrics_router.get("/torrents", tags=["metrics"])
async def get_torrents_count(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    count = await TorrentStreams.get_motor_collection().estimated_document_count()
    return {
        "total_torrents": count,
        "total_torrents_readable": humanize.intword(count),
    }


@metrics_router.get("/torrents/sources", tags=["metrics"])
async def get_torrents_by_sources(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)

    cache_key = "torrents:sources"
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)

    if cached_data:
        return json.loads(cached_data)

    results = await TorrentStreams.aggregate(
        [
            {"$group": {"_id": "$source", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20},  # Limit to top 20 sources
        ]
    ).to_list()
    torrent_sources = [
        {"name": source["_id"], "count": source["count"]} for source in results
    ]

    # Cache the results for 5 minutes
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(torrent_sources), ex=300)
    return torrent_sources


@metrics_router.get("/metadata", tags=["metrics"])
async def get_total_metadata(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    results = await asyncio.gather(
        MediaFusionMetaData.get_motor_collection().count_documents({"type": "movie"}),
        MediaFusionMetaData.get_motor_collection().count_documents({"type": "series"}),
        MediaFusionMetaData.get_motor_collection().count_documents({"type": "tv"}),
    )
    movies_count, series_count, tv_channels_count = results

    return {
        "movies": movies_count,
        "series": series_count,
        "tv_channels": tv_channels_count,
    }


@metrics_router.get("/scrapy-schedulers", tags=["metrics"])
async def get_schedulers_last_run(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    tasks = [
        fetch_last_run(spider_id, spider_name)
        for spider_id, spider_name in const.SCRAPY_SPIDERS.items()
    ]
    results = await asyncio.gather(*tasks)
    return results


async def update_metrics(request: Request, response: Response):
    # Define each task as a coroutine
    count_task = TorrentStreams.count()
    torrent_sources_task = get_torrents_by_sources(response)
    total_metadata_task = get_total_metadata(response)
    schedulers_last_run_task = get_schedulers_last_run(request, response)

    # Run all tasks concurrently
    count, torrent_sources, results, stats = await asyncio.gather(
        count_task, torrent_sources_task, total_metadata_task, schedulers_last_run_task
    )

    # Update torrent count
    total_torrents_gauge.set(count)

    # Update torrent sources
    for source in torrent_sources:
        torrent_sources_gauge.labels(source=source["name"]).set(source["count"])

    # Update metadata counts
    metadata_count_gauge.labels(metadata_type="movies").set(results["movies"])
    metadata_count_gauge.labels(metadata_type="series").set(results["series"])
    metadata_count_gauge.labels(metadata_type="tv_channels").set(results["tv_channels"])

    # Update spider metrics
    for data in stats:
        spider_last_run_gauge.labels(spider_name=data["name"]).set(
            data["time_since_last_run_seconds"]
        )


@metrics_router.get("/prometheus-metrics", tags=["metrics"])
async def prometheus_metrics(request: Request, response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    await update_metrics(request, response)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@metrics_router.get("/redis")
async def redis_metrics():
    return await get_redis_metrics()


@metrics_router.get("/debrid-cache")
async def debrid_cache_metrics():
    """
    Get comprehensive metrics about debrid cache usage.
    Returns statistics about cache size, memory usage, and usage patterns per service.
    """
    return await get_debrid_cache_metrics()


@metrics_router.get("/torrents/uploaders", tags=["metrics"])
async def get_torrents_by_uploaders(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    results = await TorrentStreams.aggregate(
        [
            {"$match": {"uploader": {"$ne": None}}},
            {"$group": {"_id": "$uploader", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20},  # Limit to top 20 uploaders
        ]
    ).to_list()

    uploader_stats = [
        {"name": stat["_id"] if stat["_id"] else "Unknown", "count": stat["count"]}
        for stat in results
    ]
    return uploader_stats


@metrics_router.get("/torrents/uploaders/weekly", tags=["metrics"])
async def get_weekly_top_uploaders(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)

    # Calculate the start of the current week (Monday)
    today = datetime.now(tz=timezone.utc)
    start_of_week = today - timedelta(days=today.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

    results = await TorrentStreams.aggregate(
        [
            {
                "$match": {
                    "source": "Contribution Stream",
                    "created_at": {"$gte": start_of_week},
                    "is_blocked": {"$ne": True},
                }
            },
            {
                "$group": {
                    "_id": "$uploader",
                    "count": {"$sum": 1},
                    "latest_upload": {"$max": "$created_at"},
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 20},
        ]
    ).to_list()

    uploaders_stats = [
        {
            "name": stat["_id"],
            "count": stat["count"],
            "latest_upload": (
                stat["latest_upload"].isoformat() if stat["latest_upload"] else None
            ),
        }
        for stat in results
    ]

    return {"week_start": start_of_week.isoformat(), "uploaders": uploaders_stats}
