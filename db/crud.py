import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from beanie.exceptions import RevisionIdWasChanged
from beanie.operators import In, Set
from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from db import schemas
from db.config import settings
from db.models import (
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    TorrentStreams,
    Season,
    Episode,
    MediaFusionTVMetaData,
    TVStreams,
    MediaFusionEventsMetaData,
    MediaFusionMetaData,
)
from db.schemas import Stream, TorrentStreamsList
from scrapers.imdb_data import get_imdb_movie_data, search_imdb
from scrapers.prowlarr import get_streams_from_prowlarr
from scrapers.torrentio import get_streams_from_torrentio
from utils import crypto
from utils.parser import (
    parse_stream_data,
    get_catalogs,
    parse_tv_stream_data,
    fetch_downloaded_info_hashes,
)
from utils.validation_helper import validate_parent_guide_nudity


async def get_meta_list(
    user_data: schemas.UserData,
    catalog_type: str,
    catalog: str,
    is_watchlist_catalog: bool,
    skip: int = 0,
    limit: int = 25,
    user_ip: str | None = None,
) -> list[schemas.Meta]:
    # Define query filters for TorrentStreams based on catalog
    if is_watchlist_catalog:
        downloaded_info_hashes = await fetch_downloaded_info_hashes(user_data, user_ip)
        if not downloaded_info_hashes:
            return []
        query_filters = {"_id": {"$in": downloaded_info_hashes}}
    else:
        query_filters = {"catalog": {"$in": [catalog]}}

    poster_path = f"{settings.poster_host_url}/poster/{catalog_type}/"
    meta_class = MediaFusionMetaData

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
                    {
                        "$match": {
                            "type": catalog_type,
                            "$or": [
                                {  # If nudity status exists and matches regex, filter it out
                                    "parent_guide_nudity_status": {
                                        "$not": {
                                            "$regex": settings.parent_guide_nudity_filter_types_regex,
                                            "$options": "i",
                                        }
                                    }
                                },
                                {  # Check certificates only if nudity status is null
                                    "parent_guide_nudity_status": {"$eq": None},
                                    "parent_guide_certificates": {
                                        "$not": {
                                            "$elemMatch": {
                                                "$regex": settings.parent_guide_certificates_filter_regex,
                                                "$options": "i",
                                            }
                                        }
                                    },
                                },
                            ],
                        }
                    },
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
        "namespace": {"$in": [namespace, "mediafusion", None]},
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


async def get_movie_data_by_id(
    movie_id: str, redis: Redis
) -> Optional[MediaFusionMovieMetaData]:
    # Check if the movie data is already in the cache
    cached_data = await redis.get(f"movie_data:{movie_id}")
    if cached_data:
        return MediaFusionMovieMetaData.model_validate_json(cached_data)

    movie_data = await MediaFusionMovieMetaData.get(movie_id)
    # store it in the db for feature reference.
    if not movie_data and movie_id.startswith("tt"):
        movie = await get_imdb_movie_data(movie_id)
        if not movie:
            return None

        movie_data = MediaFusionMovieMetaData(
            id=movie_id,
            title=movie.get("title"),
            year=movie.get("year"),
            poster=movie.get("full-size cover url"),
            background=movie.get("full-size cover url"),
            streams=[],
            description=movie.get("plot outline"),
            genres=movie.get("genres"),
            imdb_rating=movie.get("rating"),
            parent_guide_nudity_status=movie.get("parent_guide_nudity_status"),
            parent_guide_certificates=movie.get("parent_guide_certificates"),
            aka_titles=movie.get("aka_titles"),
            stars=movie.get("stars"),
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
        await redis.set(
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
        series = await get_imdb_movie_data(series_id)
        if not series:
            return None

        series_data = MediaFusionSeriesMetaData(
            id=series_id,
            title=series.get("title"),
            year=series.get("year"),
            poster=series.get("full-size cover url"),
            background=series.get("full-size cover url"),
            streams=[],
            description=series.get("plot outline"),
            genres=series.get("genres"),
            imdb_rating=series.get("rating"),
            parent_guide_nudity_status=series.get("parent_guide_nudity_status"),
            parent_guide_certificates=series.get("parent_guide_certificates"),
            stars=series.get("stars"),
            aka_titles=series.get("aka_titles"),
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
    redis: Redis,
    video_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> list[TorrentStreams]:
    # Create a unique key for Redis
    cache_key = f"torrent_streams:{video_id}:{season}:{episode}"

    # Try to get the data from the Redis cache
    cached_data = await redis.get(cache_key)

    if cached_data is not None:
        # If the data is in the cache, deserialize it and return it
        streams = TorrentStreamsList.model_validate_json(cached_data).streams
    else:
        # If the data is not in the cache, query it from the database
        if season is not None and episode is not None:
            streams = (
                await TorrentStreams.find({"meta_id": video_id})
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
                await TorrentStreams.find({"meta_id": video_id})
                .sort("-updated_at")
                .to_list()
            )

        torrent_streams = TorrentStreamsList(streams=streams)

        # Serialize the data and store it in the Redis cache for 30 minutes
        await redis.set(
            cache_key, torrent_streams.model_dump_json(exclude_none=True), ex=1800
        )

    return streams


async def get_movie_streams(
    user_data, secret_str: str, redis: Redis, video_id: str, user_ip: str | None = None
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
    movie_metadata = await get_movie_data_by_id(video_id, redis)
    if not (movie_metadata and validate_parent_guide_nudity(movie_metadata)):
        return []

    streams = await get_cached_torrent_streams(redis, video_id)

    if video_id.startswith("tt"):
        if (
            settings.is_scrap_from_torrentio
            and "torrentio_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_torrentio(
                redis,
                streams,
                video_id,
                catalog_type="movie",
                title=movie_metadata.title,
                aka_titles=movie_metadata.aka_titles,
            )
        if (
            settings.prowlarr_api_key
            and "prowlarr_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_prowlarr(
                redis,
                streams,
                video_id,
                "movie",
                movie_metadata.title,
                movie_metadata.aka_titles,
                movie_metadata.year,
            )

    return await parse_stream_data(streams, user_data, secret_str, user_ip=user_ip)


async def get_series_streams(
    user_data,
    secret_str: str,
    redis: Redis,
    video_id: str,
    season: int,
    episode: int,
    user_ip: str | None = None,
) -> list[Stream]:
    if season is None or episode is None:
        season = episode = 1
    series_metadata = await get_series_data_by_id(video_id, False)
    if not (series_metadata and validate_parent_guide_nudity(series_metadata)):
        return []

    streams = await get_cached_torrent_streams(redis, video_id, season, episode)

    if video_id.startswith("tt"):
        if (
            settings.is_scrap_from_torrentio
            and "torrentio_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_torrentio(
                redis,
                streams,
                video_id,
                catalog_type="series",
                title=series_metadata.title,
                aka_titles=series_metadata.aka_titles,
                season=season,
                episode=episode,
            )

        if (
            settings.prowlarr_api_key
            and "prowlarr_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_prowlarr(
                redis,
                streams,
                video_id,
                "series",
                series_metadata.title,
                series_metadata.aka_titles,
                series_metadata.year,
                season,
                episode,
            )

    matched_episode_streams = filter(
        lambda stream: stream.get_episode(season, episode), streams
    )

    return await parse_stream_data(
        matched_episode_streams, user_data, secret_str, season, episode, user_ip=user_ip
    )


async def get_tv_streams(redis: Redis, video_id: str, namespace: str) -> list[Stream]:
    tv_streams = await TVStreams.find(
        {
            "meta_id": video_id,
            "is_working": True,
            "namespace": {"$in": [namespace, "mediafusion", None]},
        },
    ).to_list()

    return await parse_tv_stream_data(tv_streams, redis)


async def get_movie_meta(meta_id: str, redis: Redis):
    movie_data = await get_movie_data_by_id(meta_id, redis)

    if not (movie_data and validate_parent_guide_nudity(movie_data)):
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


async def get_series_meta(meta_id: str):
    series_data = await get_series_data_by_id(meta_id, True)

    if not (series_data and validate_parent_guide_nudity(series_data)):
        return {}

    metadata = {
        "meta": {
            "_id": meta_id,
            "type": "series",
            "title": series_data.title,
            "poster": f"{settings.poster_host_url}/poster/series/{meta_id}.jpg",
            "background": series_data.background or series_data.poster,
            "videos": [],
        }
    }

    # Loop through streams to populate the videos list
    for stream in series_data.streams:
        stream: TorrentStreams
        if stream.season:  # Ensure the stream has season data
            for episode in stream.season.episodes:
                stream_id = (
                    f"{meta_id}:{stream.season.season_number}:{episode.episode_number}"
                )
                # check if the stream is already in the list
                if next(
                    (
                        video
                        for video in metadata["meta"]["videos"]
                        if video["id"] == stream_id
                    ),
                    None,
                ):
                    continue

                released_date = episode.released or stream.created_at

                metadata["meta"]["videos"].append(
                    {
                        "id": stream_id,
                        "title": f"S{stream.season.season_number} EP{episode.episode_number}"
                        if not episode.title
                        else episode.title,
                        "season": stream.season.season_number,
                        "episode": episode.episode_number,
                        "released": released_date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    }
                )

    # Sort the videos by season and episode
    metadata["meta"]["videos"] = sorted(
        metadata["meta"]["videos"], key=lambda x: (x["season"], x["episode"])
    )

    return metadata


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
    if issubclass(model, MediaFusionMovieMetaData):
        filters["year"] = metadata.get("year")

    return await model.find_one(
        filters,
        projection_model=schemas.MetaIdProjection,
    )


def create_metadata_object(metadata, imdb_data, model):
    poster = imdb_data.get("poster") or metadata["poster"]
    background = imdb_data.get("background") or metadata["poster"]
    return model(
        id=metadata["id"],
        title=metadata["title"],
        year=metadata["year"],
        poster=poster,
        background=background,
        streams=[],
        description=metadata.get("description"),
        runtime=metadata.get("runtime"),
        website=metadata.get("website"),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", False),
        stars=metadata.get("stars"),
        aka_titles=imdb_data.get("aka_titles"),
    )


def create_stream_object(metadata):
    languages = (
        [metadata["language"]]
        if isinstance(metadata["language"], str)
        else metadata["language"]
    )
    return TorrentStreams(
        id=metadata["info_hash"],
        torrent_name=metadata["torrent_name"],
        announce_list=metadata["announce_list"],
        size=metadata["total_size"],
        filename=metadata["largest_file"]["filename"],
        file_index=metadata["largest_file"]["index"],
        languages=languages,
        resolution=metadata.get("resolution"),
        codec=metadata.get("codec"),
        quality=metadata.get("quality"),
        audio=metadata.get("audio"),
        encoder=metadata.get("encoder"),
        source=metadata["source"],
        catalog=get_catalogs(metadata["catalog"], languages),
        created_at=metadata["created_at"],
        meta_id=metadata["id"],
    )


async def save_movie_metadata(metadata: dict, is_imdb: bool = True):
    if await is_torrent_stream_exists(metadata["info_hash"]):
        logging.info("Stream already exists for movie %s", metadata["title"])
        return

    existing_movie = await get_existing_metadata(metadata, MediaFusionMovieMetaData)
    if not existing_movie:
        imdb_data = {}
        if is_imdb:
            imdb_data = search_imdb(metadata["title"], metadata.get("year"))
        metadata["id"] = imdb_data.get("imdb_id") or f"mf{uuid4().fields[-1]}"
        movie_data = create_metadata_object(
            metadata, imdb_data, MediaFusionMovieMetaData
        )
        try:
            await movie_data.create()
        except DuplicateKeyError:
            logging.warning("Duplicate movie found: %s", movie_data.title)

    new_stream = create_stream_object(metadata)
    await new_stream.create()
    logging.info(
        "Added stream for movie %s, info_hash: %s",
        metadata["title"],
        metadata["info_hash"],
    )


async def save_series_metadata(metadata: dict, is_imdb: bool = True):
    if await is_torrent_stream_exists(metadata["info_hash"]):
        logging.info("Stream already exists for series %s", metadata["title"])
        return
    series_data = await get_existing_metadata(metadata, MediaFusionSeriesMetaData)
    if not series_data:
        imdb_data = {}
        if is_imdb:
            imdb_data = search_imdb(metadata["title"], metadata.get("year"))
        metadata["id"] = imdb_data.get("imdb_id") or f"mf{uuid4().fields[-1]}"
        series_data = create_metadata_object(
            metadata, imdb_data, MediaFusionSeriesMetaData
        )
        try:
            await series_data.create()
        except DuplicateKeyError:
            logging.warning("Duplicate series found: %s", series_data.title)

    if metadata.get("episodes"):
        episodes = metadata["episodes"]
    else:
        episodes = [
            Episode(
                episode_number=file["episode"],
                filename=file["filename"],
                size=file["size"],
                file_index=file["index"],
            )
            for file in metadata["file_data"]
            if file["episode"]
        ]

    if not episodes:
        logging.warning("No episodes found for series %s", series_data.title)
        return
    new_stream = create_stream_object(metadata)
    new_stream.season = Season(
        season_number=metadata.get("season", 1), episodes=episodes
    )
    await new_stream.create()
    logging.info(
        "Added stream for series %s, info_hash: %s",
        metadata["title"],
        metadata["info_hash"],
    )


async def process_search_query(
    search_query: str, catalog_type: str, user_data: schemas.UserData
) -> dict:
    pipeline = [
        {
            "$match": {
                "$text": {"$search": search_query},  # Perform the text search
                "type": catalog_type,
                "$or": [  # Nudity filtering
                    {
                        "parent_guide_nudity_status": {
                            "$not": {
                                "$regex": settings.parent_guide_nudity_filter_types_regex,
                                "$options": "i",
                            }
                        }
                    },
                    {
                        "parent_guide_nudity_status": {"$eq": None},
                        "parent_guide_certificates": {
                            "$not": {
                                "$elemMatch": {
                                    "$regex": settings.parent_guide_certificates_filter_regex,
                                    "$options": "i",
                                }
                            }
                        },
                    },
                ],
            }
        },
        {"$limit": 50},  # Limit the search results to 50
        {
            "$lookup": {
                "from": TorrentStreams.get_collection_name(),
                "localField": "_id",
                "foreignField": "meta_id",
                "pipeline": [
                    {"$match": {"catalog": {"$in": user_data.selected_catalogs}}},
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
    search_results = (
        await MediaFusionMetaData.get_motor_collection().aggregate(pipeline).to_list(50)
    )

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
                            "namespace": {"$in": [namespace, "mediafusion", None]},
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
    search_results = (
        await MediaFusionMetaData.get_motor_collection().aggregate(pipeline).to_list(50)
    )

    return {"metas": search_results}


async def get_stream_by_info_hash(info_hash: str) -> TorrentStreams | None:
    stream = await TorrentStreams.get(info_hash)
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
        await MediaFusionTVMetaData.find_one(
            MediaFusionTVMetaData.id == channel_id
        ).upsert(
            Set({}),  # Update operation is a no-op for now
            on_insert=MediaFusionTVMetaData(
                id=channel_id,
                title=tv_metadata.title,
                poster=tv_metadata.poster,
                background=tv_metadata.background,
                country=tv_metadata.country,
                tv_language=tv_metadata.tv_language,
                logo=tv_metadata.logo,
                genres=genres,
                type="tv",
                streams=[],
            ),
        )
    except DuplicateKeyError:
        pass

    # Stream processing
    stream_ids = []
    for stream in tv_metadata.streams:
        # Define stream document with meta_id
        stream_doc = TVStreams(
            url=stream.url,
            name=stream.name,
            behaviorHints=stream.behaviorHints.model_dump(exclude_none=True)
            if stream.behaviorHints
            else None,
            ytId=stream.ytId,
            source=stream.source,
            country=stream.country,
            meta_id=channel_id,
        )

        # Check if the stream exists (by URL or ytId) and upsert accordingly
        existing_stream = await TVStreams.find_one(
            TVStreams.url == stream.url,
            TVStreams.ytId == stream.ytId,
        )
        if existing_stream:
            stream_ids.append(existing_stream.id)
        else:
            inserted_stream = await stream_doc.insert()
            stream_ids.append(inserted_stream.id)

    # Update the TV channel with new stream links, if there are any new streams
    if stream_ids:
        await MediaFusionTVMetaData.find_one(
            MediaFusionTVMetaData.id == channel_id
        ).update(
            {
                "$addToSet": {
                    "streams": {
                        "$each": [
                            TVStreams.link_from_id(stream_id)
                            for stream_id in stream_ids
                        ]
                    }
                }
            }
        )

    logging.info(f"Processed TV channel {tv_metadata.title}")
    return channel_id


async def save_events_data(redis: Redis, metadata: dict) -> str:
    # Generate a unique event key
    meta_id = "mf" + crypto.get_text_hash(metadata["title"])
    event_key = f"event:{meta_id}"

    # Attempt to fetch existing event data
    existing_event_json = await redis.get(event_key)

    if existing_event_json:
        # Deserialize the existing event data
        existing_event_data = MediaFusionEventsMetaData.model_validate_json(
            existing_event_json
        )
        # Use a dictionary keyed by 'url' to ensure uniqueness of streams
        existing_streams = {
            stream.url or stream.ytId or stream.externalUrl: stream
            for stream in existing_event_data.streams
        }
    else:
        existing_streams = {}

    # Update or add streams based on the uniqueness of 'url'
    for stream in metadata["streams"]:
        # Create a TVStreams instance for each stream
        stream_instance = TVStreams(**stream)
        existing_streams[
            stream_instance.url or stream_instance.ytId or stream_instance.externalUrl
        ] = stream_instance.dict()

    # Update the event metadata with the updated list of streams
    streams = list(existing_streams.values())

    event_start_timestamp = metadata.get("event_start_timestamp", 0)

    # Create or update the event data
    events_data = MediaFusionEventsMetaData(
        id=meta_id,
        streams=streams,
        title=metadata["title"],
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
    await redis.set(event_key, events_json, ex=cache_ttl)

    logging.info(
        f"{'Updating' if existing_event_json else 'Inserting'} event data for {events_data.title} with event key {event_key}"
    )

    # Add the event key to a set of all events
    await redis.zadd("events:all", {event_key: event_start_timestamp})

    # Index the event by genre
    for genre in events_data.genres:
        await redis.zadd(f"events:genre:{genre}", {event_key: event_start_timestamp})

    return event_key


async def get_events_meta_list(
    redis, genre=None, skip=0, limit=25
) -> list[schemas.Meta]:
    if genre:
        key_pattern = f"events:genre:{genre}"
    else:
        key_pattern = "events:all"

    # Fetch event keys sorted by timestamp in descending order
    events_keys = await redis.zrevrange(key_pattern, skip, skip + limit - 1)
    events = []

    # Iterate over event keys, fetching and decoding JSON data
    for key in events_keys:
        events_json = await redis.get(key)
        if events_json:
            meta_data = schemas.Meta.model_validate_json(events_json)
            meta_data.poster = (
                f"{settings.poster_host_url}/poster/events/{meta_data.id}.jpg"
            )
            events.append(meta_data)
        else:
            # Cleanup: Remove expired or missing event key from the index
            await redis.zrem(key_pattern, key)

    return events


async def get_event_meta(redis, meta_id: str) -> dict:
    events_key = f"event:{meta_id}"
    events_json = await redis.get(events_key)
    if not events_json:
        return {}

    event_data = MediaFusionTVMetaData.model_validate_json(events_json)
    return {
        "meta": {
            "_id": meta_id,
            **event_data.model_dump(),
            "description": event_data.description or event_data.title,
        }
    }


async def get_event_data_by_id(redis, meta_id: str) -> MediaFusionEventsMetaData | None:
    event_key = f"event:{meta_id}"
    events_json = await redis.get(event_key)
    if not events_json:
        return None

    return MediaFusionEventsMetaData.model_validate_json(events_json)


async def get_event_streams(redis, meta_id: str) -> list[Stream]:
    event_key = f"event:{meta_id}"
    event_json = await redis.get(event_key)
    if not event_json:
        return await parse_tv_stream_data([], redis)

    event_data = MediaFusionEventsMetaData.model_validate_json(event_json)
    return await parse_tv_stream_data(event_data.streams, redis)


async def get_genres(catalog_type: str, redis: Redis) -> list[str]:
    if catalog_type == "movie":
        meta_class = MediaFusionMovieMetaData
    elif catalog_type == "tv":
        meta_class = MediaFusionTVMetaData
    else:
        meta_class = MediaFusionSeriesMetaData

    genres = await redis.get(f"{catalog_type}_genres")
    if genres:
        return json.loads(genres)

    genres = await meta_class.distinct("genres", {"genres": {"$ne": ""}})

    # cache the genres for 30 minutes
    await redis.set(f"{catalog_type}_genres", json.dumps(genres), ex=1800)
    return genres


async def fetch_last_run(redis: Redis, spider_id: str, spider_name: str):
    task_key = f"background_tasks:run_spider:spider_name={spider_id}"
    last_run_timestamp = await redis.get(task_key)

    if last_run_timestamp:
        last_run = datetime.fromtimestamp(float(last_run_timestamp))
        delta = datetime.now() - last_run
        delta_str = str(delta)
        return spider_name, {
            "last_run": last_run.isoformat(),
            "time_since_last_run": delta_str,
            "time_since_last_run_seconds": delta.total_seconds(),
        }
    else:
        return spider_name, {
            "last_run": "Never",
            "time_since_last_run": "Never run",
            "time_since_last_run_seconds": -1,
        }
