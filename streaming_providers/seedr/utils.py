import time
from seedrcc import Seedr

from streaming_providers.exceptions import ProviderException
from utils.parser import select_episode_file


def check_torrent_status(seedr, info_hash: str):
    """Checks if a torrent with a given info_hash is currently downloading."""
    folder_content = seedr.listContents()
    torrents = folder_content.get("torrents", [])
    return next((t for t in torrents if t["hash"] == info_hash), None)


def check_folder_status(seedr, folder_name: str):
    """Checks if a torrent with a given folder_name has completed downloading."""
    folder_content = seedr.listContents()
    folders = folder_content.get("folders", [])
    return next((f for f in folders if f["name"] == folder_name), None)


def add_magnet_and_get_torrent(seedr, magnet_link: str, info_hash: str):
    """Adds a magnet link to Seedr and returns the corresponding torrent."""
    transfer = seedr.addTorrent(magnet_link)

    # Handle potential errors from Seedr response
    if "error" in transfer:
        if transfer["error"] == "invalid_token":
            raise ProviderException("Invalid Seedr token", "invalid_token.mp4")
        raise ProviderException("Error transferring magnet link to Seedr", "transfer_error.mp4")

    # Return the appropriate torrent name based on the transfer result
    if transfer["result"] is True and "title" in transfer:
        return transfer["title"]
    elif transfer["result"] is True:
        torrent = check_torrent_status(seedr, info_hash)
        if torrent:
            return torrent["name"]
    elif transfer["result"] in ("not_enough_space_added_to_wishlist", "not_enough_space_wishlist_full"):
        raise ProviderException("Not enough space in Seedr account to add this torrent", "not_enough_space.mp4")

    raise ProviderException("Error transferring magnet link to Seedr", "transfer_error.mp4")


def wait_for_torrent_to_complete(seedr, info_hash: str, max_retries: int, retry_interval: int):
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


def get_file_details_from_folder(seedr, folder_id: int, episode: int | None):
    """Gets the details of the largest file in a given folder."""
    folder_content = seedr.listContents(folder_id)
    if episode:
        return select_episode_file(folder_content["files"], episode, "name")
    return max(folder_content["files"], key=lambda x: x["size"])


def get_direct_link_from_seedr(
    info_hash: str,
    magnet_link: str,
    token: str,
    torrent_name: str,
    episode: str | None,
    max_retries=5,
    retry_interval=5,
) -> str:
    """Gets a direct download link from Seedr using a magnet link and token."""
    seedr = Seedr(token=token)

    # Check for existing torrent or folder
    torrent = check_torrent_status(seedr, info_hash)
    folder = check_folder_status(seedr, torrent_name)

    # Handle the torrent based on its status or if it's already in a folder
    if folder:
        folder_id = folder["id"]
    else:
        if torrent:
            folder_title = torrent["name"]
        else:
            folder_title = add_magnet_and_get_torrent(seedr, magnet_link, info_hash)
        folder = check_folder_status(seedr, folder_title)
        if not folder:
            wait_for_torrent_to_complete(seedr, info_hash, max_retries, retry_interval)
            folder = check_folder_status(seedr, folder_title)
        folder_id = folder["id"]
        if torrent_name != folder_title:
            seedr.renameFolder(folder_id, torrent_name)

    selected_file = get_file_details_from_folder(seedr, folder_id, episode)
    video_link = seedr.fetchFile(selected_file["folder_file_id"])["url"]

    return video_link
