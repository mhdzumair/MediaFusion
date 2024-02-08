from typing import Any

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
) -> str:
    torbox_client = Torbox(token=user_data.streaming_provider.token)

    # Check if the torrent already exists
    torrent_info = torbox_client.get_available_torrent(info_hash)
    if torrent_info:
        if (
            torrent_info["download_finished"] is True
            and torrent_info["download_present"] is True
        ):
            file_id = select_file_id_from_torrent(torrent_info, filename)
            response = torbox_client.create_download_link(
                torrent_info.get("id"), file_id
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


def select_file_id_from_torrent(torrent_info: dict[str, Any], filename: str) -> int:
    """Select the file id from the torrent info."""
    for index, file in enumerate(torrent_info["files"]):
        if filename in file["name"]:
            return file["id"]
    raise ProviderException(
        "No matching file available for this torrent", "api_error.mp4"
    )
