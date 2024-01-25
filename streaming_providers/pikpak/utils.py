import asyncio
from datetime import datetime

import httpx
from pikpakapi import PikPakApi, PikpakException
from thefuzz import fuzz

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException


async def get_info_hash_folder_id(pikpak: PikPakApi, info_hash: str):
    """Gets the folder_id of a folder with a given info_hash in the root directory."""
    files_list_content = await pikpak.file_list()
    info_hash_file = next(
        (f for f in files_list_content["files"] if f["name"] == info_hash), None
    )
    return info_hash_file["id"] if info_hash_file else None


async def check_torrent_status(
    pikpak: PikPakApi, info_hash: str, torrent_name: str
) -> dict | None:
    """Checks the status of a torrent with a given info_hash or torrent_name."""
    try:
        available_task = await pikpak.offline_list()
    except PikpakException:
        raise ProviderException("Invalid PikPak token", "invalid_token.mp4")
    for task in available_task["tasks"]:
        if task["name"] == info_hash or task["name"] == torrent_name:
            return task


async def create_folder(pikpak: PikPakApi, info_hash: str) -> str:
    """Creates a folder with the given info_hash and returns the folder_id."""
    try:
        response = await pikpak.create_folder(info_hash)
    except PikpakException as e:
        if "File name cannot be repeated" in str(e):
            raise ProviderException(
                "Torrent already in download queue", "torrent_not_downloaded.mp4"
            )
        else:
            raise e

    info_hash_folder_id = response["file"]["id"]
    return info_hash_folder_id


async def add_magnet(
    pikpak: PikPakApi, magnet_link: str, info_hash_folder_id: str
) -> str:
    """Adds a magnet link to the PikPak account and returns the folder_id of the torrent."""
    await pikpak.offline_download(magnet_link, parent_id=info_hash_folder_id)
    return info_hash_folder_id


async def wait_for_torrent_to_complete(
    pikpak: PikPakApi,
    info_hash: str,
    torrent_name: str,
    max_retries: int,
    retry_interval: int,
):
    """Waits for a torrent with the given info_hash to complete downloading."""
    retries = 0
    while retries < max_retries:
        torrent = await check_torrent_status(pikpak, info_hash, torrent_name)
        if torrent is None:
            return  # Torrent was already downloaded
        if torrent and torrent.get("progress") == "100":
            return
        await asyncio.sleep(retry_interval)
        retries += 1

    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


async def get_files_from_folder(pikpak: PikPakApi, folder_id: str) -> list[dict]:
    """Helper function to recursively get files from a folder including subfolders."""
    contents = await pikpak.file_list(parent_id=folder_id)
    files = [item for item in contents["files"] if item["kind"] == "drive#file"]
    subfolders = [item for item in contents["files"] if item["kind"] == "drive#folder"]
    for folder in subfolders:
        files.extend(await get_files_from_folder(pikpak, folder["id"]))
    return files


async def find_file_in_folder_tree(
    pikpak: PikPakApi, folder_id: str, filename: str
) -> dict | None:
    files = await get_files_from_folder(pikpak, folder_id)
    if not files:
        return None

    exact_match = next((f for f in files if f["name"] == filename), None)
    if exact_match:
        return exact_match

    # Fuzzy matching as a fallback
    for file in files:
        file["fuzzy_ratio"] = fuzz.ratio(filename, file["name"])
    selected_file = max(files, key=lambda x: x["fuzzy_ratio"])

    # If the fuzzy ratio is less than 50, then select the largest file
    if selected_file["fuzzy_ratio"] < 50:
        selected_file = max(files, key=lambda x: x["size"])

    if "video" not in selected_file["mime_type"]:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    return selected_file


async def initialize_pikpak(user_data: UserData):
    pikpak = PikPakApi(
        username=user_data.streaming_provider.username,
        password=user_data.streaming_provider.password,
        httpx_client_args={"transport": httpx.AsyncHTTPTransport(retries=3)},
    )
    try:
        await pikpak.login()
    except PikpakException:
        raise ProviderException("Invalid PikPak credentials", "invalid_credentials.mp4")
    return pikpak


async def handle_torrent_status(
    pikpak: PikPakApi,
    info_hash: str,
    torrent_name: str,
    max_retries: int,
    retry_interval: int,
):
    torrent = await check_torrent_status(pikpak, info_hash, torrent_name)
    if torrent and torrent["phase"] == "PHASE_TYPE_ERROR":
        await handle_torrent_error(pikpak, torrent)

    await wait_for_torrent_to_complete(
        pikpak, info_hash, torrent_name, max_retries, retry_interval
    )
    return torrent


async def handle_torrent_error(pikpak: PikPakApi, torrent: dict):
    if torrent["message"] == "Save failed, retry please":
        await pikpak.delete_tasks([torrent["id"]])
    else:
        raise ProviderException(
            f"Error downloading torrent: {torrent['message']}", "transfer_error.mp4"
        )


async def get_or_create_folder(pikpak: PikPakApi, info_hash: str) -> str:
    folder_id = await get_info_hash_folder_id(pikpak, info_hash)
    return folder_id if folder_id else await create_folder(pikpak, info_hash)


async def retrieve_or_download_file(
    pikpak: PikPakApi,
    folder_id: str,
    filename: str,
    magnet_link: str,
    info_hash: str,
    stream: TorrentStreams,
    max_retries: int,
    retry_interval: int,
):
    selected_file = await find_file_in_folder_tree(pikpak, folder_id, filename)
    if not selected_file:
        await free_up_space(pikpak, stream.size)
        await add_magnet(pikpak, magnet_link, folder_id)
        await wait_for_torrent_to_complete(
            pikpak, info_hash, stream.torrent_name, max_retries, retry_interval
        )
        selected_file = await find_file_in_folder_tree(pikpak, folder_id, filename)
    return selected_file


async def free_up_space(pikpak: PikPakApi, required_space):
    """Frees up space in the Seedr account by deleting folders until the required space is available."""
    quota_info = await pikpak.get_quota_info()
    available_space = int(quota_info["quota"]["limit"]) - int(
        quota_info["quota"]["usage"]
    )

    if available_space >= required_space:
        return  # There's enough space, no need to delete anything

    contents = await pikpak.file_list(parent_id="*", size=1000)
    # get trashed files
    trashed_contents = await pikpak.file_list(
        parent_id="*", size=1000, additional_filters={"trashed": {"eq": True}}
    )
    contents["files"].extend(trashed_contents["files"])

    files = sorted(
        contents["files"],
        key=lambda x: (
            x["trashed"] is False,
            -int(x["size"]),
            datetime.strptime(x["created_time"], "%Y-%m-%dT%H:%M:%S.%f%z"),
        ),
    )

    for file in files:
        if available_space >= required_space:
            break
        await pikpak.delete_forever([file["parent_id"], file["id"]])
        available_space += int(file["size"])


async def get_direct_link_from_pikpak(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreams,
    filename: str,
    max_retries=5,
    retry_interval=5,
) -> str:
    pikpak = await initialize_pikpak(user_data)
    await handle_torrent_status(
        pikpak, info_hash, stream.torrent_name, max_retries, retry_interval
    )

    folder_id = await get_or_create_folder(pikpak, info_hash)
    selected_file = await retrieve_or_download_file(
        pikpak,
        folder_id,
        filename or stream.torrent_name,
        magnet_link,
        info_hash,
        stream,
        max_retries,
        retry_interval,
    )

    file_data = await pikpak.get_download_url(selected_file["id"])
    return file_data["web_content_link"]


async def update_pikpak_cache_status(
    streams: list[TorrentStreams], user_data: UserData
):
    """Updates the cache status of streams based on PikPak's instant availability."""
    try:
        pikpak = await initialize_pikpak(user_data)
    except ProviderException:
        return
    tasks = await pikpak.offline_list(phase=["PHASE_TYPE_COMPLETE"])
    for stream in streams:
        stream.cached = any(
            task["name"] == stream.torrent_name or task["name"] == stream.id
            for task in tasks["tasks"]
        )


async def fetch_downloaded_info_hashes_from_pikpak(user_data: UserData) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the PikPak account."""
    try:
        pikpak = await initialize_pikpak(user_data)
    except ProviderException:
        return []

    file_list_content = await pikpak.file_list()
    return [
        file["name"] for file in file_list_content["files"] if file["name"] != "My Pack"
    ]
