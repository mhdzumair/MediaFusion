import os
import re
from datetime import datetime

import PTN
import httpx
from fastapi import BackgroundTasks

from db import crud
from db.config import settings
from db.models import TorrentStreams
from utils.parser import convert_size_to_bytes, get_imdb_title
from utils.torrent import info_hashes_to_torrent_metadata


async def fetch_stream_data(url: str, params: dict, headers: dict) -> dict:
    """Fetch stream data asynchronously."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=100, params=params, headers=headers)
        response.raise_for_status()  # Will raise an exception for 4xx/5xx responses
        return response.json()


async def scrap_streams_from_prowlarr(
        video_id: str, catalog_type: str, background_tasks: BackgroundTasks = None, season: int = None, episode: int = None
) -> list[TorrentStreams]:
    """
    Get streams by IMDb ID from prowlarr.
    """
    url = f"{settings.prowlarr_url}/api/v1/search"
    stream_data = []

    prefixes = ["{" + f"ImdbId:{video_id}" + "} "]
    if catalog_type == "tv":
        prefixes = ["{" + f"Season:{season}" + "} ",
                    "{" + f"Season:{season}" + "} {" + f"Episode:{episode}" + "} "]
    for prefix in prefixes:
        headers = {
            'accept': 'application/json',
            'X-Api-Key': settings.prowlarr_api_key,
        }
        params = {
            'query': f"{prefix}{get_imdb_title(video_id.removeprefix('tt'))}",
            'categories': [
                '2000', # Movies
                '5000', # TV
                '8000', # Other
            ],
            'type': "tvsearch" if catalog_type == "tv" else "moviesearch",
            'limit': '20',
            'offset': '0',
        }
        try:
            stream_data.extend(await fetch_stream_data(url, params, headers))
        except (httpx.HTTPError, httpx.TimeoutException):
            return []  # Return empty list in case of HTTP errors or timeouts

    return await store_and_parse_stream_data(
        video_id, stream_data, background_tasks
    )


async def store_and_parse_stream_data(
        video_id: str, stream_data: list, background_tasks: BackgroundTasks
) -> list[TorrentStreams]:
    streams = []
    info_hashes = []
    for stream in stream_data:
        infohash = stream.get("infoHash", "").lower()
        if infohash:
            torrent_stream = None # TODO ADD ME LATER await TorrentStreams.get(infohash)
            if torrent_stream:
                # Update existing stream
                torrent_stream.seeders = stream["seeders"]
                torrent_stream.updated_at = datetime.now()
                # TODO ADD ME LATER
                await torrent_stream.save()
            else:
                title = stream["title"]
                metadata = PTN.parse(title)
                languages = []
                language = metadata.get("language", "")
                languages.extend(language if type(language) == list else [language])
                # Create new stream
                torrent_stream = TorrentStreams(
                    id=infohash,
                    torrent_name=title,
                    announce_list=[],
                    size=stream["size"],
                    filename=None,
                    file_index=stream.get("indexerId"),
                    languages=languages,
                    resolution=metadata.get("resolution"),
                    codec=metadata.get("codec"),
                    quality=metadata.get("quality"),
                    audio=metadata.get("audio"),
                    encoder=metadata.get("encoder"),
                    source="Prowlarr",
                    catalog=["prowlarr_streams"],
                    updated_at=datetime.now(),
                    seeders=stream["seeders"],
                    meta_id=video_id,
                )
                # TODO ADD ME LATER
                await torrent_stream.save()

            streams.append(torrent_stream)
            if torrent_stream.filename is None:
                info_hashes.append(infohash)

    # TODO ADD ME LATER
    background_tasks.add_task(update_torrent_streams_metadata, info_hashes)

    return streams


async def update_torrent_streams_metadata(info_hashes: list[str]):
    """Update torrent streams metadata."""
    streams_metadata = await info_hashes_to_torrent_metadata(info_hashes, [])

    for stream_metadata in streams_metadata:
        if not stream_metadata:
            continue

        torrent_stream = await TorrentStreams.get(stream_metadata["info_hash"])
        if torrent_stream:
            torrent_stream.torrent_name = stream_metadata.get("torrent_name")
            torrent_stream.size = stream_metadata.get("total_size")

            largest_file = max(stream_metadata.get("file_data"), key=lambda x: x["size"])
            torrent_stream.filename = largest_file["filename"]
            torrent_stream.file_index = largest_file["index"]
            torrent_stream.updated_at = datetime.now()
            torrent_stream.resolution = stream_metadata.get("resolution")
            torrent_stream.quality = stream_metadata.get("quality")
            torrent_stream.codec = stream_metadata.get("codec")
            await torrent_stream.save()
