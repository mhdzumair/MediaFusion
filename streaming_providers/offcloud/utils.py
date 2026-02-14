import asyncio

from db.schemas import StreamingProvider, TorrentStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.offcloud.client import OffCloud


async def get_video_url_from_offcloud(
    info_hash: str,
    magnet_link: str,
    streaming_provider: StreamingProvider,
    stream: TorrentStreamData,
    filename: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    max_retries: int = 5,
    retry_interval: int = 5,
    **kwargs,
) -> str:
    async with OffCloud(token=streaming_provider.token) as oc_client:
        # Check if the torrent already exists
        torrent_info = await oc_client.get_available_torrent(info_hash)
        if torrent_info:
            request_id = torrent_info.get("requestId")
            torrent_info = await oc_client.get_torrent_info(request_id)
            if torrent_info["status"] == "downloaded":
                return await oc_client.create_download_link(
                    request_id,
                    torrent_info,
                    stream,
                    filename,
                    season,
                    episode,
                )
            if torrent_info["status"] == "error":
                raise ProviderException(
                    f"Error transferring magnet link to OffCloud. {torrent_info['errorMessage']}",
                    "transfer_error.mp4",
                )
        else:
            # If torrent doesn't exist, add it
            if stream.torrent_file:
                response_data = await oc_client.add_torrent_file(stream.torrent_file, stream.name)
            else:
                response_data = await oc_client.add_magnet_link(magnet_link)
            request_id = response_data["requestId"]

        # Wait for download completion and get the direct link
        torrent_info = await oc_client.wait_for_status(request_id, "downloaded", max_retries, retry_interval)
        return await oc_client.create_download_link(
            request_id,
            torrent_info,
            stream,
            filename,
            season,
            episode,
        )


async def update_oc_cache_status(streams: list[TorrentStreamData], streaming_provider: StreamingProvider, **kwargs):
    """Updates the cache status of streams based on OffCloud's instant availability."""
    try:
        async with OffCloud(token=streaming_provider.token) as oc_client:
            instant_availability_data = await oc_client.get_torrent_instant_availability(
                [stream.info_hash for stream in streams]
            )
            if not instant_availability_data:
                return
            for stream in streams:
                stream.cached = stream.info_hash in instant_availability_data
    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_oc(streaming_provider: StreamingProvider, **kwargs) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the OffCloud account."""
    try:
        async with OffCloud(token=streaming_provider.token) as oc_client:
            available_torrents = await oc_client.get_user_torrent_list()
            return [
                torrent["originalLink"].split("btih:")[1].split("&")[0]
                for torrent in available_torrents
                if "btih:" in torrent["originalLink"]
            ]
    except ProviderException:
        return []


async def fetch_torrent_details_from_oc(streaming_provider: StreamingProvider, **kwargs) -> list[dict]:
    """
    Fetches detailed torrent information from the OffCloud account.
    Returns torrent details including files for import functionality.
    """
    try:
        async with OffCloud(token=streaming_provider.token) as oc_client:
            available_torrents = await oc_client.get_user_torrent_list()
            result = []
            for torrent in available_torrents:
                if "btih:" not in torrent.get("originalLink", ""):
                    continue

                info_hash = torrent["originalLink"].split("btih:")[1].split("&")[0].lower()

                # Get detailed info for files
                try:
                    torrent_info = await oc_client.get_torrent_info(torrent["requestId"])
                    files = []
                    if "fileName" in torrent_info:
                        files.append(
                            {
                                "id": None,
                                "path": torrent_info.get("fileName", ""),
                                "size": torrent_info.get("fileSize", 0),
                            }
                        )
                except Exception:
                    files = []

                result.append(
                    {
                        "id": torrent.get("requestId"),
                        "hash": info_hash,
                        "filename": torrent.get("fileName", ""),
                        "size": torrent.get("fileSize", 0),
                        "files": files,
                    }
                )
            return result
    except ProviderException:
        return []


async def delete_all_torrents_from_oc(streaming_provider: StreamingProvider, **kwargs):
    """Deletes all torrents from the Offcloud account."""
    async with OffCloud(token=streaming_provider.token) as oc_client:
        torrents = await oc_client.get_user_torrent_list()
        await asyncio.gather(
            *[oc_client.delete_torrent(torrent["requestId"]) for torrent in torrents],
            return_exceptions=True,
        )


async def delete_torrent_from_oc(streaming_provider: StreamingProvider, info_hash: str, **kwargs) -> bool:
    """Deletes a specific torrent from OffCloud by info_hash."""
    try:
        async with OffCloud(token=streaming_provider.token) as oc_client:
            torrents = await oc_client.get_user_torrent_list()
            for torrent in torrents:
                if "btih:" not in torrent.get("originalLink", ""):
                    continue
                torrent_hash = torrent["originalLink"].split("btih:")[1].split("&")[0].lower()
                if torrent_hash == info_hash.lower():
                    await oc_client.delete_torrent(torrent["requestId"])
                    return True
            return False
    except ProviderException:
        return False


async def validate_offcloud_credentials(streaming_provider: StreamingProvider, **kwargs) -> dict:
    """Validates the OffCloud credentials."""
    try:
        async with OffCloud(token=streaming_provider.token) as oc_client:
            await oc_client.get_user_torrent_list()
            return {"status": "success"}
    except ProviderException:
        return {
            "status": "error",
            "message": "OffCloud API key is invalid or has expired",
        }
