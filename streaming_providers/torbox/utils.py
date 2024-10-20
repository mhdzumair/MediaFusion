import asyncio
import logging
from typing import Any, Dict, List, Optional, Iterator

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.torbox.client import Torbox


async def get_video_url_from_torbox(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    episode: Optional[int] = None,
    **kwargs: Any,
) -> str:
    async with Torbox(token=user_data.streaming_provider.token) as torbox_client:
        # Check if the torrent already exists
        torrent_info = await torbox_client.get_available_torrent(info_hash)
        if torrent_info:
            if (
                torrent_info["download_finished"] is True
                and torrent_info["download_present"] is True
            ):
                file_id = select_file_id_from_torrent(torrent_info, filename, episode)
                response = await torbox_client.create_download_link(
                    torrent_info.get("id", ""),
                    file_id,
                )
                return response["data"]
        else:
            # If torrent doesn't exist, add it
            response = await torbox_client.add_magnet_link(magnet_link)
            # Response detail has "Found Cached Torrent" If it's a cached torrent,
            # create a download link from it directly in the same call.
            if "Found Cached" in response.get("detail", ""):
                torrent_info = await torbox_client.get_available_torrent(info_hash)
                if torrent_info:
                    file_id = select_file_id_from_torrent(
                        torrent_info, filename, episode
                    )
                    response = await torbox_client.create_download_link(
                        torrent_info.get("id", ""),
                        file_id,
                    )
                    return response["data"]

    raise ProviderException(
        "Torrent did not reach downloaded status.",
        "torrent_not_downloaded.mp4",
    )


def divide_chunks(lst: List[Any], n: int) -> Iterator[List[Any]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def update_chunk_cache_status(
    torbox_client: Torbox, streams_chunk: List[TorrentStreams]
) -> None:
    """Update cache status for a chunk of streams."""
    try:
        instant_availability_data = (
            await torbox_client.get_torrent_instant_availability(
                [stream.id for stream in streams_chunk]
            )
            or []
        )
        for stream in streams_chunk:
            stream.cached = bool(stream.id in instant_availability_data)
    except ProviderException as e:
        logging.error(f"Failed to get cached status from torbox for a chunk: {e}")


async def update_torbox_cache_status(
    streams: List[TorrentStreams], user_data: UserData, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Torbox's instant availability."""
    async with Torbox(token=user_data.streaming_provider.token) as torbox_client:
        # Torbox allows only 100 torrents to be passed for cache status, send 80 at a time.
        chunks = list(divide_chunks(streams, 80))
        update_tasks = [
            update_chunk_cache_status(torbox_client, chunk) for chunk in chunks
        ]
        await asyncio.gather(*update_tasks)


async def fetch_downloaded_info_hashes_from_torbox(
    user_data: UserData, **kwargs: Any
) -> List[str]:
    """Fetches the info_hashes of all torrents downloaded in the Torbox account."""
    try:
        async with Torbox(token=user_data.streaming_provider.token) as torbox_client:
            available_torrents = await torbox_client.get_user_torrent_list()
            if not available_torrents.get("data"):
                return []
            return [torrent["hash"] for torrent in available_torrents["data"]]

    except ProviderException:
        return []


def select_file_id_from_torrent(
    torrent_info: Dict[str, Any], filename: str, episode: Optional[int]
) -> int:
    """Select the file id from the torrent info."""
    file_index = select_file_index_from_torrent(
        torrent_info,
        filename,
        None,
        episode,
        name_key="short_name",
    )
    return torrent_info["files"][file_index]["id"]


async def delete_all_torrents_from_torbox(user_data: UserData, **kwargs: Any) -> None:
    """Deletes all torrents from the Torbox account."""
    async with Torbox(token=user_data.streaming_provider.token) as torbox_client:
        torrents = (await torbox_client.get_user_torrent_list()).get("data")
        if not torrents:
            return
        for torrent in torrents:
            await torbox_client.delete_torrent(torrent.get("id", ""))
