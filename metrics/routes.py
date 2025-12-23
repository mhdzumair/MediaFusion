import asyncio
import json
from datetime import datetime, timezone, timedelta

import humanize
from fastapi import APIRouter, Request, Response
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from db.config import settings
from db import sql_crud
from db.database import get_read_session
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
    async for session in get_read_session():
        count = await sql_crud.get_torrent_count(session)
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

    async for session in get_read_session():
        torrent_sources = await sql_crud.get_torrents_by_source(session, limit=20)

    # Cache the results for 30 minutes
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(torrent_sources), ex=1800)
    return torrent_sources


@metrics_router.get("/metadata", tags=["metrics"])
async def get_total_metadata(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    async for session in get_read_session():
        return await sql_crud.get_metadata_counts(session)


@metrics_router.get("/scrapy-schedulers", tags=["metrics"])
async def get_schedulers_last_run(response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)
    tasks = [
        sql_crud.fetch_last_run(spider_id, spider_name)
        for spider_id, spider_name in const.SCRAPY_SPIDERS.items()
    ]
    results = await asyncio.gather(*tasks)
    return results


async def update_metrics(request: Request, response: Response):
    # Define each task as a coroutine
    count_task = get_torrents_count(response)
    torrent_sources_task = get_torrents_by_sources(response)
    total_metadata_task = get_total_metadata(response)
    schedulers_last_run_task = get_schedulers_last_run(response)

    # Run all tasks concurrently
    count_result, torrent_sources, results, stats = await asyncio.gather(
        count_task, torrent_sources_task, total_metadata_task, schedulers_last_run_task
    )

    # Update torrent count
    total_torrents_gauge.set(count_result["total_torrents"])

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
    async for session in get_read_session():
        return await sql_crud.get_torrents_by_uploader(session, limit=20)


@metrics_router.get("/torrents/uploaders/weekly/{week_date}", tags=["metrics"])
async def get_weekly_top_uploaders_endpoint(week_date: str, response: Response):
    response.headers.update(const.NO_CACHE_HEADERS)

    try:
        # Parse the input date
        selected_date = datetime.strptime(week_date, "%Y-%m-%d")
        selected_date = selected_date.replace(tzinfo=timezone.utc)

        # Calculate the start of the week (Monday) for the selected date
        start_of_week = selected_date - timedelta(days=selected_date.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

        # Calculate end of week
        end_of_week = start_of_week + timedelta(days=7)

        # Get raw aggregation results
        async for session in get_read_session():
            raw_results = await sql_crud.get_weekly_top_uploaders(
                session, start_of_week, end_of_week
            )

        # Process and clean the results (keeping existing logic)
        processed_results = []
        anonymous_total = 0
        anonymous_latest = None

        for stat in raw_results:
            uploader_name = stat["_id"]
            count = stat["count"]
            latest_upload = stat["latest_upload"]

            if count <= 0:
                continue

            if (
                uploader_name is None
                or uploader_name.strip() == ""
                or uploader_name.lower() == "anonymous"
            ):
                anonymous_total += count
                if anonymous_latest is None or (
                    latest_upload and latest_upload > anonymous_latest
                ):
                    anonymous_latest = latest_upload
            else:
                processed_results.append(
                    {
                        "name": uploader_name.strip(),
                        "count": count,
                        "latest_upload": (
                            latest_upload.strftime("%Y-%m-%d")
                            if latest_upload
                            else None
                        ),
                    }
                )

        if anonymous_total > 0:
            processed_results.append(
                {
                    "name": "Anonymous",
                    "count": anonymous_total,
                    "latest_upload": (
                        anonymous_latest.strftime("%Y-%m-%d")
                        if anonymous_latest
                        else None
                    ),
                }
            )

        final_results = sorted(
            processed_results, key=lambda x: x["count"], reverse=True
        )[:20]

        return {
            "week_start": start_of_week.strftime("%Y-%m-%d"),
            "week_end": end_of_week.strftime("%Y-%m-%d"),
            "uploaders": final_results,
        }

    except ValueError as e:
        return {
            "error": f"Invalid date format. Please use YYYY-MM-DD format. Details: {str(e)}"
        }
    except Exception as e:
        return {"error": f"An error occurred: {str(e)}"}
