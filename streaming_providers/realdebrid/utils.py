from typing import Any

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.realdebrid.client import RealDebrid


def create_download_link(rd_client, torrent_info, filename, file_index):
    file_index = select_file_index_from_torrent(torrent_info, filename, file_index)
    try:
        response = rd_client.create_download_link(torrent_info["links"][file_index])
    except IndexError:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )
    return response.get("download")


def get_direct_link_from_realdebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    file_index: int,
    max_retries=5,
    retry_interval=5,
) -> str:
    rd_client = RealDebrid(token=user_data.streaming_provider.token)
    torrent_info = rd_client.get_available_torrent(info_hash)
    if not torrent_info:
        torrent_id = rd_client.add_magent_link(magnet_link).get("id")
        torrent_info = rd_client.get_torrent_info(torrent_id)
    else:
        torrent_id = torrent_info.get("id")

    if not torrent_id:
        raise ProviderException(
            "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
        )

    status = torrent_info["status"]
    if status in ["magnet_error", "error", "virus", "dead"]:
        rd_client.delete_torrent(torrent_id)
        raise ProviderException(
            f"Torrent cannot be downloaded due to status: {status}",
            "transfer_error.mp4",
        )
    elif status in ["queued", "downloading", "downloaded"]:
        pass  # No action needed, proceed to create download link
    else:
        # "waiting_files_selection", "magnet_conversion", "compressing", "uploading"
        rd_client.wait_for_status(
            torrent_id, "waiting_files_selection", max_retries, retry_interval
        )
        rd_client.start_torrent_download(torrent_id)

    torrent_info = rd_client.wait_for_status(
        torrent_id, "downloaded", max_retries, retry_interval
    )

    return create_download_link(rd_client, torrent_info, filename, file_index)


def update_rd_cache_status(streams: list[TorrentStreams], user_data: UserData):
    """Updates the cache status of streams based on RealDebrid's instant availability."""

    try:
        rd_client = RealDebrid(token=user_data.streaming_provider.token)
        instant_availability_data = rd_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream in streams:
            stream.cached = bool(instant_availability_data.get(stream.id, False))

    except ProviderException:
        pass


def select_file_index_from_torrent(
    torrent_info: dict[str, Any], filename: str, file_index: int
) -> int:
    """Select the file index from the torrent info."""
    if file_index is not None and file_index < len(torrent_info["links"]):
        return file_index

    selected_files = [file for file in torrent_info["files"] if file["selected"] == 1]
    if filename:
        for index, file in enumerate(selected_files):
            if file["path"] == "/" + filename:
                return index

    # If no file index is provided, select the largest file
    largest_file = max(selected_files, key=lambda file: file["bytes"])
    return selected_files.index(largest_file)


def fetch_downloaded_info_hashes_from_rd(user_data: UserData) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the RealDebrid account."""
    try:
        rd_client = RealDebrid(token=user_data.streaming_provider.token)
        available_torrents = rd_client.get_user_torrent_list()
        return [torrent["hash"] for torrent in available_torrents]

    except ProviderException:
        return []
