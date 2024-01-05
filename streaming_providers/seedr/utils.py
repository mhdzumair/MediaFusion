import re
import time
from datetime import datetime

from seedrcc import Seedr
from thefuzz import fuzz

from db.models import Streams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException


def get_info_hash_folder_id(seedr, info_hash: str):
    """Gets the folder_id of a folder with a given info_hash."""
    folder_content = seedr.listContents()
    info_hash_folder = next(
        (f for f in folder_content["folders"] if f["name"] == info_hash), None
    )
    if info_hash_folder:
        return info_hash_folder["id"]


def get_folder_content(seedr, info_hash: str, sub_content_key: str):
    """Gets the folder content based on the info_hash and sub_content_key."""
    if info_hash_folder_id := get_info_hash_folder_id(seedr, info_hash):
        sub_folder_content = seedr.listContents(info_hash_folder_id)
        if sub_folder_content[sub_content_key]:
            return sub_folder_content[sub_content_key][0]


def check_torrent_status(seedr, info_hash: str):
    """Checks if a torrent with a given info_hash is currently downloading."""
    return get_folder_content(seedr, info_hash, "torrents")


def check_folder_status(seedr, info_hash: str) -> dict | None:
    """Checks if a torrent with a given folder_name has completed downloading."""
    return get_folder_content(seedr, info_hash, "folders")


def add_magnet_and_get_torrent(seedr, magnet_link: str, info_hash: str) -> None:
    """Adds a magnet link to Seedr and returns the corresponding torrent."""
    seedr.addFolder(info_hash)
    info_hash_folder_id = get_info_hash_folder_id(seedr, info_hash)
    transfer = seedr.addTorrent(magnet_link, folderId=info_hash_folder_id)

    if transfer["result"] is True:
        return
    elif transfer["result"] in (
        "not_enough_space_added_to_wishlist",
        "not_enough_space_wishlist_full",
    ):
        raise ProviderException(
            "Not enough space in Seedr account to add this torrent",
            "not_enough_space.mp4",
        )
    elif transfer["result"] == "queue_full_added_to_wishlist":
        raise ProviderException(
            "Seedr queue is full, remove queued torrents and try again",
            "queue_full.mp4",
        )

    raise ProviderException(
        "Error transferring magnet link to Seedr", "transfer_error.mp4"
    )


def wait_for_torrent_to_complete(
    seedr, info_hash: str, max_retries: int, retry_interval: int
):
    """Waits for a torrent with the given info_hash to complete downloading."""
    retries = 0
    while retries < max_retries:
        torrent = check_torrent_status(seedr, info_hash)
        if torrent is None:
            return  # Torrent was already downloaded
        if torrent and torrent.get("progress") == "100":
            return
        time.sleep(retry_interval)
        retries += 1

    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


def get_file_details_from_folder(seedr, folder_id: int, filename: str):
    """Gets the details of the file in a given folder."""
    folder_content = seedr.listContents(folder_id)
    exact_match = [f for f in folder_content["files"] if f["name"] == filename]
    if exact_match:
        return exact_match[0]

    # If the file is not found with exact match, try to find it by fuzzy ratio
    for file in folder_content["files"]:
        file["fuzzy_ratio"] = fuzz.ratio(filename, file["name"])
    return sorted(
        folder_content["files"], key=lambda x: x["fuzzy_ratio"], reverse=True
    )[0]


def seedr_clean_name(name: str, replace: str = " ") -> str:
    # Only allow alphanumeric characters, spaces, and `.,;:_~-[]()`
    cleaned_name = re.sub(r"[^a-zA-Z0-9 .,;:_~\-()\[\]]", replace, name)
    return cleaned_name


def get_seedr_client(user_data: UserData) -> Seedr:
    """Returns a Seedr client with the user's token."""
    try:
        seedr = Seedr(token=user_data.streaming_provider.token)
    except Exception:
        raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
    response = seedr.testToken()
    if "error" in response:
        raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
    return seedr


async def get_direct_link_from_seedr(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: Streams,
    filename: str,
    max_retries=5,
    retry_interval=5,
) -> str:
    """Gets a direct download link from Seedr using a magnet link and token."""
    seedr = get_seedr_client(user_data)

    # Check for existing torrent or folder
    torrent = check_torrent_status(seedr, info_hash)
    if torrent:
        wait_for_torrent_to_complete(seedr, info_hash, max_retries, retry_interval)
    folder = check_folder_status(seedr, info_hash)

    # Handle the torrent based on its status or if it's already in a folder
    if not folder:
        free_up_space(seedr, stream.size)
        add_magnet_and_get_torrent(seedr, magnet_link, info_hash)
        wait_for_torrent_to_complete(seedr, info_hash, max_retries, retry_interval)
        folder = check_folder_status(seedr, info_hash)
    folder_id = folder["id"]

    selected_file = get_file_details_from_folder(
        seedr,
        folder_id,
        seedr_clean_name(filename, ""),
    )
    video_link = seedr.fetchFile(selected_file["folder_file_id"])["url"]

    return video_link


def free_up_space(seedr, required_space):
    """Frees up space in the Seedr account by deleting folders until the required space is available."""
    contents = seedr.listContents()
    available_space = contents["space_max"] - contents["space_used"]

    if available_space >= required_space:
        return  # There's enough space, no need to delete anything

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
        # delete sub folder torrents and folders
        sub_folder_content = seedr.listContents(folder["id"])
        for sub_folder in sub_folder_content["folders"]:
            seedr.deleteFolder(sub_folder["id"])
        for sub_folder_torrent in sub_folder_content["torrents"]:
            seedr.deleteTorrent(sub_folder_torrent["id"])
        seedr.deleteFolder(folder["id"])
        available_space += folder["size"]


def update_seedr_cache_status(streams: list[Streams], user_data: UserData):
    """Updates the cache status of the streams based on the user's Seedr account."""
    try:
        seedr = get_seedr_client(user_data)
    except ProviderException:
        return

    folder_content = {
        folder["name"]: folder["id"] for folder in seedr.listContents()["folders"]
    }

    for stream in streams:
        if stream.id in folder_content:
            # check if folder is not empty
            sub_folder_content = seedr.listContents(folder_content[stream.id])
            if sub_folder_content["folders"]:
                stream.cached = True
                continue
        stream.cached = False


def fetch_downloaded_info_hashes_from_seedr(user_data: UserData) -> list[str]:
    """Fetches the info_hashes of all the torrents downloaded in the user's Seedr account."""
    try:
        seedr = get_seedr_client(user_data)
    except ProviderException:
        return []

    return [folder["name"] for folder in seedr.listContents()["folders"]]
