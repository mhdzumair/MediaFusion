import logging
import re
from datetime import datetime, timedelta
from os import path

import PTN
import httpx

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from db.schemas import UserData
from scrappers.helpers import (
    UA_HEADER,
    update_torrent_series_streams_metadata,
    update_torrent_movie_streams_metadata,
)
from utils.parser import convert_size_to_bytes
from utils.validation_helper import is_video_file


async def get_streams_from_torrentio(
    user_data: UserData,
    streams: list[TorrentStreams],
    video_id: str,
    catalog_type: str,
    season: int = None,
    episode: int = None,
):
    last_stream = next(
        (stream for stream in streams if stream.source == "Torrentio"), None
    )
    if video_id.startswith("tt") and "torrentio_streams" in user_data.selected_catalogs:
        if last_stream is None or last_stream.updated_at < datetime.now() - timedelta(
            days=3
        ):
            if catalog_type == "movie":
                streams.extend(
                    await scrap_movie_streams_from_torrentio(video_id, catalog_type)
                )
            elif catalog_type == "series":
                streams.extend(
                    await scrap_series_streams_from_torrentio(
                        video_id, catalog_type, season, episode
                    )
                )
    return streams


async def fetch_stream_data(url: str) -> dict:
    """Fetch stream data asynchronously."""
    async with httpx.AsyncClient(
        headers=UA_HEADER, proxy=settings.scrapper_proxy_url
    ) as client:
        response = await client.get(url, timeout=10)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def scrap_movie_streams_from_torrentio(
    video_id: str, catalog_type: str
) -> list[TorrentStreams]:
    """
    Get streams by IMDb ID from torrentio stremio addon.
    """
    url = f"{settings.torrentio_url}/stream/{catalog_type}/{video_id}.json"
    try:
        stream_data = await fetch_stream_data(url)
        return await store_and_parse_movie_stream_data(
            video_id, stream_data.get("streams", [])
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from torrentio: {e}")
        return []


async def scrap_series_streams_from_torrentio(
    video_id: str,
    catalog_type: str,
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
            video_id, season, episode, stream_data.get("streams", [])
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return []  # Return an empty list in case of HTTP errors or timeouts
    except Exception as e:
        logging.error(f"Error while fetching stream data from torrentio: {e}")
        return []


def parse_stream_title(stream: dict) -> dict:
    """Parse the stream title for metadata and other details."""
    torrent_name, file_name = stream["title"].splitlines()[:2]
    metadata = PTN.parse(torrent_name)

    return {
        "torrent_name": torrent_name,
        "size": convert_size_to_bytes(extract_size_string(stream["title"])),
        "seeders": extract_seeders(stream["title"]),
        "languages": extract_languages(metadata, stream["title"]),
        "metadata": metadata,
        "file_name": path.basename(file_name) if is_video_file(file_name) else None,
    }


async def store_and_parse_movie_stream_data(
    video_id: str, stream_data: list
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        parsed_data = parse_stream_title(stream)
        if not parsed_data["seeders"]:
            continue

        torrent_stream = await TorrentStreams.get(stream["infoHash"])

        if torrent_stream:
            # Update existing stream
            torrent_stream.seeders = parsed_data["seeders"]
            torrent_stream.updated_at = datetime.now()
            await torrent_stream.save()
        else:
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
                encoder=parsed_data["metadata"].get("encoder"),
                source="Torrentio",
                catalog=["torrentio_streams"],
                updated_at=datetime.now(),
                seeders=parsed_data["seeders"],
                meta_id=video_id,
            )
            await torrent_stream.save()

        streams.append(torrent_stream)
        if torrent_stream.filename is None:
            info_hashes.append(stream["infoHash"])

    update_torrent_movie_streams_metadata.send(info_hashes)

    return streams


async def store_and_parse_series_stream_data(
    video_id: str,
    season: int,
    episode: int,
    stream_data: list,
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        parsed_data = parse_stream_title(stream)
        if not parsed_data["seeders"]:
            continue

        if parsed_data["metadata"].get("season"):
            if isinstance(parsed_data["metadata"]["season"], int):
                season_number = parsed_data["metadata"]["season"]
            else:
                # Skip This Stream due to multiple seasons in one torrent.
                # TODO: Handle this case later.
                #  Need to refactor DB and how streaming provider works.
                continue
        else:
            season_number = season

        if parsed_data["metadata"].get("episode"):
            if isinstance(parsed_data["metadata"]["episode"], int):
                episode_data = [
                    Episode(episode_number=parsed_data["metadata"]["episode"])
                ]
            else:
                episode_data = [
                    Episode(
                        episode_number=episode_number,
                        file_index=stream.get("fileIdx")
                        if episode_number == episode
                        else None,
                    )
                    for episode_number in parsed_data["metadata"]["episode"]
                ]

        else:
            episode_data = [
                Episode(episode_number=episode, file_index=stream.get("fileIdx"))
            ]

        torrent_stream = await TorrentStreams.get(stream["infoHash"])

        if torrent_stream:
            # Update existing stream
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
                encoder=parsed_data["metadata"].get("encoder"),
                source="Torrentio",
                catalog=["torrentio_streams"],
                updated_at=datetime.now(),
                seeders=parsed_data["seeders"],
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
            info_hashes.append(stream["infoHash"])

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
    language = metadata.get("language")
    if language:
        if isinstance(language, str):
            return [language]
        elif isinstance(language, list):
            return language
    return extract_languages_from_title(title)


def extract_size_string(details: str) -> str:
    """Extract the size string from the details."""
    size_match = re.search(r"💾 (\d+(?:\.\d+)?\s*(GB|MB))", details, re.IGNORECASE)
    return size_match.group(1) if size_match else ""
