import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from os import path
from typing import Optional, AsyncGenerator, Any
from urllib.parse import urljoin, urlparse, quote

from aiohttp import (
    ClientConnectorError,
    ClientSession,
    CookieJar,
    ClientTimeout,
    ClientResponseError,
)
from aioqbt.api import AddFormBuilder, TorrentInfo, InfoFilter
from aioqbt.client import create_client, APIClient
from aioqbt.exc import LoginError, AddTorrentError, NotFoundError
from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import RemoteResourceNotFound, NoConnection

from db.enums import TorrentType
from db.models import TorrentStreams
from db.schemas import UserData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent


async def check_torrent_status(
    qbittorrent: APIClient, info_hash: str
) -> TorrentInfo | None:
    """Checks the status of a torrent with a given info_hash or torrent_name."""
    available_torrents = await qbittorrent.torrents.info(hashes=[info_hash])
    return available_torrents[0] if available_torrents else None


async def add_torrent_or_magnet(
    qbittorrent: APIClient,
    magnet_link: str,
    info_hash: str,
    user_data: UserData,
    stream: TorrentStreams,
):
    """Adds a magnet link to the qbittorrent server."""
    try:
        torrent_form = AddFormBuilder.with_client(qbittorrent)
        if stream.torrent_type != TorrentType.PUBLIC and stream.torrent_file:
            torrent_form = torrent_form.include_file(
                stream.torrent_file, stream.torrent_name
            )
        else:
            torrent_form = torrent_form.include_url(magnet_link)

        torrent_form = (
            torrent_form.savepath(info_hash)
            .sequential_download(True)
            .seeding_time_limit(
                timedelta(
                    minutes=user_data.streaming_provider.qbittorrent_config.seeding_time_limit
                )
            )
            .ratio_limit(
                user_data.streaming_provider.qbittorrent_config.seeding_ratio_limit
            )
            .category(user_data.streaming_provider.qbittorrent_config.category)
            .build()
        )
        await qbittorrent.torrents.add(torrent_form)
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
    webdav: WebDavClient,
    user_data: UserData,
    info_hash: str,
    stream: TorrentStreams,
    filename: Optional[str],
    season: Optional[int],
    episode: Optional[int],
) -> dict | None:
    base_url_path = urlparse(
        user_data.streaming_provider.qbittorrent_config.webdav_url
    ).path

    downloads_root_path = path.join(
        user_data.streaming_provider.qbittorrent_config.webdav_downloads_path,
        info_hash,
    )

    files = await get_files_from_folder(webdav, base_url_path, downloads_root_path)
    if not files:
        return None

    selected_file_index = await select_file_index_from_torrent(
        torrent_info={"files": files},
        torrent_stream=stream,
        filename=filename,
        season=season,
        episode=episode,
        is_filename_trustable=True,
        is_index_trustable=True,
    )
    selected_file = files[selected_file_index]
    return selected_file


@asynccontextmanager
async def initialize_qbittorrent(user_data: UserData) -> AsyncGenerator[APIClient, Any]:
    async with ClientSession(
        cookie_jar=CookieJar(unsafe=True), timeout=ClientTimeout(total=15)
    ) as session:
        try:
            qbittorrent = await create_client(
                user_data.streaming_provider.qbittorrent_config.qbittorrent_url.rstrip(
                    "/"
                )
                + "/api/v2/",
                username=user_data.streaming_provider.qbittorrent_config.qbittorrent_username,
                password=user_data.streaming_provider.qbittorrent_config.qbittorrent_password,
                http=session,
            )
        except LoginError:
            raise ProviderException(
                "Invalid qBittorrent credentials", "invalid_credentials.mp4"
            )
        except ClientResponseError as err:
            if err.status == 403:
                raise ProviderException(
                    "Invalid qBittorrent credentials", "invalid_credentials.mp4"
                )
            raise ProviderException(
                f"An error occurred while connecting to qBittorrent: {err}",
                "qbittorrent_error.mp4",
            )
        except (ClientConnectorError, NotFoundError):
            raise ProviderException(
                "Failed to connect to qBittorrent", "qbittorrent_error.mp4"
            )
        except Exception as err:
            raise ProviderException(
                f"An error occurred while connecting to qBittorrent: {err}",
                "qbittorrent_error.mp4",
            )

        yield qbittorrent


@asynccontextmanager
async def initialize_webdav(user_data: UserData):
    webdav = None
    try:
        webdav_options = {
            "webdav_hostname": user_data.streaming_provider.qbittorrent_config.webdav_url,
            "webdav_login": user_data.streaming_provider.qbittorrent_config.webdav_username,
            "webdav_password": user_data.streaming_provider.qbittorrent_config.webdav_password,
        }
        webdav = WebDavClient(webdav_options)
        # Perform an initial check to validate the WebDAV credentials/connection
        if not await webdav.check():
            raise ProviderException(
                "Invalid WebDAV credentials", "invalid_credentials.mp4"
            )
        yield webdav
    except NoConnection:
        raise ProviderException(
            "Failed to connect to the WebDAV server", "webdav_error.mp4"
        )
    finally:
        if webdav:
            await webdav.close()


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


async def set_qbittorrent_preferences(qbittorrent, torrent_type):
    if torrent_type == "private":
        await qbittorrent.app.set_preferences(
            {"dht": False, "pex": False, "lsd": False}
        )
    # else:
    #    # Enable DHT, PEX, and LSD for public trackers
    #     await qbittorrent.app.set_preferences({"dht": True, "pex": True, "lsd": True})


async def retrieve_or_download_file(
    qbittorrent: APIClient,
    webdav: WebDavClient,
    user_data: UserData,
    play_video_after: int,
    magnet_link: str,
    info_hash: str,
    stream: TorrentStreams,
    filename: Optional[str],
    season: Optional[int],
    episode: Optional[int],
    max_retries: int,
    retry_interval: int,
):
    selected_file = await find_file_in_folder_tree(
        webdav, user_data, info_hash, stream, filename, season, episode
    )
    if not selected_file:
        await set_qbittorrent_preferences(qbittorrent, stream.torrent_type)
        await add_torrent_or_magnet(
            qbittorrent, magnet_link, info_hash, user_data, stream
        )
        await wait_for_torrent_to_complete(
            qbittorrent, info_hash, play_video_after, max_retries, retry_interval
        )
        selected_file = await find_file_in_folder_tree(
            webdav, user_data, info_hash, stream, filename, season, episode
        )
        if not selected_file:
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


async def get_video_url_from_qbittorrent(
    info_hash: str,
    magnet_link: str,
    user_data: UserData,
    stream: TorrentStreams,
    filename: Optional[str],
    season: Optional[int],
    episode: Optional[int],
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    """Get the direct link from the qBittorrent server."""
    play_video_after = user_data.streaming_provider.qbittorrent_config.play_video_after
    async with initialize_qbittorrent(user_data) as qbittorrent, initialize_webdav(
        user_data
    ) as webdav:
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
            magnet_link,
            info_hash,
            stream,
            filename,
            season,
            episode,
            max_retries,
            retry_interval,
        )

        return generate_webdav_url(user_data, selected_file)


async def update_qbittorrent_cache_status(
    streams: list[TorrentStreams], user_data: UserData, **kwargs
):
    """Updates the cache status of streams based on qBittorrent's instant availability."""
    try:
        async with initialize_qbittorrent(user_data) as qbittorrent:
            torrents_info = await qbittorrent.torrents.info(
                hashes=[stream.id for stream in streams]
            )
    except (ProviderException, Exception):
        return

    torrents_dict = {torrent.hash: torrent.progress for torrent in torrents_info}
    for stream in streams:
        stream.cached = torrents_dict.get(stream.id, 0) == 1


async def fetch_info_hashes_from_webdav(user_data: UserData, **kwargs) -> list[str]:
    """Fetches the info_hashes from directories in the WebDAV server that are named after the torrent's info hashes."""
    try:
        async with initialize_webdav(user_data) as webdav:
            directories = await webdav.list(
                user_data.streaming_provider.qbittorrent_config.webdav_downloads_path
            )
    except ProviderException:
        return []

    # Filter out directory names that match the length of an info hash (40 chars) plus the trailing slash
    info_hashes = [
        dirname.removesuffix("/")
        for dirname in directories
        if len(dirname) == 41 and dirname.endswith("/")
    ]

    return info_hashes


async def delete_all_torrents_from_qbittorrent(user_data: UserData, **kwargs):
    """Deletes all torrents from the qBittorrent server."""
    async with initialize_qbittorrent(user_data) as qbittorrent:
        torrents = await qbittorrent.torrents.info(filter=InfoFilter.COMPLETED)
        await qbittorrent.torrents.delete(
            hashes=[torrent.hash for torrent in torrents], delete_files=True
        )


async def validate_qbittorrent_credentials(user_data: UserData, **kwargs) -> dict:
    """Validates the qBittorrent credentials."""
    try:
        async with initialize_qbittorrent(user_data):
            pass
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify QBittorrent, error: {error.message}",
        }

    try:
        async with initialize_webdav(user_data):
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify WebDAV, error: {error.message}",
        }
