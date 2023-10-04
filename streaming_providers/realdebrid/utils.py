import time

from db.models import Streams, Episode
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.realdebrid.client import RealDebrid


def check_existing_torrent(
    rd_client, info_hash: str, filename: str, max_retries: int, retry_interval: int
) -> dict | None:
    """Check if the torrent is already in torrent list and return the direct link if available."""
    retries = 0
    torrent_info = rd_client.get_available_torrent(info_hash, filename)
    if not torrent_info:
        return None
    torrent_id = torrent_info.get("id")
    while retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == "downloaded":
            response = rd_client.create_download_link(torrent_info["links"][0])
            return response
        elif torrent_info["status"] == "waiting_files_selection":
            return torrent_info
        elif torrent_info["status"] == "magnet_error":
            raise ProviderException(
                "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
            )
        time.sleep(retry_interval)
        retries += 1
    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


def wait_for_file_selection(
    rd_client, torrent_id: str, max_retries: int, retry_interval: int
):
    """Wait for the torrent status to be 'waiting_files_selection'."""
    retries = 0
    while retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == "waiting_files_selection":
            return
        time.sleep(retry_interval)
        retries += 1
    raise ProviderException(
        "Not enough seeders available for parse magnet link",
        "torrent_not_downloaded.mp4",
    )


def wait_for_torrent_download(
    rd_client, torrent_id: str, max_retries: int, retry_interval: int
) -> str:
    """Wait for the torrent to be downloaded and return the direct link."""
    retries = 0
    while retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == "downloaded":
            response = rd_client.create_download_link(torrent_info["links"][0])
            return response.get("download")
        time.sleep(retry_interval)
        retries += 1
    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


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

    response_data = check_existing_torrent(
        rd_client, info_hash, filename, max_retries, retry_interval
    )
    if response_data and "download" in response_data:
        return response_data.get("download")

    # Convert a magnet link to a Real-Debrid torrent
    if response_data is None:
        response_data = rd_client.add_magent_link(magnet_link)
    if "id" not in response_data:
        raise ProviderException(
            "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
        )

    torrent_id = response_data["id"]
    wait_for_file_selection(rd_client, torrent_id, max_retries, retry_interval)

    # Retrieve torrent information
    torrent_info = rd_client.get_torrent_info(torrent_id)
    if not torrent_info["files"]:
        raise ProviderException(
            "No files available for this torrent", "transfer_error.mp4"
        )

    # select the file to download
    selected_file = [
        file for file in torrent_info["files"] if file["path"] == filename
    ][0]

    file_id = selected_file["id"]
    rd_client.start_torrent_download(torrent_id, file_id)

    return wait_for_torrent_download(rd_client, torrent_id, max_retries, retry_interval)
