from typing import Any

from db.models import Streams
from db.schemas import UserData
from streaming_providers.alldebrid.client import AllDebrid
from streaming_providers.exceptions import ProviderException


def get_direct_link_from_alldebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    max_retries=5,
    retry_interval=5,
) -> str:
    ad_client = AllDebrid(token=user_data.streaming_provider.token)

    # Check if the torrent already exists
    torrent_info = ad_client.get_available_torrent(info_hash)
    if torrent_info:
        torrent_id = torrent_info.get("id")
        if torrent_info["status"] == "Ready":
            file_index = select_file_index_from_torrent(torrent_info, filename)
            response = ad_client.create_download_link(
                torrent_info["links"][file_index]["link"]
            )
            return response["data"]["link"]
        elif torrent_info["statusCode"] == 7:
            ad_client.delete_torrent(torrent_id)
            raise ProviderException(
                "Not enough seeders available for parse magnet link",
                "transfer_error.mp4",
            )
    else:
        # If torrent doesn't exist, add it
        response_data = ad_client.add_magnet_link(magnet_link)
        torrent_id = response_data["data"]["magnets"][0]["id"]

    # Wait for download completion and get the direct link
    torrent_info = ad_client.wait_for_status(
        torrent_id, "Ready", max_retries, retry_interval
    )
    file_index = select_file_index_from_torrent(torrent_info, filename)
    response = ad_client.create_download_link(torrent_info["links"][file_index]["link"])
    return response["data"]["link"]


def update_ad_cache_status(streams: list[Streams], user_data: UserData):
    """Updates the cache status of streams based on AllDebrid's instant availability."""

    try:
        ad_client = AllDebrid(token=user_data.streaming_provider.token)
        instant_availability_data = ad_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream in streams:
            stream.cached = any(
                torrent["instant"]
                for torrent in instant_availability_data
                if torrent["hash"] == stream.id
            )

    except ProviderException:
        pass


def select_file_index_from_torrent(torrent_info: dict[str, Any], filename: str) -> int:
    """Select the file index from the torrent info."""
    for index, file in enumerate(torrent_info["links"]):
        if file["filename"] == filename:
            return index
    raise ProviderException(
        "No matching file available for this torrent", "api_error.mp4"
    )


def fetch_downloaded_info_hashes_from_ad(user_data: UserData) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the AllDebrid account."""
    try:
        ad_client = AllDebrid(token=user_data.streaming_provider.token)
        available_torrents = ad_client.get_user_torrent_list()
        if not available_torrents.get("data"):
            return []
        return [torrent["hash"] for torrent in available_torrents["data"]["magnets"]]

    except ProviderException:
        return []
