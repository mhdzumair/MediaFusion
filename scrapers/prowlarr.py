import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import PTN
import dramatiq
import httpx
from redis.asyncio import Redis
from thefuzz import fuzz
from torf import Magnet, MagnetError

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from scrapers import therarbg, torrent_downloads
from scrapers.helpers import (
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.const import UA_HEADER
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import is_contain_18_plus_keywords
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
        new_streams = await fetch_stream_data_with_timeout(
            scrap_movies_streams_from_prowlarr, video_id, title, year
        )
        streams.extend(new_streams)
        if settings.prowlarr_live_title_search:
            new_streams = await fetch_stream_data_with_timeout(
                scrape_movie_title_streams_from_prowlarr, video_id, title, year
            )
            streams.extend(new_streams)
        # run background task for title search to get more streams
        background_movie_title_search.send(video_id, title, year)
    elif catalog_type == "series":
        new_streams = await fetch_stream_data_with_timeout(
            scrap_series_streams_from_prowlarr, video_id, title, season, episode
        )
        streams.extend(new_streams)
        if settings.prowlarr_live_title_search:
            new_streams = await fetch_stream_data_with_timeout(
                scrape_series_title_streams_from_prowlarr,
                video_id,
                title,
                season,
                episode,
            )
            streams.extend(new_streams)
        background_series_title_search.send(video_id, title, season, episode)
    # Cache the data for 24 hours
    await redis.set(
        cache_key,
        "True",
        ex=int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds()),
    )

    return streams


async def fetch_stream_data_with_timeout(func, *args):
    """
    Attempts to fetch stream data within a specified timeout.
    If the operation exceeds the timeout, it logs a warning and ignores the operation.
    """
    try:
        # Attempt the operation with a prowlarr immediate max process time.
        return await asyncio.wait_for(
            func(*args), timeout=settings.prowlarr_immediate_max_process_time
        )
    except asyncio.TimeoutError:
        # Log a warning if the operation takes too long but don't reschedule.
        logging.warning(
            f"Timeout exceeded for operation: {func.__name__} {args}. Skipping."
        )
    except Exception as e:
        # Log any other errors that occur.
        logging.error(f"Error during operation: {e}")
    return []


@asynccontextmanager
async def get_prowlarr_client(timeout: int = 10):
    headers = {
        "accept": "application/json",
        "X-Api-Key": settings.prowlarr_api_key,
    }
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        yield client


async def fetch_stream_data(
    url: str, params: dict, timeout: int = 120
) -> dict | list[dict]:
    """Fetch stream data asynchronously."""
    async with get_prowlarr_client(timeout) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def should_retry_prowlarr_scrap(retries_so_far, exception) -> bool:
    should_retry = retries_so_far < 10 and isinstance(exception, httpx.HTTPError)
    if not should_retry:
        logging.error(f"Failed to fetch data from Prowlarr: {exception}")
        return False
    return True


async def scrap_movies_streams_from_prowlarr(
    video_id: str, title: str, year: str
) -> list[TorrentStreams]:
    """
    Perform a movie stream search by IMDb ID from Prowlarr, processing immediately.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}",
        "categories": [2000],  # Movies
        "type": "movie",
    }

    try:
        imdb_search = await fetch_stream_data(url, params_imdb)
    except Exception as e:
        logging.warning(
            f"Failed to fetch API data from Prowlarr for {title} ({year}): {e}"
        )
        return []

    logging.info(f"Found {len(imdb_search)} streams for {title} ({year}) with IMDb ID")
    return await parse_and_store_movie_stream_data(
        video_id, title, year, imdb_search[: settings.prowlarr_immediate_max_process]
    )


async def scrape_movie_title_streams_from_prowlarr(
    video_id: str, title: str, year: str
) -> list[TorrentStreams]:
    """
    Perform a movie stream search by title and year from Prowlarr, processing immediately.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    params_title = {
        "query": title,
        "categories": [2000],  # Movies
        "type": "search",
    }

    try:
        title_search = await fetch_stream_data(url, params_title)
    except Exception as e:
        logging.warning(
            f"Failed to fetch API data from Prowlarr for {title} ({year}): {e}"
        )
        return []

    logging.info(f"Found {len(title_search)} streams for {title} ({year}) by title")
    return await parse_and_store_movie_stream_data(
        video_id, title, year, title_search[: settings.prowlarr_immediate_max_process]
    )


@dramatiq.actor(
    time_limit=60 * 60 * 1000,  # 60 minutes
    min_backoff=2 * 60 * 1000,  # 2 minutes
    max_backoff=60 * 60 * 1000,  # 60 minutes
    retry_when=should_retry_prowlarr_scrap,
    priority=100,
)
async def background_movie_title_search(video_id: str, title: str, year: str):
    await scrape_movie_title_streams_from_prowlarr(video_id, title, year)
    logging.info(f"Background title search completed for {title} ({year})")


async def scrap_series_streams_from_prowlarr(
    video_id: str, title: str, season: int = None, episode: int = None
) -> list[TorrentStreams]:
    """
    Perform a series stream search by IMDb ID, season, and episode from Prowlarr, processing immediately.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    params_imdb = {
        "query": f"{{ImdbId:{video_id}}}{{Season:{season}}}{{Episode:{episode}}}",
        "categories": [5000],  # TV
        "type": "tvsearch",
    }

    try:
        imdb_search = await fetch_stream_data(url, params_imdb)
    except Exception as e:
        logging.warning(
            f"Failed to fetch API data from Prowlarr for {title} ({season}) ({episode}): {e}"
        )
        return []

    logging.info(
        f"Found {len(imdb_search)} streams for {title} ({season}) ({episode}) with IMDb ID"
    )
    return await parse_and_store_series_stream_data(
        video_id, title, season, imdb_search[: settings.prowlarr_immediate_max_process]
    )


async def scrape_series_title_streams_from_prowlarr(
    video_id: str, title: str, season: int, episode: int
) -> list[TorrentStreams]:
    """
    Perform a series stream search by title, season, and episode from Prowlarr, processing immediately.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    params_title = {
        "query": title,
        "categories": [5000],  # TV
        "type": "search",
    }

    # Adjust parameters if needed for season and episode specificity
    if season is not None and episode is not None:
        params_title.update({"season": season, "episode": episode})

    try:
        title_search = await fetch_stream_data(url, params_title)
    except Exception as e:
        logging.warning(
            f"Failed to fetch API data from Prowlarr for {title} ({season}) ({episode}): {e}"
        )
        return []

    logging.info(
        f"Found {len(title_search)} streams for {title} ({season}) ({episode}) by title"
    )
    return await parse_and_store_series_stream_data(
        video_id, title, season, title_search[: settings.prowlarr_immediate_max_process]
    )


@dramatiq.actor(
    time_limit=60 * 60 * 1000,  # 60 minutes
    min_backoff=2 * 60 * 1000,  # 2 minutes
    max_backoff=60 * 60 * 1000,  # 60 minutes
    retry_when=should_retry_prowlarr_scrap,
    priority=100,
)
async def background_series_title_search(
    video_id: str, title: str, season: int, episode: int
):
    await scrape_series_title_streams_from_prowlarr(video_id, title, season, episode)
    logging.info(f"Background title search completed for {title} S{season}E{episode}")


async def get_torrent_data_from_prowlarr(
    download_url: str, indexer: str
) -> tuple[dict, bool]:
    """Get torrent data from prowlarr."""
    if not download_url:
        raise ValueError("No download URL provided")
    if download_url.startswith("magnet:"):
        magnet = Magnet.from_string(download_url)
        return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False

    async with httpx.AsyncClient() as client:
        response = await client.get(
            download_url, follow_redirects=False, timeout=20, headers=UA_HEADER
        )

    if response.status_code == 301:
        redirect_url = response.headers.get("Location")
        return await get_torrent_data_from_prowlarr(redirect_url, indexer)
    elif response.status_code == 200:
        return extract_torrent_metadata(response.content), True
    logging.error(
        f"Failed to fetch torrent data from {indexer}: {response.status_code} : {download_url}"
    )
    response.raise_for_status()
    raise ValueError(f"Failed to fetch torrent data from {download_url}")


async def prowlarr_data_parser(meta_data: dict, video_id: str) -> tuple[dict, bool]:
    """Parse prowlarr data."""
    if is_contain_18_plus_keywords(meta_data.get("title")):
        logging.warning(
            f"Skipping '{meta_data.get('title')}' due to adult content keyword in {video_id}"
        )
        return {}, False

    if meta_data.get("indexer") in [
        "Torlock",
        "YourBittorrent",
        "The Pirate Bay",
        "RuTracker.RU",
        "BitSearch",
    ]:
        # For these indexers, the guid is a direct torrent file download link
        download_url = meta_data.get("guid")
    else:
        if meta_data.get("indexer") == "TheRARBG":
            meta_data.update(await therarbg.get_torrent_info(meta_data.get("infoUrl")))
        elif meta_data.get("indexer") == "Torrent Downloads":
            meta_data.update(
                await torrent_downloads.get_torrent_info(meta_data.get("infoUrl"))
            )

        download_url = meta_data.get("downloadUrl") or meta_data.get("magnetUrl")

    try:
        torrent_data, is_torrent_downloaded = await get_torrent_data_from_prowlarr(
            download_url, meta_data.get("indexer")
        )
    except Exception as e:
        if meta_data.get("magnetUrl", "").startswith("magnet:"):
            try:
                magnet = Magnet.from_string(meta_data.get("magnetUrl"))
            except MagnetError:
                logging.error(
                    f"Error parsing {meta_data.get('indexer')} magnet link: {meta_data.get('magnetUrl')}",
                )
                return {}, False
            torrent_data = {
                "info_hash": magnet.infohash,
                "announce_list": magnet.tr,
            }
            is_torrent_downloaded = False
        elif meta_data.get("infoHash"):
            torrent_data = {
                "info_hash": meta_data.get("infoHash"),
                "announce_list": [],
            }
            is_torrent_downloaded = False
        else:
            if isinstance(e, (httpx.HTTPError, ValueError)):
                return {}, False
            logging.error(
                f"Error getting torrent data: {e} {e.__class__.__name__}", exc_info=True
            )
            return {}, False

    info_hash = torrent_data.get("info_hash")
    if not info_hash:
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
            f"{parsed_data.get('source', '').lower()}_movies",
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
        f"{parsed_data.get('source', '').lower()}_series",
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
    parsed_data, _ = await prowlarr_data_parser(stream_data, video_id)
    info_hash = parsed_data.get("info_hash", "").lower()
    torrent_stream, torrent_needed_update = None, False

    if not info_hash:
        logging.warning(
            f"Skipping {stream_data.get('title')} due to missing info_hash."
        )
        return torrent_stream, torrent_needed_update

    title_similarity_ratio = fuzz.ratio(
        parsed_data.get("title", "").lower(), title.lower()
    )

    if catalog_type == "movie":
        if (
            not (title_similarity_ratio > 80 and parsed_data.get("year") == year)
            and parsed_data.get("imdb_id") != video_id
        ):
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}' ratio: {title_similarity_ratio} or year mismatch: '{parsed_data.get('year')}' != '{year}'"
            )
            return torrent_stream, torrent_needed_update

        torrent_stream, torrent_needed_update = await handle_movie_stream_store(
            info_hash, parsed_data, video_id
        )
    elif catalog_type == "series":
        if title_similarity_ratio < 80 and parsed_data.get("imdb_id") != video_id:
            logging.warning(
                f"Skipping {info_hash} due to title mismatch: '{parsed_data.get('title')}' != '{title}' ratio: {title_similarity_ratio}"
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
        10,
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
    if info_hashes:
        update_torrent_movie_streams_metadata.send(info_hashes)

    return streams


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
    if info_hashes:
        update_torrent_series_streams_metadata.send(info_hashes)

    return streams
