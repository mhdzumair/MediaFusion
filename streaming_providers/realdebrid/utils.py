import asyncio

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from streaming_providers.realdebrid.client import RealDebrid


async def create_download_link(rd_client, torrent_info, filename, file_index, episode):
    file_index = select_file_index_from_torrent(
        torrent_info,
        filename,
        file_index,
        episode,
        "files",
        "path",
        "bytes",
        "selected",
        True,
    )
    try:
        response = await rd_client.create_download_link(
            torrent_info["links"][file_index]
        )
    except IndexError:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )
    return response.get("download")


async def get_video_url_from_realdebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    filename: str,
    file_index: int,
    max_retries=5,
    retry_interval=5,
    user_ip: str = None,
    episode: int = None,
    **kwargs,
) -> str:
    async with RealDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as rd_client:
        torrent_info = await rd_client.get_available_torrent(info_hash)
        if not torrent_info:
            response = await rd_client.get_active_torrents()
            if response["limit"] == response["nb"]:
                raise ProviderException(
                    "Torrent limit reached. Please try again later.",
                    "torrent_limit.mp4",
                )

            if info_hash in response["list"]:
                raise ProviderException(
                    "Torrent is already being downloading", "torrent_not_downloaded.mp4"
                )

            torrent_id = (await rd_client.add_magnet_link(magnet_link)).get("id")
            if not torrent_id:
                raise ProviderException(
                    "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
                )
            torrent_info = await rd_client.get_torrent_info(torrent_id)
        else:
            torrent_id = torrent_info.get("id")

        status = torrent_info["status"]
        if status in ["magnet_error", "error", "virus", "dead"]:
            await rd_client.delete_torrent(torrent_id)
            raise ProviderException(
                f"Torrent cannot be downloaded due to status: {status}",
                "transfer_error.mp4",
            )
        elif status in ["queued", "downloading", "downloaded"]:
            pass  # No action needed, proceed to create download link
        else:
            # "waiting_files_selection", "magnet_conversion", "compressing", "uploading"
            await rd_client.wait_for_status(
                torrent_id, "waiting_files_selection", max_retries, retry_interval
            )
            try:
                await rd_client.start_torrent_download(torrent_id)
            except ProviderException as error:
                await rd_client.delete_torrent(torrent_id)
                raise ProviderException(
                    f"Failed to start torrent download, {error}", "transfer_error.mp4"
                )

        torrent_info = await rd_client.wait_for_status(
            torrent_id, "downloaded", max_retries, retry_interval
        )

        return await create_download_link(
            rd_client, torrent_info, filename, file_index, episode
        )


async def update_rd_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on RealDebrid's instant availability."""

    try:
        async with RealDebrid(token=user_data.streaming_provider.token) as rd_client:
            instant_availability_data = (
                await rd_client.get_torrent_instant_availability(
                    [stream.id for stream in streams]
                )
            )
            if not instant_availability_data:
                return
            for stream in streams:
                stream.cached = bool(instant_availability_data.get(stream.id, False))

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_rd(
    user_data: UserData, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the RealDebrid account."""
    try:
        async with RealDebrid(token=user_data.streaming_provider.token) as rd_client:
            available_torrents = await rd_client.get_user_torrent_list()
            return [torrent["hash"] for torrent in available_torrents]

    except ProviderException:
        return []


async def delete_all_watchlist_rd(user_data: UserData, **kwargs):
    """Deletes all torrents from the RealDebrid watchlist."""
    async with RealDebrid(token=user_data.streaming_provider.token) as rd_client:
        torrents = await rd_client.get_user_torrent_list()
        await asyncio.gather(
            *[rd_client.delete_torrent(torrent["id"]) for torrent in torrents]
        )
