import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from beanie import WriteRules
from beanie.operators import In
from fastapi import BackgroundTasks
from pymongo.errors import DuplicateKeyError

from db import schemas, models
from db.config import settings
from db.models import (
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    TorrentStreams,
    Season,
    Episode,
    MediaFusionTVMetaData,
)
from db.schemas import Stream, MetaIdProjection
from scrappers import tamilmv
from scrappers.prowlarr import scrap_streams_from_prowlarr
from scrappers.torrentio import scrap_streams_from_torrentio
from utils.parser import (
    parse_stream_data,
    get_catalogs,
    search_imdb,
    parse_tv_stream_data,
    fetch_downloaded_info_hashes,
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
    query = MediaFusionTVMetaData.find(
        MediaFusionTVMetaData.is_approved == True, fetch_links=True
    )
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


async def get_movie_streams(
    user_data, secret_str: str, video_id: str, background_tasks: BackgroundTasks
) -> list[Stream]:
    streams = (
        await TorrentStreams.find({"meta_id": video_id})
        .sort(-TorrentStreams.updated_at)
        .to_list()
    )
    last_torrentio_stream = next(
        (stream for stream in streams if stream.source == "Torrentio"), None
    )
    last_prowlarr_stream = next(
        (stream for stream in streams if stream.source == "Prowlarr"), None
    )

    if video_id.startswith("tt"):
        if "torrentio_streams" in user_data.selected_catalogs:
            if (
                last_torrentio_stream is None
                or last_torrentio_stream.updated_at < datetime.now() - timedelta(days=3)
            ):
                streams.extend(
                    await scrap_streams_from_torrentio(video_id, "movie", background_tasks)
                )
        if "prowlarr_streams" in user_data.selected_catalogs:
            # TODO ADD ME LATER
            #if (
            #        last_prowlarr_stream is None
            #        or last_prowlarr_stream.updated_at < datetime.now() - timedelta(days=1)
            #):
            streams.extend(
                await scrap_streams_from_prowlarr(video_id, "movie", background_tasks)
            )

    return await parse_stream_data(streams, user_data, secret_str)


async def get_series_streams(
    user_data, secret_str: str, video_id: str, season: int, episode: int
) -> list[Stream]:
    series_data = await get_series_data_by_id(video_id, True)
    if not series_data:
        return []

    matched_episode_streams = [
        stream for stream in series_data.streams if stream.get_episode(season, episode)
    ]

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
            "background": series_data.poster,
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

                metadata["meta"]["videos"].append(
                    {
                        "id": stream_id,
                        "title": f"S{stream.season.season_number} EP{episode.episode_number}",
                        "season": stream.season.season_number,
                        "episode": episode.episode_number,
                        "released": stream.created_at.strftime(
                            "%Y-%m-%dT%H:%M:%S.000Z"
                        ),
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

    # Determine file index for the main movie file (largest file)
    largest_file = max(metadata["file_data"], key=lambda x: x["size"])

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
        filename=largest_file["filename"],
        file_index=largest_file["index"],
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
    if settings.enable_search_scrapper and catalog_type in ["movie", "series"]:
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


async def save_tv_channel_metadata(tv_metadata: schemas.TVMetaData) -> tuple[str, bool]:
    # Try to get the existing TV channel
    channel_id = "mf" + hashlib.sha256(tv_metadata.title.encode()).hexdigest()[:10]
    logging.info(f"Processing TV channel id {channel_id}")
    existing_channel = await models.MediaFusionTVMetaData.get(channel_id)

    # Map the TVStreams schema to the TVStreams model
    streams_models = []
    for stream in tv_metadata.streams:
        streams_models.append(
            models.TVStreams(
                url=stream.url,
                name=stream.name,
                behaviorHints=stream.behaviorHints.model_dump(exclude_none=True),
                ytId=stream.ytId,
                source=stream.source,
            )
        )

    if existing_channel:
        # Check for existing streams and update if necessary
        await existing_channel.fetch_all_links()
        for new_stream in streams_models:
            matching_stream = next(
                (
                    s
                    for s in existing_channel.streams
                    if (s.url and s.url == new_stream.url)
                    or (s.ytId and s.ytId == new_stream.ytId)
                ),
                None,
            )
            if not matching_stream:
                existing_channel.streams.append(new_stream)
        await existing_channel.save(link_rule=WriteRules.WRITE)
        logging.info(f"Updated TV channel {existing_channel.title}")
        is_new = False
    else:
        tv_metadata.genres.extend([tv_metadata.country, tv_metadata.tv_language])
        genres = list(set(tv_metadata.genres))

        # If the channel doesn't exist, create a new one
        tv_channel_data = models.MediaFusionTVMetaData(
            id=channel_id,
            title=tv_metadata.title,
            poster=tv_metadata.poster,
            background=tv_metadata.background,
            streams=streams_models,
            type="tv",
            country=tv_metadata.country,
            tv_language=tv_metadata.tv_language,
            logo=tv_metadata.logo,
            genres=genres,
            is_approved=False,
        )
        await tv_channel_data.insert(link_rule=WriteRules.WRITE)
        logging.info(f"Added TV channel {tv_channel_data.title}")
        is_new = True

    return channel_id, is_new


async def delete_search_history():
    # Delete search history older than 3 days
    await models.SearchHistory.delete_many(
        {"last_searched": {"$lt": datetime.now() - timedelta(days=3)}}
    )
    logging.info("Deleted search history")
