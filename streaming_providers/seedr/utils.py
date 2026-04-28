import asyncio
import logging
import math
import os
import re
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Any

from seedrcc import AsyncSeedr, Token
from seedrcc.exceptions import APIError, AuthenticationError, NetworkError, ServerError

from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import StreamingProvider, TorrentStreamData
from streaming_providers.exceptions import ProviderException
from streaming_providers.parser import select_file_index_from_torrent
from utils import crypto


class TorrentStatus(Enum):
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    NOT_FOUND = "not_found"


SEEDR_TORRENT_DETAILS_CONCURRENCY = 4
SEEDR_PASSWORD_TOKEN_TTL_SECONDS = 50 * 60


def clean_filename(name: str | None, replace: str = "") -> str:
    if not name:
        return ""
    return re.sub(r"[^a-zA-Z0-9 .,;:_~\-()]", replace, name)


def _seedr_password_cache_key(email: str, password: str) -> str:
    return f"seedr:pw:{crypto.get_text_hash(email + password, full_hash=True)}"


async def _load_cached_password_token(email: str, password: str) -> str | None:
    encrypted = await REDIS_ASYNC_CLIENT.get(_seedr_password_cache_key(email, password))
    if not encrypted:
        return None
    try:
        return crypto.decrypt_text(encrypted, password)
    except Exception:
        logging.warning("Failed to decrypt cached Seedr password token; re-authenticating")
        return None


async def _store_password_token(email: str, password: str, token_b64: str) -> None:
    await REDIS_ASYNC_CLIENT.set(
        _seedr_password_cache_key(email, password),
        crypto.encrypt_text(token_b64, password),
        ex=SEEDR_PASSWORD_TOKEN_TTL_SECONDS,
    )


async def _invalidate_cached_password_token(email: str, password: str) -> None:
    await REDIS_ASYNC_CLIENT.delete(_seedr_password_cache_key(email, password))


async def _login_with_password(email: str, password: str) -> str:
    """Exchange email/password for a Seedr access token, returned as base64 JSON."""
    try:
        seedr = await AsyncSeedr.from_password(email, password)
        token_b64 = seedr.token.to_base64()
        await seedr.close()
        return token_b64
    except AuthenticationError as error:
        logging.warning("Seedr password login failed: %s", error)
        raise ProviderException("Invalid Seedr credentials", "invalid_credentials.mp4") from error
    except NetworkError as error:
        logging.warning("Seedr password login network error: %s", error)
        if "timeout" in str(error).lower() or "timed out" in str(error).lower():
            raise ProviderException("Seedr request timed out", "debrid_service_down_error.mp4") from error
        raise ProviderException("Seedr request failed. Please try again.", "debrid_service_down_error.mp4") from error


async def _resolve_seedr_token(streaming_provider: StreamingProvider) -> tuple[Token, bool]:
    """Return (Token, used_password_login).

    Static token takes priority. Otherwise use cached password-login token,
    or perform a fresh login when no cached entry exists.
    """
    if streaming_provider.token:
        try:
            return Token.from_base64(streaming_provider.token), False
        except Exception:
            raise ProviderException("Invalid Seedr token", "invalid_token.mp4")

    if not (streaming_provider.email and streaming_provider.password):
        raise ProviderException(
            "Seedr credentials missing. Provide an API token or email/password.",
            "invalid_token.mp4",
        )

    cached_b64 = await _load_cached_password_token(streaming_provider.email, streaming_provider.password)
    if cached_b64:
        try:
            return Token.from_base64(cached_b64), True
        except Exception:
            logging.warning("Cached Seedr token has invalid format; re-authenticating")
            await _invalidate_cached_password_token(streaming_provider.email, streaming_provider.password)

    token_b64 = await _login_with_password(streaming_provider.email, streaming_provider.password)
    await _store_password_token(streaming_provider.email, streaming_provider.password, token_b64)
    return Token.from_base64(token_b64), True


@asynccontextmanager
async def get_seedr_client(streaming_provider: StreamingProvider) -> AsyncGenerator[AsyncSeedr, Any]:
    """Context manager providing an authenticated AsyncSeedr client.

    Accepts either a pre-issued token (OAuth device flow) or email+password.
    Password-login tokens are cached in Redis and auto-refreshed by the library.
    """
    token, used_password_login = await _resolve_seedr_token(streaming_provider)

    async def _on_token_refresh(new_token: Token) -> None:
        if used_password_login:
            await _store_password_token(streaming_provider.email, streaming_provider.password, new_token.to_base64())

    try:
        async with AsyncSeedr(token=token, on_token_refresh=_on_token_refresh) as seedr:
            yield seedr
    except AuthenticationError as error:
        logging.warning("Seedr authentication failed: %s", error)
        if used_password_login:
            await _invalidate_cached_password_token(streaming_provider.email, streaming_provider.password)
        raise ProviderException("Invalid Seedr token", "invalid_token.mp4") from error
    except NetworkError as error:
        error_str = str(error).lower()
        if "timeout" in error_str or "timed out" in error_str:
            logging.warning("Seedr request timed out: %s", error)
            raise ProviderException("Seedr request timed out", "debrid_service_down_error.mp4") from error
        logging.warning("Seedr request failed: %s", error)
        raise ProviderException("Seedr request failed. Please try again.", "debrid_service_down_error.mp4") from error
    except ServerError as error:
        logging.warning("Seedr server error: %s", error)
        raise ProviderException("Seedr server error", "debrid_service_down_error.mp4") from error
    except APIError as error:
        logging.warning("Seedr API error: %s", error)
        raise ProviderException("Seedr server error", "debrid_service_down_error.mp4") from error
    except ProviderException:
        raise
    except Exception as error:
        logging.exception("Unexpected Seedr client error")
        raise ProviderException("Seedr server error", "debrid_service_down_error.mp4") from error


async def get_folder_by_info_hash(seedr: AsyncSeedr, info_hash: str):
    """Find a folder by info_hash. Returns a Folder model or None."""
    contents = await seedr.list_contents()
    return next((f for f in contents.folders if f.name == info_hash), None)


async def check_torrent_status(seedr: AsyncSeedr, info_hash: str) -> tuple[TorrentStatus, Any]:
    root = await seedr.list_contents()

    # Seedr tracks active downloads in the root torrents list regardless of target folder.
    for torrent in root.torrents:
        if torrent.hash.lower() == info_hash.lower():
            return TorrentStatus.DOWNLOADING, torrent

    folder = next((f for f in root.folders if f.name == info_hash), None)
    if not folder:
        return TorrentStatus.NOT_FOUND, None

    folder_content = await seedr.list_contents(str(folder.id))

    if folder_content.torrents:
        return TorrentStatus.DOWNLOADING, folder_content.torrents[0]
    elif folder_content.folders:
        return TorrentStatus.COMPLETED, folder_content.folders[0]

    return TorrentStatus.NOT_FOUND, None


async def _delete_folder_contents(seedr: AsyncSeedr, folder_id: str, delete_torrents: bool) -> None:
    sub_content = await seedr.list_contents(folder_id)

    for torrent in sub_content.torrents:
        if not delete_torrents:
            raise ProviderException("An existing torrent is being downloaded", "torrent_downloading.mp4")
        await seedr.delete_torrent(str(torrent.id))

    for file_entry in sub_content.files:
        await seedr.delete_file(str(file_entry.folder_file_id))

    for subfolder in sub_content.folders:
        await _delete_folder_contents(seedr, str(subfolder.id), delete_torrents=delete_torrents)
        await seedr.delete_folder(str(subfolder.id))


async def ensure_space_available(seedr: AsyncSeedr, required_space: int | float) -> None:
    contents = await seedr.list_contents()
    if required_space != math.inf and required_space > contents.space_max:
        raise ProviderException("Not enough space in Seedr account", "not_enough_space.mp4")

    available_space = contents.space_max - contents.space_used

    if available_space >= required_space:
        return

    folders = sorted(
        contents.folders,
        key=lambda x: (
            -x.size,
            x.last_update or datetime.min,
        ),
    )

    for folder in folders:
        if available_space >= required_space:
            break
        await _delete_folder_contents(seedr, str(folder.id), delete_torrents=False)
        await seedr.delete_folder(str(folder.id))
        available_space += folder.size


async def add_torrent(seedr: AsyncSeedr, magnet_link: str, info_hash: str, stream: TorrentStreamData) -> None:
    try:
        await seedr.add_folder(info_hash)
    except APIError as exc:
        # 409 = folder left behind by a previous failed attempt — reuse it.
        if exc.response is None or exc.response.status_code != 409:
            raise

    folder = await get_folder_by_info_hash(seedr, info_hash)
    if not folder:
        raise ProviderException("Failed to create folder", "folder_creation_error.mp4")

    try:
        if stream.torrent_file:
            with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
                tmp.write(stream.torrent_file)
                tmp_path = tmp.name
            try:
                await seedr.add_torrent(torrent_file=tmp_path, folder_id=str(folder.id))
            finally:
                os.unlink(tmp_path)
        else:
            await seedr.add_torrent(magnet_link=magnet_link, folder_id=str(folder.id))
    except APIError as exc:
        # error_type comes from data.get("result"), but Seedr uses "reason_phrase" for HTTP 4xx
        # responses (e.g. 413 for queue-full). Check both fields.
        error_type = exc.error_type or ""
        if not error_type and exc.response is not None:
            try:
                error_type = exc.response.json().get("reason_phrase", "")
            except Exception:
                pass

        error_messages = {
            "not_enough_space_added_to_wishlist": ("Not enough space in Seedr account", "not_enough_space.mp4"),
            "not_enough_space_wishlist_full": ("Not enough space in Seedr account", "not_enough_space.mp4"),
            "queue_full_added_to_wishlist": (
                "Seedr is already downloading another torrent. Please wait for it to finish.",
                "queue_full.mp4",
            ),
        }
        if error_type in error_messages:
            msg, video = error_messages[error_type]
            raise ProviderException(msg, video)
        raise ProviderException("Error transferring magnet link to Seedr", "transfer_error.mp4") from exc


async def wait_for_completion(seedr: AsyncSeedr, info_hash: str, max_retries: int = 1, retry_interval: int = 1) -> None:
    for _ in range(max_retries):
        status, data = await check_torrent_status(seedr, info_hash)

        if status == TorrentStatus.COMPLETED:
            return
        elif status == TorrentStatus.DOWNLOADING and data.progress == "100":
            return

        await asyncio.sleep(retry_interval)

    raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")


async def clean_names(seedr: AsyncSeedr, folder_id: str) -> None:
    content = await seedr.list_contents(folder_id)
    for file in content.files:
        clean_name = clean_filename(file.name)
        if file.name != clean_name:
            await seedr.rename_file(str(file.folder_file_id), clean_name)


async def get_files_from_folder(seedr: AsyncSeedr, folder_id: str) -> list[dict[str, Any]]:
    """Recursively get all files from a folder. Returns raw dicts for parser compatibility."""
    content = await seedr.list_contents(folder_id)
    files = [f.get_raw() for f in content.files]
    for folder in content.folders:
        files.extend(await get_files_from_folder(seedr, str(folder.id)))
    return files


async def get_video_url_from_seedr(
    info_hash: str,
    magnet_link: str,
    streaming_provider: StreamingProvider,
    stream: TorrentStreamData,
    filename: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    **kwargs,
) -> str:
    async with get_seedr_client(streaming_provider) as seedr:
        status, data = await check_torrent_status(seedr, info_hash)

        if status == TorrentStatus.NOT_FOUND:
            await ensure_space_available(seedr, stream.size)
            await add_torrent(seedr, magnet_link, info_hash, stream)
            await wait_for_completion(seedr, info_hash)
            status, data = await check_torrent_status(seedr, info_hash)

        if status == TorrentStatus.DOWNLOADING:
            raise ProviderException("Torrent not downloaded yet.", "torrent_not_downloaded.mp4")

        if status != TorrentStatus.COMPLETED or not data:
            raise ProviderException("Failed to get completed torrent", "torrent_error.mp4")

        await clean_names(seedr, str(data.id))

        folder_content = await get_files_from_folder(seedr, str(data.id))
        file_index = await select_file_index_from_torrent(
            torrent_info={"files": folder_content},
            torrent_stream=stream,
            filename=clean_filename(filename),
            season=season,
            episode=episode,
        )

        selected_file = folder_content[file_index]
        if not selected_file["play_video"]:
            raise ProviderException("No matching file available", "no_matching_file.mp4")

        video_data = await seedr.fetch_file(str(selected_file["folder_file_id"]))
        return video_data.url


async def update_seedr_cache_status(
    streams: list[TorrentStreamData], streaming_provider: StreamingProvider, **kwargs
) -> None:
    try:
        async with get_seedr_client(streaming_provider) as seedr:
            contents = await seedr.list_contents()
            folder_map = {folder.name: folder.id for folder in contents.folders if len(folder.name) in (40, 32)}
            if not folder_map:
                return
            for stream in streams:
                if stream.info_hash in folder_map:
                    folder_content = await seedr.list_contents(str(folder_map[stream.info_hash]))
                    if folder_content.folders:
                        stream.cached = True
    except ProviderException:
        return


async def fetch_downloaded_info_hashes_from_seedr(streaming_provider: StreamingProvider, **kwargs) -> list[str]:
    try:
        async with get_seedr_client(streaming_provider) as seedr:
            contents = await seedr.list_contents()
            return [folder.name for folder in contents.folders if len(folder.name) in (40, 32)]
    except ProviderException:
        return []


async def fetch_torrent_details_from_seedr(streaming_provider: StreamingProvider, **kwargs) -> list[dict]:
    try:
        async with get_seedr_client(streaming_provider) as seedr:
            contents = await seedr.list_contents()
            sem = asyncio.Semaphore(SEEDR_TORRENT_DETAILS_CONCURRENCY)
            target_hashes = {str(info_hash).lower() for info_hash in kwargs.get("target_hashes", set()) if info_hash}
            hash_folders = [folder for folder in contents.folders if len(folder.name) in (40, 32)]
            if target_hashes:
                hash_folders = [folder for folder in hash_folders if folder.name.lower() in target_hashes]

            async def fetch_subfolder_files(subfolder) -> list[dict[str, Any]]:
                async with sem:
                    return await get_files_from_folder(seedr, str(subfolder.id))

            async def fetch_folder_details(folder) -> dict:
                base = {
                    "id": folder.id,
                    "hash": folder.name.lower(),
                    "filename": folder.name,
                    "size": folder.size,
                    "files": [],
                }
                try:
                    async with sem:
                        folder_content = await seedr.list_contents(str(folder.id))

                    subfolder_results = await asyncio.gather(
                        *(fetch_subfolder_files(subfolder) for subfolder in folder_content.folders)
                    )
                    files = []
                    for subfolder_files in subfolder_results:
                        for file_item in subfolder_files:
                            if file_item.get("play_video"):
                                files.append(
                                    {
                                        "id": file_item.get("folder_file_id"),
                                        "path": file_item.get("name", ""),
                                        "size": file_item.get("size", 0),
                                    }
                                )
                    base["files"] = files
                except Exception as error:
                    logging.debug("Failed to fetch Seedr folder details for id=%s: %s", folder.id, error)
                return base

            return await asyncio.gather(*(fetch_folder_details(folder) for folder in hash_folders))
    except ProviderException:
        return []


async def delete_all_torrents_from_seedr(streaming_provider: StreamingProvider, **kwargs) -> None:
    async with get_seedr_client(streaming_provider) as seedr:
        root_content = await seedr.list_contents()

        for torrent in root_content.torrents:
            await seedr.delete_torrent(str(torrent.id))

        for file_entry in root_content.files:
            await seedr.delete_file(str(file_entry.folder_file_id))

        for folder in root_content.folders:
            await _delete_folder_contents(seedr, str(folder.id), delete_torrents=True)
            await seedr.delete_folder(str(folder.id))


async def delete_torrent_from_seedr(streaming_provider: StreamingProvider, info_hash: str, **kwargs) -> bool:
    try:
        async with get_seedr_client(streaming_provider) as seedr:
            contents = await seedr.list_contents()
            for folder in contents.folders:
                if folder.name.lower() == info_hash.lower():
                    await _delete_folder_contents(seedr, str(folder.id), delete_torrents=True)
                    await seedr.delete_folder(str(folder.id))
                    return True
            return False
    except ProviderException:
        return False


async def validate_seedr_credentials(streaming_provider: StreamingProvider, **kwargs) -> dict:
    try:
        async with get_seedr_client(streaming_provider):
            return {"status": "success"}
    except ProviderException as error:
        return {
            "status": "error",
            "message": f"Failed to validate Seedr credentials: {error.message}",
        }
    except Exception as error:
        return {
            "status": "error",
            "message": f"Failed to validate Seedr credentials: {error}",
        }
