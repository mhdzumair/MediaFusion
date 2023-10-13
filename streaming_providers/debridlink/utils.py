import time
from typing import Any

from db.models import Streams, Episode
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.debridlink.client import DebridLink

import PTN


def get_direct_link_from_debridlink(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: Streams,
    episode_data: Episode = None,
    max_retries=5,
    retry_interval=5,
) -> str:
    dl_client = DebridLink(encoded_token=user_data.streaming_provider.token)
    filename = episode_data.filename if episode_data else stream.filename

    # Check if the torrent already exists
    downloadUrl = check_existing_torrent(dl_client, info_hash, episode_data, max_retries, retry_interval)
    if downloadUrl:
        return downloadUrl

    # If torrent doesn't exist, add it
    response_data = dl_client.add_magent_link(magnet_link)
    if "error" in response_data:
        raise ProviderException("Failed to add magnet link to Debrid-Link", "transfer_error.mp4")

    torrent_id = response_data["value"]["id"]

    return wait_for_torrent_download(dl_client, torrent_id, filename, max_retries, retry_interval)


def check_existing_torrent(
    dl_client: DebridLink, info_hash: str, episode_data: Episode | None, max_retries: int, retry_interval: int
) -> str:
    """Check if the torrent is already in torrent list and return the direct link if available."""
    retries = 0

    torrent_info = dl_client.get_available_torrent(info_hash)
    if not torrent_info:
        return None

    torrent_id = torrent_info.get("id")
    while retries < max_retries:
        torrent_info_response = dl_client.get_torrent_info(torrent_id)
        if not torrent_info_response["success"] and not torrent_info_response["value"]:
            raise ProviderException("Failed to get torrent info from Debrid-Link", "transfer_error.mp4")

        torrent_info = torrent_info_response["value"][0]
        if torrent_info["downloadPercent"] == 100:
            return get_direct_link(torrent_info, episode_data)

        time.sleep(retry_interval)
        retries += 1
    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


def wait_for_torrent_download(dl_client, torrent_id: str, episode_data: Episode | None, max_retries: int, retry_interval: int) -> str:
    """Wait for the torrent to be downloaded and return the direct link."""
    retries = 0
    while retries < max_retries:
        torrent_info_response = dl_client.get_torrent_info(torrent_id)

        if not torrent_info_response["success"] and not torrent_info_response["value"]:
            raise ProviderException("Failed to get torrent info from Debrid-Link", "transfer_error.mp4")

        torrent_info = torrent_info_response["value"][0]
        if not torrent_info["files"]:
            raise ProviderException("No files available for this torrent", "transfer_error.mp4")

        if torrent_info["downloadPercent"] == 100:
            return get_direct_link(torrent_info, episode_data)

        time.sleep(retry_interval)
        retries += 1
    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


def get_direct_link(torrent_info, episode_data: Episode | None) -> str:
    if episode_data:
        selected_file = select_episode_file(torrent_info["files"], episode_data.episode_number, "name")
    else:
        # Otherwise, select the largest file for download
        selected_file = max(torrent_info["files"], key=lambda x: x["size"])
    return selected_file["downloadUrl"]


def select_episode_file(torrent_files: list, episode: int, file_name_key: str) -> dict:
    """Select the file with the specified episode number."""

    for file in torrent_files:
        torrent_data = PTN.parse(file[file_name_key])
        file_episode = torrent_data.get("episode")
        if file_episode and int(file_episode) == episode:
            return file
    else:
        raise ProviderException(f"Episode {episode} not found in this torrent", "episode_not_found.mp4")