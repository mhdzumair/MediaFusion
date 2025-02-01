import asyncio
import logging
from typing import Any, Dict, List, Optional, Iterator

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.easydebrid.client import EasyDebrid


async def get_video_url_from_easydebrid(
    magnet_link: str,
    user_data: UserData,
    filename: str,
    user_ip: str,
    episode: Optional[int] = None,
    **kwargs: Any,
) -> str:
    async with EasyDebrid(
        token=user_data.streaming_provider.token,
        user_ip=user_ip,
    ) as easydebrid_client:
        torrent_info = await easydebrid_client.create_download_link(magnet_link)
        # If create download link returns an error, we try to add the link for caching, the error returned is generally
        # {'error': 'Unsupported link for direct download.'}
        if torrent_info.get("error", ""):
            await easydebrid_client.add_torrent_file(magnet_link)
            raise ProviderException(
                "Torrent did not reach downloaded status.",
                "torrent_not_downloaded.mp4",
            )

        file_index = await select_file_index_from_torrent(
            torrent_info,
            filename,
            episode,
            name_key="filename",
        )
        return torrent_info["files"][file_index]["url"]


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
        )
        for stream, instant_availability in zip(
            streams_chunk, instant_availability_data
        ):
            stream.cached = instant_availability
    except ProviderException as e:
        logging.error(f"Failed to get cached status from easydebrid for a chunk: {e}")


async def update_easydebrid_cache_status(
    streams: List[TorrentStreams], user_data: UserData, user_ip: str, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Easydebrid's instant availability."""
    async with EasyDebrid(
        token=user_data.streaming_provider.token,
        user_ip=user_ip,
    ) as easydebrid_client:
        chunks = list(divide_chunks(streams, 50))
        update_tasks = [
            update_chunk_cache_status(easydebrid_client, chunk) for chunk in chunks
        ]
        await asyncio.gather(*update_tasks)


async def validate_easydebrid_credentials(
    user_data: UserData, user_ip: str, **kwargs: Any
) -> Dict[str, str]:
    """Validates the EasyDebrid credentials."""
    try:
        async with EasyDebrid(
            token=user_data.streaming_provider.token,
            user_ip=user_ip,
        ) as easydebrid_client:
            await easydebrid_client.get_user_info()
            return {"status": "success"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate EasyDebrid credentials: {error.message}",
        }
