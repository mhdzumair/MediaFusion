import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from db.schemas import StreamingProvider, TorrentStreamData
from db.schemas.media import UsenetStreamData
from streaming_providers.debrider.client import Debrider
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.usenet_file_selection import select_usenet_file_index


async def get_video_url_from_debrider(
    magnet_link: str,
    streaming_provider: StreamingProvider,
    filename: str,
    user_ip: str,
    stream: TorrentStreamData,
    season: int | None = None,
    episode: int | None = None,
    **kwargs: Any,
) -> str:
    async with Debrider(
        token=streaming_provider.token,
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


def divide_chunks(lst: list[Any], n: int) -> Iterator[list[Any]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def update_chunk_cache_status(debrider_client: Debrider, streams_chunk: list[TorrentStreamData]) -> None:
    """Update cache status for a chunk of streams."""
    try:
        instant_availability_data = await debrider_client.get_torrent_instant_availability(
            [f"magnet:?xt=urn:btih:{stream.info_hash}" for stream in streams_chunk]
        )
        for stream, instant_availability in zip(streams_chunk, instant_availability_data):
            stream.cached = instant_availability["cached"]
    except ProviderException as e:
        logging.error(f"Failed to get cached status from debrider for a chunk: {e}")


async def update_debrider_cache_status(
    streams: list[TorrentStreamData], streaming_provider: StreamingProvider, user_ip: str, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Debrider's instant availability."""
    async with Debrider(
        token=streaming_provider.token,
        user_ip=user_ip,
    ) as debrider_client:
        chunks = list(divide_chunks(streams, 50))
        update_tasks = [update_chunk_cache_status(debrider_client, chunk) for chunk in chunks]
        await asyncio.gather(*update_tasks)


async def validate_debrider_credentials(
    streaming_provider: StreamingProvider, user_ip: str, **kwargs: Any
) -> dict[str, str]:
    """Validates the Debrider credentials."""
    try:
        async with Debrider(
            token=streaming_provider.token,
            user_ip=user_ip,
        ) as debrider_client:
            await debrider_client.get_user_info()
            return {"status": "success"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate Debrider credentials: {error.message}",
        }


# =========================================================================
# Usenet/NZB Functions
# =========================================================================


async def get_video_url_from_usenet_debrider(
    nzb_url: str,
    streaming_provider: StreamingProvider,
    filename: str,
    user_ip: str,
    stream: UsenetStreamData,
    season: int | None = None,
    episode: int | None = None,
    **kwargs: Any,
) -> str:
    """Get video URL from Debrider for Usenet/NZB content.

    Args:
        nzb_url: URL to the NZB file
        streaming_provider: Provider configuration
        filename: Target filename
        user_ip: User's IP address
        stream: Usenet stream data
        season: Season number for series
        episode: Episode number for series

    Returns:
        Download URL for the video file
    """
    async with Debrider(
        token=streaming_provider.token,
        user_ip=user_ip,
    ) as debrider_client:
        usenet_info = await debrider_client.create_usenet_download_link(nzb_url)
        if not usenet_info.get("files", []):
            raise ProviderException(
                "Unable to generate Usenet link.",
                "torrent_not_downloaded.mp4",
            )

        file_index = await select_file_index_from_usenet(
            usenet_info=usenet_info,
            usenet_stream=stream,
            filename=filename,
            season=season,
            episode=episode,
            episode_air_date=kwargs.get("episode_air_date"),
        )
        return usenet_info["files"][file_index]["download_link"]


async def select_file_index_from_usenet(
    usenet_info: dict[str, Any],
    usenet_stream: UsenetStreamData,
    filename: str,
    season: int | None,
    episode: int | None,
    episode_air_date: str | None = None,
) -> int:
    """Select the file index from the usenet download info.

    Args:
        usenet_info: Usenet download info from Debrider
        usenet_stream: Usenet stream data
        filename: Target filename
        season: Season number for series
        episode: Episode number for series
        episode_air_date: Optional YYYY-MM-DD for dated releases

    Returns:
        File index for the target file
    """
    files = usenet_info.get("files", [])
    if not files:
        raise ProviderException(
            "No files found in usenet download",
            "no_video_file_found.mp4",
        )

    return select_usenet_file_index(
        files,
        filename=filename,
        season=season,
        episode=episode,
        display_name=lambda f: f.get("name", ""),
        match_path_suffix=True,
        episode_air_date=episode_air_date,
    )


async def update_usenet_chunk_cache_status(debrider_client: Debrider, streams_chunk: list[UsenetStreamData]) -> None:
    """Update cache status for a chunk of usenet streams."""
    try:
        # Debrider uses NZB URLs for cache checking
        nzb_urls = [stream.nzb_url for stream in streams_chunk if stream.nzb_url]
        if not nzb_urls:
            return
        instant_availability_data = await debrider_client.get_usenet_instant_availability(nzb_urls)
        url_to_stream = {stream.nzb_url: stream for stream in streams_chunk if stream.nzb_url}
        for i, availability in enumerate(instant_availability_data):
            if i < len(nzb_urls) and nzb_urls[i] in url_to_stream:
                url_to_stream[nzb_urls[i]].cached = availability.get("cached", False)
    except ProviderException as e:
        logging.error(f"Failed to get usenet cached status from debrider for a chunk: {e}")


async def update_debrider_usenet_cache_status(
    streams: list[UsenetStreamData], streaming_provider: StreamingProvider, user_ip: str, **kwargs: Any
) -> None:
    """Updates the cache status of usenet streams based on Debrider's instant availability."""
    async with Debrider(
        token=streaming_provider.token,
        user_ip=user_ip,
    ) as debrider_client:
        # Debrider allows 50 items per request
        chunks = list(divide_chunks(streams, 50))
        update_tasks = [update_usenet_chunk_cache_status(debrider_client, chunk) for chunk in chunks]
        await asyncio.gather(*update_tasks)
