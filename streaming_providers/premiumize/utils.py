from typing import Any

from thefuzz import fuzz

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.premiumize.client import Premiumize


def create_or_get_folder_id(pm_client: Premiumize, info_hash: str):
    folder_data = pm_client.get_folder_list()
    for folder in folder_data["content"]:
        if folder["name"] == info_hash:
            return folder["id"]

    folder_data = pm_client.create_folder(info_hash)
    if folder_data.get("status") != "success":
        raise ProviderException(
            "Folder already created in meanwhile", "torrent_not_downloaded.mp4"
        )
    return folder_data.get("id")


def get_video_url_from_premiumize(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    torrent_name: str,
    filename: str,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    pm_client = Premiumize(token=user_data.streaming_provider.token)

    # Check if the torrent already exists
    torrent_info = pm_client.get_available_torrent(info_hash, torrent_name)
    if torrent_info:
        torrent_id = torrent_info.get("id")
        if torrent_info["status"] == "error":
            pm_client.delete_torrent(torrent_id)
            raise ProviderException(
                "Not enough seeders available for parse magnet link",
                "transfer_error.mp4",
            )
    else:
        # If torrent doesn't exist, add it
        folder_id = create_or_get_folder_id(pm_client, info_hash)
        response_data = pm_client.add_magent_link(magnet_link, folder_id)
        if "id" not in response_data:
            raise ProviderException(
                "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
            )
        torrent_id = response_data["id"]

    # Wait for file selection and then start torrent download
    torrent_info = pm_client.wait_for_status(
        torrent_id, "finished", max_retries, retry_interval
    )
    return get_stream_link(pm_client, torrent_info, filename, info_hash)


def get_stream_link(
    pm_client: Premiumize, torrent_info: dict[str, Any], filename: str, info_hash: str
) -> str:
    """Get the stream link from the torrent info."""
    if torrent_info["folder_id"] is None:
        torrent_folder_data = pm_client.get_folder_list(
            create_or_get_folder_id(pm_client, info_hash)
        )
    else:
        torrent_folder_data = pm_client.get_folder_list(torrent_info["folder_id"])
    exact_match = next(
        (f for f in torrent_folder_data["content"] if f["name"] == filename), None
    )
    if exact_match:
        return exact_match["link"]

    # Fuzzy matching as a fallback
    for file in torrent_folder_data["content"]:
        file["fuzzy_ratio"] = fuzz.ratio(filename, file["name"])
    selected_file = max(torrent_folder_data["content"], key=lambda x: x["fuzzy_ratio"])

    # If the fuzzy ratio is less than 50, then select the largest file
    if selected_file["fuzzy_ratio"] < 50:
        selected_file = max(
            torrent_folder_data["content"], key=lambda x: x.get("size", 0)
        )

    if "video" not in selected_file["mime_type"]:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    return selected_file["link"]


def update_pm_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on Premiumize's instant availability."""

    try:
        pm_client = Premiumize(token=user_data.streaming_provider.token)
        instant_availability_data = pm_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream, cached_status in zip(
            streams, instant_availability_data.get("response")
        ):
            stream.cached = cached_status

    except ProviderException:
        pass


def fetch_downloaded_info_hashes_from_premiumize(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the Premiumize account."""
    try:
        pm_client = Premiumize(token=user_data.streaming_provider.token)
        available_folders = pm_client.get_folder_list()
        return [
            folder["name"]
            for folder in available_folders["content"]
            if folder["name"] and folder["type"] == "folder"
        ]

    except ProviderException:
        return []


def delete_all_torrents_from_pm(user_data: UserData, **kwargs):
    """Deletes all torrents from the Premiumize account."""
    pm_client = Premiumize(token=user_data.streaming_provider.token)
    folders = pm_client.get_folder_list()
    for folder in folders["content"]:
        pm_client.delete_folder(folder["id"])
