import asyncio
import logging
from typing import Any, Dict, List, Optional, Iterator

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.easydebrid.client import EasyDebrid


async def get_video_url_from_easydebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    episode: Optional[int] = None,
    **kwargs: Any,
) -> str:
    async with EasyDebrid(token=user_data.streaming_provider.token) as easydebrid_client:
        response = await easydebrid_client.create_download_link(
            magnet_link,
            filename,
        )
        torrent_info = {'files': []}
        for index, r in enumerate(response):
            torrent_info.get('files').append({
                'id': index,
                'name': r['filename'],
                'size': r['size'],
                'url': r['url'],
            })

        file_id = await select_file_from_torrent(
            torrent_info, filename, episode
        )
        return torrent_info['files'][file_id]['url']


def divide_chunks(lst: List[Any], n: int) -> Iterator[List[Any]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def update_chunk_cache_status(
    easydebrid_client: EasyDebrid, streams_chunk: List[TorrentStreams]
) -> None:
    """Update cache status for a chunk of streams."""
    try:
        instant_availability_data = (
            await easydebrid_client.get_torrent_instant_availability(
                [f"magnet:?xt=urn:btih:{stream.id}" for stream in streams_chunk]
            )
            or []
        )
        for index, stream in enumerate(streams_chunk):
            stream.cached = instant_availability_data[index]
    except ProviderException as e:
        logging.error(f"Failed to get cached status from easydebrid for a chunk: {e}")


async def update_easydebrid_cache_status(
    streams: List[TorrentStreams], user_data: UserData, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Easydebrid's instant availability."""
    async with EasyDebrid(token=user_data.streaming_provider.token) as easydebrid_client:
        chunks = list(divide_chunks(streams, 50))
        update_tasks = [
            update_chunk_cache_status(easydebrid_client, chunk) for chunk in chunks
        ]
        await asyncio.gather(*update_tasks)


async def fetch_downloaded_info_hashes_from_easydebrid(
    user_data: UserData, **kwargs: Any
) -> List[str]:
    """Fetches the info_hashes of all torrents downloaded in the EasyDebrid account."""
    return []


async def select_file_from_torrent(
    torrent_info: Dict[str, Any], filename: str, episode: Optional[int]
) -> int:
    """Select the file id from the torrent info."""
    file_index = await select_file_index_from_torrent(
        torrent_info,
        filename,
        episode,
    )
    return torrent_info["files"][file_index]["id"]


async def delete_all_torrents_from_easydebrid(user_data: UserData, **kwargs: Any) -> None:
    """Deletes all torrents from the EasyDebrid account."""
    pass


async def validate_easydebrid_credentials(
    user_data: UserData, **kwargs: Any
) -> Dict[str, str]:
    """Validates the EasyDebrid credentials."""
    try:
        async with EasyDebrid(token=user_data.streaming_provider.token) as easydebrid_client:
            await easydebrid_client.get_user_info()
            return {"status": "success"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate EasyDebrid credentials: {error.message}",
        }
