"""SABnzbd utility functions for Usenet streaming."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from os import path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from aiowebdav.client import Client as WebDavClient
from aiowebdav.exceptions import NoConnection, RemoteResourceNotFound

from db.schemas import StreamingProvider
from db.schemas.media import UsenetStreamData
from streaming_providers.exceptions import ProviderException, USENET_TRANSFER_ERROR_VIDEO
from streaming_providers.sabnzbd.client import SABnzbd
from streaming_providers.usenet_file_selection import select_usenet_file_dict

# ``fail_message`` substrings that often indicate transient Usenet/network issues.
# SABnzbd can re-queue these via ``mode=retry`` (NzbDAV currently does not).
_FAIL_MESSAGE_SAB_AUTO_RETRY_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "time out",
    "connection reset",
    "connection refused",
    "broken pipe",
    "too many connections",
    "server busy",
    "temporary failure",
    "temporary error",
    "503",
    "502",
    "504",
    "500 internal",
    "cannot connect",
    "failed to connect",
    "errno",
    "ssl handshake",
    "tls",
    "network is unreachable",
    "name or service not known",
    "getaddrinfo failed",
)


def _sab_fail_message_is_auto_retryable(fail_message: str) -> bool:
    if not (fail_message or "").strip():
        return False
    low = fail_message.lower()
    return any(needle in low for needle in _FAIL_MESSAGE_SAB_AUTO_RETRY_SUBSTRINGS)


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
            nested = item["path"].removeprefix(base_url_path)
            if not nested.startswith("/"):
                nested = f"/{nested}"
            files.extend(await get_files_from_folder(webdav, base_url_path, nested))
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
    episode_air_date: str | None = None,
) -> dict | None:
    """Select the appropriate file from usenet download files.

    Args:
        files: List of available files
        stream: Usenet stream data
        filename: Target filename
        season: Season number for series
        episode: Episode number for series
        episode_air_date: Optional YYYY-MM-DD for dated releases

    Returns:
        Selected file dict or None
    """
    return select_usenet_file_dict(
        files,
        filename=filename,
        season=season,
        episode=episode,
        display_name=lambda f: f.get("name", ""),
        episode_air_date=episode_air_date,
    )


async def find_file_in_sabnzbd_downloads(
    webdav: WebDavClient,
    streaming_provider: StreamingProvider,
    download_name: str,
    stream: UsenetStreamData,
    filename: str | None,
    season: int | None,
    episode: int | None,
    episode_air_date: str | None = None,
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

    return await select_file_from_usenet(files, stream, filename, season, episode, episode_air_date)


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


def _sab_download_failed_user_message(status: dict, *, provider_label: str) -> str:
    """Build a user-visible message from SAB-compatible queue/history status."""
    fail = (status.get("fail_message") or "").strip()
    if fail:
        return f"{provider_label}: {fail}"
    raw = (status.get("raw_history_status") or "").strip()
    if raw and raw.lower() != "failed":
        return f"{provider_label}: download failed ({raw})"
    return f"{provider_label}: download failed"


async def wait_for_download_completion(
    sabnzbd: SABnzbd,
    nzo_id: str,
    max_retries: int = 60,
    retry_interval: int = 5,
    min_progress: float = 100.0,
    provider_label: str = "SABnzbd",
    *,
    stream_name: str | None = None,
    sab_auto_retry_failed: int = 1,
) -> dict:
    """Wait for an NZB download to reach a terminal history state (completed or failed).

    ``min_progress`` is kept for backward compatibility; completion is determined only when
    the job appears in history as **completed**, not when the queue percentage hits 100%
    (verify/repair/extract may still be running).

    For failures whose ``fail_message`` looks transient, calls SABnzbd's ``mode=retry``
    (``value=<nzo_id>``) up to ``sab_auto_retry_failed`` times before surfacing the error.
    Downloader stacks that do not implement retry (e.g. NzbDAV) return ``status: false``
    and behave as before.

    Args:
        sabnzbd: SABnzbd-compatible client (SABnzbd or NzbDAV)
        nzo_id: NZO ID of the download
        max_retries: Maximum number of retries
        retry_interval: Seconds between retries
        min_progress: Unused (legacy)
        provider_label: Product name for error messages (e.g. ``NzbDAV``)
        stream_name: Stream display name used to re-resolve ``nzo_id`` after a retry
        sab_auto_retry_failed: Max SAB ``mode=retry`` attempts for retryable messages

    Returns:
        Download status dict

    Raises:
        ProviderException: If download doesn't complete in time
    """
    del min_progress
    retries = 0
    sab_retries_used = 0
    while retries < max_retries:
        status = await sabnzbd.get_nzb_status(nzo_id)
        if not status:
            raise ProviderException(
                f"{provider_label}: download not found (missing from queue and history)",
                USENET_TRANSFER_ERROR_VIDEO,
            )

        norm = (status.get("status") or "unknown").strip().lower()
        if norm == "failed":
            fail_raw = (status.get("fail_message") or "").strip()
            if (
                sab_auto_retry_failed > 0
                and sab_retries_used < sab_auto_retry_failed
                and stream_name
                and _sab_fail_message_is_auto_retryable(fail_raw)
            ):
                if await sabnzbd.retry_failed_history_item(nzo_id):
                    sab_retries_used += 1
                    retries = 0
                    logging.info(
                        "%s: SAB mode=retry re-queued failed job (%s)",
                        provider_label,
                        fail_raw[:160],
                    )
                    await asyncio.sleep(2)
                    found = await sabnzbd.find_download_by_name(stream_name)
                    if found and found.get("nzo_id"):
                        nzo_id = str(found["nzo_id"])
                    continue
            raise ProviderException(
                _sab_download_failed_user_message(status, provider_label=provider_label),
                USENET_TRANSFER_ERROR_VIDEO,
            )
        if norm == "completed":
            return status

        await asyncio.sleep(retry_interval)
        retries += 1

    raise ProviderException(
        f"{provider_label}: download did not finish in time; check queue/history for errors",
        USENET_TRANSFER_ERROR_VIDEO,
    )


async def raise_no_matching_usenet_video_file(
    client: SABnzbd,
    nzo_id: str,
    last_status: dict,
    folder_hint: str,
    *,
    provider_label: str = "SABnzbd",
) -> None:
    """Raise when WebDAV lists no playable file but the SAB API reported completion."""
    refreshed = await client.get_nzb_status(nzo_id)
    st = refreshed if refreshed else last_status
    fail = (st.get("fail_message") or "").strip()
    if fail:
        raise ProviderException(f"{provider_label}: {fail}", USENET_TRANSFER_ERROR_VIDEO)
    raw = (st.get("raw_history_status") or "").strip()
    norm = (st.get("status") or "").strip().lower()
    if norm == "failed" or (raw and raw.lower() == "failed"):
        raise ProviderException(
            _sab_download_failed_user_message(st, provider_label=provider_label),
            USENET_TRANSFER_ERROR_VIDEO,
        )
    raise ProviderException(
        f"{provider_label}: job finished but no playable video was found under WebDAV "
        f"(folder {folder_hint!r}). Open {provider_label} history for repair/unpack/par errors "
        f"(e.g. missing articles, bad RAR set).",
        "no_video_file_found.mp4",
    )


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
    episode_air_date = kwargs.get("episode_air_date")

    async with initialize_sabnzbd(streaming_provider) as sabnzbd:
        # Check if download already exists
        existing = await sabnzbd.find_download_by_name(stream.name)

        if existing and existing["status"] == "completed":
            # Download exists and is complete, find the file
            async with initialize_webdav(streaming_provider) as webdav:
                selected_file = await find_file_in_sabnzbd_downloads(
                    webdav,
                    streaming_provider,
                    existing["filename"],
                    stream,
                    filename,
                    season,
                    episode,
                    episode_air_date,
                )
                if selected_file:
                    return generate_webdav_url(streaming_provider, selected_file)
            await raise_no_matching_usenet_video_file(
                sabnzbd,
                existing["nzo_id"],
                existing,
                existing.get("filename") or stream.name,
                provider_label="SABnzbd",
            )

        # Add the NZB if not exists or not complete
        if not existing:
            if stream.nzb_url:
                nzo_id = await sabnzbd.add_nzb_by_url(stream.nzb_url, category, stream.name)
            else:
                raise ProviderException("No NZB URL available for this stream", USENET_TRANSFER_ERROR_VIDEO)
        else:
            nzo_id = existing["nzo_id"]

        # Wait for completion
        status = await wait_for_download_completion(
            sabnzbd,
            nzo_id,
            max_retries,
            retry_interval,
            stream_name=stream.name,
        )

        # Find the file in completed downloads
        async with initialize_webdav(streaming_provider) as webdav:
            selected_file = await find_file_in_sabnzbd_downloads(
                webdav,
                streaming_provider,
                status["filename"],
                stream,
                filename,
                season,
                episode,
                episode_air_date,
            )
            if not selected_file:
                await raise_no_matching_usenet_video_file(
                    sabnzbd,
                    nzo_id,
                    status,
                    status.get("filename") or stream.name,
                    provider_label="SABnzbd",
                )

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
