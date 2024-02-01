import asyncio
import logging
from datetime import datetime, timedelta

import PTN
import httpx
from fastapi import BackgroundTasks
from torf import Magnet

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from db.schemas import UserData
from scrappers.helpers import (
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.torrent import extract_torrent_metadata


async def get_streams_from_prowlarr(
    user_data: UserData,
    streams: list[TorrentStreams],
    video_id: str,
    catalog_type: str,
    title: str,
    year: str,
    background_tasks: BackgroundTasks,
    season: int = None,
    episode: int = None,
):
    last_stream = next(
        (stream for stream in streams if "prowlarr_streams" in stream.catalog), None
    )
    if video_id.startswith("tt") and "prowlarr_streams" in user_data.selected_catalogs:
        if last_stream is None or last_stream.updated_at < datetime.now() - timedelta(
            hours=settings.prowlarr_search_interval_hour
        ):
            if catalog_type == "movie":
                streams.extend(
                    await scrap_movies_streams_from_prowlarr(
                        video_id, title, year, background_tasks
                    )
                )
            elif catalog_type == "series":
                streams.extend(
                    await scrap_series_streams_from_prowlarr(
                        video_id, title, background_tasks, season, episode
                    )
                )
    return streams


async def fetch_stream_data(url: str, params: dict) -> dict:
    """Fetch stream data asynchronously."""
    headers = {
        "accept": "application/json",
        "X-Api-Key": settings.prowlarr_api_key,
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=10, params=params, headers=headers)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def scrap_movies_streams_from_prowlarr(
    video_id: str,
    title: str,
    year: str,
    background_tasks: BackgroundTasks = None,
) -> list[TorrentStreams]:
    """
    Get movie streams by IMDb ID from prowlarr.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    stream_data = []

    # Params for IMDb ID search
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}",
        "categories": [2000],  # Movies
        "type": "movie",
        "limit": 20,
        "offset": 0,
    }

    # Params for title search
    params_title = {
        "query": title,
        "categories": [2000],  # Movies
        "type": "search",
        "limit": 20,
        "offset": 0,
    }

    try:
        # Fetch data for both searches simultaneously
        imdb_search, title_search = await asyncio.gather(
            fetch_stream_data(url, params_imdb),
            fetch_stream_data(url, params_title),
        )
        stream_data.extend(imdb_search)
        stream_data.extend(title_search)
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts

    return await store_and_parse_movie_stream_data(
        video_id, title, year, stream_data, background_tasks
    )


async def scrap_series_streams_from_prowlarr(
    video_id: str,
    title: str,
    background_tasks: BackgroundTasks = None,
    season: int = None,
    episode: int = None,
) -> list[TorrentStreams]:
    """
    Get series streams by IMDb ID from prowlarr.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    stream_data = []

    # Params for IMDb ID, season, and episode search
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}{{Season:{season}}}{{Episode:{episode}}}",
        "categories": [5000],  # TV
        "type": "tvsearch",
        "limit": 20,
        "offset": 0,
    }

    # Params for title search
    params_title = {
        "query": title,
        "categories": [5000],  # TV
        "type": "search",
        "limit": 20,
        "offset": 0,
    }

    try:
        # Fetch data for both searches simultaneously
        imdb_search, title_search = await asyncio.gather(
            fetch_stream_data(url, params_imdb),
            fetch_stream_data(url, params_title),
        )
        stream_data.extend(imdb_search)
        stream_data.extend(title_search)
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts

    return await store_and_parse_series_stream_data(
        video_id, title, season, episode, stream_data, background_tasks
    )


async def get_torrent_data_from_prowlarr(download_url: str) -> tuple[dict, bool]:
    """Get torrent data from prowlarr."""
    if not download_url:
        return {}, False
    if download_url.startswith("magnet:"):
        magnet = Magnet.from_string(download_url)
        return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False

    async with httpx.AsyncClient() as client:
        response = await client.get(download_url, follow_redirects=False)

    if response.status_code == 301:
        magnet_url = response.headers.get("Location")
        magnet = Magnet.from_string(magnet_url)
        return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False
    elif response.status_code == 200:
        return extract_torrent_metadata(response.content), True
    else:
        return {}, False


async def prowlarr_data_parser(meta_data: dict) -> tuple[dict, bool]:
    """Parse prowlarr data."""
    try:
        torrent_data, is_torrent_downloaded = await get_torrent_data_from_prowlarr(
            meta_data.get("downloadUrl") or meta_data.get("magnetUrl")
        )
    except Exception as e:
        logging.warning(f"Error parsing torrent data: {e} {e.__class__.__name__}")
        if meta_data.get("infoHash"):
            torrent_data = {
                "info_hash": meta_data.get("infoHash"),
                "announce_list": [],
            }
            is_torrent_downloaded = False
        else:
            return {}, False

    torrent_data.update(
        {
            "seeders": meta_data.get("seeders"),
            "created_at": datetime.strptime(
                meta_data.get("publishDate"), "%Y-%m-%dT%H:%M:%SZ"
            ),
            "source": meta_data.get("indexer"),
            "poster_url": meta_data.get("posterUrl"),
        }
    )
    if is_torrent_downloaded is False:
        torrent_data.update(
            {
                "torrent_name": meta_data.get("title"),
                "total_size": meta_data.get("size"),
                **PTN.parse(meta_data.get("title")),
            }
        )
    return torrent_data, is_torrent_downloaded


async def store_and_parse_movie_stream_data(
    video_id: str,
    title: str,
    year: str,
    stream_data: list,
    background_tasks: BackgroundTasks,
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        parsed_data, _ = await prowlarr_data_parser(stream)
        info_hash = parsed_data.get("info_hash", "").lower()
        if not info_hash or not parsed_data.get("seeders"):
            logging.warning(
                f"Skipping {info_hash} due to missing info_hash or seeders: {parsed_data.get('seeders')}"
            )
            continue

        if not (
            parsed_data.get("title").lower() == title.lower()
            and parsed_data.get("year") == year
        ) and (str(stream.get("imdbId", "")) not in video_id):
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}' or year mismatch: '{parsed_data.get('year')}' != '{year}'"
            )
            continue

        torrent_stream = await TorrentStreams.get(info_hash)

        if torrent_stream:
            # Update existing stream
            torrent_stream.seeders = parsed_data["seeders"]
            torrent_stream.updated_at = datetime.now()
            await torrent_stream.save()
        else:
            # Create new stream
            torrent_stream = TorrentStreams(
                id=info_hash,
                torrent_name=parsed_data.get("torrent_name"),
                announce_list=parsed_data.get("announce_list"),
                size=parsed_data.get("total_size"),
                filename=parsed_data.get("largest_file", {}).get("file_name"),
                file_index=parsed_data.get("largest_file", {}).get("index"),
                languages=parsed_data.get("languages", []),
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                audio=parsed_data.get("audio"),
                encoder=parsed_data.get("encoder"),
                source=parsed_data.get("source"),
                catalog=[
                    "prowlarr_streams",
                    "prowlarr_movies",
                    f"{parsed_data.get('source').lower()}_movies",
                ],
                updated_at=datetime.now(),
                seeders=parsed_data.get("seeders"),
                created_at=parsed_data.get("created_at"),
                meta_id=video_id,
            )
            await torrent_stream.save()

        streams.append(torrent_stream)
        if torrent_stream.filename is None:
            info_hashes.append(info_hash)

    background_tasks.add_task(update_torrent_movie_streams_metadata, info_hashes)

    return streams


async def store_and_parse_series_stream_data(
    video_id: str,
    title: str,
    season: int,
    episode: int,
    stream_data: list,
    background_tasks: BackgroundTasks,
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        parsed_data, is_torrent_downloaded = await prowlarr_data_parser(stream)
        info_hash = parsed_data.get("info_hash", "").lower()
        if not info_hash or not parsed_data.get("seeders"):
            logging.warning(
                f"Skipping {info_hash} due to missing info_hash or seeders: {parsed_data.get('seeders')}"
            )
            continue

        if not (parsed_data.get("title").lower() == title.lower()) and (
            str(stream.get("imdbId", "")) not in video_id
        ):
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}'"
            )
            continue

        if parsed_data.get("season"):
            if isinstance(parsed_data["season"], int):
                season_number = parsed_data["season"]
            else:
                # Skip This Stream due to multiple seasons in one torrent.
                # TODO: Handle this case later.
                #  Need to refactor DB and how streaming provider works.
                continue
        else:
            season_number = season

        if is_torrent_downloaded is True:
            episode_data = [
                Episode(
                    episode_number=file["episode"],
                    filename=file["filename"],
                    size=file["size"],
                    file_index=file["index"],
                )
                for file in parsed_data["file_data"]
                if file["episode"]
            ]
        elif parsed_data.get("episode"):
            if isinstance(parsed_data["episode"], int):
                episode_data = [Episode(episode_number=parsed_data["episode"])]
            else:
                episode_data = [
                    Episode(
                        episode_number=episode_number,
                    )
                    for episode_number in parsed_data["episode"]
                ]

        else:
            episode_data = [Episode(episode_number=episode)]

        torrent_stream = await TorrentStreams.get(info_hash)

        if torrent_stream:
            torrent_stream.seeders = parsed_data["seeders"]
            torrent_stream.updated_at = datetime.now()
            episode_item = torrent_stream.get_episode(season, episode)
            if episode_item is None:
                if torrent_stream.season:
                    torrent_stream.season.episodes.extend(episode_data)
                else:
                    torrent_stream.season = Season(
                        season_number=season_number,
                        episodes=episode_data,
                    )
                episode_item = torrent_stream.get_episode(season, episode)
            await torrent_stream.save()
        else:
            # Create new stream
            torrent_stream = TorrentStreams(
                id=info_hash,
                torrent_name=parsed_data.get("torrent_name"),
                announce_list=parsed_data.get("announce_list"),
                size=parsed_data.get("total_size"),
                filename=None,
                languages=parsed_data.get("languages", []),
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                audio=parsed_data.get("audio"),
                encoder=parsed_data.get("encoder"),
                source=parsed_data.get("source"),
                catalog=[
                    "prowlarr_streams",
                    "prowlarr_series",
                    f"{parsed_data.get('source').lower()}_series",
                ],
                updated_at=datetime.now(),
                seeders=parsed_data.get("seeders"),
                created_at=parsed_data.get("created_at"),
                meta_id=video_id,
                season=Season(
                    season_number=season_number,
                    episodes=episode_data,
                ),
            )
            await torrent_stream.save()
            episode_item = torrent_stream.get_episode(season, episode)

        streams.append(torrent_stream)

        if episode_item and episode_item.size is None:
            info_hashes.append(info_hash)

    background_tasks.add_task(update_torrent_series_streams_metadata, info_hashes)

    return streams
