import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

from db.crud import fetch_last_run
from db.models import (
    MediaFusionMetaData,
    TorrentStreams,
)
from utils import const

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


@metrics_router.get("/torrents", tags=["metrics"])
async def get_torrents_data():
    count = await TorrentStreams.get_motor_collection().count_documents({})
    torrent_sources = (
        await TorrentStreams.get_motor_collection()
        .aggregate(
            [
                {"$group": {"_id": "$source", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
        .to_list(length=None)
    )
    torrent_sources = [
        {"source": source["_id"], "count": source["count"]}
        for source in torrent_sources
    ]
    return {"total_torrents": count, "torrent_sources": torrent_sources}


@metrics_router.get("/metadata", tags=["metrics"])
async def get_total_metadata():
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
async def get_schedulers_last_run(request: Request):
    tasks = [
        fetch_last_run(request.app.state.redis, spider_id, spider_name)
        for spider_id, spider_name in const.SCRAPY_SPIDERS.items()
    ]
    results = await asyncio.gather(*tasks)
    stats = {spider_name: data for spider_name, data in results}
    return stats


@metrics_router.get("/scrapy-scheduler/{spider_name}", tags=["metrics"])
async def get_scheduler_last_run(
    request: Request,
    spider_name: Literal[
        "formula_tgx",
        "mhdtvworld",
        "mhdtvsports",
        "tamilultra",
        "sport_video",
        "streamed",
        "mrgamingstreams",
        "tamil_blasters",
        "tamilmv",
        "crictime",
        "streambtw",
        "dlhd",
    ],
):
    data = await fetch_last_run(
        request.app.state.redis, spider_name, const.SCRAPY_SPIDERS[spider_name]
    )
    return data


async def update_metrics(request: Request):
    # Update torrent count
    torrents = await get_torrents_data()
    total_torrents_gauge.set(torrents["total_torrents"])
    for source in torrents["torrent_sources"]:
        torrent_sources_gauge.labels(source=source["source"]).set(source["count"])

    # Update metadata counts
    results = await get_total_metadata()
    metadata_count_gauge.labels(metadata_type="movies").set(results["movies"])
    metadata_count_gauge.labels(metadata_type="series").set(results["series"])
    metadata_count_gauge.labels(metadata_type="tv_channels").set(results["tv_channels"])

    # Update spider metrics
    stats = await get_schedulers_last_run(request)
    for spider_name, data in stats.items():
        spider_last_run_gauge.labels(spider_name=spider_name).set(
            data["time_since_last_run_seconds"]
        )


@metrics_router.get("/prometheus-metrics", tags=["metrics"])
async def prometheus_metrics(request: Request):
    await update_metrics(request)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
