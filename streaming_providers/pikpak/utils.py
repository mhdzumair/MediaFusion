import asyncio
import logging
from datetime import datetime

import aiohttp
import httpx
from pikpakapi import PikPakApi, PikpakException

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from utils import crypto
from utils.runtime_const import REDIS_ASYNC_CLIENT


async def get_torrent_file_by_info_hash(
    pikpak: PikPakApi, my_pack_folder_id: str, info_hash: str
):
    """Gets the folder_id of a folder with a given info_hash in the root directory."""
    files_list_content = await pikpak.file_list(parent_id=my_pack_folder_id)
    info_hash_file = next(
        (
            f
            for f in files_list_content["files"]
            if info_hash in f.get("params", {}).get("url", "")
        ),
        None,
    )
    return info_hash_file


async def get_info_hash_folder_id(pikpak: PikPakApi, info_hash: str):
    """Gets the folder_id of a folder with a given info_hash in the root directory."""
    files_list_content = await pikpak.file_list()
    info_hash_file = next(
        (f for f in files_list_content["files"] if f["name"] == info_hash), None
    )
    return info_hash_file["id"] if info_hash_file else None


async def check_torrent_status(pikpak: PikPakApi, info_hash: str) -> dict | None:
    """Checks the status of a torrent with a given info_hash or torrent_name."""
    try:
        available_task = await pikpak.offline_list()
    except PikpakException:
        raise ProviderException("Invalid PikPak token", "invalid_token.mp4")
    for task in available_task["tasks"]:
        magnet_link = task.get("params", {}).get("url", "")
        if info_hash in magnet_link:
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


async def add_magnet(pikpak: PikPakApi, magnet_link: str):
    """Adds a magnet link to the PikPak account and returns the folder_id of the torrent."""
    try:
        await pikpak.offline_download(magnet_link)
    except PikpakException as e:
        match str(e):
            case "You have reached the limits of free usage today":
                raise ProviderException(
                    "Daily download limit reached", "daily_download_limit.mp4"
                )
            case "Storage space is not enough":
                raise ProviderException(
                    "Not enough storage space available", "not_enough_space.mp4"
                )
            case _:
                raise ProviderException(
                    f"Failed to add magnet link to PikPak: {e}", "not_enough_space.mp4"
                )


async def wait_for_torrent_to_complete(
    pikpak: PikPakApi,
    info_hash: str,
    max_retries: int,
    retry_interval: int,
):
    """Waits for a torrent with the given info_hash to complete downloading."""
    retries = 0
    while retries < max_retries:
        torrent = await check_torrent_status(pikpak, info_hash)
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
    pikpak: PikPakApi,
    my_pack_folder_id: str,
    info_hash: str,
    filename: str,
    file_index: int,
    episode: int | None,
) -> dict | None:
    torrent_file = await get_torrent_file_by_info_hash(
        pikpak, my_pack_folder_id, info_hash
    )
    if not torrent_file:
        return None

    if torrent_file["kind"] == "drive#file":
        files = [torrent_file]
    else:
        files = await get_files_from_folder(pikpak, torrent_file["id"])

    file_index = select_file_index_from_torrent(
        {"files": files}, filename, file_index, episode
    )
    return files[file_index]


async def initialize_pikpak(user_data: UserData):
    cache_key = f"pikpak:{crypto.get_text_hash(user_data.streaming_provider.email + user_data.streaming_provider.password, full_hash=True)}"
    if pikpak_encrypted_token := await REDIS_ASYNC_CLIENT.get(cache_key):
        pikpak_encoded_token = crypto.decrypt_text(
            pikpak_encrypted_token, user_data.streaming_provider.password
        )
        pikpak = PikPakApi(
            encoded_token=pikpak_encoded_token,
            httpx_client_args={
                "transport": httpx.AsyncHTTPTransport(retries=3),
                "timeout": 10,
            },
            token_refresh_callback=store_pikpak_token_in_cache,
            token_refresh_callback_kwargs={"user_data": user_data},
        )
        return pikpak

    pikpak = PikPakApi(
        username=user_data.streaming_provider.email,
        password=user_data.streaming_provider.password,
        httpx_client_args={
            "transport": httpx.AsyncHTTPTransport(retries=3),
            "timeout": 10,
        },
        token_refresh_callback=store_pikpak_token_in_cache,
        token_refresh_callback_kwargs={"user_data": user_data},
    )

    try:
        await pikpak.login()
    except PikpakException as error:
        if "Invalid username or password" == str(error):
            raise ProviderException(
                "Invalid PikPak credentials", "invalid_credentials.mp4"
            )
        logging.error(f"Failed to connect to PikPak: {error}")
        raise ProviderException(
            "Failed to connect to PikPak. Please try again later.",
            "debrid_service_down_error.mp4",
        )
    except (aiohttp.ClientError, httpx.ReadTimeout):
        raise ProviderException(
            "Failed to connect to PikPak. Please try again later.",
            "debrid_service_down_error.mp4",
        )

    await store_pikpak_token_in_cache(pikpak, user_data)
    return pikpak


async def store_pikpak_token_in_cache(pikpak: PikPakApi, user_data: UserData):
    cache_key = f"pikpak:{crypto.get_text_hash(user_data.streaming_provider.email + user_data.streaming_provider.password, full_hash=True)}"
    await REDIS_ASYNC_CLIENT.set(
        cache_key,
        crypto.encrypt_text(
            pikpak.encoded_token, user_data.streaming_provider.password
        ),
        ex=7 * 24 * 60 * 60,  # Store for 7 days
    )


async def handle_torrent_status(
    pikpak: PikPakApi,
    info_hash: str,
    max_retries: int,
    retry_interval: int,
):
    torrent = await check_torrent_status(pikpak, info_hash)
    if not torrent:
        return

    if torrent["phase"] == "PHASE_TYPE_ERROR":
        await handle_torrent_error(pikpak, torrent)
    await wait_for_torrent_to_complete(pikpak, info_hash, max_retries, retry_interval)


async def handle_torrent_error(pikpak: PikPakApi, torrent: dict):
    match torrent["message"]:
        case "Save failed, retry please":
            await pikpak.delete_tasks([torrent["id"]])
        case "Storage space is not enough":
            try:
                await pikpak.delete_tasks([torrent["id"]])
            except PikpakException:
                pass
            raise ProviderException(
                "Not enough storage space available", "not_enough_space.mp4"
            )
        case "You have reached the limits of free usage today":
            try:
                await pikpak.offline_task_retry(torrent["id"])
            except PikpakException:
                raise ProviderException(
                    "Daily download limit reached", "daily_download_limit.mp4"
                )
        case _:
            raise ProviderException(
                f"Error downloading torrent: {torrent['message']}", "transfer_error.mp4"
            )


async def get_my_pack_folder_id(pikpak: PikPakApi) -> str:
    """Gets the folder_id of the 'My Pack' folder in the PikPak account."""
    files_list_content = await pikpak.file_list()
    my_pack_folder = next(
        (f for f in files_list_content["files"] if f["name"] == "My Pack"), None
    )
    if not my_pack_folder:
        raise ProviderException("My Pack folder not found", "api_error.mp4")
    return my_pack_folder["id"]


async def retrieve_or_download_file(
    pikpak: PikPakApi,
    my_pack_folder_id: str,
    filename: str,
    magnet_link: str,
    info_hash: str,
    stream: TorrentStreams,
    episode: int | None,
    max_retries: int,
    retry_interval: int,
):
    selected_file = await find_file_in_folder_tree(
        pikpak, my_pack_folder_id, info_hash, filename, stream.file_index, episode
    )
    if not selected_file:
        await free_up_space(pikpak, stream.size)
        await add_magnet(pikpak, magnet_link)
        await wait_for_torrent_to_complete(
            pikpak, info_hash, max_retries, retry_interval
        )
        selected_file = await find_file_in_folder_tree(
            pikpak, my_pack_folder_id, info_hash, filename, stream.file_index, episode
        )
        if selected_file is None:
            raise ProviderException(
                "Torrent not downloaded yet.", "torrent_not_downloaded.mp4"
            )
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


async def get_video_url_from_pikpak(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreams,
    filename: str,
    episode: int | None,
    max_retries=1,
    retry_interval=0,
    **kwargs,
) -> str:
    pikpak = await initialize_pikpak(user_data)
    await handle_torrent_status(pikpak, info_hash, max_retries, retry_interval)

    my_pack_folder_id = await get_my_pack_folder_id(pikpak)
    selected_file = await retrieve_or_download_file(
        pikpak,
        my_pack_folder_id,
        filename,
        magnet_link,
        info_hash,
        stream,
        episode,
        max_retries,
        retry_interval,
    )

    file_data = await pikpak.get_download_url(selected_file["id"])

    # if file_data.get("medias"):
    #     return file_data["medias"][0]["link"]["url"]

    return file_data["web_content_link"]


async def update_pikpak_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on PikPak's instant availability."""
    try:
        pikpak = await initialize_pikpak(user_data)
    except ProviderException:
        return
    tasks = await pikpak.offline_list(phase=["PHASE_TYPE_COMPLETE"])
    for stream in streams:
        stream.cached = any(
            stream.id in task.get("params", {}).get("url", "")
            for task in tasks["tasks"]
        )


async def fetch_downloaded_info_hashes_from_pikpak(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the PikPak account."""
    try:
        pikpak = await initialize_pikpak(user_data)
    except ProviderException:
        return []

    my_pack_folder_id = await get_my_pack_folder_id(pikpak)
    file_list_content = await pikpak.file_list(parent_id=my_pack_folder_id)

    def _parse_info_hash_from_magnet(magnet_link: str) -> str:
        return magnet_link.split(":")[-1]

    return [
        _parse_info_hash_from_magnet(file.get("params", {}).get("url"))
        for file in file_list_content["files"]
        if file.get("params", {}).get("url", "").startswith("magnet:")
    ]


async def delete_all_torrents_from_pikpak(user_data: UserData, **kwargs):
    """Deletes all torrents from the PikPak account."""
    try:
        pikpak = await initialize_pikpak(user_data)
    except ProviderException:
        return

    my_pack_folder_id = await get_my_pack_folder_id(pikpak)
    file_list_content = await pikpak.file_list(parent_id=my_pack_folder_id)
    file_ids = [file["id"] for file in file_list_content["files"]]
    await pikpak.delete_forever(file_ids)
