import asyncio
import logging
from typing import Any, Dict, List, Optional, Iterator

from db.schemas import UserData, TorrentStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.debrider.client import Debrider


async def get_video_url_from_debrider(
    magnet_link: str,
    user_data: UserData,
    filename: str,
    user_ip: str,
    stream: TorrentStreamData,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    **kwargs: Any,
) -> str:
    async with Debrider(
        token=user_data.streaming_provider.token,
        user_ip=user_ip,
    ) as debrider_client:
        torrent_info = await debrider_client.create_download_link(magnet_link)
        if not torrent_info.get("files", []):
            raise ProviderException(
                "Unable to generate link.",
                "torrent_not_downloaded.mp4",
            )

        file_index = await select_file_index_from_torrent(
            torrent_info=torrent_info,
            torrent_stream=stream,
            filename=filename,
            season=season,
            episode=episode,
        )
        return torrent_info["files"][file_index]["download_link"]


def divide_chunks(lst: List[Any], n: int) -> Iterator[List[Any]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def update_chunk_cache_status(
    debrider_client: Debrider, streams_chunk: List[TorrentStreamData]
) -> None:
    """Update cache status for a chunk of streams."""
    try:
        instant_availability_data = (
            await debrider_client.get_torrent_instant_availability(
                [f"magnet:?xt=urn:btih:{stream.id}" for stream in streams_chunk]
            )
        )
        for stream, instant_availability in zip(
            streams_chunk, instant_availability_data
        ):
            stream.cached = instant_availability["cached"]
    except ProviderException as e:
        logging.error(f"Failed to get cached status from debrider for a chunk: {e}")


async def update_debrider_cache_status(
    streams: List[TorrentStreamData], user_data: UserData, user_ip: str, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Debrider's instant availability."""
    async with Debrider(
        token=user_data.streaming_provider.token,
        user_ip=user_ip,
    ) as debrider_client:
        chunks = list(divide_chunks(streams, 50))
        update_tasks = [
            update_chunk_cache_status(debrider_client, chunk) for chunk in chunks
        ]
        await asyncio.gather(*update_tasks)


async def validate_debrider_credentials(
    user_data: UserData, user_ip: str, **kwargs: Any
) -> Dict[str, str]:
    """Validates the Debrider credentials."""
    try:
        async with Debrider(
            token=user_data.streaming_provider.token,
            user_ip=user_ip,
        ) as debrider_client:
            await debrider_client.get_user_info()
            return {"status": "success"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate Debrider credentials: {error.message}",
        }
