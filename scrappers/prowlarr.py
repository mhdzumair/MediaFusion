import asyncio
import logging
from datetime import datetime, timedelta

import PTN
import dramatiq
import httpx
from redis.asyncio import Redis
from torf import Magnet

from db import database
from db.config import settings
from db.models import TorrentStreams, Season, Episode
from scrappers.helpers import (
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.torrent import extract_torrent_metadata


async def get_streams_from_prowlarr(
    redis: Redis,
    streams: list[TorrentStreams],
    video_id: str,
    catalog_type: str,
    title: str,
    year: str,
    season: int = None,
    episode: int = None,
):
    cache_key = f"{catalog_type}_{video_id}_{year}_{season}_{episode}_prowlarr_streams"
    cached_data = await redis.get(cache_key)
    if cached_data:
        return streams

    if catalog_type == "movie":
        streams.extend(await scrap_movies_streams_from_prowlarr(video_id, title, year))
    elif catalog_type == "series":
        streams.extend(
            await scrap_series_streams_from_prowlarr(video_id, title, season, episode)
        )
    # Cache the data for 24 hours
    await redis.set(
        cache_key,
        "True",
        ex=int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds()),
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
) -> list[TorrentStreams]:
    """
    Get movie streams by IMDb ID and title from prowlarr, processing only the first 10 immediately
    and handling the rest in the background.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    stream_data = []

    # Params for IMDb ID search
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}",
        "categories": [2000],  # Movies
        "type": "movie",
    }

    # Params for title search
    params_title = {
        "query": title,
        "categories": [2000],  # Movies
        "type": "search",
    }

    # Fetch data for both searches simultaneously
    imdb_search, title_search = await asyncio.gather(
        fetch_stream_data(url, params_imdb),
        fetch_stream_data(url, params_title),
        return_exceptions=True,
    )
    if not isinstance(imdb_search, Exception):
        stream_data.extend(imdb_search)
    if not isinstance(title_search, Exception):
        stream_data.extend(title_search)

    if not stream_data:
        logging.warning(f"Failed to fetch API data from prowlarr for {title} ({year})")
        return []  # Return an empty list in case of HTTP errors or timeouts

    logging.info(f"Found {len(stream_data)} streams for {title} ({year})")
    # Slice the results to process only the first 10 immediately
    immediate_processing_data = stream_data[: settings.prowlarr_immediate_max_process]
    remaining_data = stream_data[settings.prowlarr_immediate_max_process :]

    # Process the first 10 torrents immediately
    immediate_results = await parse_and_store_movie_stream_data(
        video_id, title, year, immediate_processing_data
    )

    # Schedule the processing of any remaining torrents as a batches of background task if any exist
    if remaining_data:
        for i in range(0, len(remaining_data), 5):
            parse_and_store_movie_stream_data_actor.send(
                video_id, title, year, remaining_data[i : i + 5]
            )

    return immediate_results


async def scrap_series_streams_from_prowlarr(
    video_id: str,
    title: str,
    season: int = None,
    episode: int = None,
) -> list[TorrentStreams]:
    """
    Get movie streams by IMDb ID and title from prowlarr, processing only the first 10 immediately
    and handling the rest in the background.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    stream_data = []

    # Params for IMDb ID, season, and episode search
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}{{Season:{season}}}{{Episode:{episode}}}",
        "categories": [5000],  # TV
        "type": "tvsearch",
    }

    # Params for title search
    params_title = {
        "query": title,
        "categories": [5000],  # TV
        "type": "search",
    }

    # Fetch data for both searches simultaneously
    imdb_search, title_search = await asyncio.gather(
        fetch_stream_data(url, params_imdb),
        fetch_stream_data(url, params_title),
        return_exceptions=True,
    )
    if not isinstance(imdb_search, Exception):
        stream_data.extend(imdb_search)
    if not isinstance(title_search, Exception):
        stream_data.extend(title_search)

    if not stream_data:
        logging.warning(
            f"Failed to fetch API data from prowlarr for {title} ({season}) ({episode})"
        )
        return []  # Return an empty list in case of HTTP errors or timeouts

    logging.info(f"Found {len(stream_data)} streams for {title} ({season}) ({episode})")
    # Slice the results to process only the first 10 immediately
    immediate_processing_data = stream_data[: settings.prowlarr_immediate_max_process]
    remaining_data = stream_data[settings.prowlarr_immediate_max_process :]

    # Process the first 10 torrents immediately
    immediate_results = await parse_and_store_series_stream_data(
        video_id, title, season, immediate_processing_data
    )

    # Schedule the processing of any remaining torrents as a batches of background task if any exist
    if remaining_data:
        for i in range(0, len(remaining_data), 5):
            parse_and_store_series_stream_data_actor.send(
                video_id,
                title,
                season,
                remaining_data[i : i + 5],
            )

    return immediate_results


async def get_torrent_data_from_prowlarr(download_url: str) -> tuple[dict, bool]:
    """Get torrent data from prowlarr."""
    if not download_url:
        return {}, False
    if download_url.startswith("magnet:"):
        magnet = Magnet.from_string(download_url)
        return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False

    async with httpx.AsyncClient() as client:
        response = await client.get(download_url, follow_redirects=False, timeout=20)

    if response.status_code == 301:
        magnet_url = response.headers.get("Location")
        magnet = Magnet.from_string(magnet_url)
        return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False
    elif response.status_code == 200:
        return extract_torrent_metadata(response.content), True
    response.raise_for_status()
    return {}, False


async def prowlarr_data_parser(meta_data: dict) -> tuple[dict, bool]:
    """Parse prowlarr data."""
    try:
        torrent_data, is_torrent_downloaded = await get_torrent_data_from_prowlarr(
            meta_data.get("downloadUrl") or meta_data.get("magnetUrl")
        )
    except Exception as e:
        if meta_data.get("infoHash"):
            torrent_data = {
                "info_hash": meta_data.get("infoHash"),
                "announce_list": [],
            }
            is_torrent_downloaded = False
        elif meta_data.get("magnetUrl"):
            magnet = Magnet.from_string(meta_data.get("magnetUrl"))
            torrent_data = {
                "info_hash": magnet.infohash,
                "announce_list": magnet.tr,
            }
            is_torrent_downloaded = False
        else:
            if isinstance(
                e,
                httpx.HTTPError,
            ):
                raise e
            logging.error(
                f"Error getting torrent data: {e} {e.__class__.__name__}", exc_info=True
            )
            return {}, False

    torrent_data.update(
        {
            "seeders": meta_data.get("seeders"),
            "created_at": datetime.strptime(
                meta_data.get("publishDate"), "%Y-%m-%dT%H:%M:%SZ"
            ),
            "source": meta_data.get("indexer"),
            "poster_url": meta_data.get("posterUrl"),
            "imdb_id": f"tt{meta_data.get('imdbId')}",
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


async def handle_movie_stream_store(info_hash, parsed_data, video_id):
    """
    Handles the store logic for a single torrent stream.
    Checks if the stream exists and updates or creates it accordingly.
    """

    try:
        # Check if the torrent stream already exists
        torrent_stream = await TorrentStreams.get(info_hash)
        prowlarr_catalogs = [
            "prowlarr_streams",
            "prowlarr_movies",
            f"{parsed_data.get('source').lower()}_movies",
        ]

        if torrent_stream:
            # Update existing stream
            torrent_stream.source = parsed_data.get("source")
            torrent_stream.seeders = parsed_data["seeders"]
            torrent_stream.updated_at = datetime.now()
            torrent_stream.catalog.extend(
                [c for c in prowlarr_catalogs if c not in torrent_stream.catalog]
            )
            logging.info(f"Updated movies stream {info_hash} for {video_id}")
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
                catalog=prowlarr_catalogs,
                updated_at=datetime.now(),
                seeders=parsed_data.get("seeders"),
                created_at=parsed_data.get("created_at"),
                meta_id=video_id,
            )
            logging.info(f"Created movies stream {info_hash} for {video_id}")

        await torrent_stream.save()

        # Determine if filename is None, indicating metadata update is needed
        torrent_needed_update = torrent_stream.filename is None

        return torrent_stream, torrent_needed_update
    except Exception as e:
        logging.error(
            f"Error handling movie stream store for {info_hash}: {e}", exc_info=True
        )
        return None, False


async def handle_series_stream_store(info_hash, parsed_data, video_id, season):
    """
    Handles the storage logic for a single series torrent stream, including updating
    or creating records for all episodes contained within the torrent. Skips torrents
    if no valid episode data is found.
    """
    # Check for unsupported torrents spanning multiple seasons
    if isinstance(parsed_data.get("season"), list):
        return None, False  # Skip torrents spanning multiple seasons

    # Prepare episode data based on detailed file data or basic episode numbers
    episode_data = []
    if "file_data" in parsed_data and parsed_data["file_data"]:
        episode_data = [
            Episode(
                episode_number=file["episode"],
                filename=file.get("filename"),
                size=file.get("size"),
                file_index=file.get("index"),
            )
            for file in parsed_data["file_data"]
            if file.get("episode")
        ]
    elif parsed_data.get("episode"):
        if isinstance(parsed_data["episode"], list):
            episode_data = [Episode(episode_number=ep) for ep in parsed_data["episode"]]
        else:
            episode_data = [Episode(episode_number=parsed_data["episode"])]

    # Skip the torrent if no episode data is available
    if not episode_data:
        return None, False  # Indicate that no operation was performed

    season_number = parsed_data.get("season", season)

    # Fetch or create the torrent stream object
    torrent_stream = await TorrentStreams.get(info_hash)
    prowlarr_catalog = [
        "prowlarr_streams",
        "prowlarr_series",
        f"{parsed_data.get('source').lower()}_series",
    ]

    if torrent_stream:
        # Update existing stream
        torrent_stream.source = parsed_data.get("source")
        torrent_stream.seeders = parsed_data["seeders"]
        torrent_stream.updated_at = datetime.now()
        torrent_stream.catalog.extend(
            [c for c in prowlarr_catalog if c not in torrent_stream.catalog]
        )
        available_episodes = [
            ep.episode_number for ep in torrent_stream.season.episodes
        ]
        torrent_stream.season.episodes.extend(
            [ep for ep in episode_data if ep.episode_number not in available_episodes]
        )
        logging.info(f"Updated series stream {info_hash} for {video_id}")
    else:
        # Create new stream, initially without episodes
        torrent_stream = TorrentStreams(
            id=info_hash,
            torrent_name=parsed_data.get("torrent_name"),
            announce_list=parsed_data.get("announce_list"),
            size=parsed_data.get("total_size"),
            filename=None,  # To be updated if detailed file data is available
            languages=parsed_data.get("languages", []),
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            audio=parsed_data.get("audio"),
            encoder=parsed_data.get("encoder"),
            source=parsed_data.get("source"),
            catalog=prowlarr_catalog,
            updated_at=datetime.now(),
            seeders=parsed_data.get("seeders"),
            created_at=parsed_data.get("created_at"),
            meta_id=video_id,
            season=Season(
                season_number=season_number, episodes=episode_data
            ),  # Add episodes
        )
        logging.info(f"Created series stream {info_hash} for {video_id}")

    await torrent_stream.save()

    # Indicate whether a metadata update is needed (e.g., missing size information)
    torrent_needed_update = any(ep.size is None for ep in episode_data)

    return torrent_stream, torrent_needed_update


async def parse_and_store_stream(
    stream_data: dict,
    video_id: str,
    title: str,
    year: str,
    catalog_type: str,
    season: int = None,
) -> tuple[TorrentStreams | None, bool]:
    parsed_data, _ = await prowlarr_data_parser(stream_data)
    info_hash = parsed_data.get("info_hash", "").lower()
    torrent_stream, torrent_needed_update = None, False

    if not info_hash or parsed_data.get("seeders", 0) == 0:
        logging.warning(
            f"Skipping {info_hash} due to missing info_hash or seeders: {parsed_data.get('seeders')}"
        )
        return torrent_stream, torrent_needed_update

    if catalog_type == "movie":
        if (
            not (
                parsed_data.get("title").lower() == title.lower()
                and parsed_data.get("year") == year
            )
            and parsed_data.get("imdb_id") != video_id
        ):
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}' or year mismatch: '{parsed_data.get('year')}' != '{year}'"
            )
            return torrent_stream, torrent_needed_update

        torrent_stream, torrent_needed_update = await handle_movie_stream_store(
            info_hash, parsed_data, video_id
        )
    elif catalog_type == "series":
        if (
            parsed_data.get("title").lower() != title.lower()
            and parsed_data.get("imdb_id") != video_id
        ):
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}'"
            )
            return torrent_stream, torrent_needed_update

        torrent_stream, torrent_needed_update = await handle_series_stream_store(
            info_hash, parsed_data, video_id, season
        )

    return torrent_stream, torrent_needed_update


async def parse_and_store_movie_stream_data(
    video_id: str,
    title: str,
    year: str,
    stream_data: list,
) -> list[TorrentStreams]:
    if not stream_data:
        return []

    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )
    parsed_results = await batch_process_with_circuit_breaker(
        parse_and_store_stream,
        stream_data,
        5,
        3,
        circuit_breaker,
        video_id,
        title,
        year,
        "movie",
    )

    streams = [stream for stream, _ in parsed_results if stream is not None]
    info_hashes = [
        stream.id for stream, needed_update in parsed_results if needed_update
    ]

    # Continue to use background tasks for demagnetization
    update_torrent_movie_streams_metadata.send(info_hashes)

    return streams


@dramatiq.actor(time_limit=15 * 60 * 1000)
async def parse_and_store_movie_stream_data_actor(
    video_id: str,
    title: str,
    year: str,
    stream_data: list,
) -> list[TorrentStreams]:
    await database.init()
    return await parse_and_store_movie_stream_data(video_id, title, year, stream_data)


async def parse_and_store_series_stream_data(
    video_id: str,
    title: str,
    season: int,
    stream_data: list,
) -> list[TorrentStreams]:
    if not stream_data:
        return []

    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )
    parsed_results = await batch_process_with_circuit_breaker(
        parse_and_store_stream,
        stream_data,
        5,
        3,
        circuit_breaker,
        video_id,
        title,
        "series",
        season,
    )

    streams = [stream for stream, _ in parsed_results if stream is not None]
    info_hashes = [
        stream.id for stream, needed_update in parsed_results if needed_update
    ]

    # Use background tasks for metadata updates
    update_torrent_series_streams_metadata.send(info_hashes)

    return streams


@dramatiq.actor(time_limit=15 * 60 * 1000)
async def parse_and_store_series_stream_data_actor(
    video_id: str,
    title: str,
    season: int,
    stream_data: list,
) -> list[TorrentStreams]:
    await database.init()
    return await parse_and_store_series_stream_data(
        video_id, title, season, stream_data
    )
