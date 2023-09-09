import logging

import time
from seedrcc import Seedr

from streaming_providers.exceptions import ProviderException


def check_torrent_status(seedr, torrent_name: str):
    folder_content = seedr.listContents()
    torrents = folder_content.get("torrents", [])
    return next((t for t in torrents if t["name"] == torrent_name), None)


def check_folder_status(seedr, folder_name: str):
    folder_content = seedr.listContents()
    folders = folder_content.get("folders", [])
    return next((f for f in folders if f["name"] == folder_name), None)


def add_magnet_and_get_torrent(seedr, magnet_link: str):
    transfer = seedr.addTorrent(magnet_link)
    if transfer["result"] is True and "title" in transfer:
        title = transfer["title"]
        return title, check_torrent_status(seedr, title)
    elif transfer["result"] is True:
        folder_content = seedr.listContents()
        torrents = folder_content.get("torrents", [])
        if torrents:
            return torrents[0]["name"], torrents[0]
    elif transfer["result"] in ("not_enough_space_added_to_wishlist", "not_enough_space_wishlist_full"):
        raise ProviderException("Not enough space in Seedr account to add this torrent", "not_enough_space.mp4")
    raise ProviderException("Error transferring magnet link to Seedr", "transfer_error.mp4")


def wait_for_torrent_to_complete(seedr, torrent_name: str, max_retries: int, retry_interval: int):
    retries = 0
    torrent = check_torrent_status(seedr, torrent_name)
    while retries < max_retries and torrent and torrent.get("progress") != "100":
        time.sleep(retry_interval)
        torrent = check_torrent_status(seedr, torrent_name)
        retries += 1
    if retries == max_retries:
        raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")
    return torrent


def get_largest_file_from_folder(seedr, folder_id: int):
    folder_content = seedr.listContents(folder_id)
    largest_file = max(folder_content["files"], key=lambda x: x["size"])
    return largest_file


def get_direct_link_from_seedr(magnet_link: str, token: str, torrent_name: str, max_retries=5, retry_interval=8) -> str:
    seedr = Seedr(token=token)
    logging.debug("Adding magnet link to Seedr: %s torrent_name: %s", magnet_link, torrent_name)

    # Check if the torrent is already in the list or being downloaded
    torrent = check_torrent_status(seedr, torrent_name)

    # Check if the torrent has already been completed and is in the folders list
    folder = check_folder_status(seedr, torrent_name)

    # If the torrent is neither being downloaded nor completed, add the magnet link
    if not torrent and not folder:
        folder_title, _ = add_magnet_and_get_torrent(seedr, magnet_link)
        # Wait for the torrent to complete downloading
        wait_for_torrent_to_complete(seedr, folder_title, max_retries, retry_interval)
        folder_id = check_folder_status(seedr, folder_title)["id"]
        if torrent_name != folder_title:
            seedr.renameFolder(folder_id, torrent_name)
    else:
        folder_id = folder["id"]

    # Get the largest file from the desired folder
    largest_file = get_largest_file_from_folder(seedr, folder_id)
    file_id = largest_file["folder_file_id"]

    # Fetch the direct link to the file
    video_link = seedr.fetchFile(file_id)["url"]
    return video_link
