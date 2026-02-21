import asyncio
import logging

from db.schemas import StreamingProvider, TorrentStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.offcloud.client import OffCloud

logger = logging.getLogger(__name__)
OFFCLOUD_TORRENT_DETAILS_CONCURRENCY = 6


def _normalize_status(status: str | None) -> str:
    return (status or "").strip().casefold()


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
        current_torrent_info = None
        if torrent_info:
            request_id = torrent_info.get("requestId")
            history_status = _normalize_status(torrent_info.get("status"))

            # Some OffCloud history entries may already be directly downloadable.
            if history_status == "downloaded":
                return await oc_client.create_download_link(
                    request_id,
                    torrent_info,
                    stream,
                    filename,
                    season,
                    episode,
                )

            current_torrent_info = await oc_client.get_torrent_info(request_id)
            status = _normalize_status(current_torrent_info.get("status"))
            if status == "downloaded":
                return await oc_client.create_download_link(
                    request_id,
                    current_torrent_info,
                    stream,
                    filename,
                    season,
                    episode,
                )
            if status == "error":
                raise ProviderException(
                    f"Error transferring magnet link to OffCloud. {current_torrent_info.get('errorMessage', '')}",
                    "transfer_error.mp4",
                )
        else:
            # OffCloud API changes are magnet-first and stable across legacy/new APIs.
            # Prefer magnet submission to avoid brittle file-upload endpoint variations.
            response_data = await oc_client.add_magnet_link(magnet_link)
            request_id = response_data["requestId"]
            current_torrent_info = response_data

        # Wait for download completion and get the direct link
        torrent_info = await oc_client.wait_for_status(
            request_id,
            "downloaded",
            max_retries,
            retry_interval,
            torrent_info=current_torrent_info,
        )
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
            sem = asyncio.Semaphore(OFFCLOUD_TORRENT_DETAILS_CONCURRENCY)
            target_hashes = {str(info_hash).lower() for info_hash in kwargs.get("target_hashes", set()) if info_hash}
            btih_torrents = [t for t in available_torrents if "btih:" in t.get("originalLink", "")]
            if target_hashes:
                btih_torrents = [
                    torrent
                    for torrent in btih_torrents
                    if torrent["originalLink"].split("btih:")[1].split("&")[0].lower() in target_hashes
                ]

            async def fetch_torrent_details(torrent: dict) -> dict:
                info_hash = torrent["originalLink"].split("btih:")[1].split("&")[0].lower()
                base = {
                    "id": torrent.get("requestId"),
                    "hash": info_hash,
                    "filename": torrent.get("fileName", ""),
                    "size": torrent.get("fileSize", 0),
                    "files": [],
                }

                request_id = torrent.get("requestId")
                if not request_id:
                    return base

                try:
                    async with sem:
                        torrent_info = await oc_client.get_torrent_info(request_id)
                    if "fileName" in torrent_info:
                        base["files"] = [
                            {
                                "id": None,
                                "path": torrent_info.get("fileName", ""),
                                "size": torrent_info.get("fileSize", 0),
                            }
                        ]
                except Exception as error:
                    logger.debug("Failed to fetch OffCloud torrent details for id=%s: %s", request_id, error)

                return base

            return await asyncio.gather(*(fetch_torrent_details(t) for t in btih_torrents))
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
