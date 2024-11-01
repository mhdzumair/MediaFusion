import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

import humanize
from apscheduler.triggers.cron import CronTrigger
from beanie import BulkWriter
from beanie.exceptions import RevisionIdWasChanged
from beanie.operators import Set
from fastapi import BackgroundTasks
from pymongo.errors import DuplicateKeyError

from db import schemas
from db.config import settings
from db.models import (
    Episode,
    MediaFusionEventsMetaData,
    MediaFusionMetaData,
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    MediaFusionTVMetaData,
    Season,
    TorrentStreams,
    TVStreams,
)
from db.schemas import Stream, TorrentStreamsList
from scrapers.utils import run_scrapers
from scrapers.imdb_data import get_imdb_movie_data, search_imdb
from utils import crypto
from utils.lock import acquire_redis_lock, release_redis_lock
from utils.parser import (
    fetch_downloaded_info_hashes,
    parse_stream_data,
    parse_tv_stream_data,
)
from utils.runtime_const import REDIS_ASYNC_CLIENT
from utils.validation_helper import (
    validate_parent_guide_nudity,
    get_filter_certification_values,
)


async def get_meta_list(
    user_data: schemas.UserData,
    catalog_type: str,
    catalog: str,
    is_watchlist_catalog: bool,
    skip: int = 0,
    limit: int = 25,
    user_ip: str | None = None,
    genre: Optional[str] = None,
) -> list[schemas.Meta]:
    poster_path = f"{settings.poster_host_url}/poster/{catalog_type}/"
    meta_class = MediaFusionMetaData

    # Define query filters for TorrentStreams based on catalog
    if is_watchlist_catalog:
        downloaded_info_hashes = await fetch_downloaded_info_hashes(user_data, user_ip)
        if not downloaded_info_hashes:
            return []
        query_filters = {"_id": {"$in": downloaded_info_hashes}}
    else:
        query_filters = {"catalog": {"$in": [catalog]}}

    query_filters["is_blocked"] = {"$ne": True}
    match_filter = {
        "type": catalog_type,
    }

    if genre:
        match_filter["genres"] = {"$in": [genre]}

    if "Disable" not in user_data.nudity_filter:
        match_filter["parent_guide_nudity_status"] = {"$nin": user_data.nudity_filter}

    if "Disable" not in user_data.certification_filter:
        match_filter["parent_guide_certificates"] = {
            "$nin": get_filter_certification_values(user_data)
        }

    # Define the pipeline for aggregation
    pipeline = [
        {"$match": query_filters},
        {"$group": {"_id": "$meta_id", "created_at": {"$max": "$created_at"}}},
        {"$sort": {"created_at": -1}},
        {
            "$lookup": {  # Optimized lookup with pagination and filtering
                "from": meta_class.get_collection_name(),
                "localField": "_id",
                "foreignField": "_id",
                "pipeline": [
                    {"$match": match_filter},
                ],
                "as": "meta_data",
            }
        },
        {"$unwind": "$meta_data"},  # Flatten the results of the lookup
        {"$replaceRoot": {"newRoot": "$meta_data"}},  # Flatten the structure
        {"$skip": skip},  # Pagination with skip and limit
        {"$limit": limit},
        {"$set": {"poster": {"$concat": [poster_path, "$_id", ".jpg"]}}},
    ]

    # Execute the aggregation pipeline
    meta_list = await TorrentStreams.aggregate(
        pipeline, projection_model=schemas.Meta
    ).to_list()

    return meta_list


async def get_tv_meta_list(
    namespace: str, genre: Optional[str] = None, skip: int = 0, limit: int = 25
) -> list[schemas.Meta]:
    # Define query filters for TVStreams
    query_filters = {
        "is_working": True,
        "namespaces": {"$in": [namespace, "mediafusion", None]},
    }

    poster_path = f"{settings.poster_host_url}/poster/tv/"

    # Define the pipeline for aggregation
    pipeline = [
        {"$match": query_filters},
        {"$group": {"_id": "$meta_id", "created_at": {"$max": "$created_at"}}},
        {"$sort": {"created_at": -1}},
        {
            "$lookup": {
                "from": MediaFusionTVMetaData.get_collection_name(),
                "localField": "_id",
                "foreignField": "_id",
                "pipeline": [
                    {"$match": {**({"genres": {"$in": [genre]}} if genre else {})}},
                ],
                "as": "meta_data",
            }
        },
        {"$unwind": "$meta_data"},  # Flatten the results of the lookup
        {"$replaceRoot": {"newRoot": "$meta_data"}},  # Flatten the structure
        {"$skip": skip},  # Pagination with skip and limit
        {"$limit": limit},
        {"$set": {"poster": {"$concat": [poster_path, "$_id", ".jpg"]}}},
    ]

    # Execute the aggregation pipeline
    tv_meta_list = await TVStreams.aggregate(
        pipeline, projection_model=schemas.Meta
    ).to_list()

    return tv_meta_list


async def get_movie_data_by_id(movie_id: str) -> Optional[MediaFusionMovieMetaData]:
    # Check if the movie data is already in the cache
    cached_data = await REDIS_ASYNC_CLIENT.get(f"movie_data:{movie_id}")
    if cached_data:
        return MediaFusionMovieMetaData.model_validate_json(cached_data)

    movie_data = await MediaFusionMovieMetaData.get(movie_id)
    # store it in the db for feature reference.
    if not movie_data and movie_id.startswith("tt"):
        movie = await get_imdb_movie_data(movie_id, "movie")
        if not movie:
            return None

        movie_data = MediaFusionMovieMetaData(
            id=movie_id,
            title=movie.title,
            year=movie.year,
            poster=movie.primary_image,
            background=movie.primary_image,
            description=movie.plot.get("en-US"),
            genres=movie.genres,
            imdb_rating=movie.rating,
            parent_guide_nudity_status=movie.advisories.nudity.status,
            parent_guide_certificates=list(
                set(cert.certificate for cert in movie.certification.certificates)
            ),
            aka_titles=list(set(aka.title for aka in movie.akas)),
            stars=list(set(star.name for star in movie.cast))[10:],
        )
        try:
            await movie_data.create()
            logging.info("Added metadata for movie %s", movie_data.title)
        except (RevisionIdWasChanged, DuplicateKeyError):
            # Wait for a moment before re-fetching to mitigate rapid retry issues
            await asyncio.sleep(1)
            movie_data = await MediaFusionMovieMetaData.get(movie_id)

    # Serialize the data and store it in the Redis cache for 1 day
    if movie_data:
        await REDIS_ASYNC_CLIENT.set(
            f"movie_data:{movie_id}",
            movie_data.model_dump_json(exclude_none=True),
            ex=86400,
        )
    return movie_data


async def get_series_data_by_id(
    series_id: str, fetch_links: bool = False
) -> Optional[MediaFusionSeriesMetaData]:
    series_data = await MediaFusionSeriesMetaData.get(
        series_id, fetch_links=fetch_links
    )

    if not series_data and series_id.startswith("tt"):
        series = await get_imdb_movie_data(series_id, "series")
        if not series:
            return None

        series_data = MediaFusionSeriesMetaData(
            id=series_id,
            title=series.title,
            year=series.year,
            end_year=series.end_year if hasattr(series, "end_year") else None,
            poster=series.primary_image,
            background=series.primary_image,
            description=series.plot.get("en-US"),
            genres=series.genres,
            imdb_rating=series.rating,
            parent_guide_nudity_status=series.advisories.nudity.status,
            parent_guide_certificates=list(
                set(cert.certificate for cert in series.certification.certificates)
            ),
            aka_titles=list(set(aka.title for aka in series.akas))[10:],
            stars=list(set(star.name for star in series.cast))[10:],
        )
        try:
            await series_data.create()
            logging.info("Added metadata for series %s", series_data.title)
        except (RevisionIdWasChanged, DuplicateKeyError):
            # Wait for a moment before re-fetching to mitigate rapid retry issues
            await asyncio.sleep(1)
            series_data = await MediaFusionMovieMetaData.get(series_id)

    return series_data


async def get_tv_data_by_id(
    tv_id: str, fetch_links: bool = False
) -> Optional[MediaFusionTVMetaData]:
    tv_data = await MediaFusionTVMetaData.get(tv_id, fetch_links=fetch_links)
    return tv_data


async def get_cached_torrent_streams(
    cache_key: str,
    video_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> list[TorrentStreams]:
    # Create a unique key for Redis
    # Try to get the data from the Redis cache
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)

    if cached_data is not None:
        # If the data is in the cache, deserialize it and return it
        streams = TorrentStreamsList.model_validate_json(cached_data).streams
    else:
        # If the data is not in the cache, query it from the database
        if season is not None and episode is not None:
            streams = (
                await TorrentStreams.find(
                    {"meta_id": video_id, "is_blocked": {"$ne": True}}
                )
                .find(
                    {
                        "season.season_number": season,
                        "season.episodes.episode_number": episode,
                    }
                )
                .sort("-updated_at")
                .to_list()
            )
        else:
            streams = (
                await TorrentStreams.find(
                    {"meta_id": video_id, "is_blocked": {"$ne": True}}
                )
                .sort("-updated_at")
                .to_list()
            )

        torrent_streams = TorrentStreamsList(streams=streams)

        # Serialize the data and store it in the Redis cache for 30 minutes
        await REDIS_ASYNC_CLIENT.set(
            cache_key, torrent_streams.model_dump_json(exclude_none=True), ex=1800
        )

    return streams


async def get_movie_streams(
    user_data,
    secret_str: str,
    video_id: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
) -> list[Stream]:
    if video_id.startswith("dl"):
        if not video_id.endswith(user_data.streaming_provider.service):
            return []
        return [
            schemas.Stream(
                name=f"MediaFusion {user_data.streaming_provider.service.title()} 🗑️💩",
                description="🚨💀⚠️\nDelete all files in streaming provider",
                url=f"{settings.host_url}/streaming_provider/{secret_str}/delete_all",
            )
        ]
    movie_metadata = await get_movie_data_by_id(video_id)
    if not (movie_metadata and validate_parent_guide_nudity(movie_metadata, user_data)):
        return []

    live_search_streams = user_data.live_search_streams and video_id.startswith("tt")
    cache_key = f"torrent_streams:{video_id}"
    lock_key = f"{cache_key}_lock" if live_search_streams else None
    redis_lock = None

    # Acquire the lock to prevent multiple scrapers from running at the same time
    if lock_key:
        _, redis_lock = await acquire_redis_lock(lock_key, timeout=60, block=True)

    cached_streams = await get_cached_torrent_streams(cache_key, video_id)

    if live_search_streams:
        new_streams = await run_scrapers(
            movie_metadata,
            "movie",
            user_data,
        )
        all_streams = set(cached_streams).union(new_streams)
        if new_streams:
            # reset the cache
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            background_tasks.add_task(
                store_new_torrent_streams, new_streams, redis_lock=redis_lock
            )
        else:
            await release_redis_lock(redis_lock)
    else:
        all_streams = cached_streams

    return await parse_stream_data(all_streams, user_data, secret_str, user_ip=user_ip)


async def get_series_streams(
    user_data,
    secret_str: str,
    video_id: str,
    season: int,
    episode: int,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
) -> list[Stream]:
    series_metadata = await get_series_data_by_id(video_id, False)
    if not (
        series_metadata and validate_parent_guide_nudity(series_metadata, user_data)
    ):
        return []

    live_search_streams = user_data.live_search_streams and video_id.startswith("tt")
    cache_key = f"torrent_streams:{video_id}:{season}:{episode}"
    lock_key = f"{cache_key}_lock" if live_search_streams else None
    redis_lock = None

    # Acquire the lock to prevent multiple scrapers from running at the same time
    if lock_key:
        _, redis_lock = await acquire_redis_lock(lock_key, timeout=60, block=True)

    cached_streams = await get_cached_torrent_streams(
        cache_key, video_id, season, episode
    )

    if live_search_streams:
        new_streams = await run_scrapers(
            series_metadata,
            "series",
            user_data,
            season,
            episode,
        )
        all_streams = set(cached_streams).union(new_streams)
        if new_streams:
            # reset the cache
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            background_tasks.add_task(
                store_new_torrent_streams, new_streams, redis_lock
            )
        else:
            await release_redis_lock(redis_lock)
    else:
        all_streams = cached_streams

    return await parse_stream_data(
        all_streams,
        user_data,
        secret_str,
        season,
        episode,
        user_ip=user_ip,
        is_series=True,
    )


async def store_new_torrent_streams(
    streams: list[TorrentStreams] | set[TorrentStreams], redis_lock=None
):
    if not streams:
        return
    bulk_writer = BulkWriter()

    for stream in streams:
        try:
            existing_stream = await TorrentStreams.get(stream.id)
            if existing_stream:
                update_data = {"seeders": stream.seeders, "updated_at": datetime.now()}
                if stream.season and stream.season.episodes:
                    # Check if existing_stream.season exists before accessing its episodes
                    existing_episodes = set()
                    if existing_stream.season:
                        existing_episodes = (
                            {
                                ep.episode_number
                                for ep in existing_stream.season.episodes
                            }
                            if existing_stream.season.episodes
                            else set()
                        )

                    new_episodes = [
                        ep
                        for ep in stream.season.episodes
                        if ep.episode_number not in existing_episodes
                    ]
                    if new_episodes:
                        logging.info(
                            "Adding new %s episodes to stream %s",
                            len(new_episodes),
                            stream.id,
                        )
                        if existing_stream.season and existing_stream.season.episodes:
                            update_data["season.episodes"] = (
                                existing_stream.season.episodes + new_episodes
                            )
                        else:
                            update_data["season"] = stream.season

                await existing_stream.update(Set(update_data), bulk_writer=bulk_writer)
                logging.info("Updated stream %s for %s", stream.id, stream.meta_id)
            else:
                await TorrentStreams.insert_one(stream, bulk_writer=bulk_writer)
                logging.info("Added new stream %s for %s", stream.id, stream.meta_id)
        except DuplicateKeyError:
            logging.warning(
                "Duplicate stream found: %s for %s", stream.id, stream.meta_id
            )

    await bulk_writer.commit()
    if redis_lock:
        await release_redis_lock(redis_lock)


async def get_tv_streams(video_id: str, namespace: str, user_data) -> list[Stream]:
    tv_streams = await TVStreams.find(
        {
            "meta_id": video_id,
            "is_working": True,
            "namespaces": {"$in": [namespace, "mediafusion", None]},
        },
    ).to_list()

    return await parse_tv_stream_data(tv_streams, user_data)


async def get_movie_meta(meta_id: str, user_data: schemas.UserData):
    movie_data = await get_movie_data_by_id(meta_id)

    if not (movie_data and validate_parent_guide_nudity(movie_data, user_data)):
        return {}

    return {
        "meta": {
            "_id": meta_id,
            "type": "movie",
            "title": movie_data.title,
            "poster": f"{settings.poster_host_url}/poster/movie/{meta_id}.jpg",
            "background": movie_data.poster,
            "description": movie_data.description,
            "runtime": movie_data.runtime,
            "website": movie_data.website,
        }
    }


async def get_series_meta(meta_id: str, user_data: schemas.UserData):
    # Initialize the match filter
    match_filter = {
        "_id": meta_id,
    }

    if "Disable" not in user_data.nudity_filter:
        match_filter["parent_guide_nudity_status"] = {"$nin": user_data.nudity_filter}

    if "Disable" not in user_data.certification_filter:
        match_filter["parent_guide_certificates"] = {
            "$nin": get_filter_certification_values(user_data)
        }

    poster_path = f"{settings.poster_host_url}/poster/series/{meta_id}.jpg"

    # Define the aggregation pipeline
    pipeline = [
        {"$match": match_filter},
        {
            "$lookup": {
                "from": "TorrentStreams",
                "localField": "_id",
                "foreignField": "meta_id",
                "as": "streams",
            }
        },
        {"$unwind": "$streams"},
        {
            "$group": {
                "_id": "$_id",
                "series_title": {"$first": "$title"},  # Store series title separately
                "poster": {"$first": "$poster"},
                "background": {"$first": "$background"},
                "streams": {"$push": "$streams"},
            }
        },
        {
            "$addFields": {
                "videos": {
                    "$reduce": {
                        "input": "$streams",
                        "initialValue": [],
                        "in": {
                            "$concatArrays": [
                                "$$value",
                                {
                                    "$map": {
                                        "input": {
                                            "$filter": {
                                                "input": "$$this.season.episodes",
                                                "as": "episode",
                                                "cond": {"$ne": ["$$episode", None]},
                                            }
                                        },
                                        "as": "episode",
                                        "in": {
                                            "id": {
                                                "$concat": [
                                                    {"$toString": "$_id"},
                                                    ":",
                                                    {
                                                        "$toString": "$$this.season.season_number"
                                                    },
                                                    ":",
                                                    {
                                                        "$toString": "$$episode.episode_number"
                                                    },
                                                ]
                                            },
                                            "title": {
                                                "$ifNull": [
                                                    "$$episode.title",
                                                    {
                                                        "$concat": [
                                                            "S",
                                                            {
                                                                "$toString": "$$this.season.season_number"
                                                            },
                                                            " EP",
                                                            {
                                                                "$toString": "$$episode.episode_number"
                                                            },
                                                        ]
                                                    },
                                                ]
                                            },
                                            "season": "$$this.season.season_number",
                                            "episode": "$$episode.episode_number",
                                            "released": {
                                                "$dateToString": {
                                                    "format": "%Y-%m-%dT%H:%M:%S.000Z",
                                                    "date": {
                                                        "$ifNull": [
                                                            "$$episode.released",
                                                            "$$this.created_at",
                                                        ]
                                                    },
                                                }
                                            },
                                        },
                                    }
                                },
                            ]
                        },
                    }
                }
            }
        },
        {"$unwind": "$videos"},
        {
            "$group": {
                "_id": {
                    "id": "$videos.id",
                    "meta_id": "$_id",
                    "series_title": "$series_title",  # Store series title
                    "background": "$background",
                },
                "video": {"$first": "$videos"},
            }
        },
        {
            "$replaceRoot": {
                "newRoot": {
                    "id": "$_id.id",
                    "season": "$video.season",
                    "episode": "$video.episode",
                    "meta_id": "$_id.meta_id",
                    "series_title": "$_id.series_title",
                    "background": "$_id.background",
                    "video": "$video",
                }
            }
        },
        {"$sort": {"season": 1, "episode": 1}},
        {
            "$group": {
                "_id": "$meta_id",
                "title": {"$first": "$series_title"},
                "background": {"$first": "$background"},
                "videos": {"$push": "$video"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "meta": {
                    "_id": "$_id",
                    "type": {"$literal": "series"},
                    "title": "$title",
                    "poster": {"$literal": poster_path},
                    "background": {"$ifNull": ["$background", "$poster"]},
                    "videos": "$videos",
                },
            }
        },
    ]

    # Execute the aggregation pipeline
    series_data = await MediaFusionSeriesMetaData.aggregate(pipeline).to_list()

    return series_data[0] if series_data else {}


async def get_tv_meta(meta_id: str):
    tv_data = await get_tv_data_by_id(meta_id)

    if not tv_data:
        return {}

    return {
        "meta": {
            "_id": meta_id,
            **tv_data.model_dump(),
            "description": tv_data.description or tv_data.title,
        }
    }


async def get_existing_metadata(metadata, model):
    filters = {"title": metadata["title"]}
    year_filter = metadata.get("year", 0)

    if issubclass(model, MediaFusionMovieMetaData):
        filters["year"] = year_filter
    else:
        filters["year"] = {"$gte": year_filter}
        filters["$or"] = [
            {"end_year": {"$lte": year_filter}},
            {"end_year": None},
        ]

    return await model.find_one(
        filters,
        projection_model=schemas.MetaIdProjection,
    )


def create_metadata_object(metadata, imdb_data, model):
    poster = imdb_data.get("poster") or metadata.get("poster")
    background = imdb_data.get("background", metadata.get("background", poster))
    year = imdb_data.get("year", metadata.get("year"))
    end_year = imdb_data.get("end_year", metadata.get("end_year"))
    if isinstance(year, str) and "-" in year:
        year, end_year = year.split("-")
    return model(
        id=metadata["id"],
        title=imdb_data.get("title", metadata["title"]),
        year=year,
        end_year=end_year,
        poster=poster,
        background=background,
        description=metadata.get("description"),
        runtime=metadata.get("runtime"),
        website=metadata.get("website"),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", False),
        stars=metadata.get("stars"),
        aka_titles=imdb_data.get("aka_titles", metadata.get("aka_titles")),
        genres=imdb_data.get("genres", metadata.get("genres")),
    )


def create_stream_object(metadata, is_movie: bool = False):
    catalog = metadata.get("catalog")
    return TorrentStreams(
        id=metadata["info_hash"],
        torrent_name=metadata["torrent_name"],
        announce_list=metadata["announce_list"],
        size=metadata["total_size"],
        filename=metadata["largest_file"]["filename"] if is_movie else None,
        file_index=metadata["largest_file"]["index"] if is_movie else None,
        languages=metadata.get("languages"),
        resolution=metadata.get("resolution"),
        codec=metadata.get("codec"),
        quality=metadata.get("quality"),
        audio=metadata.get("audio"),
        source=metadata["source"],
        catalog=[catalog] if isinstance(catalog, str) else catalog,
        created_at=metadata["created_at"],
        meta_id=metadata["id"],
    )


async def get_or_create_metadata(metadata, media_type, is_imdb):
    metadata_class = (
        MediaFusionMovieMetaData if media_type == "movie" else MediaFusionSeriesMetaData
    )
    existing_data = await get_existing_metadata(metadata, metadata_class)
    if not existing_data:
        imdb_data = {}
        if is_imdb:
            imdb_data = search_imdb(metadata["title"], metadata.get("year"), media_type)

        metadata["id"] = imdb_data.get(
            "imdb_id", metadata.get("id", f"mf{uuid4().fields[-1]}")
        )
        is_exist_db = await metadata_class.find_one({"_id": metadata["id"]}).project(
            schemas.MetaIdProjection
        )
        if not is_exist_db:
            new_data = create_metadata_object(metadata, imdb_data, metadata_class)
            try:
                await new_data.create()
            except DuplicateKeyError:
                logging.warning("Duplicate %s found: %s", media_type, new_data.title)
    else:
        metadata["id"] = existing_data.id

    return metadata


async def save_metadata(metadata: dict, media_type: str, is_imdb: bool = True):
    if await is_torrent_stream_exists(metadata["info_hash"]):
        logging.info("Stream already exists for %s %s", media_type, metadata["title"])
        return
    metadata = await get_or_create_metadata(metadata, media_type, is_imdb)

    new_stream = create_stream_object(metadata, media_type == "movie")
    if media_type == "series":
        if metadata.get("episodes") and isinstance(metadata["episodes"][0], Episode):
            episodes = metadata["episodes"]
        else:
            episodes = [
                Episode(
                    episode_number=file["episodes"][0],
                    filename=file["filename"],
                    size=file["size"],
                    file_index=file["index"],
                )
                for file in metadata["file_data"]
                if file["episodes"]
            ]

        if not episodes:
            logging.warning("No episodes found for series %s", metadata["title"])
            return
        new_stream.season = Season(
            season_number=metadata.get("seasons")[0] if metadata.get("seasons") else 1,
            episodes=episodes,
        )

    await new_stream.create()
    logging.info(
        "Added stream for %s %s, info_hash: %s",
        media_type,
        metadata["title"],
        metadata["info_hash"],
    )


async def save_movie_metadata(metadata: dict, is_imdb: bool = True):
    await save_metadata(metadata, "movie", is_imdb)


async def save_series_metadata(metadata: dict, is_imdb: bool = True):
    await save_metadata(metadata, "series", is_imdb)


async def process_search_query(
    search_query: str, catalog_type: str, user_data: schemas.UserData
) -> dict:
    # Get user's certification filter values
    certification_values = get_filter_certification_values(user_data)

    # Initialize the match filter
    match_filter = {
        "$text": {"$search": search_query},  # Perform the text search
        "type": catalog_type,
    }

    if "Disable" not in user_data.nudity_filter:
        match_filter["parent_guide_nudity_status"] = {"$nin": user_data.nudity_filter}

    if "Disable" not in user_data.certification_filter:
        match_filter["parent_guide_certificates"] = {"$nin": certification_values}

    # Define the aggregation pipeline
    pipeline = [
        {"$match": match_filter},
        {"$limit": 50},  # Limit the search results to 50
        {
            "$lookup": {
                "from": TorrentStreams.get_collection_name(),
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {
                        "$match": {
                            "catalog": {"$in": user_data.selected_catalogs},
                            "is_blocked": {"$ne": True},
                        }
                    },
                    {"$count": "num_torrents"},
                ],
                "as": "torrent_count",
            }
        },
        {"$match": {"torrent_count.num_torrents": {"$gt": 0}}},
        {
            "$set": {
                "poster": {
                    "$concat": [
                        f"{settings.poster_host_url}/poster/{catalog_type}/",
                        "$_id",
                        ".jpg",
                    ]
                }
            }
        },
    ]

    # Execute the aggregation pipeline
    search_results = await MediaFusionMetaData.aggregate(pipeline).to_list(50)

    return {"metas": search_results}


async def process_tv_search_query(search_query: str, namespace: str) -> dict:
    pipeline = [
        {
            "$match": {
                "$text": {"$search": search_query},  # Perform the text search
                "type": "tv",
            }
        },
        {"$limit": 50},  # Limit the search results to 50
        {
            "$lookup": {
                "from": TVStreams.get_collection_name(),
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {
                        "$match": {
                            "is_working": True,
                            "namespaces": {"$in": [namespace, "mediafusion", None]},
                        }
                    },
                    {"$count": "num_working_streams"},
                ],
                "as": "working_stream_count",
            }
        },
        {
            "$match": {
                "working_stream_count.num_working_streams": {
                    "$gt": 0
                }  # Ensure at least one working stream exists
            }
        },
        {
            "$set": {
                "poster": {
                    "$concat": [
                        f"{settings.poster_host_url}/poster/tv/",
                        "$_id",
                        ".jpg",
                    ]
                }
            }
        },
    ]

    # Execute the aggregation pipeline
    search_results = await MediaFusionMetaData.aggregate(pipeline).to_list(50)

    return {"metas": search_results}


async def get_stream_by_info_hash(info_hash: str) -> TorrentStreams | None:
    stream = await TorrentStreams.find_one(
        {"_id": info_hash, "is_blocked": {"$ne": True}}
    )
    return stream


async def is_torrent_stream_exists(info_hash: str) -> bool:
    stream = await TorrentStreams.find_one({"_id": info_hash}).count()
    return stream > 0


async def save_tv_channel_metadata(tv_metadata: schemas.TVMetaData) -> str:
    channel_id = "mf" + crypto.get_text_hash(tv_metadata.title)

    # Prepare the genres list
    genres = list(
        filter(
            None,
            set(tv_metadata.genres + [tv_metadata.country, tv_metadata.tv_language]),
        )
    )

    # Ensure the channel document is upserted
    try:
        channel_data = await MediaFusionTVMetaData.get(channel_id)
        if channel_data:
            if channel_data.is_poster_working is False:
                background = (
                    {"background": tv_metadata.background}
                    if tv_metadata.background
                    else {}
                )
                await channel_data.update(
                    Set(
                        {
                            "poster": tv_metadata.poster,
                            "is_poster_working": True,
                            **background,
                        }
                    )
                )
        else:
            channel_data = MediaFusionTVMetaData(
                id=channel_id,
                title=tv_metadata.title,
                poster=tv_metadata.poster,
                background=tv_metadata.background,
                country=tv_metadata.country,
                tv_language=tv_metadata.tv_language,
                logo=tv_metadata.logo,
                genres=genres,
                type="tv",
            )
            await channel_data.create()
    except DuplicateKeyError:
        pass

    # Stream processing
    bulk_writer = BulkWriter()
    for stream in tv_metadata.streams:
        # Define stream document with meta_id
        stream_doc = TVStreams(
            url=stream.url,
            name=stream.name,
            behaviorHints=(
                stream.behaviorHints.model_dump(exclude_none=True)
                if stream.behaviorHints
                else None
            ),
            ytId=stream.ytId,
            source=stream.source,
            country=stream.country,
            meta_id=channel_id,
            namespaces=[tv_metadata.namespace],
            drm_key_id=stream.drm_key_id,
            drm_key=stream.drm_key,
        )

        # Check if the stream exists (by URL or ytId) and upsert accordingly
        existing_stream = await TVStreams.find_one(
            TVStreams.url == stream.url,
            TVStreams.ytId == stream.ytId,
        )
        if existing_stream == stream_doc:
            update_data = {}
            if (
                stream.drm_key_id != existing_stream.drm_key_id
                or stream.drm_key != existing_stream.drm_key
            ) and tv_metadata.namespace in existing_stream.namespaces:
                update_data = {
                    "drm_key_id": stream.drm_key_id,
                    "drm_key": stream.drm_key,
                }
            if tv_metadata.namespace not in existing_stream.namespaces:
                existing_stream.namespaces.append(tv_metadata.namespace)
                update_data.update({"namespaces": existing_stream.namespaces})

            if update_data:
                await existing_stream.update(
                    Set(update_data),
                    bulk_writer=bulk_writer,
                )
        else:
            await TVStreams.insert_one(stream_doc, bulk_writer=bulk_writer)

    await bulk_writer.commit()
    logging.info(f"Processed TV channel {tv_metadata.title}")
    return channel_id


async def save_events_data(metadata: dict) -> str:
    # Generate a unique event key
    meta_id = "mf" + crypto.get_text_hash(metadata["title"])
    event_key = f"event:{meta_id}"

    # Attempt to fetch existing event data
    existing_event_json = await REDIS_ASYNC_CLIENT.get(event_key)

    if existing_event_json:
        # Deserialize the existing event data
        existing_event_data = MediaFusionEventsMetaData.model_validate_json(
            existing_event_json
        )
        existing_streams = set(existing_event_data.streams)
    else:
        existing_streams = set()

    # Update or add streams based on the uniqueness of 'url'
    for stream in metadata["streams"]:
        # Create a TVStreams instance for each stream
        stream_instance = TVStreams(meta_id=meta_id, **stream)
        existing_streams.add(stream_instance)

    streams = list(existing_streams)

    event_start_timestamp = metadata.get("event_start_timestamp", 0)

    # Create or update the event data
    events_data = MediaFusionEventsMetaData(
        id=meta_id,
        streams=streams,
        title=metadata["title"],
        description=metadata["description"] or metadata["title"],
        genres=metadata.get("genres", []),
        event_start_timestamp=event_start_timestamp,
        poster=metadata.get("poster"),
        background=metadata.get("background"),
        logo=metadata.get("logo"),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", False),
        website=metadata.get("website"),
    )

    # Serialize the event data for storage
    events_json = events_data.model_dump_json(exclude_none=True, by_alias=True)

    # Set or update the event data in Redis with an appropriate TTL
    cache_ttl = 86400 if events_data.event_start_timestamp == 0 else 3600
    await REDIS_ASYNC_CLIENT.set(event_key, events_json, ex=cache_ttl)

    logging.info(
        f"{'Updating' if existing_event_json else 'Inserting'} event data for {events_data.title} with event key {event_key}"
    )

    # Add the event key to a set of all events
    await REDIS_ASYNC_CLIENT.zadd("events:all", {event_key: event_start_timestamp})

    # Index the event by genre
    for genre in events_data.genres:
        await REDIS_ASYNC_CLIENT.zadd(
            f"events:genre:{genre}", {event_key: event_start_timestamp}
        )

    return event_key


async def get_events_meta_list(genre=None, skip=0, limit=25) -> list[schemas.Meta]:
    if genre:
        key_pattern = f"events:genre:{genre}"
    else:
        key_pattern = "events:all"

    # Fetch event keys sorted by timestamp in descending order
    events_keys = await REDIS_ASYNC_CLIENT.zrevrange(
        key_pattern, skip, skip + limit - 1
    )
    events = []

    # Iterate over event keys, fetching and decoding JSON data
    for key in events_keys:
        events_json = await REDIS_ASYNC_CLIENT.get(key)
        if events_json:
            meta_data = schemas.Meta.model_validate_json(events_json)
            meta_data.poster = (
                f"{settings.poster_host_url}/poster/events/{meta_data.id}.jpg"
            )
            events.append(meta_data)
        else:
            # Cleanup: Remove expired or missing event key from the index
            await REDIS_ASYNC_CLIENT.zrem(key_pattern, key)

    return events


async def get_event_meta(meta_id: str) -> dict:
    events_key = f"event:{meta_id}"
    events_json = await REDIS_ASYNC_CLIENT.get(events_key)
    if not events_json:
        return {}

    event_data = MediaFusionTVMetaData.model_validate_json(events_json)
    return {
        "meta": {
            "_id": meta_id,
            **event_data.model_dump(),
        }
    }


async def get_event_data_by_id(meta_id: str) -> MediaFusionEventsMetaData | None:
    event_key = f"event:{meta_id}"
    events_json = await REDIS_ASYNC_CLIENT.get(event_key)
    if not events_json:
        return None

    return MediaFusionEventsMetaData.model_validate_json(events_json)


async def get_event_streams(meta_id: str, user_data) -> list[Stream]:
    event_key = f"event:{meta_id}"
    event_json = await REDIS_ASYNC_CLIENT.get(event_key)
    if not event_json:
        return await parse_tv_stream_data([], user_data)

    event_data = MediaFusionEventsMetaData.model_validate_json(event_json)
    return await parse_tv_stream_data(event_data.streams, user_data)


async def get_genres(catalog_type: str) -> list[str]:
    genres = await REDIS_ASYNC_CLIENT.get(f"{catalog_type}_genres")
    if genres:
        return json.loads(genres)

    genres = await MediaFusionMetaData.distinct(
        "genres", {"type": catalog_type, "genres": {"$nin": ["", None]}}
    )

    # cache the genres for 30 minutes
    await REDIS_ASYNC_CLIENT.set(f"{catalog_type}_genres", json.dumps(genres), ex=1800)
    return genres


async def fetch_last_run(spider_id: str, spider_name: str):
    task_key = f"background_tasks:run_spider:spider_name={spider_id}"
    state_key = f"scrapy_stats:{spider_id}"
    last_run_timestamp = await REDIS_ASYNC_CLIENT.get(task_key)
    last_run_state = await REDIS_ASYNC_CLIENT.get(state_key)

    if settings.disable_all_scheduler:
        next_schedule_in = None
        is_scheduler_disabled = True
    else:
        crontab_expression = getattr(settings, f"{spider_id}_scheduler_crontab")
        is_scheduler_disabled = getattr(settings, f"disable_{spider_id}_scheduler")
        cron_trigger = CronTrigger.from_crontab(crontab_expression)
        next_time = cron_trigger.get_next_fire_time(
            None, datetime.now(tz=cron_trigger.timezone)
        )
        next_schedule_in = humanize.naturaldelta(
            next_time - datetime.now(tz=cron_trigger.timezone)
        )

    response = {
        "name": spider_name,
        "last_run": "Never",
        "time_since_last_run": "Never run",
        "time_since_last_run_seconds": -1,
        "next_schedule_in": next_schedule_in,
        "is_scheduler_disabled": is_scheduler_disabled,
        "last_run_state": json.loads(last_run_state or "null"),
    }

    if last_run_timestamp:
        last_run = datetime.fromtimestamp(float(last_run_timestamp))
        delta = datetime.now() - last_run
        response.update(
            {
                "last_run": last_run.isoformat(),
                "time_since_last_run": humanize.precisedelta(
                    delta, minimum_unit="minutes"
                ),
                "time_since_last_run_seconds": delta.total_seconds(),
            }
        )

    return response
