import time

from streaming_providers.exceptions import ProviderException
from streaming_providers.realdebrid.client import RealDebrid


def check_existing_torrent(rd_client, info_hash: str, max_retries: int, retry_interval: int) -> str | None:
    """Check if the torrent is already in torrent list and return the direct link if available."""
    retries = 0
    torrent_info = rd_client.get_available_torrent(info_hash)
    torrent_id = torrent_info.get("id") if torrent_info else None
    while torrent_id and retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == "downloaded":
            response = rd_client.create_download_link(torrent_info["links"][0])
            return response.get("download")
        if torrent_info["status"] == "magnet_error":
            raise ProviderException("Failed to add magnet link to Real-Debrid", "transfer_error.mp4")
        time.sleep(retry_interval)
        retries += 1
    return None


def wait_for_file_selection(rd_client, torrent_id: str, max_retries: int, retry_interval: int):
    """Wait for the torrent status to be 'waiting_files_selection'."""
    retries = 0
    while retries < max_retries:
        torrent_info = rd_client.get_torrent_info(torrent_id)
        if torrent_info["status"] == "waiting_files_selection":
            return
        time.sleep(retry_interval)
        retries += 1
    raise ProviderException("Not enough seeders available for parse magnet link", "torrent_not_downloaded.mp4")


def wait_for_torrent_download(rd_client, torrent_id: str, max_retries: int, retry_interval: int) -> str:
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
    info_hash: str, magnet_link: str, token: str, max_retries=5, retry_interval=5
) -> str:
    rd_client = RealDebrid(encoded_token=token)

    direct_link = check_existing_torrent(rd_client, info_hash, max_retries, retry_interval)
    if direct_link:
        return direct_link

    # Convert a magnet link to a Real-Debrid torrent
    response_data = rd_client.add_magent_link(magnet_link)
    if "id" not in response_data:
        raise ProviderException("Failed to add magnet link to Real-Debrid", "transfer_error.mp4")

    torrent_id = response_data["id"]
    wait_for_file_selection(rd_client, torrent_id, max_retries, retry_interval)

    # Select the largest file for download
    torrent_info = rd_client.get_torrent_info(torrent_id)
    if not torrent_info["files"]:
        raise ProviderException("No files available for this torrent", "transfer_error.mp4")
    largest_file = max(torrent_info["files"], key=lambda x: x["bytes"])
    file_id = largest_file["id"]
    rd_client.start_torrent_download(torrent_id, file_id)

    return wait_for_torrent_download(rd_client, torrent_id, max_retries, retry_interval)
