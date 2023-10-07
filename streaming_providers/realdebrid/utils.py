import time
from typing import Any

from db.models import Streams, Episode
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.realdebrid.client import RealDebrid


def wait_for_status(
    rd_client,
    torrent_id: str,
    target_status: str,
    max_retries: int,
    retry_interval: int,
):
    """Wait for the torrent to reach a particular status."""
    retries = 0
    while retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == target_status:
            return torrent_info
        time.sleep(retry_interval)
        retries += 1
    raise ProviderException(
        f"Torrent did not reach {target_status} status.", "torrent_not_downloaded.mp4"
    )


def get_direct_link_from_realdebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: Streams,
    episode_data: Episode = None,
    max_retries=5,
    retry_interval=5,
) -> str:
    rd_client = RealDebrid(encoded_token=user_data.streaming_provider.token)
    filename = episode_data.filename if episode_data else stream.filename

    # Check if the torrent already exists
    torrent_info = rd_client.get_available_torrent(info_hash)
    if torrent_info:
        torrent_id = torrent_info.get("id")
        if torrent_info["status"] == "downloaded":
            torrent_info = rd_client.get_torrent_info(torrent_id)
            file_index = select_file_index_from_torrent(torrent_info, filename)
            response = rd_client.create_download_link(torrent_info["links"][file_index])
            return response.get("download")
        elif torrent_info["status"] == "magnet_error":
            rd_client.delete_torrent(torrent_id)
            raise ProviderException(
                "Not enough seeders available for parse magnet link",
                "torrent_not_downloaded.mp4",
            )
    else:
        # If torrent doesn't exist, add it
        response_data = rd_client.add_magent_link(magnet_link)
        if "id" not in response_data:
            raise ProviderException(
                "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
            )
        torrent_id = response_data["id"]

    # Wait for file selection and then start torrent download
    torrent_info = wait_for_status(
        rd_client, torrent_id, "waiting_files_selection", max_retries, retry_interval
    )
    if torrent_info["status"] == "magnet_error":
        rd_client.delete_torrent(torrent_id)
        raise ProviderException(
            "Not enough seeders available for parse magnet link",
            "torrent_not_downloaded.mp4",
        )
    rd_client.start_torrent_download(torrent_id)

    # Wait for download completion and get the direct link
    torrent_info = wait_for_status(
        rd_client, torrent_id, "downloaded", max_retries, retry_interval
    )
    file_index = select_file_index_from_torrent(torrent_info, filename)
    response = rd_client.create_download_link(torrent_info["links"][file_index])

    return response.get("download")


def order_streams_by_instant_availability_and_date(
    streams: list[Streams], user_data: UserData
) -> list[Streams]:
    """Orders the streams by instant availability."""
    rd_client = RealDebrid(encoded_token=user_data.streaming_provider.token)
    for stream in streams:
        try:
            stream.cached = bool(
                rd_client.get_torrent_instant_availability(stream.id)[stream.id]
            )
        except ProviderException:
            return streams

    return sorted(
        streams,
        key=lambda x: (
            x.cached,
            x.created_at,
        ),
        reverse=True,
    )


def select_file_index_from_torrent(torrent_info: dict[str, Any], filename: str) -> int:
    """Select the file index from the torrent info."""
    selected_files = [file for file in torrent_info["files"] if file["selected"] == 1]
    for index, file in enumerate(selected_files):
        if file["path"] == "/" + filename:
            return index
    raise ProviderException(
        "No matching file available for this torrent", "api_error.mp4"
    )
