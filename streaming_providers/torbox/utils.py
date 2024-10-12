from typing import Any

import PTT

from thefuzz import fuzz

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.torbox.client import Torbox
from streaming_providers.parser import select_file_index_from_torrent


def get_video_url_from_torbox(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    episode: int = None,
    **kwargs,
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
                torrent_info.get("id"),
                file_id,
            )
            return response["data"]
    else:
        # If torrent doesn't exist, add it
        response = torbox_client.add_magnet_link(magnet_link)
        # Response detail has "Found Cached Torrent. Using Cached Torrent." if it's a cached torrent,
        # create download link from it directly in the same call.
        if "Found Cached" in response.get("detail"):
            torrent_info = torbox_client.get_available_torrent(info_hash)
            if torrent_info:
                file_id = select_file_id_from_torrent(torrent_info, filename, episode)
                response = torbox_client.create_download_link(
                    torrent_info.get("id"),
                    file_id,
                )
                return response["data"]

    raise ProviderException(
        f"Torrent did not reach downloaded status.",
        "torrent_not_downloaded.mp4",
    )


def update_torbox_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
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


def fetch_downloaded_info_hashes_from_torbox(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Torbox account."""
    try:
        torbox_client = Torbox(token=user_data.streaming_provider.token)
        available_torrents = torbox_client.get_user_torrent_list()
        if not available_torrents.get("data"):
            return []
        return [torrent["hash"] for torrent in available_torrents["data"]]

    except ProviderException:
        return []


def select_file_id_from_torrent(
    torrent_info: dict[str, Any], filename: str, episode: int
) -> int:
    """Select the file id from the torrent info."""
    file_index = select_file_index_from_torrent(
        torrent_info, filename, None, episode, name_key="short_name",
    )
    return torrent_info["files"][file_index]["id"]


def delete_all_torrents_from_torbox(user_data: UserData, **kwargs):
    """Deletes all torrents from the Torbox account."""
    torbox_client = Torbox(token=user_data.streaming_provider.token)
    torrents = torbox_client.get_user_torrent_list().get("data")
    if not torrents:
        return
    for torrent in torrents:
        torbox_client.delete_torrent(torrent.get("id"))
