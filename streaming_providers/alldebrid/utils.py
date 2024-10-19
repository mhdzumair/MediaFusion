import asyncio

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.alldebrid.client import AllDebrid
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent


async def get_torrent_info(ad_client, info_hash):
    torrent_info = await ad_client.get_available_torrent(info_hash)
    if torrent_info and torrent_info["status"] == "Ready":
        return torrent_info
    elif torrent_info and torrent_info["statusCode"] == 7:
        await ad_client.delete_torrent(torrent_info.get("id"))
        raise ProviderException(
            "Not enough seeders available for parse magnet link",
            "transfer_error.mp4",
        )
    return None


async def add_new_torrent(ad_client, magnet_link):
    response_data = await ad_client.add_magnet_link(magnet_link)
    return response_data["data"]["magnets"][0]["id"]


async def wait_for_download_and_get_link(
    ad_client, torrent_id, filename, file_index, episode, max_retries, retry_interval
):
    torrent_info = await ad_client.wait_for_status(
        torrent_id, "Ready", max_retries, retry_interval
    )
    file_index = select_file_index_from_torrent(
        torrent_info,
        filename,
        file_index,
        episode,
        file_key="links",
        name_key="filename",
    )
    response = await ad_client.create_download_link(
        torrent_info["links"][file_index]["link"]
    )
    return response["data"]["link"]


async def get_video_url_from_alldebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    user_ip: str,
    file_index: int,
    episode: int = None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with AllDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as ad_client:
        torrent_info = await get_torrent_info(ad_client, info_hash)
        if not torrent_info:
            torrent_id = await add_new_torrent(ad_client, magnet_link)
        else:
            torrent_id = torrent_info.get("id")

        return await wait_for_download_and_get_link(
            ad_client,
            torrent_id,
            filename,
            file_index,
            episode,
            max_retries,
            retry_interval,
        )


async def update_ad_cache_status(
    streams: list[TorrentStreams], user_data: UserData, user_ip: str, **kwargs
):
    """Updates the cache status of streams based on AllDebrid's instant availability."""

    try:
        async with AllDebrid(
            token=user_data.streaming_provider.token, user_ip=user_ip
        ) as ad_client:
            instant_availability_data = (
                await ad_client.get_torrent_instant_availability(
                    [stream.id for stream in streams]
                )
            )
            for stream in streams:
                stream.cached = any(
                    torrent["instant"]
                    for torrent in instant_availability_data
                    if torrent["hash"] == stream.id
                )

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_ad(
    user_data: UserData, user_ip: str, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the AllDebrid account."""
    try:
        async with AllDebrid(
            token=user_data.streaming_provider.token, user_ip=user_ip
        ) as ad_client:
            available_torrents = await ad_client.get_user_torrent_list()
            if not available_torrents.get("data"):
                return []
            return [
                torrent["hash"] for torrent in available_torrents["data"]["magnets"]
            ]

    except ProviderException:
        return []


async def delete_all_torrents_from_ad(user_data: UserData, user_ip: str, **kwargs):
    """Deletes all torrents from the AllDebrid account."""
    async with AllDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as ad_client:
        torrents = await ad_client.get_user_torrent_list()
        await asyncio.gather(
            *[
                ad_client.delete_torrent(torrent["id"])
                for torrent in torrents["data"]["magnets"]
            ]
        )
