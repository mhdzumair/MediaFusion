import json
import logging
from datetime import datetime

import dramatiq
from bs4 import BeautifulSoup
import httpx

from db.config import settings
from db.models import TorrentStreams, Episode, Season
from utils.torrent import info_hashes_to_torrent_metadata

# set httpx logging level
logging.getLogger("httpx").setLevel(logging.WARNING)


@dramatiq.actor(time_limit=30 * 60 * 1000, priority=10)
async def update_torrent_movie_streams_metadata(
    info_hashes: list[str], tracker: list[str] = None
):
    """Update torrent streams metadata."""
    if not info_hashes:
        return

    streams_metadata = await info_hashes_to_torrent_metadata(info_hashes, tracker or [])

    for stream_metadata in streams_metadata:
        if not stream_metadata:
            continue

        torrent_stream = await TorrentStreams.get(stream_metadata["info_hash"])
        if torrent_stream:
            torrent_stream.torrent_name = stream_metadata["torrent_name"]
            torrent_stream.size = stream_metadata["total_size"]

            torrent_stream.filename = stream_metadata["largest_file"]["filename"]
            torrent_stream.file_index = stream_metadata["largest_file"]["index"]
            torrent_stream.updated_at = datetime.now()
            await torrent_stream.save()
            logging.info(f"Updated {torrent_stream.id} metadata")


@dramatiq.actor(time_limit=30 * 60 * 1000, priority=10)
async def update_torrent_series_streams_metadata(
    info_hashes: list[str], tracker: list[str] = None
):
    """Update torrent streams metadata."""
    if not info_hashes:
        return

    streams_metadata = await info_hashes_to_torrent_metadata(info_hashes, tracker or [])

    for stream_metadata in streams_metadata:
        if not (stream_metadata and "seasons" in stream_metadata):
            continue

        torrent_stream = await TorrentStreams.get(stream_metadata["info_hash"])
        if torrent_stream:
            episodes = [
                Episode(
                    episode_number=file["episodes"][0],
                    filename=file["filename"],
                    size=file["size"],
                    file_index=file["index"],
                )
                for file in stream_metadata["file_data"]
                if file["episodes"]
            ]
            if not episodes:
                continue

            torrent_stream.season = Season(
                season_number=stream_metadata["seasons"][0],
                episodes=episodes,
            )

            torrent_stream.torrent_name = stream_metadata["torrent_name"]
            torrent_stream.size = stream_metadata["total_size"]

            torrent_stream.updated_at = datetime.now()

            await torrent_stream.save()
            logging.info(f"Updated {torrent_stream.id} metadata")


def get_country_name(country_code):
    with open("resources/json/countries.json") as file:
        countries = json.load(file)
    return countries.get(country_code.upper(), "India")


async def get_page_bs4(url: str):
    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as session:
            response = await session.get(url, timeout=10)
            if response.status_code != 200:
                return None
            return BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        logging.error(f"Error fetching page: {url}, error: {e}")
        return None
