from db.models import Streams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.offcloud.client import OffCloud


def get_direct_link_from_offcloud(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    max_retries=5,
    retry_interval=5,
) -> str:
    oc_client = OffCloud(token=user_data.streaming_provider.token)

    # Check if the torrent already exists
    torrent_info = oc_client.get_available_torrent(info_hash)
    if torrent_info:
        request_id = torrent_info.get("requestId")
        torrent_info = oc_client.get_torrent_info(request_id)
        if torrent_info["status"] == "downloaded":
            return oc_client.create_download_link(request_id, torrent_info, filename)
        if torrent_info["status"] == "error":
            raise ProviderException(
                f"Error transferring magnet link to OffCloud. {torrent_info['errorMessage']}",
                "transfer_error.mp4",
            )
    else:
        # If torrent doesn't exist, add it
        response_data = oc_client.add_magent_link(magnet_link)
        request_id = response_data["requestId"]

    # Wait for download completion and get the direct link
    torrent_info = oc_client.wait_for_status(
        request_id, "downloaded", max_retries, retry_interval
    )
    return oc_client.create_download_link(request_id, torrent_info, filename)


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
                [True for torrent in instant_availability_data if torrent == stream.id]
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
