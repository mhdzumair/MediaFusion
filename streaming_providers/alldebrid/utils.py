from typing import Any
from urllib.parse import quote_plus

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
            return f"https://alldebrid.com/service/?url={quote_plus(torrent_info['links'][file_index]['link'])}"
        elif torrent_info["statusCode"] == 7:
            ad_client.delete_torrent(torrent_id)
            raise ProviderException(
                "Not enough seeders available for parse magnet link",
                "transfer_error.mp4",
            )
    else:
        # alldebrid does not support adding magnet links from server side. We need to add it from client side.
        return f"https://api.alldebrid.com/v4/magnet/upload?agent={ad_client.AGENT}&apikey={user_data.streaming_provider.token}&magnets[]={magnet_link}"

    # Wait for download completion and get the direct link
    torrent_info = ad_client.wait_for_status(
        torrent_id, "Ready", max_retries, retry_interval
    )
    file_index = select_file_index_from_torrent(torrent_info, filename)
    return f"https://alldebrid.com/link/unlock?link={quote_plus(torrent_info['links'][file_index]['link'])}"


def order_streams_by_instant_availability_and_date(
    streams: list[Streams], user_data: UserData
) -> list[Streams]:
    """Orders the streams by instant availability."""

    try:
        ad_client = AllDebrid(token=user_data.streaming_provider.token)
        instant_availability_data = ad_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream in streams:
            stream.cached = any(
                [
                    torrent["instant"]
                    for torrent in instant_availability_data
                    if torrent["hash"] == stream.id
                ]
            )

    except ProviderException:
        return sorted(streams, key=lambda x: x.created_at, reverse=True)

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
    for index, file in enumerate(torrent_info["links"]):
        if file["filename"] == filename:
            return index
    raise ProviderException(
        "No matching file available for this torrent", "api_error.mp4"
    )
