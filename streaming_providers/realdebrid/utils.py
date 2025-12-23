import asyncio
from typing import Optional

from db.schemas import TorrentStreamData
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
)
from streaming_providers.realdebrid.client import RealDebrid


async def create_download_link(
    rd_client: RealDebrid,
    magnet_link: str,
    torrent_info: dict,
    filename: Optional[str],
    episode: Optional[int],
    season: Optional[int],
    stream: TorrentStreamData,
    max_retries: int,
    retry_interval: int,
) -> str:
    selected_file_index = await select_file_index_from_torrent(
        torrent_info=torrent_info,
        torrent_stream=stream,
        filename=filename,
        season=season,
        episode=episode,
        file_key="files",
        name_key="path",
        size_key="bytes",
        is_filename_trustable=True,
        is_index_trustable=True,
    )

    relevant_file = torrent_info["files"][selected_file_index]
    selected_files = [file for file in torrent_info["files"] if file["selected"] == 1]

    if relevant_file.get("selected") != 1 or len(selected_files) != len(
        torrent_info["links"]
    ):
        await rd_client.delete_torrent(torrent_info["id"])
        torrent_id = (await rd_client.add_magnet_link(magnet_link)).get("id")
        torrent_info = await rd_client.wait_for_status(
            torrent_id, "waiting_files_selection", max_retries, retry_interval
        )
        await rd_client.start_torrent_download(
            torrent_info["id"],
            file_ids=torrent_info["files"][selected_file_index]["id"],
        )
        torrent_info = await rd_client.wait_for_status(
            torrent_id, "downloaded", max_retries, retry_interval
        )
        link_index = 0
    else:
        link_index = selected_files.index(relevant_file)

    response = await rd_client.create_download_link(torrent_info["links"][link_index])

    if not response.get("mimeType", "").startswith("video"):
        # await rd_client.delete_torrent(torrent_info["id"])
        raise ProviderException(
            f"Requested file is not a video file, deleting torrent and retrying. {response['mimeType']}",
            "torrent_not_downloaded.mp4",
        )

    return response.get("download")


async def get_video_url_from_realdebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    user_ip: str,
    filename: Optional[str],
    stream: TorrentStreamData,
    max_retries=5,
    retry_interval=5,
    episode: Optional[int] = None,
    season: Optional[int] = None,
    **kwargs,
) -> str:
    async with RealDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as rd_client:
        torrent_info = await rd_client.get_available_torrent(info_hash)

        if not torrent_info:
            torrent_info = await add_new_torrent(
                rd_client, magnet_link, info_hash, stream
            )

        torrent_id = torrent_info["id"]
        status = torrent_info["status"]

        if status in ["magnet_error", "error", "virus", "dead"]:
            await rd_client.delete_torrent(torrent_id)
            raise ProviderException(
                f"Torrent cannot be downloaded due to status: {status}",
                "transfer_error.mp4",
            )

        if status not in ["queued", "downloading", "downloaded"]:
            torrent_info = await rd_client.wait_for_status(
                torrent_id,
                "waiting_files_selection",
                max_retries,
                retry_interval,
                torrent_info,
            )
            try:
                await rd_client.start_torrent_download(
                    torrent_info["id"],
                    file_ids="all",
                )
            except ProviderException as error:
                await rd_client.delete_torrent(torrent_id)
                raise ProviderException(
                    f"Failed to start torrent download, {error}", "transfer_error.mp4"
                )

        torrent_info = await rd_client.wait_for_status(
            torrent_id, "downloaded", max_retries, retry_interval
        )

        return await create_download_link(
            rd_client,
            magnet_link,
            torrent_info,
            filename,
            episode,
            season,
            stream,
            max_retries,
            retry_interval,
        )


async def add_new_torrent(rd_client, magnet_link, info_hash, stream):
    response = await rd_client.get_active_torrents()
    if response["limit"] == response["nb"]:
        raise ProviderException(
            "Torrent limit reached. Please try again later.", "torrent_limit.mp4"
        )
    if info_hash in response["list"]:
        raise ProviderException(
            "Torrent is already being downloading", "torrent_not_downloaded.mp4"
        )

    if stream.torrent_file:
        torrent_id = (await rd_client.add_torrent_file(stream.torrent_file)).get("id")
    else:
        torrent_id = (await rd_client.add_magnet_link(magnet_link)).get("id")

    if not torrent_id:
        raise ProviderException(
            "Failed to add magnet link to Real-Debrid", "transfer_error.mp4"
        )

    return await rd_client.get_torrent_info(torrent_id)


async def update_rd_cache_status(
    streams: list[TorrentStreamData], user_data: UserData, user_ip: str, **kwargs
):
    """Updates the cache status of streams based on user's downloaded torrents in RealDebrid."""

    try:
        downloaded_hashes = set(
            await fetch_downloaded_info_hashes_from_rd(user_data, user_ip, **kwargs)
        )
        if not downloaded_hashes:
            return
        for stream in streams:
            stream.cached = stream.id in downloaded_hashes

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_rd(
    user_data: UserData, user_ip: str, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the RealDebrid account."""
    try:
        async with RealDebrid(
            token=user_data.streaming_provider.token, user_ip=user_ip
        ) as rd_client:
            available_torrents = await rd_client.get_user_torrent_list()
            return [
                torrent["hash"]
                for torrent in available_torrents
                if torrent["status"] == "downloaded"
            ]

    except ProviderException:
        return []


async def delete_all_watchlist_rd(user_data: UserData, user_ip: str, **kwargs):
    """Deletes all torrents from the RealDebrid watchlist."""
    async with RealDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as rd_client:
        torrents = await rd_client.get_user_torrent_list()
        semaphore = asyncio.Semaphore(3)

        async def delete_torrent(torrent_id):
            async with semaphore:
                await rd_client.delete_torrent(torrent_id)

        await asyncio.gather(
            *[delete_torrent(torrent["id"]) for torrent in torrents],
            return_exceptions=True,
        )


async def validate_realdebrid_credentials(user_data: UserData, user_ip: str) -> dict:
    """Validates the RealDebrid credentials."""
    try:
        async with RealDebrid(
            token=user_data.streaming_provider.token, user_ip=user_ip
        ) as rd_client:
            await rd_client.get_user_info()
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify RealDebrid credential, error: {error.message}",
        }
