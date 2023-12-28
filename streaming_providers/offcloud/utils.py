from typing import Any

from db.models import Streams, Episode
from db.schemas import UserData
from streaming_providers.offcloud.client import OffCloud
from streaming_providers.exceptions import ProviderException


def get_direct_link_from_offcloud(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: Streams,
    episode_data: Episode = None,
    max_retries=5,
    retry_interval=5,
) -> str:
    oc_client = OffCloud(token=user_data.streaming_provider.token)
    filename = episode_data.filename if episode_data else stream.filename

    # Check if the torrent already exists
    torrent_info = oc_client.get_available_torrent(info_hash)
    if torrent_info:
        torrent_id = torrent_info.get("requestId")
        if torrent_info["status"] == "downloaded":
            response = oc_client.create_download_link(
                torrent_info.get("requestId")
            )
            # Handling for when offcloud returns a Bad Archive response when it's a
            # plain mkv file and manually construct the URL in this scenario.
            if response == "Bad archive":
                torrent_server = torrent_info.get("server")
                filename = torrent_info.get("fileName")
                return f"https://{torrent_server}.offcloud.com/cloud/download/{torrent_id}/{filename}"
            return select_file_from_download_links(response, filename)
    else:
        # If torrent doesn't exist, add it
        response_data = oc_client.add_magent_link(magnet_link)
        torrent_id = response_data["requestId"]

    # Wait for download completion and get the direct link
    torrent_info = oc_client.wait_for_status(
        torrent_id, "downloaded", max_retries, retry_interval
    )
    response = oc_client.create_download_link(
        torrent_info.get("requestId")
    )
    # Shit handling
    if response == "Bad archive":
        torrent_server = torrent_info.get("server")
        filename = torrent_info.get("fileName")
        return f"https://{torrent_server}.offcloud.com/cloud/download/{torrent_id}/{filename}"
    return select_file_from_download_links(torrent_info, filename)


def order_streams_by_instant_availability_and_date(
    streams: list[Streams], user_data: UserData
) -> list[Streams]:
    """Orders the streams by instant availability."""

    try:
        oc_client = OffCloud(token=user_data.streaming_provider.token)
        instant_availability_data = oc_client.get_torrent_instant_availability(
            [stream.id for stream in streams]
        )
        for stream in streams:
            stream.cached = any(
                [
                    True
                    for torrent in instant_availability_data
                    if torrent == stream.id
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


def select_file_from_download_links(links: list[str], filename: str) -> str:
    """Select the file index from the torrent info."""
    for _, file in enumerate(links):
        if filename in file:
            return file
    raise ProviderException(
        "No matching file available for this torrent", "api_error.mp4"
    )
