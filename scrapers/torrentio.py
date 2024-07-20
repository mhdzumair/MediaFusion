import logging
import re
from datetime import datetime, timedelta
from os import path

import PTT
import httpx
from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from scrapers.helpers import (
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.const import UA_HEADER
from utils.parser import (
    convert_size_to_bytes,
    is_contain_18_plus_keywords,
    calculate_max_similarity_ratio,
)
from utils.validation_helper import is_video_file


async def get_streams_from_torrentio(
    redis: Redis,
    streams: list[TorrentStreams],
    video_id: str,
    catalog_type: str,
    title: str,
    aka_titles: list[str],
    season: int = None,
    episode: int = None,
):
    cache_key = f"{catalog_type}_{video_id}_{season}_{episode}_torrentio_streams"
    cached_data = await redis.get(cache_key)
    if cached_data:
        return streams

    if catalog_type == "movie":
        streams.extend(
            await scrap_movie_streams_from_torrentio(
                video_id, catalog_type, title, aka_titles
            )
        )
    elif catalog_type == "series":
        streams.extend(
            await scrap_series_streams_from_torrentio(
                video_id, catalog_type, title, aka_titles, season, episode
            )
        )

    # Cache the data for 24 hours
    await redis.set(
        cache_key,
        "True",
        ex=int(timedelta(days=settings.torrentio_search_interval_days).total_seconds()),
    )

    return streams


async def fetch_stream_data(url: str) -> dict:
    """Fetch stream data asynchronously."""
    async with httpx.AsyncClient(
        headers=UA_HEADER, proxy=settings.scraper_proxy_url
    ) as client:
        response = await client.get(url, timeout=10)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def scrap_movie_streams_from_torrentio(
    video_id: str, catalog_type: str, title: str, aka_titles: list[str]
) -> list[TorrentStreams]:
    """
    Get streams by IMDb ID from torrentio stremio addon.
    """
    url = f"{settings.torrentio_url}/stream/{catalog_type}/{video_id}.json"
    try:
        stream_data = await fetch_stream_data(url)
        return await store_and_parse_movie_stream_data(
            video_id, title, aka_titles, stream_data.get("streams", [])
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from torrentio: {e}")
        return []


async def scrap_series_streams_from_torrentio(
    video_id: str,
    catalog_type: str,
    title: str,
    aka_titles: list[str],
    season: int,
    episode: int,
) -> list[TorrentStreams]:
    """
    Get streams by IMDb ID from torrentio stremio addon.
    """
    url = f"{settings.torrentio_url}/stream/{catalog_type}/{video_id}:{season}:{episode}.json"
    try:
        stream_data = await fetch_stream_data(url)
        return await store_and_parse_series_stream_data(
            video_id, title, aka_titles, season, episode, stream_data.get("streams", [])
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from torrentio: {e}")
        return []


def parse_stream_title(stream: dict) -> dict:
    """Parse the stream title for metadata and other details."""
    torrent_name, file_name = stream["title"].splitlines()[:2]
    metadata = PTT.parse_title(torrent_name)

    return {
        "torrent_name": torrent_name,
        "title": metadata.get("title") or "Unknown",
        "size": convert_size_to_bytes(extract_size_string(stream["title"])),
        "seeders": extract_seeders(stream["title"]),
        "languages": extract_languages(metadata, stream["title"]),
        "metadata": metadata,
        "file_name": stream.get("behaviorHints", {}).get("filename")
        or path.basename(file_name)
        if is_video_file(file_name)
        else None,
    }


async def store_and_parse_movie_stream_data(
    video_id: str, title: str, aka_titles: list[str], stream_data: list
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

        parsed_data = parse_stream_title(stream)
        source = (
            stream["name"].split()[0].title() if stream.get("name") else "Torrentio"
        )

        if source != "Torrentio":
            # validate non-torrentio sources
            max_similarity_ratio = calculate_max_similarity_ratio(
                parsed_data.get("title"), title, aka_titles
            )
            if max_similarity_ratio < 85:
                logging.error(
                    f"Title mismatch: '{title}' != '{parsed_data.get('title')}' ratio: {max_similarity_ratio}"
                )
                continue

        # Create new stream
        torrent_stream = TorrentStreams(
            id=stream["infoHash"],
            torrent_name=parsed_data["torrent_name"],
            announce_list=[],
            size=parsed_data["size"],
            filename=parsed_data["file_name"],
            file_index=stream.get("fileIdx"),
            languages=parsed_data["languages"],
            resolution=parsed_data["metadata"].get("resolution"),
            codec=parsed_data["metadata"].get("codec"),
            quality=parsed_data["metadata"].get("quality"),
            audio=parsed_data["metadata"].get("audio"),
            source=source,
            catalog=["torrentio_streams"],
            updated_at=datetime.now(),
            seeders=parsed_data["seeders"],
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

        source = (
            stream["name"].split()[0].title() if stream.get("name") else "Torrentio"
        )
        parsed_data = parse_stream_title(stream)

        if source != "Torrentio":
            # validate non-torrentio sources
            max_similarity_ratio = calculate_max_similarity_ratio(
                parsed_data.get("title"), title, aka_titles
            )
            if max_similarity_ratio < 85:
                logging.error(
                    f"Title mismatch: '{title}' != '{parsed_data.get('title')}' ratio: {max_similarity_ratio}"
                )
                continue

        if seasons := parsed_data["metadata"].get("seasons"):
            if len(seasons) == 1:
                season_number = seasons[0]
            else:
                # Skip This Stream due to multiple seasons in one torrent.
                # TODO: Handle this case later.
                #  Need to refactor DB and how streaming provider works.
                continue
        else:
            season_number = season

        if parsed_data["metadata"].get("episodes"):
            episode_data = [
                Episode(
                    episode_number=episode_number,
                    file_index=stream.get("fileIdx")
                    if episode_number == episode
                    else None,
                )
                for episode_number in parsed_data["metadata"]["episodes"]
            ]

        else:
            episode_data = [
                Episode(episode_number=episode, file_index=stream.get("fileIdx"))
            ]

        # Create new stream
        torrent_stream = TorrentStreams(
            id=stream["infoHash"],
            torrent_name=parsed_data["torrent_name"],
            announce_list=[],
            size=parsed_data["size"],
            filename=None,
            languages=parsed_data["languages"],
            resolution=parsed_data["metadata"].get("resolution"),
            codec=parsed_data["metadata"].get("codec"),
            quality=parsed_data["metadata"].get("quality"),
            audio=parsed_data["metadata"].get("audio"),
            source=source,
            catalog=["torrentio_streams"],
            updated_at=datetime.now(),
            seeders=parsed_data["seeders"],
            meta_id=video_id,
            season=Season(
                season_number=season_number,
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


def extract_seeders(details: str) -> int:
    """Extract seeders from details string."""
    seeders_match = re.search(r"👤 (\d+)", details)
    return int(seeders_match.group(1)) if seeders_match else None


def extract_languages_from_title(title: str) -> list:
    """Extract languages and country flags from the title string."""
    languages = []
    if "Multi Audio" in title or "Multi Language" in title:
        languages.append("Multi Language")
    elif "Dual Audio" in title or "Dual Language" in title:
        languages.append("Dual Language")

    # Regex to match country flag emojis
    flag_emojis = re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", title)
    if flag_emojis:
        languages.extend(flag_emojis)

    return languages


def extract_languages(metadata: dict, title: str) -> list:
    """Extract languages from metadata or title."""
    languages = [language.title() for language in metadata.get("languages", [])]
    if languages:
        return languages
    return extract_languages_from_title(title)


def extract_size_string(details: str) -> str:
    """Extract the size string from the details."""
    size_match = re.search(r"💾 (\d+(?:\.\d+)?\s*(GB|MB))", details, re.IGNORECASE)
    return size_match.group(1) if size_match else ""
