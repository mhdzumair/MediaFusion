import asyncio

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.stremthru.client import StremThru


def _get_client(user_data: UserData) -> StremThru:
    return StremThru(
        url=user_data.streaming_provider.url,
        token=user_data.streaming_provider.token,
    )


async def get_torrent_info(st_client, info_hash):
    torrent_info = await st_client.get_available_torrent(info_hash)
    if torrent_info and torrent_info["status"] == "downloaded":
        return torrent_info
    return None


async def add_new_torrent(st_client, magnet_link):
    response_data = await st_client.add_magnet_link(magnet_link)
    return response_data["id"]


async def wait_for_download_and_get_link(
    st_client, torrent_id, filename, episode, max_retries, retry_interval
):
    torrent_info = await st_client.wait_for_status(
        torrent_id, "downloaded", max_retries, retry_interval
    )
    file_index = await select_file_index_from_torrent(
        torrent_info,
        filename,
        episode,
    )
    response = await st_client.create_download_link(
        torrent_info["files"][file_index]["link"]
    )
    return response["link"]


async def get_video_url_from_stremthru(
    magnet_link: str,
    user_data: UserData,
    filename: str,
    episode: int = None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with _get_client(user_data) as st_client:
        torrent_id = await add_new_torrent(st_client, magnet_link)

        return await wait_for_download_and_get_link(
            st_client,
            torrent_id,
            filename,
            episode,
            max_retries,
            retry_interval,
        )


async def update_st_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on StremThru's instant availability."""

    try:
        async with _get_client(user_data) as st_client:
            instant_availability_data = (
                await st_client.get_torrent_instant_availability(
                    [stream.id for stream in streams]
                )
            )
            for stream in streams:
                stream.cached = any(
                    torrent["status"] == "cached"
                    for torrent in instant_availability_data["items"]
                    if torrent.get("hash") == stream.id
                )

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_st(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the StremThru account."""
    try:
        async with _get_client(user_data) as st_client:
            available_torrents = await st_client.get_user_torrent_list()
            return [torrent["hash"] for torrent in available_torrents["items"]]

    except ProviderException:
        return []


async def delete_all_torrents_from_st(user_data: UserData, **kwargs):
    """Deletes all torrents from the StremThru account."""
    async with _get_client(user_data) as st_client:
        torrents = await st_client.get_user_torrent_list()
        await asyncio.gather(
            *[st_client.delete_torrent(torrent["id"]) for torrent in torrents["items"]]
        )


async def validate_stremthru_credentials(user_data: UserData, **kwargs) -> dict:
    """Validates the StremThru credentials."""
    try:
        async with _get_client(user_data) as client:
            response = await client.get_user_info()
            if response:
                return {"status": "success"}
            return {
                "status": "error",
                "message": "Invalid StremThru credentials.",
            }
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify StremThru credential, error: {error.message}",
        }
