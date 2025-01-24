import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Type, Literal
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
from db.enums import NudityStatus
from db.models import (
    EpisodeFile,
    MediaFusionEventsMetaData,
    MediaFusionMetaData,
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    MediaFusionTVMetaData,
    TorrentStreams,
    TVStreams,
    SeriesEpisode,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import Stream, TorrentStreamsList
from scrapers.dlhd import dlhd_schedule_service
from scrapers.mdblist import initialize_mdblist_scraper
from scrapers.scraper_tasks import run_scrapers, meta_fetcher
from streaming_providers.cache_helpers import store_cached_info_hashes
from utils import crypto
from utils.const import (
    USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS,
    USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS,
)
from utils.lock import acquire_redis_lock, release_redis_lock
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import (
    fetch_downloaded_info_hashes,
    parse_stream_data,
    parse_tv_stream_data,
    calculate_max_similarity_ratio,
    create_exception_stream,
    create_content_warning_message,
)
from utils.validation_helper import (
    validate_parent_guide_nudity,
    get_filter_certification_values,
    is_video_file,
)


async def get_meta_list(
    user_data: schemas.UserData,
    catalog_type: str,
    catalog: str,
    is_watchlist_catalog: bool,
    skip: int = 0,
    limit: int = 50,
    user_ip: str | None = None,
    genre: Optional[str] = None,
) -> list[schemas.Meta]:
    """Get a list of metadata entries based on various filters"""
    if catalog.startswith("contribution_"):
        catalog = "contribution_stream"
    poster_path = f"{settings.poster_host_url}/poster/{catalog_type}/"
    meta_class = (
        MediaFusionMovieMetaData
        if catalog_type == "movie"
        else MediaFusionSeriesMetaData
    )

    # Handle watchlist case first
    if is_watchlist_catalog:
        downloaded_info_hashes = await fetch_downloaded_info_hashes(user_data, user_ip)
        if not downloaded_info_hashes:
            return []

        # Store cached info hashes
        await store_cached_info_hashes(
            user_data.streaming_provider, downloaded_info_hashes
        )

        # First get meta_ids from TorrentStreams
        meta_ids = await TorrentStreams.distinct(
            "meta_id",
            {"_id": {"$in": downloaded_info_hashes}, "is_blocked": {"$ne": True}},
        )

        if not meta_ids:
            return []

        # Now use these meta_ids in the main query
        match_filter = {
            "_id": {"$in": meta_ids},
            "type": catalog_type,
            "total_streams": {"$gt": 0},
        }
    else:
        # Regular catalog query
        match_filter = {
            "type": catalog_type,
            "catalogs": catalog,
            "total_streams": {"$gt": 0},
        }

    # Add genre filter if specified
    if genre:
        match_filter["genres"] = genre
    else:
        # Add genre filter to ignore 'Adult' genre
        match_filter["genres"] = {"$nin": ["Adult"]}

    # Handle nudity filter
    if "Disable" not in user_data.nudity_filter:
        if "Unknown" in user_data.nudity_filter:
            match_filter["parent_guide_nudity_status"] = {"$exists": True}
        elif user_data.nudity_filter:
            match_filter["parent_guide_nudity_status"] = {
                "$nin": user_data.nudity_filter
            }

    # Handle certification filter
    if "Disable" not in user_data.certification_filter:
        cert_filters = []
        if "Unknown" in user_data.certification_filter:
            cert_filters.append(
                {"parent_guide_certificates": {"$exists": True, "$ne": []}}
            )
        filter_values = get_filter_certification_values(user_data)
        if filter_values:
            cert_filters.append({"parent_guide_certificates": {"$nin": filter_values}})
        if cert_filters:
            match_filter["$or"] = cert_filters

    # Define the pipeline
    pipeline = [
        {"$match": match_filter},
        {"$sort": {"last_stream_added": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$set": {"poster": {"$concat": [poster_path, "$_id", ".jpg"]}}},
    ]

    # Execute the aggregation pipeline
    meta_list = await meta_class.aggregate(
        pipeline, projection_model=schemas.Meta
    ).to_list()
    return meta_list


async def get_mdblist_meta_list(
    user_data: schemas.UserData,
    background_tasks: BackgroundTasks,
    catalog: str,
    catalog_type: str,
    genre: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> list[schemas.Meta]:
    """Get a list of metadata entries from MDBList"""
    if not user_data.mdblist_config:
        return []

    # Extract list info from catalog ID
    _, media_type, list_id = catalog.split("_", 2)
    list_config = next(
        (
            list_item
            for list_item in user_data.mdblist_config.lists
            if str(list_item.id) == list_id and list_item.catalog_type == media_type
        ),
        None,
    )

    if not list_config:
        return []

    meta_class = (
        MediaFusionMovieMetaData
        if catalog_type == "movie"
        else MediaFusionSeriesMetaData
    )

    # Initialize MDBList scraper
    mdblist_scraper = await initialize_mdblist_scraper(user_data.mdblist_config.api_key)
    try:
        if not list_config.use_filters:
            return await mdblist_scraper.get_list_items(
                list_id=list_id,
                media_type=media_type,
                skip=skip,
                limit=limit,
                genre=genre,
                use_filters=False,
            )

        # For filtered results, get all IMDb IDs first
        imdb_ids = await mdblist_scraper.get_list_items(
            list_id=list_id,
            media_type=media_type,
            skip=0,
            limit=0,  # Ignored for filtered results
            genre=genre,
            use_filters=True,
        )

        if not imdb_ids:
            return []

        # Build filter pipeline
        match_filter = {
            "_id": {"$in": imdb_ids},
            "type": catalog_type,
            "total_streams": {"$gt": 0},
        }

        # Handle nudity filter
        if "Disable" not in user_data.nudity_filter:
            if "Unknown" in user_data.nudity_filter:
                match_filter["parent_guide_nudity_status"] = {"$exists": True}
            elif user_data.nudity_filter:
                match_filter["parent_guide_nudity_status"] = {
                    "$nin": user_data.nudity_filter
                }

        # Handle certification filter
        if "Disable" not in user_data.certification_filter:
            cert_filters = []
            if "Unknown" in user_data.certification_filter:
                cert_filters.append(
                    {"parent_guide_certificates": {"$exists": True, "$ne": []}}
                )
            filter_values = get_filter_certification_values(user_data)
            if filter_values:
                cert_filters.append(
                    {"parent_guide_certificates": {"$nin": filter_values}}
                )
            if cert_filters:
                match_filter["$or"] = cert_filters

        # Get filtered results with pagination
        poster_path = f"{settings.poster_host_url}/poster/{catalog_type}/"

        pipeline = [
            {"$match": match_filter},
            {"$sort": {"last_stream_added": -1}},
            {"$skip": skip},
            {"$limit": limit},
            {"$set": {"poster": {"$concat": [poster_path, "$_id", ".jpg"]}}},
        ]

        results = await meta_class.get_motor_collection().aggregate(pipeline).to_list()
        if not results:
            # Check for missing metadata and trigger background fetch
            existing_ids = set(
                doc["_id"]
                for doc in await meta_class.get_motor_collection()
                .find({"_id": {"$in": imdb_ids}}, {"_id": 1})
                .to_list(None)
            )
            missing_ids = list(set(imdb_ids) - existing_ids)

            if missing_ids:
                background_tasks.add_task(
                    fetch_metadata,
                    missing_ids,
                    catalog_type,
                )
            return []
        return [schemas.Meta.model_validate(result) for result in results]

    finally:
        await mdblist_scraper.close()


async def get_tv_meta_list(
    namespace: str, genre: Optional[str] = None, skip: int = 0, limit: int = 50
) -> list[schemas.Meta]:
    poster_path = f"{settings.poster_host_url}/poster/tv/"

    # Initialize base match filter
    match_filter = {"type": "tv", "total_streams": {"$gt": 0}}

    # Add genre filter if specified
    if genre:
        match_filter["genres"] = genre

    # First get meta_ids from TVStreams
    meta_ids = await TVStreams.distinct(
        "meta_id",
        {"is_working": True, "namespaces": {"$in": [namespace, "mediafusion", None]}},
    )

    if not meta_ids:
        return []

    match_filter["_id"] = {"$in": meta_ids}

    # Define the pipeline
    pipeline = [
        {"$match": match_filter},
        {"$sort": {"last_stream_added": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {"$set": {"poster": {"$concat": [poster_path, "$_id", ".jpg"]}}},
    ]

    # Execute the aggregation pipeline
    meta_list = await MediaFusionTVMetaData.aggregate(
        pipeline, projection_model=schemas.Meta
    ).to_list()

    return meta_list


async def get_media_data_by_id(
    meta_id: str,
    media_type: Literal["movie", "series"],
    model_class: Type[MediaFusionMovieMetaData | MediaFusionSeriesMetaData],
    counter_part_model: Type[MediaFusionMovieMetaData | MediaFusionSeriesMetaData],
) -> Optional[MediaFusionMovieMetaData | MediaFusionSeriesMetaData]:
    """
    Generic function to fetch media metadata by ID.

    Args:
        meta_id: Media ID to fetch
        media_type: Type of media ("movie" or "series")
        model_class: Class to use for the media data (MediaFusionMovieMetaData or MediaFusionSeriesMetaData)
        counter_part_model: Counterpart class for the media data.

    Returns:
        Optional[T]: Media metadata object or None if not found
    """
    # Check cache first
    cached_data = await REDIS_ASYNC_CLIENT.get(f"{media_type}_data:{meta_id}")
    if cached_data:
        return model_class.model_validate_json(cached_data)

    lock_key = f"meta_id_lock:{meta_id}"
    _, redis_lock = await acquire_redis_lock(lock_key, timeout=30, block=True)
    # Fetch existing data
    media_data = await model_class.get(meta_id)

    # Fetch and create new data if needed
    if not media_data and meta_id.startswith("tt"):
        raw_data = await meta_fetcher.get_metadata(meta_id, media_type)
        if not raw_data:
            await release_redis_lock(redis_lock)
            return None

        if raw_data["type"] != media_type:
            logging.warning(
                "Mismatched media type for %s %s: %s",
                media_type,
                meta_id,
                raw_data["type"],
            )
            await release_redis_lock(redis_lock)
            return None

        # Create metadata object with common fields
        common_fields = {
            "id": meta_id,
            "title": raw_data["title"],
            "year": raw_data["year"],
            "poster": raw_data["poster"],
            "background": raw_data["background"],
            "description": raw_data["description"],
            "genres": raw_data["genres"],
            "imdb_rating": raw_data["imdb_rating"],
            "parent_guide_nudity_status": raw_data["parent_guide_nudity_status"],
            "parent_guide_certificates": raw_data["parent_guide_certificates"],
            "aka_titles": raw_data["aka_titles"],
            "stars": raw_data["stars"],
        }

        # Add series-specific fields if needed
        if media_type == "series":
            common_fields.update(
                {"end_year": raw_data["end_year"], "episodes": raw_data["episodes"]}
            )

        media_data = model_class(**common_fields)

        try:
            await media_data.create()
            logging.info(f"Added metadata for {media_type} {media_data.title}")
        except DuplicateKeyError as error:
            if "_id_ dup key:" in str(error):
                existing_media = await counter_part_model.find_one({"_id": meta_id})
            else:
                # Handle duplicate title/year combination
                existing_media = await model_class.find_one(
                    {
                        "title": media_data.title,
                        "year": media_data.year,
                        "_id": {"$regex": "^mf"},
                    }
                )

            if not existing_media:
                logging.error(f"Error occurred while adding metadata: {error}")
                await release_redis_lock(redis_lock)
                return None

            if existing_media.id != media_data.id:
                # Update TorrentStreams meta_id and replace existing record
                await TorrentStreams.find({"meta_id": existing_media.id}).update(
                    Set({"meta_id": media_data.id})
                )
            media_data.catalogs = existing_media.catalogs
            media_data.total_streams = existing_media.total_streams
            await existing_media.delete()
            await media_data.create()
            logging.info(
                f"Replace meta id {existing_media.id} ({existing_media.type}) with {media_data.id} ({media_data.type})"
            )
        except RevisionIdWasChanged:
            await asyncio.sleep(1)
            media_data = await model_class.get(meta_id)

    # Cache the data
    if media_data:
        await REDIS_ASYNC_CLIENT.set(
            f"{media_type}_data:{meta_id}",
            media_data.model_dump_json(exclude_none=True),
            ex=86400,  # 1 day
        )
    await release_redis_lock(redis_lock)
    return media_data


async def get_movie_data_by_id(movie_id: str) -> Optional[MediaFusionMovieMetaData]:
    return await get_media_data_by_id(
        movie_id, "movie", MediaFusionMovieMetaData, MediaFusionSeriesMetaData
    )


async def get_series_data_by_id(series_id: str) -> Optional[MediaFusionSeriesMetaData]:
    return await get_media_data_by_id(
        series_id, "series", MediaFusionSeriesMetaData, MediaFusionMovieMetaData
    )


async def get_tv_data_by_id(tv_id: str) -> Optional[MediaFusionTVMetaData]:
    tv_data = await MediaFusionTVMetaData.get(tv_id)
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
            streams = await TorrentStreams.find(
                {
                    "meta_id": video_id,
                    "is_blocked": {"$ne": True},
                    "episode_files": {
                        "$elemMatch": {
                            "season_number": season,
                            "episode_number": episode,
                        }
                    },
                }
            ).to_list()
        else:
            streams = await TorrentStreams.find(
                {"meta_id": video_id, "is_blocked": {"$ne": True}}
            ).to_list()

        torrent_streams = TorrentStreamsList(streams=streams)

        # Serialize the data and store it in the Redis cache for 30 minutes
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            torrent_streams.model_dump_json(
                exclude_none=True, exclude={"streams": {"__all__": {"torrent_file"}}}
            ),
            ex=1800,
        )

    return streams


async def get_streams_base(
    user_data,
    secret_str: str,
    video_id: str,
    metadata,
    content_type: str,
    content_catalogs: list[str],
    user_ip: str | None,
    background_tasks: BackgroundTasks,
    season: int | None = None,
    episode: int | None = None,
) -> list[Stream]:
    """
    Base function for fetching streams for both movies and series.

    Args:
        user_data: User data containing preferences
        secret_str: Secret string for authentication
        video_id: ID of the video content
        metadata: Metadata for the content
        content_type: Type of content ("movie" or "series")
        content_catalogs: List of supported catalogs for the content
        user_ip: User's IP address
        background_tasks: Background tasks manager
        season: Season number (for series only)
        episode: Episode number (for series only)
    """
    # Handle special case for streaming provider deletion
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

    if not metadata:
        return []

    # Check content appropriateness
    if validate_parent_guide_nudity(metadata, user_data) is False:
        return [
            create_exception_stream(
                settings.addon_name,
                create_content_warning_message(metadata),
                "inappropriate_content.mp4",
            )
        ]

    # Create user feeds for supported catalogs
    user_feeds = []
    if video_id.startswith("mf") and any(
        catalog in content_catalogs for catalog in metadata.catalogs
    ):
        user_feeds = [
            schemas.Stream(
                name=settings.addon_name,
                description=f"🔄 Migrate {video_id} to IMDb ID",
                externalUrl=f"{settings.host_url}/scraper/?action=migrate_id&mediafusion_id={video_id}&meta_type={content_type}",
            )
        ]

    # Handle stream caching and live search
    live_search_streams = user_data.live_search_streams and video_id.startswith("tt")
    cache_key_parts = [video_id]
    if content_type == "series":
        cache_key_parts.extend([str(season), str(episode)])

    cache_key = f"torrent_streams:{':'.join(cache_key_parts)}"
    lock_key = f"{cache_key}_lock" if live_search_streams else None
    redis_lock = None

    if lock_key:
        _, redis_lock = await acquire_redis_lock(lock_key, timeout=60, block=True)

    # Get cached streams
    cached_streams = await get_cached_torrent_streams(
        cache_key,
        video_id,
        season,
        episode,
    )

    # Handle live search and stream updates
    if live_search_streams:
        scraper_args = [metadata, content_type]
        if content_type == "series":
            scraper_args.extend([season, episode])

        new_streams = await run_scrapers(*scraper_args)
        all_streams = list(set(cached_streams).union(new_streams))

        if new_streams:
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            background_tasks.add_task(
                store_new_torrent_streams, new_streams, redis_lock=redis_lock
            )
        else:
            await release_redis_lock(redis_lock)
    else:
        all_streams = cached_streams

    # Parse and return results
    parsed_results = await parse_stream_data(
        all_streams,
        user_data,
        secret_str,
        season,
        episode,
        user_ip=user_ip,
        is_series=(content_type == "series"),
    )
    return parsed_results + user_feeds


async def get_movie_streams(
    user_data,
    secret_str: str,
    video_id: str,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
) -> list[Stream]:
    """Get streams for a movie."""
    movie_metadata = await get_movie_data_by_id(video_id)
    return await get_streams_base(
        user_data=user_data,
        secret_str=secret_str,
        video_id=video_id,
        metadata=movie_metadata,
        content_type="movie",
        content_catalogs=USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS,
        user_ip=user_ip,
        background_tasks=background_tasks,
    )


async def get_series_streams(
    user_data,
    secret_str: str,
    video_id: str,
    season: int,
    episode: int,
    user_ip: str | None,
    background_tasks: BackgroundTasks,
) -> list[Stream]:
    """Get streams for a series episode."""
    series_metadata = await get_series_data_by_id(video_id)
    return await get_streams_base(
        user_data=user_data,
        secret_str=secret_str,
        video_id=video_id,
        metadata=series_metadata,
        content_type="series",
        content_catalogs=USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS,
        user_ip=user_ip,
        background_tasks=background_tasks,
        season=season,
        episode=episode,
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

                if stream.episode_files:
                    # Get existing episode numbers
                    existing_episodes = {
                        (ep.season_number, ep.episode_number)
                        for ep in (existing_stream.episode_files or [])
                    }

                    # Find new episodes
                    new_episodes = [
                        ep
                        for ep in stream.episode_files
                        if (ep.season_number, ep.episode_number)
                        not in existing_episodes
                    ]

                    if new_episodes:
                        logging.info(
                            "Adding new %s episodes to stream %s",
                            len(new_episodes),
                            stream.id,
                        )
                        update_data["episode_files"] = (
                            existing_stream.episode_files or []
                        ) + new_episodes

                await existing_stream.update(Set(update_data), bulk_writer=bulk_writer)
                logging.info("Updated stream %s for %s", stream.id, stream.meta_id)
            else:
                await TorrentStreams.insert(stream)
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
            "year": movie_data.year,
            "poster": f"{settings.poster_host_url}/poster/movie/{meta_id}.jpg",
            "background": movie_data.background or movie_data.poster,
            "description": movie_data.description,
            "runtime": movie_data.runtime,
            "website": movie_data.website,
            "imdb_rating": movie_data.imdb_rating,
            "genres": movie_data.genres,
            "stars": movie_data.stars,
        }
    }


async def get_series_meta(meta_id: str, user_data: schemas.UserData):
    # First fetch basic series data and validate
    series = await get_series_data_by_id(meta_id)

    if not (series and validate_parent_guide_nudity(series, user_data)):
        return {}

    # Convert episodes to video format
    videos = [
        {
            "id": f"{meta_id}:{ep.season_number}:{ep.episode_number}",
            "title": ep.title,
            "season": ep.season_number,
            "episode": ep.episode_number,
            "overview": ep.overview,
            "released": (
                ep.released.strftime("%Y-%m-%dT%H:%M:%S.000Z") if ep.released else None
            ),
            "imdb_rating": ep.imdb_rating,
            "thumbnail": ep.thumbnail,
        }
        for ep in sorted(
            series.episodes, key=lambda x: (x.season_number, x.episode_number)
        )
    ]

    return {
        "meta": {
            "_id": meta_id,
            "type": "series",
            "title": series.title,
            "year": series.year,
            "end_year": series.end_year,
            "poster": f"{settings.poster_host_url}/poster/series/{meta_id}.jpg",
            "background": series.background or series.poster,
            "description": series.description,
            "imdb_rating": series.imdb_rating,
            "genres": series.genres,
            "stars": series.stars,
            "videos": videos,
        }
    }


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


async def get_existing_metadata(
    metadata: dict, model: Type[MediaFusionMovieMetaData | MediaFusionSeriesMetaData]
) -> Optional[MediaFusionMovieMetaData | MediaFusionSeriesMetaData]:
    if metadata.get("id"):
        return await model.get(metadata["id"])
    title = metadata["title"]
    year = metadata.get("year")
    if isinstance(year, str):
        year = int(year)

    # Create a list of filters to try in order
    filters = []

    # Prepare year filter based on model type
    if not year:
        year_filter = {}
    if issubclass(model, MediaFusionMovieMetaData):
        # For movies: exact year match
        year_filter = {"year": year}
    else:
        # For series: check if the series was active in the given year
        # A series is considered active if:
        # 1. The year is greater than or equal to the start year (year field)
        # 2. AND either:
        #    a. end_year is null (series still ongoing)
        #    b. OR year is less than or equal to end_year
        year_filter = {
            "year": {"$lte": year},
            "$or": [
                {"end_year": None},  # Still ongoing series
                {"end_year": {"$gte": year}},  # Series ended after the given year
            ],
        }

    # 1. Exact title match with year
    exact_match_filter = {"title": title}
    exact_match_filter.update(year_filter)
    filters.append(exact_match_filter)

    # 2. Case-insensitive regex match with year
    regex_filter = {"title": {"$regex": f"^{re.escape(title)}$", "$options": "i"}}
    regex_filter.update(year_filter)
    filters.append(regex_filter)

    # 3. Text search with year as a last resort
    text_search_filter = {
        "$text": {
            "$search": title,
            "$caseSensitive": False,
        }
    }
    text_search_filter.update(year_filter)
    filters.append(text_search_filter)

    # Try each filter in sequence
    for filter_query in filters:
        potential_matches = await model.find(
            filter_query, projection_model=schemas.MetaSearchProjection
        ).to_list(10)

        # Find the best match using calculate_max_similarity_ratio
        best_match = None
        best_ratio = 0

        for match in potential_matches:
            similarity_ratio = calculate_max_similarity_ratio(
                title, match.title, match.aka_titles
            )

            if similarity_ratio > best_ratio:
                best_ratio = similarity_ratio
                best_match = match

        # Return the match if it meets our threshold (95%)
        if best_match and best_ratio >= 95:
            return best_match

    return None


def create_metadata_object(metadata, imdb_data, model):
    poster = imdb_data.get("poster") or metadata.get("poster")
    background = imdb_data.get("background") or metadata.get("background", poster)
    year = imdb_data.get("year") or metadata.get("year")
    end_year = imdb_data.get("end_year") or metadata.get("end_year")
    if isinstance(year, str) and "-" in year:
        year, end_year = year.split("-")
    return model(
        id=metadata["id"],
        title=imdb_data.get("title") or metadata["title"],
        year=year,
        is_custom=metadata["id"].startswith("mf"),
        end_year=end_year,
        poster=poster,
        background=background,
        description=imdb_data.get("description") or metadata.get("description"),
        runtime=imdb_data.get("runtime") or metadata.get("runtime"),
        website=imdb_data.get("website") or metadata.get("website"),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", False),
        stars=imdb_data.get("stars") or metadata.get("stars"),
        aka_titles=imdb_data.get("aka_titles") or metadata.get("aka_titles"),
        genres=imdb_data.get("genres") or metadata.get("genres"),
        imdb_rating=imdb_data.get("imdb_rating") or metadata.get("imdb_rating"),
        parent_guide_nudity_status=imdb_data.get("parent_guide_nudity_status"),
        parent_guide_certificates=imdb_data.get("parent_guide_certificates"),
        episodes=imdb_data.get("episodes", []),
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
        hdr=metadata.get("hdr"),
        source=metadata["source"],
        uploader=metadata.get("uploader"),
        catalog=[catalog] if isinstance(catalog, str) else catalog,
        created_at=metadata["created_at"],
        meta_id=metadata["id"],
        seeders=metadata.get("seeders"),
    )


async def get_or_create_metadata(
    metadata: dict,
    media_type: str,
    is_search_imdb_title: bool,
    is_imdb_only: bool = False,
):
    metadata_class = (
        MediaFusionMovieMetaData if media_type == "movie" else MediaFusionSeriesMetaData
    )
    existing_data = await get_existing_metadata(metadata, metadata_class)
    if not existing_data:
        imdb_data = {}
        if is_search_imdb_title:
            imdb_data = await meta_fetcher.search_metadata(
                metadata["title"],
                metadata.get("year"),
                media_type,
                metadata.get("created_at"),
            )
        if not imdb_data and is_imdb_only:
            return

        metadata["id"] = (
            imdb_data.get("imdb_id") or metadata.get("id") or f"mf{uuid4().fields[-1]}"
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


async def organize_episodes(series_id):
    """Organize episodes by release date and assign sequential numbers"""
    # Fetch all torrent streams for this series
    torrent_streams = await TorrentStreams.find({"meta_id": series_id}).to_list()
    series_data = await MediaFusionSeriesMetaData.get(series_id)

    # Flatten all episodes from all streams and sort by release date
    all_episodes = sorted(
        (episode for stream in torrent_streams for episode in stream.episode_files),
        key=lambda e: (
            e.released.date() or datetime.min.date(),
            e.filename or "",
        ),
    )

    # Assign episode numbers, ensuring the same title across different qualities gets the same number
    season_episode_map = {}
    series_episodes = []

    for episode in all_episodes:
        season = episode.season_number
        if season not in season_episode_map:
            season_episode_map[season] = {}

        title_key = episode.title or episode.filename
        if title_key not in season_episode_map[season]:
            episode_number = len(season_episode_map[season]) + 1
            season_episode_map[season][title_key] = episode_number

            # Create SeriesEpisode for MediaFusionSeriesMetaData
            series_episodes.append(
                SeriesEpisode(
                    season_number=season,
                    episode_number=episode_number,
                    title=episode.title or f"Episode {episode_number}",
                    released=episode.released,
                )
            )

        episode.episode_number = season_episode_map[season][title_key]

    # Update episodes in each torrent stream
    for stream in torrent_streams:
        if stream.episode_files:
            stream.episode_files.sort(key=lambda e: (e.season_number, e.episode_number))
            await stream.save()

    # Update series metadata with organized episodes
    await series_data.update(Set({"episodes": series_episodes}))

    logging.info(f"Organized episodes for series {series_id}")


async def save_metadata(
    metadata: dict, media_type: str, is_search_imdb_title: bool = True
):
    if torrent_stream := await get_stream_by_info_hash(metadata["info_hash"]):
        if (
            metadata.get("expected_sources")
            and torrent_stream.source not in metadata["expected_sources"]
        ):
            logging.info(
                "Source mismatch for %s %s: %s != %s. Trying to re-create the data",
                media_type,
                metadata["title"],
                metadata["source"],
                torrent_stream.source,
            )
            await torrent_stream.delete()
        else:
            logging.info(
                "Stream already exists for %s %s", media_type, metadata["title"]
            )
            return
    metadata = await get_or_create_metadata(metadata, media_type, is_search_imdb_title)

    new_stream = create_stream_object(metadata, media_type == "movie")
    should_organize_episodes = False
    if media_type == "series":
        if metadata.get("episodes") and isinstance(
            metadata["episodes"][0], EpisodeFile
        ):
            episodes = metadata["episodes"]
        else:
            episodes = []
            for file_data in metadata["file_data"]:
                if file_data["filename"] and not is_video_file(file_data["filename"]):
                    continue
                if not file_data.get("episodes"):
                    if metadata["id"].startswith("mf"):
                        episode_number = len(episodes) + 1
                        should_organize_episodes = True
                    else:
                        continue
                else:
                    episode_number = file_data["episodes"][0]

                season_number = (
                    file_data.get("seasons")[0] if file_data.get("seasons") else 1
                )
                episodes.append(
                    EpisodeFile(
                        season_number=season_number,
                        episode_number=episode_number,
                        filename=file_data["filename"],
                        size=file_data["size"],
                        file_index=file_data["index"],
                        title=file_data.get("title"),
                    )
                )

        if not episodes:
            logging.warning("No episodes found for series %s", metadata["title"])
            return
        new_stream.episode_files = episodes

    await new_stream.create()
    if should_organize_episodes:
        await organize_episodes(metadata["id"])
    logging.info(
        "Added stream for %s %s (%s), info_hash: %s",
        media_type,
        metadata["title"],
        metadata["id"],
        metadata["info_hash"],
    )


async def save_movie_metadata(metadata: dict, is_search_imdb_title: bool = True):
    await save_metadata(metadata, "movie", is_search_imdb_title)


async def save_series_metadata(metadata: dict, is_search_imdb_title: bool = True):
    await save_metadata(metadata, "series", is_search_imdb_title)


async def process_search_query(
    search_query: str, catalog_type: str, user_data: schemas.UserData
) -> dict:
    # Initialize base match filter and conditions array
    match_filter = {
        "$text": {
            "$search": search_query,
            "$caseSensitive": False,
        },
        "type": catalog_type,
        "total_streams": {"$gt": 0},
    }
    filter_conditions = []

    # Handle nudity filter
    if "Disable" not in user_data.nudity_filter:
        nudity_conditions = []
        if user_data.nudity_filter:
            nudity_conditions.append(
                {"parent_guide_nudity_status": {"$nin": user_data.nudity_filter}}
            )
        if "Unknown" in user_data.nudity_filter:
            nudity_conditions.append({"parent_guide_nudity_status": {"$exists": True}})

        if len(nudity_conditions) > 1:
            filter_conditions.append({"$and": nudity_conditions})
        elif nudity_conditions:
            filter_conditions.append(nudity_conditions[0])

    # Handle certification filter
    if "Disable" not in user_data.certification_filter:
        cert_conditions = []
        filter_values = get_filter_certification_values(user_data)

        if filter_values:
            cert_conditions.append(
                {"parent_guide_certificates": {"$nin": filter_values}}
            )
        if "Unknown" in user_data.certification_filter:
            cert_conditions.append(
                {
                    "$nor": [
                        {"parent_guide_certificates": {"$exists": False}},
                        {"parent_guide_certificates": {"$size": 0}},
                    ]
                }
            )

        if len(cert_conditions) > 1:
            filter_conditions.append({"$and": cert_conditions})
        elif cert_conditions:
            filter_conditions.append(cert_conditions[0])

    # Combine all filter conditions with the base match filter
    if filter_conditions:
        if len(filter_conditions) > 1:
            match_filter["$and"] = filter_conditions
        else:
            match_filter.update(filter_conditions[0])

    # Define the aggregation pipeline
    pipeline = [
        {"$match": match_filter},
        {"$limit": 50},  # Limit the search results to 50
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
    search_results = (
        await MediaFusionMetaData.get_motor_collection().aggregate(pipeline).to_list(50)
    )

    return {"metas": search_results}


async def process_tv_search_query(search_query: str, namespace: str) -> dict:
    pipeline = [
        {
            "$match": {
                "$text": {
                    "$search": search_query,
                    "$caseSensitive": False,
                },
                "type": "tv",
                "total_streams": {"$gt": 0},
            }
        },
        {"$limit": 50},  # Limit the search results to 50
        # Look up TVStreams to filter by namespace
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
                    {"$limit": 1},
                ],
                "as": "working_streams",
            }
        },
        {
            "$match": {
                "working_streams": {
                    "$ne": []
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
    search_results = (
        await MediaFusionMetaData.get_motor_collection().aggregate(pipeline).to_list(50)
    )

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
            await TVStreams.insert(stream_doc)

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


async def get_events_meta_list(genre=None, skip=0, limit=50) -> list[schemas.Meta]:
    return await dlhd_schedule_service.get_scheduled_events(
        genre=genre, skip=skip, limit=limit
    )


async def get_event_meta(meta_id: str) -> dict:
    events_key = f"event:{meta_id}"
    events_json = await REDIS_ASYNC_CLIENT.get(events_key)
    if not events_json:
        return {}

    event_data = MediaFusionEventsMetaData.model_validate_json(events_json)
    if event_data.event_start_timestamp:
        # Update description with localized time
        event_data.description = f"🎬 {event_data.title} - ⏰ {dlhd_schedule_service.format_event_time(event_data.event_start_timestamp)}"

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
        "genres", {"type": catalog_type, "genres": {"$nin": ["", None, "Adult"]}}
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


async def update_metadata(imdb_ids: list[str], metadata_type: str):
    now = datetime.now()

    def merge_sets(existing_data, new_data, field_name):
        existing_set = (
            set(getattr(existing_data, field_name, [])) if existing_data else set()
        )
        new_set = set(new_data.get(field_name, []))
        return list(existing_set | new_set)

    # Initialize circuit breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )

    async for result in batch_process_with_circuit_breaker(
        meta_fetcher.get_metadata,
        imdb_ids,
        5,
        rate_limit_delay=3,
        cb=circuit_breaker,
        media_type=metadata_type,
    ):
        if not result:
            continue

        meta_id = result["imdb_id"]

        meta_class = (
            MediaFusionMovieMetaData
            if metadata_type == "movie"
            else MediaFusionSeriesMetaData
        )
        # Get existing metadata to preserve values
        existing_metadata = await meta_class.get(meta_id)
        if not existing_metadata:
            logging.warning(
                f"Metadata not found for {metadata_type} {meta_id}. Skipping update."
            )
            continue

        # Merge new data with existing data, preserving existing values when new ones are None/empty
        update_data = {
            "title": result["title"] or existing_metadata.title,
            "poster": result["poster"] or existing_metadata.poster,
            "background": result["background"] or existing_metadata.background,
            "description": result["description"] or existing_metadata.description,
            "runtime": result["runtime"] or existing_metadata.runtime,
            "stars": result["stars"] or existing_metadata.stars,
            "last_updated_at": now,
            "imdb_rating": (
                result.get("imdb_rating")
                if result.get("imdb_rating") is not None
                else existing_metadata.imdb_rating
            ),
            "tmdb_rating": (
                result.get("tmdb_rating")
                if result.get("tmdb_rating") is not None
                else existing_metadata.tmdb_rating
            ),
            "parent_guide_nudity_status": (
                result.get("parent_guide_nudity_status")
                if result.get("parent_guide_nudity_status") != NudityStatus.UNKNOWN
                else existing_metadata.parent_guide_nudity_status
            ),
            "aka_titles": merge_sets(existing_metadata, result, "aka_titles"),
            "genres": merge_sets(existing_metadata, result, "genres"),
            "parent_guide_certificates": merge_sets(
                existing_metadata, result, "parent_guide_certificates"
            ),
        }

        if metadata_type == "series" and result.get("episodes"):
            # Get current series data to compare episodes
            current_series = await MediaFusionSeriesMetaData.get(meta_id)
            if current_series:
                # Create a map of existing episodes by season and episode number
                existing_episodes = {
                    (ep.season_number, ep.episode_number): ep
                    for ep in current_series.episodes
                }

                # Process new episodes
                updated_episodes = []
                updated_episodes_keys = set()
                for new_ep in result["episodes"]:
                    key = (new_ep["season_number"], new_ep["episode_number"])
                    if key in existing_episodes:
                        # Merge new data with existing episode data
                        existing_ep = existing_episodes[key]
                        updated_ep = {
                            "season_number": new_ep["season_number"],
                            "episode_number": new_ep["episode_number"],
                            "title": new_ep["title"] or existing_ep.title,
                            "overview": new_ep["overview"] or existing_ep.overview,
                            "imdb_rating": new_ep.get("imdb_rating")
                            or existing_ep.imdb_rating,
                            "tmdb_rating": new_ep.get("tmdb_rating")
                            or existing_ep.tmdb_rating,
                            "thumbnail": new_ep["thumbnail"] or existing_ep.thumbnail,
                            "released": new_ep["released"] or existing_ep.released,
                        }
                        updated_episodes.append(updated_ep)
                    else:
                        # Add new episode
                        updated_episodes.append(new_ep)
                    updated_episodes_keys.add(key)

                # Add any existing episodes that weren't in the new data
                for ep in current_series.episodes:
                    key = (ep.season_number, ep.episode_number)
                    if key not in updated_episodes_keys:
                        updated_episodes.append(ep.model_dump())

                # Sort episodes by season and episode number
                updated_episodes.sort(
                    key=lambda x: (x["season_number"], x["episode_number"])
                )
                update_data["episodes"] = updated_episodes

        # Update stream-related metadata
        stream_metadata = await update_meta_stream(meta_id, is_update_data_only=True)
        update_data.update(stream_metadata)

        # Update database entries with the new data
        await MediaFusionMetaData.get_motor_collection().update_one(
            {"_id": meta_id},
            {"$set": update_data},
        )
        logging.info(f"Updated metadata for {metadata_type} {meta_id}")

        cache_keys = await REDIS_ASYNC_CLIENT.keys(f"{metadata_type}_{meta_id}_meta*")
        cache_keys.append(f"{metadata_type}_data:{meta_id}")
        await REDIS_ASYNC_CLIENT.delete(*cache_keys)


async def fetch_metadata(imdb_ids: list[str], metadata_type: str):
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=5, half_open_attempts=2
    )

    async for result in batch_process_with_circuit_breaker(
        get_movie_data_by_id if metadata_type == "movie" else get_series_data_by_id,
        imdb_ids,
        5,
        rate_limit_delay=1,
        cb=circuit_breaker,
    ):
        if not result:
            continue

        logging.info(f"Stored metadata for {metadata_type} {result.id}")


async def update_meta_stream(meta_id: str, is_update_data_only: bool = False) -> dict:
    """
    Update stream-related metadata for a given meta_id.
    """
    # Get TorrentStream counts & last stream added date
    streams_stats = await TorrentStreams.aggregate(
        [
            {"$match": {"meta_id": meta_id, "is_blocked": {"$ne": True}}},
            {
                "$group": {
                    "_id": None,
                    "total_streams": {"$sum": 1},
                    "last_stream_added": {"$max": "$created_at"},
                    "catalogs": {"$addToSet": "$catalog"},
                }
            },
        ]
    ).to_list(1)

    update_data = {
        "total_streams": 0,
        "last_stream_added": None,
        "catalogs": [],
        "last_updated_at": datetime.now(tz=timezone.utc),
    }

    if streams_stats:
        # Flatten the catalogs list and remove duplicates
        flattened_catalogs = list(
            {
                catalog
                for sublist in streams_stats[0].get("catalogs", [])
                for catalog in sublist
            }
        )

        update_data.update(
            {
                "total_streams": streams_stats[0].get("total_streams", 0),
                "last_stream_added": streams_stats[0].get("last_stream_added"),
                "catalogs": flattened_catalogs,
            }
        )

    if is_update_data_only:
        return update_data

    # Update database entries with the new stream data
    await MediaFusionMetaData.get_motor_collection().update_one(
        {"_id": meta_id},
        {"$set": update_data},
    )
    logging.info(f"Updated stream metadata for {meta_id}")
    return update_data
