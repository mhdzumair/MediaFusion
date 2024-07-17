import logging
from datetime import datetime, timedelta

import PTT
import httpx
from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from db.schemas import TorrentStreamsList
from scrapers.helpers import (
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.const import UA_HEADER
from utils.parser import (
    is_contain_18_plus_keywords,
    calculate_max_similarity_ratio,
)


async def get_streams_from_zilean(
    redis: Redis,
    streams: list[TorrentStreams],
    video_id: str,
    catalog_type: str,
    title: str,
    aka_titles: list[str],
    season: int = None,
    episode: int = None,
):
    cache_key = f"{catalog_type}_{video_id}_{season}_{episode}_zilean_dmm_streams"
    cached_data = await redis.get(cache_key)
    if cached_data:
        return TorrentStreamsList.model_validate_json(cached_data).streams

    if catalog_type == "movie":
        streams.extend(
            await scrap_movie_streams_from_zilean(
                video_id, title, aka_titles
            )
        )
    elif catalog_type == "series":
        streams.extend(
            await scrap_series_streams_from_zilean(
                video_id, title, aka_titles, season, episode
            )
        )

    # Cache the data for 24 hours
    await redis.set(
        cache_key,
        TorrentStreamsList(streams=streams).model_dump_json(exclude_none=True),
        ex=int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds()),
    )

    return streams


async def fetch_stream_data(url: str) -> list:
    """Fetch stream data asynchronously."""
    async with httpx.AsyncClient(
        headers=UA_HEADER, proxy=settings.scraper_proxy_url
    ) as client:
        response = await client.get(url, timeout=10)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def scrap_movie_streams_from_zilean(
    video_id: str, title: str, aka_titles: list[str],
) -> list[TorrentStreams]:
    """
    Get streams by text from zilean DMM.
    """
    url = f"{settings.zilean_url}/dmm/filtered?Query={title}"
    try:
        stream_data = await fetch_stream_data(url)
        return await store_and_parse_movie_stream_data(
            video_id, title, aka_titles, stream_data,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from zilean: {e}")
        return []


async def scrap_series_streams_from_zilean(
    video_id: str,
    title: str,
    aka_titles: list[str],
    season: int,
    episode: int,
) -> list[TorrentStreams]:
    """
    Get streams by text from zilean DMM.
    """
    url = f"{settings.zilean_url}/dmm/filtered?Query={title}&season={season}&episode={episode}"
    try:
        stream_data = await fetch_stream_data(url)
        return await store_and_parse_series_stream_data(
            video_id, title, aka_titles, season, episode, stream_data,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from zilean: {e}")
        return []


async def store_and_parse_movie_stream_data(
    video_id: str, title: str, aka_titles: list[str], stream_data: list
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        torrent_stream = await TorrentStreams.get(stream["infoHash"])
        if torrent_stream:
            continue

        if is_contain_18_plus_keywords(stream["rawTitle"]):
            logging.warning(f"Stream contains 18+ keywords: {stream['rawTitle']}")
            continue

        metadata = PTT.parse_title(stream['rawTitle'])

        # validate the ratio as zilean provides only matching torrent names
        max_similarity_ratio = calculate_max_similarity_ratio(
            metadata.get("title"), title, aka_titles
        )
        if max_similarity_ratio < 85:
            logging.error(
                f"Title mismatch: '{title}' != '{metadata.get('title')}' ratio: {max_similarity_ratio}"
            )
            continue

        # Create new stream
        torrent_stream = TorrentStreams(
            id=stream["infoHash"],
            torrent_name=stream["rawTitle"],
            announce_list=[],
            size=stream["size"],
            languages=[language.title() for language in metadata.get("languages", stream["languages"])],
            resolution=metadata.get("resolution"),
            codec=metadata.get("codec"),
            quality=metadata.get("quality"),
            audio=metadata.get("audio"),
            source="Zilean DMM",
            catalog=["zilean_dmm_streams"],
            updated_at=datetime.now(),
            meta_id=video_id,
        )
        try:
            await torrent_stream.create()
        except DuplicateKeyError:
            # Skip if the stream already exists
            continue
        streams.append(torrent_stream)
        if torrent_stream.filename is None:
            info_hashes.append(stream["infoHash"])

    if info_hashes:
        update_torrent_movie_streams_metadata.send(info_hashes)
    return streams


async def store_and_parse_series_stream_data(
    video_id: str,
    title: str,
    aka_titles: list[str],
    season: int,
    episode: int,
    stream_data: list,
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        torrent_stream = await TorrentStreams.get(stream["infoHash"])
        if torrent_stream:
            continue

        if is_contain_18_plus_keywords(stream["title"]):
            logging.warning(f"Stream contains 18+ keywords: {stream['title']}")
            continue

        metadata = PTT.parse_title(stream['rawTitle'])

        # validate the ratio as zilean provides only matching torrent names
        max_similarity_ratio = calculate_max_similarity_ratio(
            metadata.get("title"), title, aka_titles
        )
        if max_similarity_ratio < 85:
            logging.error(
                f"Title mismatch: '{title}' != '{metadata.get('title')}' ratio: {max_similarity_ratio}"
            )
            continue

        if (seasons := metadata.get("seasons")) or (seasons := stream["seasons"]):
            if len(seasons) == 1:
                season_number = seasons[0]
            else:
                # Skip This Stream due to multiple seasons in one torrent.
                # TODO: Handle this case later.
                #  Need to refactor DB and how streaming provider works.
                continue

        if (episodes := metadata.get("episodes")) or (episodes := stream.get("episodes")):
            episode_data = [
                Episode(
                    episode_number=episode_number,
                )
                for episode_number in episodes
            ]
        else:
            episode_data = [
                Episode(episode_number=episode, file_index=stream.get("fileIdx"))
            ]

        torrent_stream = TorrentStreams(
            id=stream["infoHash"],
            torrent_name=stream["rawTitle"],
            announce_list=[],
            size=stream["size"],
            languages=[language.title() for language in metadata.get("languages", stream["languages"])],
            resolution=metadata.get("resolution"),
            codec=metadata.get("codec"),
            quality=metadata.get("quality"),
            audio=metadata.get("audio"),
            source="Zilean DMM",
            catalog=["zilean_dmm_streams"],
            updated_at=datetime.now(),
            meta_id=video_id,
            season=Season(
                season_number= season_number,
                episodes=episode_data,
            ),
        )
        await torrent_stream.create()
        episode_item = torrent_stream.get_episode(season, episode)

        streams.append(torrent_stream)

        if episode_item and episode_item.size is None:
            info_hashes.append(stream["infoHash"])

    if info_hashes:
        update_torrent_series_streams_metadata.send(info_hashes)
    return streams
