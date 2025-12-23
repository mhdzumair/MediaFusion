import asyncio
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List, Any, Tuple, AsyncGenerator

from aioseedrcc import Seedr
from aioseedrcc.exception import SeedrException

from db.schemas import TorrentStreamData
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent


class TorrentStatus(Enum):
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    NOT_FOUND = "not_found"


def clean_filename(name: Optional[str], replace: str = "") -> str:
    """Clean filename of special characters."""
    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9 .,;:_~\-()]", replace, name)


@asynccontextmanager
async def get_seedr_client(user_data: UserData) -> AsyncGenerator[Seedr, Any]:
    """Context manager that provides a Seedr client instance."""
    try:
        async with Seedr(token=user_data.streaming_provider.token) as seedr:
            response = await seedr.test_token()
            if "error" in response:
                raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
            yield seedr
    except SyntaxError:
        raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
    except ProviderException as error:
        raise error
    except SeedrException as error:
        if "Unauthorized" in str(error):
            raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
        logging.exception(error)
        raise ProviderException("Seedr server error", "debrid_service_down_error.mp4")
    except Exception as error:
        logging.exception(error)
        raise error


async def get_folder_by_info_hash(
    seedr: Seedr, info_hash: str
) -> Optional[Dict[str, Any]]:
    """Find a folder by info_hash."""
    contents = await seedr.list_contents()
    return next((f for f in contents["folders"] if f["name"] == info_hash), None)


async def check_torrent_status(
    seedr: Seedr, info_hash: str
) -> Tuple[TorrentStatus, Optional[Dict]]:
    """Check the current status of a torrent."""
    folder = await get_folder_by_info_hash(seedr, info_hash)
    if not folder:
        return TorrentStatus.NOT_FOUND, None

    folder_content = await seedr.list_contents(folder["id"])

    if folder_content["torrents"]:
        return TorrentStatus.DOWNLOADING, folder_content["torrents"][0]
    elif folder_content["folders"]:
        return TorrentStatus.COMPLETED, folder_content["folders"][0]

    return TorrentStatus.NOT_FOUND, None


async def ensure_space_available(seedr: Seedr, required_space: int | float) -> None:
    """Ensure enough space is available, deleting old content if necessary."""
    contents = await seedr.list_contents()
    if required_space != math.inf and required_space > contents["space_max"]:
        raise ProviderException(
            "Not enough space in Seedr account", "not_enough_space.mp4"
        )

    available_space = contents["space_max"] - contents["space_used"]

    if available_space >= required_space:
        return

    # Sort folders by size (descending) and last update time
    folders = sorted(
        contents["folders"],
        key=lambda x: (
            -x["size"],
            datetime.strptime(x["last_update"], "%Y-%m-%d %H:%M:%S"),
        ),
    )

    for folder in folders:
        if available_space >= required_space:
            break

        # Delete folder contents first
        sub_content = await seedr.list_contents(folder["id"])
        if sub_content["torrents"]:
            # Raise exception if torrent is still downloading.
            raise ProviderException(
                "An existing torrent is being downloaded", "torrent_downloading.mp4"
            )
        for subfolder in sub_content["folders"]:
            await seedr.delete_item(subfolder["id"], "folder")

        await seedr.delete_item(folder["id"], "folder")
        available_space += folder["size"]


async def add_torrent(
    seedr: Seedr, magnet_link: str, info_hash: str, stream: TorrentStreamData
) -> None:
    """Add a new torrent to Seedr."""
    await seedr.add_folder(info_hash)
    folder = await get_folder_by_info_hash(seedr, info_hash)
    if not folder:
        raise ProviderException("Failed to create folder", "folder_creation_error.mp4")

    if stream.torrent_file:
        transfer = await seedr.add_torrent(
            torrent_file_content=stream.torrent_file,
            folder_id=folder["id"],
            torrent_file=stream.torrent_name,
        )
    else:
        transfer = await seedr.add_torrent(
            magnet_link=magnet_link, folder_id=folder["id"]
        )

    if transfer["result"] is True:
        return

    error_messages = {
        "not_enough_space_added_to_wishlist": (
            "Not enough space in Seedr account",
            "not_enough_space.mp4",
        ),
        "not_enough_space_wishlist_full": (
            "Not enough space in Seedr account",
            "not_enough_space.mp4",
        ),
        "queue_full_added_to_wishlist": ("Seedr queue is full", "queue_full.mp4"),
    }

    if transfer["result"] in error_messages:
        msg, video = error_messages[transfer["result"]]
        raise ProviderException(msg, video)

    raise ProviderException(
        "Error transferring magnet link to Seedr", "transfer_error.mp4"
    )


async def wait_for_completion(
    seedr: Seedr, info_hash: str, max_retries: int = 1, retry_interval: int = 1
) -> None:
    """Wait for torrent to complete downloading."""
    for _ in range(max_retries):
        status, data = await check_torrent_status(seedr, info_hash)

        if status == TorrentStatus.COMPLETED:
            return
        elif status == TorrentStatus.DOWNLOADING and data.get("progress") == "100":
            return

        await asyncio.sleep(retry_interval)

    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


async def clean_names(seedr: Seedr, folder_id: str) -> None:
    """Clean special characters from all file and folder names."""
    content = await seedr.list_contents(folder_id)

    for file in content["files"]:
        clean_name = clean_filename(file["name"])
        if file["name"] != clean_name:
            await seedr.rename_item(file["folder_file_id"], clean_name, "file")


async def get_files_from_folder(seedr: Seedr, folder_id: str) -> List[Dict[str, Any]]:
    """Recursively get all files from a folder."""
    content = await seedr.list_contents(folder_id)
    files = content["files"]

    for folder in content["folders"]:
        files.extend(await get_files_from_folder(seedr, folder["id"]))

    return files


async def get_video_url_from_seedr(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreamData,
    filename: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    **kwargs,
) -> str:
    """Main function to get video URL from Seedr."""
    async with get_seedr_client(user_data) as seedr:
        # Check existing torrent status
        status, data = await check_torrent_status(seedr, info_hash)

        if status == TorrentStatus.NOT_FOUND:
            await ensure_space_available(seedr, stream.size)
            await add_torrent(seedr, magnet_link, info_hash, stream)
            await wait_for_completion(seedr, info_hash)
            status, data = await check_torrent_status(seedr, info_hash)

        if status != TorrentStatus.COMPLETED or not data:
            raise ProviderException(
                "Failed to get completed torrent", "torrent_error.mp4"
            )

        # Clean filenames for compatibility
        await clean_names(seedr, data["id"])

        # Get file details
        folder_content = await get_files_from_folder(seedr, data["id"])
        file_index = await select_file_index_from_torrent(
            torrent_info={"files": folder_content},
            torrent_stream=stream,
            filename=clean_filename(filename),
            season=season,
            episode=episode,
        )

        selected_file = folder_content[file_index]
        if not selected_file["play_video"]:
            raise ProviderException(
                "No matching file available", "no_matching_file.mp4"
            )

        video_data = await seedr.fetch_file(selected_file["folder_file_id"])
        return video_data["url"]


async def update_seedr_cache_status(
    streams: List[TorrentStreamData], user_data: UserData, **kwargs
) -> None:
    """Update cache status for multiple streams."""
    try:
        async with get_seedr_client(user_data) as seedr:
            contents = await seedr.list_contents()

            # Create lookup of valid info hash folders
            folder_map = {
                folder["name"]: folder["id"]
                for folder in contents["folders"]
                if len(folder["name"]) in (40, 32)
            }

            if not folder_map:
                return

            # Update stream cache status
            for stream in streams:
                if stream.id in folder_map:
                    folder_content = await seedr.list_contents(folder_map[stream.id])
                    if folder_content["folders"]:
                        stream.cached = True
    except ProviderException:
        return


async def fetch_downloaded_info_hashes_from_seedr(
    user_data: UserData, **kwargs
) -> List[str]:
    """Fetch the info_hashes of all downloaded torrents in the user's account."""
    try:
        async with get_seedr_client(user_data) as seedr:
            contents = await seedr.list_contents()
            return [
                folder["name"]
                for folder in contents["folders"]
                if len(folder["name"]) in (40, 32)
            ]
    except ProviderException:
        return []


async def delete_all_torrents_from_seedr(user_data: UserData, **kwargs) -> None:
    """Delete all torrents from the user's account."""
    async with get_seedr_client(user_data) as seedr:
        await ensure_space_available(seedr, math.inf)


async def validate_seedr_credentials(user_data: UserData, **kwargs) -> dict:
    """Validate the Seedr credentials."""
    try:
        async with get_seedr_client(user_data):
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate Seedr credentials: {error.message}",
        }
    except Exception as error:
        return {
            "status": "error",
            "message": f"Failed to validate Seedr credentials: {error}",
        }
