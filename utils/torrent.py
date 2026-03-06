import hashlib
import json
import logging
import re
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from os.path import basename
from typing import TypeVar
from urllib.parse import quote

import anyio
import bencodepy
import httpx
import PTT
from anyio import (
    CapacityLimiter,
    create_memory_object_stream,
    create_task_group,
)
from anyio.streams.memory import MemoryObjectSendStream
from demagnetize.core import Demagnetizer
from torf import Magnet, MagnetError

import utils.runtime_const
from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.lock import acquire_redis_lock, release_redis_lock
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import TRACKERS
from utils.validation_helper import is_video_file

_VALID_TRACKER_SCHEMES = ("http://", "https://", "udp://", "wss://")
_TRACKERS_CACHE_KEY = "mediafusion:trackers:best:v1"
_TRACKERS_REFRESH_LOCK_KEY = "mediafusion:trackers:refresh_lock"
_TRACKERS_CACHE_TTL_SECONDS = 60 * 60 * 24
_TRACKERS_CACHE_WAIT_SECONDS = 5
_MAX_MAGNET_TRACKERS = 30
_INFO_HASH_HEX_RE = re.compile(r"^[a-fA-F0-9]{40}$")
_INFO_HASH_BASE32_RE = re.compile(r"^[A-Z2-7]{32}$")


def _filter_valid_trackers(trackers: list[str]) -> list[str]:
    """Filter out tracker URLs with invalid schemes (e.g. asterisk-prefixed URLs)."""
    return [t for t in trackers if any(t.startswith(s) for s in _VALID_TRACKER_SCHEMES)]


def _merge_trackers(trackers: list[str]) -> None:
    """Merge trackers into runtime tracker set for this process."""
    valid_trackers = _filter_valid_trackers(trackers)
    if not valid_trackers:
        return
    utils.runtime_const.TRACKERS.extend(valid_trackers)
    utils.runtime_const.TRACKERS = list(set(utils.runtime_const.TRACKERS))


async def _get_cached_best_trackers() -> list[str]:
    cached_trackers = await REDIS_ASYNC_CLIENT.get(_TRACKERS_CACHE_KEY)
    if not cached_trackers:
        return []

    try:
        if isinstance(cached_trackers, bytes):
            cached_trackers = cached_trackers.decode("utf-8")
        if not isinstance(cached_trackers, str):
            return []

        parsed_trackers = json.loads(cached_trackers)
        if not isinstance(parsed_trackers, list):
            return []

        return _filter_valid_trackers([tracker for tracker in parsed_trackers if isinstance(tracker, str)])
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        logging.warning("Invalid best trackers payload in Redis cache")
        return []


# remove logging from demagnetize
logging.getLogger("demagnetize").setLevel(logging.CRITICAL)


def _normalize_unix_timestamp(value: int | float | bytes | str | None) -> int | None:
    """Normalize bencoded timestamp values to int seconds."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_torrent_metadata(
    content: bytes,
    parsed_data: dict = None,
    is_raise_error: bool = False,
    episode_name_parser: str = None,
) -> dict:
    try:
        torrent_data: OrderedDict = bencodepy.decode(content)
        info = torrent_data[b"info"]
        info_encoded = bencodepy.encode(info)
        m = hashlib.sha1()
        m.update(info_encoded)
        info_hash = m.hexdigest()

        # Extract file size, file list, and announce list
        files = info[b"files"] if b"files" in info else [info]
        total_size = sum(file[b"length"] for file in files)
        created_at = torrent_data.get(b"creation date", 0)

        announce_list = [tracker[0].decode() for tracker in torrent_data.get(b"announce-list", []) if tracker]
        torrent_name = info.get(b"name", b"").decode()
        if not torrent_name:
            logging.warning("Torrent name is empty. Skipping")
            if is_raise_error:
                raise ValueError("Torrent name is empty")
            return {}

        metadata = {
            "info_hash": info_hash.lower(),
            "announce_list": announce_list,
            "total_size": total_size,
            "torrent_name": torrent_name,
            "torrent_file": content,
        }
        if parsed_data:
            metadata.update(parsed_data)
        else:
            metadata.update(PTT.parse_title(torrent_name, True))

        if is_contain_18_plus_keywords(torrent_name):
            logging.warning(f"Torrent name contains 18+ keywords: {torrent_name}. Skipping")
            if is_raise_error:
                raise ValueError("Torrent name contains 18+ keywords")
            return {}

        created_at_int = _normalize_unix_timestamp(created_at)
        if created_at_int:
            # Convert to UTC datetime
            metadata["created_at"] = datetime.fromtimestamp(created_at_int, tz=UTC)

        file_data = []
        seasons = set()
        episodes = set()

        # Compile episode name parser pattern if provided
        episode_pattern = None
        if episode_name_parser:
            try:
                episode_pattern = re.compile(episode_name_parser, re.IGNORECASE)
            except re.error as e:
                logging.warning(f"Invalid episode name parser regex: {e}")
                episode_pattern = None

        for idx, file in enumerate(files):
            full_path = "/".join([p.decode() for p in file[b"path"]]) if b"files" in info else None
            filename = basename(full_path) if full_path else file[b"name"].decode()
            if not is_video_file(filename):
                continue
            if "sample" in filename.lower():
                logging.warning(f"Skipping sample file: {filename}")
                continue
            episode_parsed_data = PTT.parse_title(filename)
            seasons.update(episode_parsed_data.get("seasons", []))
            episodes.update(episode_parsed_data.get("episodes", []))
            season_number = episode_parsed_data["seasons"][0] if episode_parsed_data.get("seasons") else None
            if season_number is None and metadata.get("seasons") and len(metadata["seasons"]) == 1:
                season_number = metadata["seasons"][0]
            episode_number = episode_parsed_data["episodes"][0] if episode_parsed_data.get("episodes") else None
            if episode_number is None and metadata.get("episodes") and len(metadata["episodes"]) == 1:
                episode_number = metadata["episodes"][0]

            # Extract episode title using custom parser if provided
            episode_title = episode_parsed_data.get("title")
            if episode_pattern:
                match = episode_pattern.search(filename)
                if match:
                    # If the pattern has named groups, use them
                    if match.groupdict():
                        groups = match.groupdict()
                        # Look for common episode name patterns
                        extracted_name = (
                            groups.get("episode_name")
                            or groups.get("title")
                            or groups.get("name")
                            or groups.get("event")
                        )
                        if extracted_name:
                            # Clean up the episode name by replacing dots with spaces
                            episode_title = extracted_name.replace(".", " ").strip()
                    else:
                        # If no named groups, use the first group or full match
                        if match.groups():
                            episode_title = match.group(1).replace(".", " ").strip()
                        else:
                            episode_title = match.group(0).replace(".", " ").strip()

            file_data.append(
                {
                    "filename": filename,
                    "size": file[b"length"],
                    "index": idx,
                    "season_number": season_number,
                    "episode_number": episode_number,
                    "episode_title": episode_title,
                }
            )
        if not file_data:
            logging.warning(f"No video files found in torrent. Skipping. Found: {files}")
            if is_raise_error:
                raise ValueError("No video files found in torrent")
            return {}

        largest_file = max(file_data, key=lambda x: x["size"])

        metadata.update(
            {
                "largest_file": largest_file,
                "file_data": file_data,
            }
        )

        if not metadata.get("seasons"):
            metadata["seasons"] = list(seasons)
        if not metadata.get("episodes"):
            metadata["episodes"] = list(episodes)

        return metadata
    except ValueError as e:
        if is_raise_error:
            raise e
        return {}
    except Exception as e:
        logging.exception(f"Error occurred: {e}")
        if is_raise_error:
            raise ValueError(f"Failed to extract torrent metadata from torrent: {e}")
        return {}


def _normalize_info_hash_for_magnet(info_hash: str) -> str:
    normalized = str(info_hash or "").strip()
    if not normalized:
        return ""

    if normalized.lower().startswith("magnet:?"):
        parsed_hash, _ = parse_magnet(normalized)
        if parsed_hash:
            normalized = parsed_hash

    if _INFO_HASH_HEX_RE.fullmatch(normalized):
        return normalized.lower()

    if _INFO_HASH_BASE32_RE.fullmatch(normalized.upper()):
        return normalized.upper()

    return ""


def convert_info_hash_to_magnet(info_hash: str, trackers: list[str]) -> str:
    normalized_info_hash = _normalize_info_hash_for_magnet(info_hash)
    if not normalized_info_hash:
        raise ValueError(f"Invalid torrent info hash: {info_hash}")

    raw_trackers = []
    for tracker in trackers or []:
        if isinstance(tracker, str):
            stripped = tracker.strip()
            if stripped:
                raw_trackers.append(stripped)

    valid_trackers = _filter_valid_trackers(raw_trackers)
    unique_trackers = list(dict.fromkeys(valid_trackers))
    trimmed_trackers = unique_trackers[:_MAX_MAGNET_TRACKERS]

    if len(raw_trackers) != len(valid_trackers):
        logging.debug(
            "Dropped %d invalid trackers while building magnet for info_hash=%s",
            len(raw_trackers) - len(valid_trackers),
            normalized_info_hash,
        )
    if len(unique_trackers) > _MAX_MAGNET_TRACKERS:
        logging.debug(
            "Trimmed magnet trackers from %d to %d for info_hash=%s",
            len(unique_trackers),
            _MAX_MAGNET_TRACKERS,
            normalized_info_hash,
        )

    magnet_link = f"magnet:?xt=urn:btih:{normalized_info_hash}"
    for tracker in trimmed_trackers:
        magnet_link += f"&tr={quote(tracker, safe='')}"
    return magnet_link


T = TypeVar("T")


@asynccontextmanager
async def acollect(
    coros: Iterable[Awaitable[T]],
    limit: CapacityLimiter | None = None,
    timeout: int = 30,
) -> AsyncIterator[AsyncIterator[T]]:
    async with create_task_group() as tg:
        sender, receiver = create_memory_object_stream[T]()
        async with sender:
            for c in coros:
                tg.start_soon(_acollect_pipe, c, limit, sender.clone(), timeout)
        async with receiver:
            yield receiver


async def _acollect_pipe(
    coro: Awaitable[T],
    limit: CapacityLimiter | None,
    sender: MemoryObjectSendStream[T],
    timeout: int,
) -> None:
    async with AsyncExitStack() as stack:
        if limit is not None:
            await stack.enter_async_context(limit)
        await stack.enter_async_context(sender)
        try:
            with anyio.fail_after(timeout):
                value = await coro
            await sender.send(value)
        except Exception as e:
            # Send the exception instead of the value
            await sender.send(e)


async def info_hashes_to_torrent_metadata(
    info_hashes: list[str],
    trackers: list[str],
    episode_name_parser: str = None,
    is_raise_error: bool = False,
) -> list[dict]:
    torrents_data = []

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        if is_raise_error:
            raise ValueError("Fetching torrent metadata from P2P is disabled")
        logging.info("Fetching torrent metadata from P2P is disabled")
        return torrents_data

    demagnetizer = Demagnetizer()
    safe_trackers = _filter_valid_trackers(trackers) or TRACKERS
    async with acollect(
        coros=[demagnetizer.demagnetize(Magnet(xt=info_hash, tr=safe_trackers)) for info_hash in info_hashes],
        limit=CapacityLimiter(10),
        timeout=60,
    ) as async_iterator:
        async for torrent_result in async_iterator:
            try:
                if isinstance(torrent_result, Exception):
                    pass
                else:
                    torrents_data.append(
                        extract_torrent_metadata(
                            torrent_result.dump(),
                            is_raise_error=is_raise_error,
                            episode_name_parser=episode_name_parser,
                        )
                    )
            except Exception as e:
                if is_raise_error:
                    raise e
                logging.error(f"Error processing torrent: {e}")

    return torrents_data


async def init_best_trackers():
    # Load best trackers from Redis cache when available.
    cached_trackers = await _get_cached_best_trackers()
    if cached_trackers:
        _merge_trackers(cached_trackers)
        logging.debug(
            f"Loaded {len(cached_trackers)} trackers from Redis cache. Total: {len(utils.runtime_const.TRACKERS)}"
        )
        return

    # One worker/pod fetches from upstream and publishes to Redis.
    acquired, lock = await acquire_redis_lock(_TRACKERS_REFRESH_LOCK_KEY, timeout=60, block=False)
    if not acquired:
        logging.debug("Another worker is fetching best trackers. Waiting for Redis cache warm-up.")
        for _ in range(_TRACKERS_CACHE_WAIT_SECONDS):
            await anyio.sleep(1)
            cached_trackers = await _get_cached_best_trackers()
            if cached_trackers:
                _merge_trackers(cached_trackers)
                logging.debug(
                    f"Loaded {len(cached_trackers)} trackers from Redis cache. Total: {len(utils.runtime_const.TRACKERS)}"
                )
                return
        return

    # get the best trackers from https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt
    try:
        # Another process may have populated the cache while we were acquiring the lock.
        cached_trackers = await _get_cached_best_trackers()
        if cached_trackers:
            _merge_trackers(cached_trackers)
            logging.debug(
                f"Loaded {len(cached_trackers)} trackers from Redis cache. Total: {len(utils.runtime_const.TRACKERS)}"
            )
            return

        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get(
                "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
                timeout=30,
            )
            if response.status_code == 200:
                trackers = _filter_valid_trackers([tracker for tracker in response.text.split("\n") if tracker])
                await REDIS_ASYNC_CLIENT.set(_TRACKERS_CACHE_KEY, json.dumps(trackers), ex=_TRACKERS_CACHE_TTL_SECONDS)
                _merge_trackers(trackers)

                logging.info(f"Loaded {len(trackers)} trackers. Total: {len(utils.runtime_const.TRACKERS)}")
            else:
                logging.error(f"Failed to load trackers: {response.status_code}")
    except (httpx.ConnectTimeout, Exception) as e:
        logging.error(f"Failed to load trackers: {e}")
    finally:
        await release_redis_lock(lock)


def parse_magnet(magnet_link: str) -> tuple[str, list[str]]:
    """
    Parse magnet link and return info hash and trackers
    """
    try:
        magnet = Magnet.from_string(magnet_link)
    except MagnetError:
        return "", []
    return magnet.infohash.lower(), _filter_valid_trackers(magnet.tr)


def get_info_hash_from_magnet(magnet_link: str) -> str:
    info_hash, _ = parse_magnet(magnet_link)
    return info_hash
