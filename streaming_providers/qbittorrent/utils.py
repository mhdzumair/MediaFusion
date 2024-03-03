import asyncio
from datetime import timedelta
from os import path
from urllib.parse import urljoin, urlparse, quote

from aiohttp import ClientConnectorError
from aioqbt.api import AddFormBuilder, TorrentInfo
from aioqbt.client import create_client, APIClient
from aioqbt.exc import LoginError, AddTorrentError
from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import RemoteResourceNotFound, NoConnection
from thefuzz import fuzz

from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException


async def check_torrent_status(
    qbittorrent: APIClient, info_hash: str
) -> TorrentInfo | None:
    """Checks the status of a torrent with a given info_hash or torrent_name."""
    available_torrents = await qbittorrent.torrents.info(hashes=[info_hash])
    return available_torrents[0] if available_torrents else None


async def add_magnet(
    qbittorrent: APIClient, magnet_link: str, info_hash: str, user_data: UserData
):
    """Adds a magnet link to the qbittorrent server."""
    try:
        await qbittorrent.torrents.add(
            AddFormBuilder.with_client(qbittorrent)
            .include_url(magnet_link)
            .savepath(info_hash)
            .seeding_time_limit(
                timedelta(
                    minutes=user_data.streaming_provider.qbittorrent_config.seeding_time_limit
                )
            )
            .ratio_limit(
                user_data.streaming_provider.qbittorrent_config.seeding_ratio_limit
            )
            .build()
        )
    except AddTorrentError:
        raise ProviderException(
            "Failed to add the torrent to qBittorrent", "add_torrent_failed.mp4"
        )


async def wait_for_torrent_to_complete(
    qbittorrent: APIClient,
    info_hash: str,
    play_video_after: int,
    max_retries: int,
    retry_interval: int,
):
    """Waits for a torrent with the given info_hash to complete downloading."""
    retries = 0
    while retries < max_retries:
        torrent = await check_torrent_status(qbittorrent, info_hash)
        if torrent and torrent.progress * 100 >= play_video_after:
            return
        await asyncio.sleep(retry_interval)
        retries += 1

    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


async def get_files_from_folder(
    webdav: WebDavClient, base_url_path: str, root_path: str
) -> list[dict]:
    """Helper function to recursively get files from a folder including subfolders."""
    try:
        contents = await webdav.list(root_path, get_info=True)
    except RemoteResourceNotFound:
        return []

    files = []
    for item in contents:
        # skip the root folder
        if item["path"].endswith(root_path.removesuffix("/") + "/"):
            continue

        if item["isdir"]:
            files.extend(
                await get_files_from_folder(
                    webdav, base_url_path, item["path"].removeprefix(base_url_path)
                )
            )
        else:
            item.update(
                {
                    "name": path.basename(item["path"]),
                    "size": int(item["size"]),
                }
            )
            files.append(item)

    return files


async def find_file_in_folder_tree(
    webdav: WebDavClient, user_data: UserData, info_hash: str, filename: str
) -> dict | None:
    base_url_path = urlparse(
        user_data.streaming_provider.qbittorrent_config.webdav_url
    ).path

    files = await get_files_from_folder(webdav, base_url_path, info_hash)
    if not files:
        return None

    exact_match = next((file for file in files if file["name"] == filename), None)
    if exact_match:
        return exact_match

    # Fuzzy matching as a fallback
    for file in files:
        file["fuzzy_ratio"] = fuzz.ratio(filename, file["name"])
    selected_file = max(files, key=lambda x: x["fuzzy_ratio"])

    # If the fuzzy ratio is less than 50, then select the largest file
    if selected_file["fuzzy_ratio"] < 50:
        selected_file = max(files, key=lambda x: x["size"])

    if "video" not in selected_file["content_type"]:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )

    return selected_file


async def initialize_qbittorrent(user_data: UserData):
    try:
        qbittorrent = await create_client(
            user_data.streaming_provider.qbittorrent_config.qbittorrent_url
            + "/api/v2/",
            username=user_data.streaming_provider.qbittorrent_config.qbittorrent_username,
            password=user_data.streaming_provider.qbittorrent_config.qbittorrent_password,
        )
    except LoginError:
        raise ProviderException(
            "Invalid qBittorrent credentials", "invalid_credentials.mp4"
        )
    except ClientConnectorError:
        raise ProviderException(
            "Failed to connect to qBittorrent", "qbittorrent_error.mp4"
        )
    return qbittorrent


async def initialize_webdav(user_data: UserData):
    webdav_options = {
        "webdav_hostname": user_data.streaming_provider.qbittorrent_config.webdav_url,
        "webdav_login": user_data.streaming_provider.qbittorrent_config.webdav_username,
        "webdav_password": user_data.streaming_provider.qbittorrent_config.webdav_password,
    }

    webdav = WebDavClient(webdav_options)

    try:
        if not await webdav.check():
            raise ProviderException(
                "Invalid WebDAV credentials", "invalid_credentials.mp4"
            )
    except NoConnection:
        raise ProviderException(
            "Failed to connect to the WebDAV server", "webdav_error.mp4"
        )
    return webdav


async def handle_torrent_status(
    qbittorrent: APIClient,
    info_hash: str,
    play_video_after: int,
    max_retries: int,
    retry_interval: int,
):
    torrent = await check_torrent_status(qbittorrent, info_hash)
    if not torrent:
        return

    if torrent.progress * 100 >= play_video_after:
        return torrent

    await wait_for_torrent_to_complete(
        qbittorrent, info_hash, play_video_after, max_retries, retry_interval
    )
    return torrent


async def retrieve_or_download_file(
    qbittorrent: APIClient,
    webdav: WebDavClient,
    user_data: UserData,
    play_video_after: int,
    filename: str,
    magnet_link: str,
    info_hash: str,
    max_retries: int,
    retry_interval: int,
):
    selected_file = await find_file_in_folder_tree(
        webdav, user_data, info_hash, filename
    )
    if not selected_file:
        await add_magnet(qbittorrent, magnet_link, info_hash, user_data)
        await wait_for_torrent_to_complete(
            qbittorrent, info_hash, play_video_after, max_retries, retry_interval
        )
        selected_file = await find_file_in_folder_tree(
            webdav, user_data, info_hash, filename
        )
    if selected_file is None:
        raise ProviderException(
            "No matching file available for this torrent", "no_matching_file.mp4"
        )
    return selected_file


def generate_webdav_url(user_data: UserData, selected_file: dict) -> str:
    webdav_url = user_data.streaming_provider.qbittorrent_config.webdav_url
    webdav_username = user_data.streaming_provider.qbittorrent_config.webdav_username
    webdav_password = user_data.streaming_provider.qbittorrent_config.webdav_password

    # Check if credentials are provided
    if webdav_username and webdav_password:
        # URL encode the username and password
        webdav_username = quote(webdav_username, safe="")
        webdav_password = quote(webdav_password, safe="")
        parsed_url = urlparse(webdav_url)
        netloc = f"{webdav_username}:{webdav_password}@{parsed_url.netloc}"
        base_url = parsed_url._replace(netloc=netloc).geturl()
    else:
        base_url = webdav_url

    return urljoin(
        base_url,
        quote(selected_file["path"]),  # Ensure the path is URL encoded
    )


async def get_direct_link_from_qbittorrent(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreams,
    filename: str,
    max_retries=5,
    retry_interval=5,
) -> str:
    """Get the direct link from the qBittorrent server."""
    qbittorrent = webdav = None
    play_video_after = user_data.streaming_provider.qbittorrent_config.play_video_after
    try:
        qbittorrent = await initialize_qbittorrent(user_data)
        webdav = await initialize_webdav(user_data)
        await handle_torrent_status(
            qbittorrent,
            info_hash,
            play_video_after,
            max_retries,
            retry_interval,
        )

        selected_file = await retrieve_or_download_file(
            qbittorrent,
            webdav,
            user_data,
            play_video_after,
            filename or stream.torrent_name,
            magnet_link,
            info_hash,
            max_retries,
            retry_interval,
        )

        return generate_webdav_url(user_data, selected_file)
    finally:
        if qbittorrent:
            await qbittorrent.close()
        if webdav:
            await webdav.close()


async def update_qbittorrent_cache_status(
    streams: list[TorrentStreams], user_data: UserData
):
    """Updates the cache status of streams based on qBittorrent's instant availability."""
    qbittorrent = None
    try:
        qbittorrent = await initialize_qbittorrent(user_data)
        torrents_info = await qbittorrent.torrents.info(
            hashes=[stream.id for stream in streams]
        )
        torrents_dict = {torrent.hash: torrent.progress for torrent in torrents_info}
    except ProviderException:
        return
    finally:
        if qbittorrent:
            await qbittorrent.close()

    for stream in streams:
        stream.cached = torrents_dict.get(stream.id, 0) == 1


async def fetch_info_hashes_from_webdav(
    user_data: UserData,
) -> list[str]:
    """Fetches the info_hashes from directories in the WebDAV server that are named after the torrent's info hashes."""
    webdav = None
    try:
        webdav = await initialize_webdav(user_data)
        directories = await webdav.list()

        # Filter out directory names that match the length of an info hash (40 chars) plus the trailing slash
        info_hashes = [
            dirname.removesuffix("/")
            for dirname in directories
            if len(dirname) == 41 and dirname.endswith("/")
        ]

        return info_hashes
    except ProviderException:
        return []
    finally:
        if webdav:
            await webdav.close()
