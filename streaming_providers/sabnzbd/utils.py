"""SABnzbd utility functions for Usenet streaming."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from os import path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import NoConnection, RemoteResourceNotFound

from db.schemas import StreamingProvider
from db.schemas.media import UsenetStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.sabnzbd.client import SABnzbd


@asynccontextmanager
async def initialize_sabnzbd(streaming_provider: StreamingProvider) -> AsyncGenerator[SABnzbd, Any]:
    """Initialize SABnzbd client from streaming provider config.

    Args:
        streaming_provider: Provider configuration

    Yields:
        Initialized SABnzbd client
    """
    config = streaming_provider.sabnzbd_config
    if not config:
        raise ProviderException("SABnzbd configuration not found", "invalid_credentials.mp4")

    async with SABnzbd(url=config.url, api_key=config.api_key) as client:
        yield client


@asynccontextmanager
async def initialize_webdav(streaming_provider: StreamingProvider) -> AsyncGenerator[WebDavClient, Any]:
    """Initialize WebDAV client for SABnzbd downloads.

    Args:
        streaming_provider: Provider configuration

    Yields:
        Initialized WebDAV client
    """
    config = streaming_provider.sabnzbd_config
    if not config or not config.webdav_url:
        raise ProviderException("WebDAV configuration not found for SABnzbd", "invalid_credentials.mp4")

    webdav = None
    try:
        webdav_options = {
            "webdav_hostname": config.webdav_url,
            "webdav_login": config.webdav_username,
            "webdav_password": config.webdav_password,
        }
        webdav = WebDavClient(webdav_options)
        if not await webdav.check():
            raise ProviderException("Invalid WebDAV credentials", "invalid_credentials.mp4")
        yield webdav
    except NoConnection:
        raise ProviderException("Failed to connect to the WebDAV server", "webdav_error.mp4")
    finally:
        if webdav:
            await webdav.close()


async def get_files_from_folder(webdav: WebDavClient, base_url_path: str, root_path: str) -> list[dict]:
    """Helper function to recursively get files from a folder including subfolders.

    Args:
        webdav: WebDAV client
        base_url_path: Base URL path for the WebDAV server
        root_path: Root path to list

    Returns:
        List of file info dicts
    """
    try:
        contents = await webdav.list(root_path, get_info=True)
    except RemoteResourceNotFound:
        return []

    files = []
    for item in contents:
        # Skip the root folder
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


async def select_file_from_usenet(
    files: list[dict],
    stream: UsenetStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
) -> dict | None:
    """Select the appropriate file from usenet download files.

    Args:
        files: List of available files
        stream: Usenet stream data
        filename: Target filename
        season: Season number for series
        episode: Episode number for series

    Returns:
        Selected file dict or None
    """
    if not files:
        return None

    video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm"}

    # If filename is provided, try to match it
    if filename:
        for f in files:
            file_name = f.get("name", "").lower()
            if file_name == filename.lower():
                return f

    # For series, try to match season/episode
    if season is not None and episode is not None:
        pattern = rf"[sS]{season:02d}[eE]{episode:02d}"
        for f in files:
            file_name = f.get("name", "")
            if re.search(pattern, file_name):
                return f

    # Return the largest video file
    video_files = []
    for f in files:
        file_name = f.get("name", "").lower()
        if any(file_name.endswith(ext) for ext in video_extensions):
            video_files.append(f)

    if video_files:
        return max(video_files, key=lambda x: x.get("size", 0))

    # Fallback to largest file
    return max(files, key=lambda x: x.get("size", 0)) if files else None


async def find_file_in_sabnzbd_downloads(
    webdav: WebDavClient,
    streaming_provider: StreamingProvider,
    download_name: str,
    stream: UsenetStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
) -> dict | None:
    """Find a file in SABnzbd completed downloads.

    Args:
        webdav: WebDAV client
        streaming_provider: Provider configuration
        download_name: Name of the download folder
        stream: Usenet stream data
        filename: Target filename
        season: Season number for series
        episode: Episode number for series

    Returns:
        Selected file dict or None
    """
    config = streaming_provider.sabnzbd_config
    base_url_path = urlparse(config.webdav_url).path

    downloads_root_path = path.join(
        config.webdav_downloads_path,
        download_name,
    )

    files = await get_files_from_folder(webdav, base_url_path, downloads_root_path)
    if not files:
        return None

    return await select_file_from_usenet(files, stream, filename, season, episode)


def generate_webdav_url(streaming_provider: StreamingProvider, selected_file: dict) -> str:
    """Generate WebDAV URL for the selected file.

    Args:
        streaming_provider: Provider configuration
        selected_file: Selected file dict

    Returns:
        WebDAV URL with credentials
    """
    config = streaming_provider.sabnzbd_config
    webdav_url = config.webdav_url
    webdav_username = config.webdav_username
    webdav_password = config.webdav_password

    if webdav_username and webdav_password:
        webdav_username = quote(webdav_username, safe="")
        webdav_password = quote(webdav_password, safe="")
        parsed_url = urlparse(webdav_url)
        netloc = f"{webdav_username}:{webdav_password}@{parsed_url.netloc}"
        base_url = parsed_url._replace(netloc=netloc).geturl()
    else:
        base_url = webdav_url

    return urljoin(
        base_url,
        quote(selected_file["path"]),
    )


async def wait_for_download_completion(
    sabnzbd: SABnzbd,
    nzo_id: str,
    max_retries: int = 60,
    retry_interval: int = 5,
    min_progress: float = 100.0,
) -> dict:
    """Wait for an NZB download to complete.

    Args:
        sabnzbd: SABnzbd client
        nzo_id: NZO ID of the download
        max_retries: Maximum number of retries
        retry_interval: Seconds between retries
        min_progress: Minimum progress percentage to consider complete

    Returns:
        Download status dict

    Raises:
        ProviderException: If download doesn't complete in time
    """
    retries = 0
    while retries < max_retries:
        status = await sabnzbd.get_nzb_status(nzo_id)
        if not status:
            raise ProviderException("Download not found in SABnzbd", "transfer_error.mp4")

        if status["status"] == "completed" or status["progress"] >= min_progress:
            return status

        if status["status"] in ("failed", "Failed"):
            raise ProviderException("Download failed in SABnzbd", "transfer_error.mp4")

        await asyncio.sleep(retry_interval)
        retries += 1

    raise ProviderException("Download did not complete in time", "torrent_not_downloaded.mp4")


async def get_video_url_from_sabnzbd(
    nzb_hash: str,
    streaming_provider: StreamingProvider,
    filename: str | None,
    stream: UsenetStreamData,
    season: int | None = None,
    episode: int | None = None,
    max_retries: int = 60,
    retry_interval: int = 5,
    **kwargs: Any,
) -> str:
    """Get video URL from SABnzbd for Usenet/NZB content.

    Args:
        nzb_hash: Hash of the NZB content
        streaming_provider: Provider configuration
        filename: Target filename
        stream: Usenet stream data
        season: Season number for series
        episode: Episode number for series
        max_retries: Maximum retries for download completion
        retry_interval: Seconds between retries

    Returns:
        WebDAV URL for the video file
    """
    config = streaming_provider.sabnzbd_config
    category = config.category if config else "MediaFusion"

    async with initialize_sabnzbd(streaming_provider) as sabnzbd:
        # Check if download already exists
        existing = await sabnzbd.find_download_by_name(stream.name)

        if existing and existing["status"] == "completed":
            # Download exists and is complete, find the file
            async with initialize_webdav(streaming_provider) as webdav:
                selected_file = await find_file_in_sabnzbd_downloads(
                    webdav, streaming_provider, existing["filename"], stream, filename, season, episode
                )
                if selected_file:
                    return generate_webdav_url(streaming_provider, selected_file)

        # Add the NZB if not exists or not complete
        if not existing:
            if stream.nzb_content:
                nzo_id = await sabnzbd.add_nzb_by_content(stream.nzb_content, stream.name, category)
            elif stream.nzb_url:
                nzo_id = await sabnzbd.add_nzb_by_url(stream.nzb_url, category, stream.name)
            else:
                raise ProviderException("No NZB content or URL available", "transfer_error.mp4")
        else:
            nzo_id = existing["nzo_id"]

        # Wait for completion
        status = await wait_for_download_completion(sabnzbd, nzo_id, max_retries, retry_interval)

        # Find the file in completed downloads
        async with initialize_webdav(streaming_provider) as webdav:
            selected_file = await find_file_in_sabnzbd_downloads(
                webdav, streaming_provider, status["filename"], stream, filename, season, episode
            )
            if not selected_file:
                raise ProviderException("No matching file found in download", "no_video_file_found.mp4")

            return generate_webdav_url(streaming_provider, selected_file)


async def update_sabnzbd_cache_status(
    streams: list[UsenetStreamData], streaming_provider: StreamingProvider, **kwargs: Any
) -> None:
    """Update cache status for usenet streams based on SABnzbd downloads.

    SABnzbd doesn't have instant availability checking like debrid services,
    so we check if downloads are already completed.

    Args:
        streams: List of usenet streams to check
        streaming_provider: Provider configuration
    """
    try:
        async with initialize_sabnzbd(streaming_provider) as sabnzbd:
            downloads = await sabnzbd.get_all_downloads()
            completed_names = {d["filename"].lower() for d in downloads if d["status"] == "completed"}

            for stream in streams:
                stream.cached = stream.name.lower() in completed_names
    except ProviderException as e:
        logging.error(f"Failed to check SABnzbd cache status: {e}")


async def fetch_downloaded_usenet_hashes_from_sabnzbd(
    streaming_provider: StreamingProvider, **kwargs: Any
) -> list[str]:
    """Fetch hashes of completed downloads from SABnzbd.

    Note: SABnzbd doesn't store NZB hashes directly, so we return download names.

    Args:
        streaming_provider: Provider configuration

    Returns:
        List of download names (used as identifiers)
    """
    try:
        async with initialize_sabnzbd(streaming_provider) as sabnzbd:
            downloads = await sabnzbd.get_all_downloads()
            return [d["filename"] for d in downloads if d["status"] == "completed"]
    except ProviderException:
        return []


async def delete_all_usenet_from_sabnzbd(streaming_provider: StreamingProvider, **kwargs: Any) -> None:
    """Delete all completed downloads from SABnzbd.

    Args:
        streaming_provider: Provider configuration
    """
    async with initialize_sabnzbd(streaming_provider) as sabnzbd:
        downloads = await sabnzbd.get_all_downloads()
        for download in downloads:
            if download["status"] == "completed":
                await sabnzbd.delete_nzb(download["nzo_id"], delete_files=True)


async def delete_usenet_from_sabnzbd(streaming_provider: StreamingProvider, nzb_hash: str, **kwargs: Any) -> bool:
    """Delete a specific download from SABnzbd.

    Args:
        streaming_provider: Provider configuration
        nzb_hash: NZB hash or name to delete

    Returns:
        True if deleted successfully
    """
    try:
        async with initialize_sabnzbd(streaming_provider) as sabnzbd:
            download = await sabnzbd.find_download_by_name(nzb_hash)
            if download:
                return await sabnzbd.delete_nzb(download["nzo_id"], delete_files=True)
            return False
    except ProviderException:
        return False


async def validate_sabnzbd_credentials(streaming_provider: StreamingProvider, **kwargs: Any) -> dict[str, str]:
    """Validate SABnzbd credentials.

    Args:
        streaming_provider: Provider configuration

    Returns:
        Status dict with success or error message
    """
    try:
        async with initialize_sabnzbd(streaming_provider) as sabnzbd:
            version = await sabnzbd.get_version()
            if not version:
                return {
                    "status": "error",
                    "message": "Failed to get SABnzbd version",
                }
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate SABnzbd credentials: {error.message}",
        }

    # Validate WebDAV if configured
    config = streaming_provider.sabnzbd_config
    if config and config.webdav_url:
        try:
            async with initialize_webdav(streaming_provider):
                return {"status": "success"}
        except ProviderException as error:
            return {
                "status": "error",
                "message": f"Failed to verify WebDAV: {error.message}",
            }

    return {"status": "success"}
