"""NzbDAV utility functions for Usenet streaming.

NzbDAV exposes a SABnzbd-compatible API and a built-in WebDAV server
on the same host/port. This module reuses the SABnzbd client and
auto-derives the WebDAV URL from the NzbDAV base URL.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from os import path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import NoConnection

from db.schemas import StreamingProvider
from db.schemas.media import UsenetStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.nzbdav.client import NzbDAVClient
from streaming_providers.sabnzbd.utils import (
    get_files_from_folder,
    select_file_from_usenet,
)


@asynccontextmanager
async def initialize_nzbdav(streaming_provider: StreamingProvider) -> AsyncGenerator[NzbDAVClient, Any]:
    """Initialize NzbDAV client from streaming provider config.

    Uses the SABnzbd-compatible API client since NzbDAV exposes the same API.

    Args:
        streaming_provider: Provider configuration

    Yields:
        Initialized NzbDAV client (SABnzbd-compatible)
    """
    config = streaming_provider.nzbdav_config
    if not config:
        raise ProviderException("NzbDAV configuration not found", "invalid_credentials.mp4")

    async with NzbDAVClient(url=config.url, api_key=config.api_key) as client:
        yield client


@asynccontextmanager
async def initialize_webdav(streaming_provider: StreamingProvider) -> AsyncGenerator[WebDavClient, Any]:
    """Initialize WebDAV client for NzbDAV.

    NzbDAV serves WebDAV from the same host/port as the API,
    so the WebDAV URL is auto-derived from the NzbDAV URL.

    Args:
        streaming_provider: Provider configuration

    Yields:
        Initialized WebDAV client
    """
    config = streaming_provider.nzbdav_config
    if not config:
        raise ProviderException("NzbDAV configuration not found", "invalid_credentials.mp4")

    webdav = None
    try:
        webdav_options = {
            "webdav_hostname": config.url.rstrip("/"),
        }
        webdav = WebDavClient(webdav_options)
        if not await webdav.check():
            raise ProviderException("Failed to connect to NzbDAV WebDAV server", "invalid_credentials.mp4")
        yield webdav
    except NoConnection:
        raise ProviderException("Failed to connect to the NzbDAV WebDAV server", "webdav_error.mp4")
    finally:
        if webdav:
            await webdav.close()


async def find_file_in_nzbdav_downloads(
    webdav: WebDavClient,
    streaming_provider: StreamingProvider,
    download_name: str,
    stream: UsenetStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
) -> dict | None:
    """Find a file in NzbDAV completed downloads.

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
    config = streaming_provider.nzbdav_config
    base_url_path = urlparse(config.url).path or "/"

    downloads_root_path = path.join(
        "/",
        config.category,
        download_name,
    )

    files = await get_files_from_folder(webdav, base_url_path, downloads_root_path)
    if not files:
        downloads_root_path = path.join("/", download_name)
        files = await get_files_from_folder(webdav, base_url_path, downloads_root_path)

    if not files:
        return None

    return await select_file_from_usenet(files, stream, filename, season, episode)


def generate_webdav_url(streaming_provider: StreamingProvider, selected_file: dict) -> str:
    """Generate WebDAV URL for the selected file.

    NzbDAV serves WebDAV from the same URL as the API.

    Args:
        streaming_provider: Provider configuration
        selected_file: Selected file dict

    Returns:
        WebDAV URL for the file
    """
    config = streaming_provider.nzbdav_config
    base_url = config.url.rstrip("/")

    return urljoin(
        base_url + "/",
        quote(selected_file["path"].lstrip("/")),
    )


async def wait_for_download_completion(
    client: NzbDAVClient,
    nzo_id: str,
    max_retries: int = 60,
    retry_interval: int = 5,
    min_progress: float = 100.0,
) -> dict:
    """Wait for an NZB download to complete in NzbDAV.

    Args:
        client: NzbDAV client
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
        status = await client.get_nzb_status(nzo_id)
        if not status:
            raise ProviderException("Download not found in NzbDAV", "transfer_error.mp4")

        if status["status"] == "completed" or status["progress"] >= min_progress:
            return status

        if status["status"] in ("failed", "Failed"):
            raise ProviderException("Download failed in NzbDAV", "transfer_error.mp4")

        await asyncio.sleep(retry_interval)
        retries += 1

    raise ProviderException("Download did not complete in time", "torrent_not_downloaded.mp4")


async def get_video_url_from_nzbdav(
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
    """Get video URL from NzbDAV for Usenet/NZB content.

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
    config = streaming_provider.nzbdav_config
    category = config.category if config else "MediaFusion"

    async with initialize_nzbdav(streaming_provider) as client:
        existing = await client.find_download_by_name(stream.name)

        if existing and existing["status"] == "completed":
            async with initialize_webdav(streaming_provider) as webdav:
                selected_file = await find_file_in_nzbdav_downloads(
                    webdav, streaming_provider, existing["filename"], stream, filename, season, episode
                )
                if selected_file:
                    return generate_webdav_url(streaming_provider, selected_file)

        if not existing:
            if stream.nzb_content:
                nzo_id = await client.add_nzb_by_content(stream.nzb_content, stream.name, category)
            elif stream.nzb_url:
                nzo_id = await client.add_nzb_by_url(stream.nzb_url, category, stream.name)
            else:
                raise ProviderException("No NZB content or URL available", "transfer_error.mp4")
        else:
            nzo_id = existing["nzo_id"]

        status = await wait_for_download_completion(client, nzo_id, max_retries, retry_interval)

        async with initialize_webdav(streaming_provider) as webdav:
            selected_file = await find_file_in_nzbdav_downloads(
                webdav, streaming_provider, status["filename"], stream, filename, season, episode
            )
            if not selected_file:
                raise ProviderException("No matching file found in download", "no_video_file_found.mp4")

            return generate_webdav_url(streaming_provider, selected_file)


async def update_nzbdav_cache_status(
    streams: list[UsenetStreamData], streaming_provider: StreamingProvider, **kwargs: Any
) -> None:
    """Update cache status for usenet streams based on NzbDAV downloads.

    Args:
        streams: List of usenet streams to check
        streaming_provider: Provider configuration
    """
    try:
        async with initialize_nzbdav(streaming_provider) as client:
            downloads = await client.get_all_downloads()
            completed_names = {d["filename"].lower() for d in downloads if d["status"] == "completed"}

            for stream in streams:
                stream.cached = stream.name.lower() in completed_names
    except ProviderException as e:
        logging.error(f"Failed to check NzbDAV cache status: {e}")


async def fetch_downloaded_usenet_hashes_from_nzbdav(
    streaming_provider: StreamingProvider, **kwargs: Any
) -> list[str]:
    """Fetch hashes of completed downloads from NzbDAV.

    Args:
        streaming_provider: Provider configuration

    Returns:
        List of download names (used as identifiers)
    """
    try:
        async with initialize_nzbdav(streaming_provider) as client:
            downloads = await client.get_all_downloads()
            return [d["filename"] for d in downloads if d["status"] == "completed"]
    except ProviderException:
        return []


async def delete_all_usenet_from_nzbdav(streaming_provider: StreamingProvider, **kwargs: Any) -> None:
    """Delete all completed downloads from NzbDAV.

    Args:
        streaming_provider: Provider configuration
    """
    async with initialize_nzbdav(streaming_provider) as client:
        downloads = await client.get_all_downloads()
        for download in downloads:
            if download["status"] == "completed":
                await client.delete_nzb(download["nzo_id"], delete_files=True)


async def delete_usenet_from_nzbdav(streaming_provider: StreamingProvider, nzb_hash: str, **kwargs: Any) -> bool:
    """Delete a specific download from NzbDAV.

    Args:
        streaming_provider: Provider configuration
        nzb_hash: NZB hash or name to delete

    Returns:
        True if deleted successfully
    """
    try:
        async with initialize_nzbdav(streaming_provider) as client:
            download = await client.find_download_by_name(nzb_hash)
            if download:
                return await client.delete_nzb(download["nzo_id"], delete_files=True)
            return False
    except ProviderException:
        return False


async def validate_nzbdav_credentials(streaming_provider: StreamingProvider, **kwargs: Any) -> dict[str, str]:
    """Validate NzbDAV credentials.

    Validates both the SABnzbd-compatible API and the built-in WebDAV server.

    Args:
        streaming_provider: Provider configuration

    Returns:
        Status dict with success or error message
    """
    try:
        async with initialize_nzbdav(streaming_provider) as client:
            version = await client.get_version()
            if not version:
                return {
                    "status": "error",
                    "message": "Failed to connect to NzbDAV API",
                }
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate NzbDAV credentials: {error.message}",
        }

    try:
        async with initialize_webdav(streaming_provider):
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to connect to NzbDAV WebDAV: {error.message}",
        }
