import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from os import path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from aiohttp import (
    ClientConnectorError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
    CookieJar,
)
from aioqbt.api import AddFormBuilder, InfoFilter, TorrentInfo
from aioqbt.client import APIClient, create_client
from aioqbt.exc import AddTorrentError, LoginError, NotFoundError
from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import MethodNotSupported, NoConnection, RemoteResourceNotFound

from db.enums import TorrentType
from db.schemas import StreamingProvider, TorrentStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent

logger = logging.getLogger(__name__)


def _qbittorrent_webdav_download_roots(streaming_provider: StreamingProvider) -> list[str]:
    cfg = streaming_provider.qbittorrent_config
    roots: list[str] = []
    primary = (cfg.webdav_downloads_path or "/").strip() or "/"
    roots.append(primary)
    for extra in cfg.webdav_extra_paths:
        e = extra.strip()
        if e and e not in roots:
            roots.append(e)
    return roots


def _is_duplicate_or_existing_torrent_add_error(message: str) -> bool:
    """qBittorrent returns plain-text failures for torrents/add; 'already in list' is common."""
    m = message.lower()
    return any(
        phrase in m
        for phrase in (
            "already in the list",
            "already in the download list",
            "torrent is already present",
            "already present",
            "duplicate torrent",
            "is already queued",
        )
    )


async def check_torrent_status(qbittorrent: APIClient, info_hash: str) -> TorrentInfo | None:
    """Checks the status of a torrent with a given info_hash or torrent_name."""
    available_torrents = await qbittorrent.torrents.info(hashes=[info_hash])
    return available_torrents[0] if available_torrents else None


async def add_torrent_or_magnet(
    qbittorrent: APIClient,
    magnet_link: str,
    info_hash: str,
    streaming_provider: StreamingProvider,
    stream: TorrentStreamData,
):
    """Adds a magnet link to the qbittorrent server."""
    if await check_torrent_status(qbittorrent, info_hash):
        logger.info("qBittorrent already has torrent %s; skipping add", info_hash)
        return

    try:
        torrent_form = AddFormBuilder.with_client(qbittorrent)
        if stream.torrent_type != TorrentType.PUBLIC and stream.torrent_file:
            torrent_form = torrent_form.include_file(stream.torrent_file, stream.name)
        else:
            torrent_form = torrent_form.include_url(magnet_link)

        torrent_form = (
            torrent_form.savepath(info_hash)
            .sequential_download(True)
            .seeding_time_limit(timedelta(minutes=streaming_provider.qbittorrent_config.seeding_time_limit))
            .ratio_limit(streaming_provider.qbittorrent_config.seeding_ratio_limit)
            .category(streaming_provider.qbittorrent_config.category)
            .build()
        )
        await qbittorrent.torrents.add(torrent_form)
    except AddTorrentError as exc:
        if _is_duplicate_or_existing_torrent_add_error(exc.message):
            logger.info(
                "qBittorrent refused add for %s (treating as already present): %s",
                info_hash,
                exc.message,
            )
            return
        logger.error("qBittorrent torrents/add failed for %s: %s", info_hash, exc.message)
        raise ProviderException("Failed to add the torrent to qBittorrent", "add_torrent_failed.mp4") from exc


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


async def get_files_from_folder(webdav: WebDavClient, base_url_path: str, root_path: str) -> list[dict]:
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
            files.extend(await get_files_from_folder(webdav, base_url_path, item["path"].removeprefix(base_url_path)))
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
    streaming_provider: StreamingProvider,
    info_hash: str,
    stream: TorrentStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
) -> dict | None:
    base_url_path = urlparse(streaming_provider.qbittorrent_config.webdav_url).path

    files: list[dict] = []
    for download_root in _qbittorrent_webdav_download_roots(streaming_provider):
        downloads_root_path = path.join(download_root, info_hash)
        files = await get_files_from_folder(webdav, base_url_path, downloads_root_path)
        if files:
            break

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
async def initialize_qbittorrent(streaming_provider: StreamingProvider) -> AsyncGenerator[APIClient, Any]:
    async with ClientSession(cookie_jar=CookieJar(unsafe=True), timeout=ClientTimeout(total=15)) as session:
        try:
            qbittorrent = await create_client(
                streaming_provider.qbittorrent_config.qbittorrent_url.rstrip("/") + "/api/v2/",
                username=streaming_provider.qbittorrent_config.qbittorrent_username,
                password=streaming_provider.qbittorrent_config.qbittorrent_password,
                http=session,
            )
        except LoginError:
            raise ProviderException("Invalid qBittorrent credentials", "invalid_credentials.mp4")
        except ClientResponseError as err:
            if err.status == 403:
                raise ProviderException("Invalid qBittorrent credentials", "invalid_credentials.mp4")
            raise ProviderException(
                f"An error occurred while connecting to qBittorrent: {err}",
                "qbittorrent_error.mp4",
            )
        except (ClientConnectorError, NotFoundError):
            raise ProviderException("Failed to connect to qBittorrent", "qbittorrent_error.mp4")
        except Exception as err:
            raise ProviderException(
                f"An error occurred while connecting to qBittorrent: {err}",
                "qbittorrent_error.mp4",
            )

        yield qbittorrent


@asynccontextmanager
async def initialize_webdav(streaming_provider: StreamingProvider):
    webdav = None
    try:
        webdav_options = {
            "webdav_hostname": streaming_provider.qbittorrent_config.webdav_url,
            "webdav_login": streaming_provider.qbittorrent_config.webdav_username,
            "webdav_password": streaming_provider.qbittorrent_config.webdav_password,
        }
        webdav = WebDavClient(webdav_options)
        # Perform an initial check to validate the WebDAV credentials/connection
        if not await webdav.check():
            raise ProviderException("Invalid WebDAV credentials", "invalid_credentials.mp4")
        yield webdav
    except NoConnection:
        raise ProviderException("Failed to connect to the WebDAV server", "webdav_error.mp4")
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

    await wait_for_torrent_to_complete(qbittorrent, info_hash, play_video_after, max_retries, retry_interval)
    return torrent


async def set_qbittorrent_preferences(qbittorrent, torrent_type):
    if torrent_type == "private":
        await qbittorrent.app.set_preferences({"dht": False, "pex": False, "lsd": False})
    # else:
    #    # Enable DHT, PEX, and LSD for public trackers
    #     await qbittorrent.app.set_preferences({"dht": True, "pex": True, "lsd": True})


async def retrieve_or_download_file(
    qbittorrent: APIClient,
    webdav: WebDavClient,
    streaming_provider: StreamingProvider,
    play_video_after: int,
    magnet_link: str,
    info_hash: str,
    stream: TorrentStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
    max_retries: int,
    retry_interval: int,
):
    selected_file = await find_file_in_folder_tree(
        webdav, streaming_provider, info_hash, stream, filename, season, episode
    )
    if not selected_file:
        await set_qbittorrent_preferences(qbittorrent, stream.torrent_type)
        await add_torrent_or_magnet(qbittorrent, magnet_link, info_hash, streaming_provider, stream)
        await wait_for_torrent_to_complete(qbittorrent, info_hash, play_video_after, max_retries, retry_interval)
        selected_file = await find_file_in_folder_tree(
            webdav, streaming_provider, info_hash, stream, filename, season, episode
        )
        if not selected_file:
            raise ProviderException("No matching file available for this torrent", "no_matching_file.mp4")
    return selected_file


def generate_webdav_url(streaming_provider: StreamingProvider, selected_file: dict) -> str:
    webdav_url = streaming_provider.qbittorrent_config.webdav_url
    webdav_username = streaming_provider.qbittorrent_config.webdav_username
    webdav_password = streaming_provider.qbittorrent_config.webdav_password

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
    streaming_provider: StreamingProvider,
    stream: TorrentStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
    max_retries=5,
    retry_interval=5,
    **kwargs,
) -> str:
    """Get the direct link from the qBittorrent server."""
    play_video_after = streaming_provider.qbittorrent_config.play_video_after
    async with (
        initialize_qbittorrent(streaming_provider) as qbittorrent,
        initialize_webdav(streaming_provider) as webdav,
    ):
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
            streaming_provider,
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

        return generate_webdav_url(streaming_provider, selected_file)


async def update_qbittorrent_cache_status(
    streams: list[TorrentStreamData], streaming_provider: StreamingProvider, **kwargs
):
    """Updates the cache status of streams based on qBittorrent's instant availability."""
    try:
        async with initialize_qbittorrent(streaming_provider) as qbittorrent:
            torrents_info = await qbittorrent.torrents.info(hashes=[stream.info_hash for stream in streams])
    except (ProviderException, Exception):
        return

    torrents_dict = {torrent.hash: torrent.progress for torrent in torrents_info}
    for stream in streams:
        stream.cached = torrents_dict.get(stream.info_hash, 0) == 1


async def fetch_info_hashes_from_webdav(streaming_provider: StreamingProvider, **kwargs) -> list[str]:
    """Fetches the info_hashes from directories in the WebDAV server that are named after the torrent's info hashes."""
    merged: set[str] = set()
    try:
        async with initialize_webdav(streaming_provider) as webdav:
            for download_root in _qbittorrent_webdav_download_roots(streaming_provider):
                try:
                    directories = await webdav.list(download_root)
                except Exception as list_err:
                    logger.debug("Skipping WebDAV list for root %r: %s", download_root, list_err)
                    continue
                for dirname in directories:
                    if len(dirname) == 41 and dirname.endswith("/"):
                        merged.add(dirname.removesuffix("/"))
    except ProviderException:
        return []
    except MethodNotSupported:
        logger.warning(
            "WebDAV server does not support LIST for qBittorrent downloads path (tunnel or limited WebDAV): %s",
            streaming_provider.qbittorrent_config.webdav_url,
        )
        return []
    except Exception as err:
        logger.warning(
            "WebDAV list failed while fetching qBittorrent info hashes (%s): %s",
            streaming_provider.qbittorrent_config.webdav_url,
            err,
        )
        return []

    return sorted(merged)


async def delete_all_torrents_from_qbittorrent(streaming_provider: StreamingProvider, **kwargs):
    """Deletes all torrents from the qBittorrent server."""
    async with initialize_qbittorrent(streaming_provider) as qbittorrent:
        torrents = await qbittorrent.torrents.info(filter=InfoFilter.COMPLETED)
        await qbittorrent.torrents.delete(hashes=[torrent.hash for torrent in torrents], delete_files=True)


async def validate_qbittorrent_credentials(streaming_provider: StreamingProvider, **kwargs) -> dict:
    """Validates the qBittorrent credentials."""
    try:
        async with initialize_qbittorrent(streaming_provider):
            pass
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify QBittorrent, error: {error.message}",
        }

    try:
        async with initialize_webdav(streaming_provider):
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to verify WebDAV, error: {error.message}",
        }
