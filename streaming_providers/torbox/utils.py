from typing import Any

import PTN

from thefuzz import fuzz

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.torbox.client import Torbox


def get_direct_link_from_torbox(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    max_retries=5,
    retry_interval=5,
    episode: int = None,
) -> str:
    torbox_client = Torbox(token=user_data.streaming_provider.token)

    # Check if the torrent already exists
    torrent_info = torbox_client.get_available_torrent(info_hash)
    if torrent_info:
        if (
            torrent_info["download_finished"] is True
            and torrent_info["download_present"] is True
        ):
            file_id = select_file_id_from_torrent(torrent_info, filename, episode)
            response = torbox_client.create_download_link(
                torrent_info.get("id"), file_id,
            )
            return response["data"]
    else:
        # If torrent doesn't exist, add it
        torbox_client.add_magnet_link(magnet_link)

    # Do not wait for download completion, just let the user retry again.
    raise ProviderException(
        f"Torrent did not reach downloaded status.",
        "torrent_not_downloaded.mp4",
    )


def update_torbox_cache_status(streams: list[TorrentStreams], user_data: UserData):
    """Updates the cache status of streams based on Torbox's instant availability."""

    try:
        torbox_client = Torbox(token=user_data.streaming_provider.token)
        instant_availability_data = torbox_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream in streams:
            stream.cached = bool(stream.id in instant_availability_data)
    except ProviderException:
        pass


def fetch_downloaded_info_hashes_from_torbox(user_data: UserData) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Torbox account."""
    try:
        torbox_client = Torbox(token=user_data.streaming_provider.token)
        available_torrents = torbox_client.get_user_torrent_list()
        if not available_torrents.get("data"):
            return []
        return [torrent["hash"] for torrent in available_torrents["data"]]

    except ProviderException:
        return []


def select_file_id_from_torrent(torrent_info: dict[str, Any], filename: str, episode: int) -> int:
    """Select the file id from the torrent info."""
    files = torrent_info["files"]
    exact_match = next((f for f in files if filename in f["name"]), None)
    if exact_match:
        return exact_match["id"]

    # Fuzzy matching as a fallback
    for file in files:
        file["fuzzy_ratio"] = fuzz.ratio(filename, file["name"])
    selected_file = max(files, key=lambda x: x["fuzzy_ratio"])

    # If the fuzzy ratio is less than 50, then select the largest file
    if selected_file["fuzzy_ratio"] < 50:
        selected_file = max(files, key=lambda x: x["size"])

    if episode:
        # Select the file with the matching episode number
        for file in files:
            if PTN.parse(file["name"]).get("episode") == episode:
                return file["id"]

    if "video" not in selected_file["mime_type"]:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    return selected_file["id"]


def delete_all_torrents_from_torbox(user_data: UserData):
    """Deletes all torrents from the Torbox account."""
    torbox_client = Torbox(token=user_data.streaming_provider.token)
    torrents = torbox_client.get_user_torrent_list().get("data")
    if not torrents:
        return
    for torrent in torrents:
        torbox_client.delete_torrent(torrent.get("id"))