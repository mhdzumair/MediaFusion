import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from beanie import WriteRules
from beanie.operators import In, Set
from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from db import schemas, models
from db.config import settings
from db.models import (
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    TorrentStreams,
    Season,
    Episode,
    MediaFusionTVMetaData,
    TVStreams,
)
from db.schemas import Stream, MetaIdProjection, TorrentStreamsList
from scrapers import tamilmv
from scrapers.prowlarr import get_streams_from_prowlarr
from scrapers.torrentio import get_streams_from_torrentio
from utils.parser import (
    parse_stream_data,
    get_catalogs,
    search_imdb,
    parse_tv_stream_data,
    fetch_downloaded_info_hashes,
    get_imdb_data,
)


async def get_meta_list(
    user_data: schemas.UserData,
    catalog_type: str,
    catalog: str,
    skip: int = 0,
    limit: int = 25,
) -> list[schemas.Meta]:
    if catalog_type == "movie":
        meta_class = MediaFusionMovieMetaData
    else:
        meta_class = MediaFusionSeriesMetaData

    query_conditions = []

    if user_data.streaming_provider and catalog.startswith(
        user_data.streaming_provider.service
    ):
        downloaded_info_hashes = await fetch_downloaded_info_hashes(user_data)
        if not downloaded_info_hashes:
            return []
        query_conditions.append(In(meta_class.streams.id, downloaded_info_hashes))
    else:
        query_conditions.append(In(meta_class.streams.catalog, [catalog]))

    meta_list = (
        await meta_class.find(
            *query_conditions,
            fetch_links=True,
        )
        .sort(-meta_class.streams.created_at)
        .skip(skip)
        .limit(limit)
        .project(schemas.Meta)
        .to_list()
    )
    for meta in meta_list:
        meta.poster = f"{settings.host_url}/poster/{catalog_type}/{meta.id}.jpg"

    return meta_list


async def get_tv_meta_list(
    genre: Optional[str] = None, skip: int = 0, limit: int = 25
) -> list[schemas.Meta]:
    query = MediaFusionTVMetaData.find(fetch_links=True)
    if genre:
        query = query.find(In(MediaFusionTVMetaData.genres, [genre]))

    tv_meta_list = (
        await query.skip(skip)
        .limit(limit)
        .sort(-MediaFusionTVMetaData.streams.created_at)
        .project(schemas.Meta)
        .to_list()
    )

    for meta in tv_meta_list:
        meta.poster = f"{settings.host_url}/poster/tv/{meta.id}.jpg"

    return tv_meta_list


async def get_movie_data_by_id(
    movie_id: str, fetch_links: bool = False
) -> Optional[MediaFusionMovieMetaData]:
    movie_data = await MediaFusionMovieMetaData.get(movie_id, fetch_links=fetch_links)
    return movie_data


async def get_series_data_by_id(
    series_id: str, fetch_links: bool = False
) -> Optional[MediaFusionSeriesMetaData]:
    series_data = await MediaFusionSeriesMetaData.get(
        series_id, fetch_links=fetch_links
    )
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
                .sort(-TorrentStreams.updated_at)
                .to_list()
            )
        else:
            streams = (
                await TorrentStreams.find({"meta_id": video_id})
                .sort(-TorrentStreams.updated_at)
                .to_list()
            )

        torrent_streams = TorrentStreamsList(streams=streams)

        # Serialize the data and store it in the Redis cache for 30 minutes
        await redis.set(
            cache_key, torrent_streams.model_dump_json(exclude_none=True), ex=1800
        )

    return streams


async def get_movie_streams(
    user_data, secret_str: str, redis: Redis, video_id: str
) -> list[Stream]:
    streams = await get_cached_torrent_streams(redis, video_id)

    if video_id.startswith("tt"):
        if (
            settings.is_scrap_from_torrentio
            and "torrentio_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_torrentio(
                redis, streams, video_id, "movie"
            )
        if (
            settings.prowlarr_api_key
            and "prowlarr_streams" in user_data.selected_catalogs
        ):
            movie_metadata = await get_movie_data_by_id(video_id, False)
            if movie_metadata:
                title, year = movie_metadata.title, movie_metadata.year
            else:
                title, year = get_imdb_data(video_id)
            if title:
                streams = await get_streams_from_prowlarr(
                    redis, streams, video_id, "movie", title, year
                )

    return await parse_stream_data(streams, user_data, secret_str)


async def get_series_streams(
    user_data,
    secret_str: str,
    redis: Redis,
    video_id: str,
    season: int,
    episode: int,
) -> list[Stream]:
    streams = await get_cached_torrent_streams(redis, video_id, season, episode)

    if video_id.startswith("tt"):
        if (
            settings.is_scrap_from_torrentio
            and "torrentio_streams" in user_data.selected_catalogs
        ):
            streams = await get_streams_from_torrentio(
                redis, streams, video_id, "series", season, episode
            )

        if (
            settings.prowlarr_api_key
            and "prowlarr_streams" in user_data.selected_catalogs
        ):
            series_metadata = await get_series_data_by_id(video_id, False)
            if series_metadata:
                title, year = series_metadata.title, series_metadata.year
            else:
                title, year = get_imdb_data(video_id)
            if title:
                streams = await get_streams_from_prowlarr(
                    redis, streams, video_id, "series", title, year, season, episode
                )

    matched_episode_streams = filter(
        lambda stream: stream.get_episode(season, episode), streams
    )

    return await parse_stream_data(
        matched_episode_streams, user_data, secret_str, season, episode
    )


async def get_tv_streams(video_id: str) -> list[Stream]:
    tv_data = await get_tv_data_by_id(video_id, True)
    if not tv_data:
        return []

    return parse_tv_stream_data(tv_data)


async def get_movie_meta(meta_id: str):
    movie_data = await get_movie_data_by_id(meta_id)

    if not movie_data:
        return {}

    return {
        "meta": {
            "_id": meta_id,
            "type": "movie",
            "title": movie_data.title,
            "poster": f"{settings.host_url}/poster/movie/{meta_id}.jpg",
            "background": movie_data.poster,
        }
    }


async def get_series_meta(meta_id: str):
    series_data = await get_series_data_by_id(meta_id, True)

    if not series_data:
        return {}

    metadata = {
        "meta": {
            "_id": meta_id,
            "type": "series",
            "title": series_data.title,
            "poster": f"{settings.host_url}/poster/series/{meta_id}.jpg",
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
        "meta": {"_id": meta_id, **tv_data.model_dump()},
    }


async def save_movie_metadata(metadata: dict):
    # Try to get the existing movie
    existing_movie = await MediaFusionMovieMetaData.find_one(
        {"title": metadata["title"], "year": metadata.get("year")}, fetch_links=True
    )

    if not existing_movie:
        # If the movie doesn't exist in our DB, search for IMDb ID
        imdb_data = search_imdb(metadata["title"], metadata.get("year"))
        meta_id = imdb_data.get("imdb_id")

        if meta_id:
            # Check if the movie with the found IMDb ID already exists in our DB
            existing_movie = await MediaFusionMovieMetaData.get(
                meta_id, fetch_links=True
            )
        else:
            meta_id = f"mf{uuid4().fields[-1]}"
        # Update the poster from IMDb if available
        poster = imdb_data.get("poster") or metadata["poster"]
        background = imdb_data.get("background") or metadata["poster"]
    else:
        poster = existing_movie.poster
        background = existing_movie.background
        meta_id = existing_movie.id

    if "language" in metadata:
        languages = (
            [metadata["language"]]
            if isinstance(metadata["language"], str)
            else metadata["language"]
        )
    else:
        languages = [metadata["scrap_language"]]

    # Create the stream object
    new_stream = TorrentStreams(
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
        meta_id=meta_id,
    )

    if existing_movie:
        # Check if the stream with the same info_hash already exists
        matching_stream = next(
            (stream for stream in existing_movie.streams if stream.id == new_stream.id),
            None,
        )
        if not matching_stream:
            existing_movie.streams.append(new_stream)
        await existing_movie.save(link_rule=WriteRules.WRITE)
        logging.info(
            "Updated movie %s. total streams: %s",
            existing_movie.title,
            len(existing_movie.streams),
        )
    else:
        # If the movie doesn't exist, create a new one
        movie_data = MediaFusionMovieMetaData(
            id=meta_id,
            title=metadata["title"],
            year=metadata["year"],
            poster=poster,
            background=background,
            streams=[new_stream],
        )
        try:
            await movie_data.insert(link_rule=WriteRules.WRITE)
        except DuplicateKeyError:
            logging.warning("Duplicate movie found: %s", movie_data.title)
        logging.info("Added movie %s", movie_data.title)


async def save_series_metadata(metadata: dict):
    # Try to get the existing series
    series = await MediaFusionSeriesMetaData.find_one(
        {"title": metadata["title"]}, fetch_links=True
    )

    if not series:
        # If the series doesn't exist in our DB, search for IMDb ID
        imdb_data = search_imdb(metadata["title"], metadata["year"])
        meta_id = imdb_data.get("imdb_id")

        if meta_id:
            # Check if the series with the found IMDb ID already exists in our DB
            series = await MediaFusionSeriesMetaData.get(meta_id, fetch_links=True)

        if not series:
            meta_id = meta_id or f"mf{uuid4().fields[-1]}"
            poster = imdb_data.get("poster") or metadata["poster"]
            background = imdb_data.get("background") or metadata["poster"]

            # Create an initial entry for the series
            series = MediaFusionSeriesMetaData(
                id=meta_id,
                title=metadata["title"],
                year=metadata["year"],
                poster=poster,
                background=background,
                streams=[],
            )
            await series.insert()
            logging.info("Added series %s", series.title)

    existing_stream = next(
        (s for s in series.streams if s.id == metadata["info_hash"]),
        None,
    )
    if existing_stream:
        # If the stream already exists, return
        logging.info("Stream already exists for series %s", series.title)
        return

    # Extract episodes
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

    # Determine languages
    if "language" in metadata:
        languages = (
            [metadata["language"]]
            if isinstance(metadata["language"], str)
            else metadata["language"]
        )
    else:
        languages = [metadata["scrap_language"]]

    # Create the stream
    stream = TorrentStreams(
        id=metadata["info_hash"],
        torrent_name=metadata["torrent_name"],
        announce_list=metadata["announce_list"],
        size=metadata["total_size"],
        languages=languages,
        resolution=metadata.get("resolution"),
        codec=metadata.get("codec"),
        quality=metadata.get("quality"),
        audio=metadata.get("audio"),
        encoder=metadata.get("encoder"),
        source=metadata["source"],
        catalog=get_catalogs(metadata["catalog"], languages),
        created_at=metadata["created_at"],
        season=Season(season_number=metadata["season"], episodes=episodes),
        meta_id=series.id,
    )

    # Add the stream to the series
    series.streams.append(stream)

    await series.save(link_rule=WriteRules.WRITE)
    logging.info("Updated series %s", series.title)


async def process_search_query(search_query: str, catalog_type: str) -> dict:
    if settings.enable_tamilmv_search_scraper and catalog_type in ["movie", "series"]:
        # check if the search query is already searched in for last 24 hours or not
        last_search = await models.SearchHistory.find_one(
            {
                "query": search_query,
                "last_searched": {"$gte": datetime.now() - timedelta(days=1)},
            }
        )
        if not last_search:
            await models.SearchHistory(query=search_query).save()
            # if the query is not searched in last 24 hours, search in tamilmv
            try:
                await tamilmv.scrap_search_keyword(search_query)
            except Exception as e:
                logging.error(e)

    if catalog_type == "movie":
        meta_class = MediaFusionMovieMetaData
    elif catalog_type == "tv":
        meta_class = MediaFusionTVMetaData
    else:
        meta_class = MediaFusionSeriesMetaData

    search_results = (
        await meta_class.find({"$text": {"$search": search_query}})
        .project(MetaIdProjection)
        .to_list()
    )

    metas = []

    for item in search_results:
        # Use the appropriate function to get the meta data
        if catalog_type == "movie":
            meta = await get_movie_meta(item.id)
        elif catalog_type == "tv":
            meta = await get_tv_meta(item.id)
        else:
            meta = await get_series_meta(item.id)

        if not meta:
            continue

        metas.append(meta["meta"])

    return {"metas": metas}


async def get_stream_by_info_hash(info_hash: str) -> TorrentStreams | None:
    stream = await TorrentStreams.get(info_hash)
    return stream


async def save_tv_channel_metadata(tv_metadata: schemas.TVMetaData) -> str:
    channel_id = "mf" + hashlib.sha256(tv_metadata.title.encode()).hexdigest()[:10]

    # Prepare the genres list
    genres = list(
        set(tv_metadata.genres + [tv_metadata.country, tv_metadata.tv_language])
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
            behaviorHints=stream.behaviorHints.model_dump(exclude_none=True),
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


async def delete_search_history():
    # Delete search history older than 3 days
    await models.SearchHistory.delete_many(
        {"last_searched": {"$lt": datetime.now() - timedelta(days=3)}}
    )
    logging.info("Deleted search history")
