from typing import Optional

from fastapi import BackgroundTasks

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.alldebrid.client import AllDebrid
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
    update_torrent_streams_metadata,
)


async def get_torrent_info(ad_client, info_hash):
    torrent_info = await ad_client.get_available_torrent(info_hash)
    if torrent_info and torrent_info["statusCode"] == 7:
        await ad_client.delete_torrents([torrent_info.get("id")])
        raise ProviderException(
            "Not enough seeders available to parse magnet link",
            "transfer_error.mp4",
        )
    return torrent_info


async def add_new_torrent(
    ad_client: AllDebrid, magnet_link: str, stream: TorrentStreams
):
    if stream.torrent_file:
        response_data = await ad_client.add_torrent_file(
            stream.torrent_file, stream.torrent_name or "torrent"
        )
        return response_data["data"]["files"][0]["id"]
    else:
        response_data = await ad_client.add_magnet_link(magnet_link)
        return response_data["data"]["magnets"][0]["id"]


def flatten_files(files):
    """Recursively flattens the nested file structure into a simpler list of dictionaries.
    Handles both nested groups and single file cases."""
    flattened = []

    def _flatten(file_group, parent_group=""):
        # Check if this is a direct file entry (has 'l' for link)
        if "l" in file_group:
            flattened.append(
                {
                    "name": file_group["n"],
                    "size": file_group["s"],
                    "link": file_group["l"],
                    "group": parent_group.strip("/"),
                }
            )
            return

        # Otherwise, process as a group
        group_name = f"{parent_group}/{file_group['n']}".strip("/")
        for file_entry in file_group.get("e", []):
            if "e" in file_entry:
                _flatten(file_entry, group_name)
            else:
                flattened.append(
                    {
                        "name": file_entry["n"],
                        "size": file_entry["s"],
                        "link": file_entry["l"],
                        "group": group_name,
                    }
                )

    for file_group in files:
        _flatten(file_group)

    return flattened


async def wait_for_download_and_get_link(
    ad_client,
    torrent_id,
    stream,
    filename,
    season,
    episode,
    max_retries,
    retry_interval,
    background_tasks,
):
    torrent_info = await ad_client.wait_for_status(
        torrent_id, "Ready", max_retries, retry_interval
    )
    files_data = {"files": flatten_files(torrent_info["files"])}

    file_index = await select_file_index_from_torrent(
        files_data,
        filename,
        season,
        episode,
    )

    if filename is None:
        background_tasks.add_task(
            update_torrent_streams_metadata,
            torrent_stream=stream,
            torrent_info=files_data,
            file_index=file_index,
            season=season,
            is_index_trustable=False,
        )

    response = await ad_client.create_download_link(
        files_data["files"][file_index]["link"]
    )
    return response["data"]["link"]


async def get_video_url_from_alldebrid(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreams,
    filename: Optional[str],
    user_ip: str,
    background_tasks: BackgroundTasks,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with AllDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as ad_client:
        torrent_info = await get_torrent_info(ad_client, info_hash)
        if not torrent_info:
            torrent_id = await add_new_torrent(ad_client, magnet_link, stream)
        else:
            torrent_id = torrent_info.get("id")

        return await wait_for_download_and_get_link(
            ad_client,
            torrent_id,
            stream,
            filename,
            season,
            episode,
            max_retries,
            retry_interval,
            background_tasks,
        )


async def update_ad_cache_status(
    streams: list[TorrentStreams], user_data: UserData, user_ip: str, **kwargs
):
    """Updates the cache status of streams based on AllDebrid's instant availability."""

    try:
        downloaded_hashes = set(
            await fetch_downloaded_info_hashes_from_ad(user_data, user_ip, **kwargs)
        )
        if not downloaded_hashes:
            return

        for stream in streams:
            stream.cached = stream.id in downloaded_hashes

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
            available_torrents = await ad_client.get_user_torrent_list(status="ready")
            if not available_torrents.get("data"):
                return []
            magnets = available_torrents["data"]["magnets"]
            if isinstance(magnets, dict):
                return [magnet["hash"] for magnet in magnets.values()]
            return [magnet["hash"] for magnet in magnets]

    except ProviderException:
        return []


async def delete_all_torrents_from_ad(user_data: UserData, user_ip: str, **kwargs):
    """Deletes all torrents from the AllDebrid account."""
    async with AllDebrid(
        token=user_data.streaming_provider.token, user_ip=user_ip
    ) as ad_client:
        torrents = await ad_client.get_user_torrent_list()
        magnet_ids = [torrent["id"] for torrent in torrents["data"]["magnets"]]
        if not magnet_ids:
            return
        response = await ad_client.delete_torrents(magnet_ids)
        if response.get("status") != "success":
            raise ProviderException(
                f"Failed to delete torrents from AllDebrid {response}",
                "api_error.mp4",
            )


async def validate_alldebrid_credentials(user_data: UserData, user_ip: str) -> dict:
    """Validates the AllDebrid credentials."""
    try:
        async with AllDebrid(
            token=user_data.streaming_provider.token, user_ip=user_ip
        ) as ad_client:
            response = await ad_client.get_user_info()
            if response.get("status") == "success":
                return {"status": "success"}

            return {
                "status": "error",
                "message": "Invalid AllDebrid credentials.",
            }
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify AllDebrid credential, error: {error.message}",
        }
