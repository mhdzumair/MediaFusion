import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from db.schemas import StreamingProvider, TorrentStreamData
from db.schemas.media import UsenetStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.torbox.client import Torbox


async def get_video_url_from_torbox(
    info_hash: str,
    magnet_link: str,
    streaming_provider: StreamingProvider,
    filename: str,
    user_ip: str,
    stream: TorrentStreamData,
    season: int | None = None,
    episode: int | None = None,
    **kwargs: Any,
) -> str:
    async with Torbox(token=streaming_provider.token) as torbox_client:
        # Check if the torrent already exists
        torrent_info = await torbox_client.get_available_torrent(info_hash)
        if torrent_info:
            if torrent_info["download_finished"] is True and torrent_info["download_present"] is True:
                file_id = await select_file_id_from_torrent(torrent_info, filename, stream, season, episode)
                response = await torbox_client.create_download_link(
                    torrent_info["id"],
                    file_id,
                    user_ip,
                )
                return response["data"]
        else:
            queued_torrents = await torbox_client.get_queued_torrents()
            for torrent in queued_torrents.get("data", []):
                if torrent.get("hash") == info_hash:
                    raise ProviderException(
                        "Torrent did not reach downloaded status.",
                        "torrent_not_downloaded.mp4",
                    )
            # If torrent doesn't exist, add it
            if stream.torrent_file:
                response = await torbox_client.add_torrent_file(stream.torrent_file, stream.name)
            else:
                response = await torbox_client.add_magnet_link(magnet_link)
            # Response detail has "Found Cached Torrent" If it's a cached torrent,
            # create a download link from it directly in the same call.
            if "Found Cached" in response.get("detail", ""):
                torrent_info = await torbox_client.get_available_torrent(info_hash)
                if torrent_info:
                    file_id = await select_file_id_from_torrent(torrent_info, filename, stream, season, episode)
                    response = await torbox_client.create_download_link(
                        torrent_info["id"],
                        file_id,
                        user_ip,
                    )
                    return response["data"]

    raise ProviderException(
        "Torrent did not reach downloaded status.",
        "torrent_not_downloaded.mp4",
    )


def divide_chunks(lst: list[Any], n: int) -> Iterator[list[Any]]:
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


async def update_chunk_cache_status(torbox_client: Torbox, streams_chunk: list[TorrentStreamData]) -> None:
    """Update cache status for a chunk of streams."""
    try:
        instant_availability_data = (
            await torbox_client.get_torrent_instant_availability([stream.info_hash for stream in streams_chunk]) or []
        )
        if not instant_availability_data:
            return
        for stream in streams_chunk:
            stream.cached = bool(stream.info_hash in instant_availability_data)
    except ProviderException as e:
        logging.error(f"Failed to get cached status from torbox for a chunk: {e}")


async def update_torbox_cache_status(
    streams: list[TorrentStreamData], streaming_provider: StreamingProvider, **kwargs: Any
) -> None:
    """Updates the cache status of streams based on Torbox's instant availability."""
    async with Torbox(token=streaming_provider.token) as torbox_client:
        # Torbox allows only 100 torrents to be passed for cache status, send 80 at a time.
        chunks = list(divide_chunks(streams, 80))
        update_tasks = [update_chunk_cache_status(torbox_client, chunk) for chunk in chunks]
        await asyncio.gather(*update_tasks)


async def fetch_downloaded_info_hashes_from_torbox(streaming_provider: StreamingProvider, **kwargs: Any) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Torbox account."""
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            available_torrents = await torbox_client.get_user_torrent_list()
            if not available_torrents.get("data"):
                return []
            return [torrent["hash"] for torrent in available_torrents["data"]]

    except ProviderException:
        return []


async def fetch_torrent_details_from_torbox(streaming_provider: StreamingProvider, **kwargs: Any) -> list[dict]:
    """
    Fetches detailed torrent information from the Torbox account.
    Returns torrent details including files for import functionality.
    """
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            available_torrents = await torbox_client.get_user_torrent_list()
            if not available_torrents.get("data"):
                return []
            result = []
            for torrent in available_torrents["data"]:
                files = []
                for f in torrent.get("files", []):
                    files.append(
                        {
                            "id": f.get("id"),
                            "path": f.get("short_name", f.get("name", "")),
                            "size": f.get("size", 0),
                        }
                    )
                result.append(
                    {
                        "id": torrent.get("id"),
                        "hash": torrent.get("hash", "").lower(),
                        "filename": torrent.get("name", ""),
                        "size": torrent.get("size", 0),
                        "files": files,
                    }
                )
            return result

    except ProviderException:
        return []


async def select_file_id_from_torrent(
    torrent_info: dict[str, Any],
    filename: str,
    stream: TorrentStreamData,
    season: int | None,
    episode: int | None,
) -> int:
    """Select the file id from the torrent info."""
    file_index = await select_file_index_from_torrent(
        torrent_info=torrent_info,
        torrent_stream=stream,
        filename=filename,
        season=season,
        episode=episode,
        name_key="short_name",
        is_filename_trustable=True,
    )
    return torrent_info["files"][file_index]["id"]


async def delete_all_torrents_from_torbox(streaming_provider: StreamingProvider, **kwargs: Any) -> None:
    """Deletes all torrents from the Torbox account."""
    async with Torbox(token=streaming_provider.token) as torbox_client:
        torrents = (await torbox_client.get_user_torrent_list()).get("data")
        if not torrents:
            return
        for torrent in torrents:
            await torbox_client.delete_torrent(torrent.get("id", ""))


async def delete_torrent_from_torbox(streaming_provider: StreamingProvider, info_hash: str, **kwargs: Any) -> bool:
    """Deletes a specific torrent from Torbox by info_hash."""
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            response = await torbox_client.get_user_torrent_list()
            torrents = response.get("data", [])
            for torrent in torrents:
                if torrent.get("hash", "").lower() == info_hash.lower():
                    await torbox_client.delete_torrent(torrent.get("id"))
                    return True
            return False
    except ProviderException:
        return False


async def validate_torbox_credentials(streaming_provider: StreamingProvider, **kwargs: Any) -> dict[str, str]:
    """Validates the Torbox credentials."""
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            await torbox_client.get_user_info()
            return {"status": "success"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate TorBox credentials: {error.message}",
        }


# =========================================================================
# Usenet/NZB Functions
# =========================================================================


async def get_video_url_from_usenet_torbox(
    nzb_hash: str,
    streaming_provider: StreamingProvider,
    filename: str,
    user_ip: str,
    stream: UsenetStreamData,
    season: int | None = None,
    episode: int | None = None,
    **kwargs: Any,
) -> str:
    """Get video URL from TorBox for Usenet/NZB content.

    Args:
        nzb_hash: Hash of the NZB content
        streaming_provider: Provider configuration
        filename: Target filename
        user_ip: User's IP address
        stream: Usenet stream data
        season: Season number for series
        episode: Episode number for series

    Returns:
        Download URL for the video file
    """
    async with Torbox(token=streaming_provider.token) as torbox_client:
        # Check if the usenet download already exists
        usenet_info = await torbox_client.get_available_usenet(nzb_hash)

        if usenet_info:
            if usenet_info.get("download_finished") is True and usenet_info.get("download_present") is True:
                file_id = await select_file_id_from_usenet(usenet_info, filename, stream, season, episode)
                response = await torbox_client.create_usenet_download_link(
                    usenet_info["id"],
                    file_id,
                    user_ip,
                )
                return response["data"]
        else:
            # Add the NZB to TorBox via URL
            if stream.nzb_url:
                response = await torbox_client.add_usenet_link(stream.nzb_url)
            else:
                raise ProviderException(
                    "No NZB URL available for this stream. Try importing the NZB from a Newznab indexer.",
                    "transfer_error.mp4",
                )

            # Check if it was found cached
            if "Found Cached" in response.get("detail", ""):
                usenet_info = await torbox_client.get_available_usenet(nzb_hash)
                if usenet_info:
                    file_id = await select_file_id_from_usenet(usenet_info, filename, stream, season, episode)
                    response = await torbox_client.create_usenet_download_link(
                        usenet_info["id"],
                        file_id,
                        user_ip,
                    )
                    return response["data"]

    raise ProviderException(
        "Usenet download did not reach downloaded status.",
        "torrent_not_downloaded.mp4",
    )


async def select_file_id_from_usenet(
    usenet_info: dict[str, Any],
    filename: str,
    stream: UsenetStreamData,
    season: int | None,
    episode: int | None,
) -> int:
    """Select the file id from the usenet download info.

    Args:
        usenet_info: Usenet download info from TorBox
        filename: Target filename
        stream: Usenet stream data
        season: Season number for series
        episode: Episode number for series

    Returns:
        File ID for the target file
    """
    files = usenet_info.get("files", [])
    if not files:
        raise ProviderException(
            "No files found in usenet download",
            "no_video_file_found.mp4",
        )

    # If filename is provided, try to match it
    if filename:
        for f in files:
            if f.get("short_name", "").lower() == filename.lower() or f.get("name", "").lower() == filename.lower():
                return f["id"]

    # For series, try to match season/episode
    if season is not None and episode is not None:
        import re

        pattern = rf"[sS]{season:02d}[eE]{episode:02d}"
        for f in files:
            file_name = f.get("short_name", f.get("name", ""))
            if re.search(pattern, file_name):
                return f["id"]

    # Return the largest video file
    video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm"}
    video_files = []
    for f in files:
        file_name = f.get("short_name", f.get("name", "")).lower()
        if any(file_name.endswith(ext) for ext in video_extensions):
            video_files.append(f)

    if video_files:
        largest = max(video_files, key=lambda x: x.get("size", 0))
        return largest["id"]

    # Fallback to first file
    return files[0]["id"]


async def update_usenet_chunk_cache_status(torbox_client: Torbox, streams_chunk: list[UsenetStreamData]) -> None:
    """Update cache status for a chunk of usenet streams."""
    try:
        instant_availability_data = await torbox_client.get_usenet_instant_availability(
            [stream.nzb_guid for stream in streams_chunk]
        )
        if not instant_availability_data:
            return
        for stream in streams_chunk:
            stream.cached = bool(stream.nzb_guid in instant_availability_data)
    except ProviderException as e:
        logging.error(f"Failed to get usenet cached status from torbox for a chunk: {e}")


async def update_torbox_usenet_cache_status(
    streams: list[UsenetStreamData], streaming_provider: StreamingProvider, **kwargs: Any
) -> None:
    """Updates the cache status of usenet streams based on Torbox's instant availability."""
    async with Torbox(token=streaming_provider.token) as torbox_client:
        # TorBox allows only 100 items for cache status, send 80 at a time
        chunks = list(divide_chunks(streams, 80))
        update_tasks = [update_usenet_chunk_cache_status(torbox_client, chunk) for chunk in chunks]
        await asyncio.gather(*update_tasks)


async def fetch_downloaded_usenet_hashes_from_torbox(streaming_provider: StreamingProvider, **kwargs: Any) -> list[str]:
    """Fetches the hashes of all usenet downloads in the Torbox account."""
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            available_usenet = await torbox_client.get_usenet_list()
            if not available_usenet.get("data"):
                return []
            return [usenet["hash"] for usenet in available_usenet["data"] if usenet.get("hash")]
    except ProviderException:
        return []


async def delete_all_usenet_from_torbox(streaming_provider: StreamingProvider, **kwargs: Any) -> None:
    """Deletes all usenet downloads from the Torbox account."""
    async with Torbox(token=streaming_provider.token) as torbox_client:
        usenet_list = (await torbox_client.get_usenet_list()).get("data", [])
        for usenet in usenet_list:
            await torbox_client.delete_usenet(usenet.get("id", 0))


async def delete_usenet_from_torbox(streaming_provider: StreamingProvider, nzb_hash: str, **kwargs: Any) -> bool:
    """Deletes a specific usenet download from Torbox by NZB hash."""
    try:
        async with Torbox(token=streaming_provider.token) as torbox_client:
            response = await torbox_client.get_usenet_list()
            usenet_list = response.get("data", [])
            for usenet in usenet_list:
                if usenet.get("hash", "").lower() == nzb_hash.lower():
                    await torbox_client.delete_usenet(usenet.get("id"))
                    return True
            return False
    except ProviderException:
        return False
