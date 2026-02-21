from db.schemas import StreamingProvider, TorrentStreamData
from streaming_providers.alldebrid.client import AllDebrid
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import (
    select_file_index_from_torrent,
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


async def add_new_torrent(ad_client: AllDebrid, magnet_link: str, stream: TorrentStreamData):
    if stream.torrent_file:
        response_data = await ad_client.add_torrent_file(stream.torrent_file, stream.name or "torrent")
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
):
    torrent_info = await ad_client.wait_for_status(torrent_id, "Ready", max_retries, retry_interval)
    files_data = {"files": flatten_files(torrent_info["files"])}

    file_index = await select_file_index_from_torrent(
        torrent_info=files_data,
        torrent_stream=stream,
        filename=filename,
        season=season,
        episode=episode,
        is_filename_trustable=True,
    )

    response = await ad_client.create_download_link(files_data["files"][file_index]["link"])
    return response["data"]["link"]


async def get_video_url_from_alldebrid(
    info_hash: str,
    magnet_link: str,
    streaming_provider: StreamingProvider,
    stream: TorrentStreamData,
    filename: str | None,
    user_ip: str,
    season: int | None = None,
    episode: int | None = None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
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
        )


async def update_ad_cache_status(
    streams: list[TorrentStreamData], streaming_provider: StreamingProvider, user_ip: str, **kwargs
):
    """Updates the cache status of streams based on AllDebrid's instant availability."""

    try:
        downloaded_hashes = set(await fetch_downloaded_info_hashes_from_ad(streaming_provider, user_ip, **kwargs))
        if not downloaded_hashes:
            return

        for stream in streams:
            stream.cached = stream.info_hash in downloaded_hashes

    except ProviderException:
        pass


async def fetch_downloaded_info_hashes_from_ad(
    streaming_provider: StreamingProvider, user_ip: str, **kwargs
) -> list[str]:
    """Fetches the info_hashes of all torrents downloaded in the AllDebrid account."""
    try:
        async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
            available_torrents = await ad_client.get_user_torrent_list(status="ready")
            if not available_torrents.get("data"):
                return []
            magnets = available_torrents["data"]["magnets"]
            if isinstance(magnets, dict):
                return [magnet["hash"] for magnet in magnets.values()]
            return [magnet["hash"] for magnet in magnets]

    except ProviderException:
        return []


async def fetch_torrent_details_from_ad(streaming_provider: StreamingProvider, user_ip: str, **kwargs) -> list[dict]:
    """
    Fetches detailed torrent information from the AllDebrid account.
    Returns torrent details including files for import functionality.
    """
    try:
        async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
            available_torrents = await ad_client.get_user_torrent_list(status="ready")
            if not available_torrents.get("data"):
                return []
            magnets = available_torrents["data"]["magnets"]
            if isinstance(magnets, dict):
                magnets = list(magnets.values())
            target_hashes = {str(info_hash).lower() for info_hash in kwargs.get("target_hashes", set()) if info_hash}

            result = []
            for magnet in magnets:
                magnet_hash = magnet.get("hash", "").lower()
                if target_hashes and magnet_hash not in target_hashes:
                    continue
                files = []
                # AllDebrid has nested file structure, flatten it
                flat_files = flatten_files(magnet.get("files", []))
                for f in flat_files:
                    files.append(
                        {
                            "id": None,
                            "path": f.get("name", ""),
                            "size": f.get("size", 0),
                        }
                    )
                result.append(
                    {
                        "id": magnet.get("id"),
                        "hash": magnet_hash,
                        "filename": magnet.get("filename", ""),
                        "size": magnet.get("size", 0),
                        "files": files,
                    }
                )
            return result

    except ProviderException:
        return []


async def delete_all_torrents_from_ad(streaming_provider: StreamingProvider, user_ip: str, **kwargs):
    """Deletes all torrents from the AllDebrid account."""
    async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
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


async def delete_torrent_from_ad(streaming_provider: StreamingProvider, user_ip: str, info_hash: str, **kwargs) -> bool:
    """Deletes a specific torrent from AllDebrid by info_hash."""
    try:
        async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
            torrents = await ad_client.get_user_torrent_list()
            if not torrents.get("data"):
                return False
            magnets = torrents["data"]["magnets"]
            if isinstance(magnets, dict):
                magnets = list(magnets.values())
            for magnet in magnets:
                if magnet.get("hash", "").lower() == info_hash.lower():
                    await ad_client.delete_torrents([magnet["id"]])
                    return True
            return False
    except ProviderException:
        return False


async def validate_alldebrid_credentials(streaming_provider: StreamingProvider, user_ip: str, **kwargs) -> dict:
    """Validates the AllDebrid credentials."""
    try:
        async with AllDebrid(token=streaming_provider.token, user_ip=user_ip) as ad_client:
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
