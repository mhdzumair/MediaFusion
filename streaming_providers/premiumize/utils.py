import asyncio
from typing import Any, Optional

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.premiumize.client import Premiumize


async def create_or_get_folder_id(pm_client: Premiumize, info_hash: str):
    folder_data = await pm_client.get_folder_list()
    for folder in folder_data.get("content", []):
        if folder["name"] == info_hash:
            return folder["id"]

    folder_data = await pm_client.create_folder(info_hash)
    if folder_data.get("status") != "success":
        if folder_data.get("message") == "This folder already exists.":
            return await create_or_get_folder_id(pm_client, info_hash)
        raise ProviderException(
            "Folder already created in meanwhile", "torrent_not_downloaded.mp4"
        )
    return folder_data.get("id")


async def get_video_url_from_premiumize(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    user_ip: str,
    filename: Optional[str],
    episode: Optional[int],
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with Premiumize(token=user_data.streaming_provider.token, user_ip=user_ip) as pm_client:
        # Check if the torrent already exists
        torrent_info = await pm_client.get_available_torrent(info_hash)
        if torrent_info:
            torrent_id = torrent_info.get("id")
            if torrent_info["status"] == "error":
                await pm_client.delete_torrent(torrent_id)
                raise ProviderException(
                    "Not enough seeders available for parse magnet link",
                    "transfer_error.mp4",
                )
        else:
            # If torrent doesn't exist, add it
            folder_id = await create_or_get_folder_id(pm_client, info_hash)
            response_data = await pm_client.add_magnet_link(magnet_link, folder_id)
            if "id" not in response_data:
                raise ProviderException(
                    "Failed to add magnet link to Premiumize", "transfer_error.mp4"
                )
            torrent_id = response_data["id"]

        # Wait for file selection and then start torrent download
        torrent_info = await pm_client.wait_for_status(
            torrent_id, "finished", max_retries, retry_interval
        )
        return await get_stream_link(
            pm_client, torrent_info, filename, info_hash, episode
        )


async def get_stream_link(
    pm_client: Premiumize,
    torrent_info: dict[str, Any],
    filename: Optional[str],
    info_hash: str,
    episode: Optional[int] = None,
) -> str:
    """Get the stream link from the torrent info."""
    if torrent_info["folder_id"] is None:
        torrent_folder_data = await pm_client.get_folder_list(
            await create_or_get_folder_id(pm_client, info_hash)
        )
    else:
        torrent_folder_data = await pm_client.get_folder_list(torrent_info["folder_id"])

    torrent_info = {
        "files": [
            file_data
            for file_data in torrent_folder_data["content"]
            if "video" in file_data.get("mime_type", "")
        ]
    }
    selected_file_index = await select_file_index_from_torrent(
        torrent_info,
        filename,
        episode,
    )
    selected_file = torrent_info["files"][selected_file_index]
    return selected_file["link"]


async def update_pm_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on Premiumize's instant availability."""

    try:
        async with Premiumize(token=user_data.streaming_provider.token) as pm_client:
            instant_availability_data = (
                await pm_client.get_torrent_instant_availability(
                    [stream.id for stream in streams]
                )
            )
            for stream, cached_status in zip(
                streams, instant_availability_data.get("response")
            ):
                stream.cached = cached_status

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_premiumize(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Premiumize account."""
    try:
        async with Premiumize(token=user_data.streaming_provider.token) as pm_client:
            available_folders = await pm_client.get_folder_list()
            if "content" not in available_folders:
                return []
            return [
                folder["name"]
                for folder in available_folders["content"]
                if folder["name"] and len(folder["name"]) in (40, 32)
            ]

    except ProviderException:
        return []


async def delete_all_torrents_from_pm(user_data: UserData, **kwargs):
    """Deletes all torrents from the Premiumize account."""
    async with Premiumize(token=user_data.streaming_provider.token) as pm_client:
        folders = await pm_client.get_folder_list()
        delete_tasks = [
            pm_client.delete_folder(folder["id"]) for folder in folders["content"]
        ]
        await asyncio.gather(*delete_tasks)


async def validate_premiumize_credentials(user_data: UserData, **kwargs) -> dict:
    """Validates the Premiumize credentials."""
    try:
        async with Premiumize(token=user_data.streaming_provider.token) as pm_client:
            response = await pm_client.get_account_info()
            if response["status"] == "success":
                return {"status": "success"}
            return {"status": "error", "message": "Premiumize token is invalid"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify Premiumize credential, error: {error.message}",
        }
