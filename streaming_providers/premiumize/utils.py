import asyncio
from os.path import basename
from typing import Any

from db.schemas import StreamingProvider, TorrentStreamData
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
        raise ProviderException("Folder already created in meanwhile", "torrent_not_downloaded.mp4")
    return folder_data.get("id")


async def add_new_torrent(pm_client: Premiumize, magnet_link: str, stream: TorrentStreamData, info_hash: str) -> dict:
    # Check if the torrent is already cached
    instant_availability_data = await pm_client.get_torrent_instant_availability([info_hash])
    if instant_availability_data.get("response")[0]:
        return await pm_client.create_direct_download(magnet_link)

    folder_id = await create_or_get_folder_id(pm_client, info_hash)
    if stream.torrent_file:
        response_data = await pm_client.add_torrent_file(stream.torrent_file, stream.name, folder_id)
    else:
        response_data = await pm_client.add_magnet_link(magnet_link, folder_id)

    if "id" not in response_data:
        raise ProviderException("Failed to add magnet link to Premiumize", "transfer_error.mp4")
    return response_data


async def get_video_url_from_premiumize(
    info_hash: str,
    magnet_link: str,
    streaming_provider: StreamingProvider,
    user_ip: str,
    stream: TorrentStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with Premiumize(token=streaming_provider.token, user_ip=user_ip) as pm_client:
        response_data = await add_new_torrent(pm_client, magnet_link, stream, info_hash)
        torrent_id = response_data.get("id")
        if not torrent_id:
            torrent_info = {
                "files": [
                    {"name": basename(file_data["path"]), **file_data}
                    for file_data in response_data.get("content", [])
                    if "stream_link" in file_data
                ]
            }
            return await get_stream_link(torrent_info, filename, stream, season, episode)

        # Wait for file selection and then start torrent download
        torrent_info = await pm_client.wait_for_status(torrent_id, "finished", max_retries, retry_interval)
        torrent_folder_data = await fetch_downloaded_folder_data(pm_client, torrent_info, info_hash)
        torrent_info = {
            "files": [
                file_data for file_data in torrent_folder_data["content"] if "video" in file_data.get("mime_type", "")
            ]
        }
        return await get_stream_link(torrent_info, filename, stream, season, episode)


async def fetch_downloaded_folder_data(pm_client: Premiumize, torrent_info: dict[str, Any], info_hash: str) -> dict:
    if torrent_info["folder_id"] is None:
        torrent_folder_data = await pm_client.get_folder_list(await create_or_get_folder_id(pm_client, info_hash))
    else:
        torrent_folder_data = await pm_client.get_folder_list(torrent_info["folder_id"])

    return torrent_folder_data


async def get_stream_link(
    torrent_info: dict[str, Any],
    filename: str | None,
    stream: TorrentStreamData,
    season: int | None = None,
    episode: int | None = None,
) -> str:
    """Get the stream link from the torrent info."""
    selected_file_index = await select_file_index_from_torrent(
        torrent_info=torrent_info,
        torrent_stream=stream,
        filename=filename,
        season=season,
        episode=episode,
    )
    selected_file = torrent_info["files"][selected_file_index]
    return selected_file["link"]


async def update_pm_cache_status(streams: list[TorrentStreamData], streaming_provider: StreamingProvider, **kwargs):
    """Updates the cache status of streams based on Premiumize's instant availability."""

    try:
        async with Premiumize(token=streaming_provider.token) as pm_client:
            instant_availability_data = await pm_client.get_torrent_instant_availability(
                [stream.info_hash for stream in streams]
            )
            for stream, cached_status in zip(streams, instant_availability_data.get("response")):
                stream.cached = cached_status

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_premiumize(streaming_provider: StreamingProvider, **kwargs) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Premiumize account."""
    try:
        async with Premiumize(token=streaming_provider.token) as pm_client:
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


async def fetch_torrent_details_from_premiumize(streaming_provider: StreamingProvider, **kwargs) -> list[dict]:
    """
    Fetches detailed torrent information from the Premiumize account.
    Returns torrent details including files for import functionality.
    """
    try:
        async with Premiumize(token=streaming_provider.token) as pm_client:
            available_folders = await pm_client.get_folder_list()
            if "content" not in available_folders:
                return []

            result = []
            for folder in available_folders["content"]:
                # Only process folders that look like info hashes
                if not folder["name"] or len(folder["name"]) not in (40, 32):
                    continue

                # Get files from the folder
                folder_content = await pm_client.get_folder_list(folder["id"])
                files = []
                total_size = 0
                for f in folder_content.get("content", []):
                    if "video" in f.get("mime_type", ""):
                        files.append(
                            {
                                "id": f.get("id"),
                                "path": f.get("name", ""),
                                "size": f.get("size", 0),
                            }
                        )
                        total_size += f.get("size", 0)

                result.append(
                    {
                        "id": folder.get("id"),
                        "hash": folder["name"].lower(),
                        "filename": folder.get("name", ""),
                        "size": total_size,
                        "files": files,
                    }
                )
            return result

    except ProviderException:
        return []


async def delete_all_torrents_from_pm(streaming_provider: StreamingProvider, **kwargs):
    """Deletes all torrents from the Premiumize account."""
    async with Premiumize(token=streaming_provider.token) as pm_client:
        folders = await pm_client.get_folder_list()
        delete_tasks = [pm_client.delete_folder(folder["id"]) for folder in folders["content"]]
        await asyncio.gather(*delete_tasks)


async def delete_torrent_from_pm(streaming_provider: StreamingProvider, info_hash: str, **kwargs) -> bool:
    """Deletes a specific torrent from Premiumize by info_hash (folder name)."""
    try:
        async with Premiumize(token=streaming_provider.token) as pm_client:
            folders = await pm_client.get_folder_list()
            if "content" not in folders:
                return False
            for folder in folders["content"]:
                if folder["name"] and folder["name"].lower() == info_hash.lower():
                    await pm_client.delete_folder(folder["id"])
                    return True
            return False
    except ProviderException:
        return False


async def validate_premiumize_credentials(streaming_provider: StreamingProvider, **kwargs) -> dict:
    """Validates the Premiumize credentials."""
    try:
        async with Premiumize(token=streaming_provider.token) as pm_client:
            response = await pm_client.get_account_info()
            if response["status"] == "success":
                return {"status": "success"}
            return {"status": "error", "message": "Premiumize token is invalid"}

    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify Premiumize credential, error: {error.message}",
        }
