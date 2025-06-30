import hashlib
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from os.path import basename
import re
from typing import Awaitable, Iterable, AsyncIterator, Optional, TypeVar, OrderedDict
from urllib.parse import quote

import PTT
import anyio
import bencodepy
import httpx
from anyio import (
    create_task_group,
    create_memory_object_stream,
    CapacityLimiter,
)
from anyio.streams.memory import MemoryObjectSendStream
from demagnetize.core import Demagnetizer
from torf import Magnet, MagnetError

import utils.runtime_const
from db.config import settings
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import TRACKERS
from utils.validation_helper import is_video_file

# remove logging from demagnetize
logging.getLogger("demagnetize").setLevel(logging.CRITICAL)


def extract_torrent_metadata(
    content: bytes, parsed_data: dict = None, is_raise_error: bool = False, episode_name_parser: str = None
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

        announce_list = [
            tracker[0].decode() for tracker in torrent_data.get(b"announce-list", [])
        ]
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
            logging.warning(
                f"Torrent name contains 18+ keywords: {torrent_name}. Skipping"
            )
            if is_raise_error:
                raise ValueError("Torrent name contains 18+ keywords")
            return {}

        if created_at:
            # Convert to UTC datetime
            metadata["created_at"] = datetime.fromtimestamp(created_at, tz=timezone.utc)

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
            full_path = (
                "/".join([p.decode() for p in file[b"path"]])
                if b"files" in info
                else None
            )
            filename = basename(full_path) if full_path else file[b"name"].decode()
            if not is_video_file(filename):
                continue
            if "sample" in filename.lower():
                logging.warning(f"Skipping sample file: {filename}")
                continue
            episode_parsed_data = PTT.parse_title(filename)
            seasons.update(episode_parsed_data.get("seasons", []))
            episodes.update(episode_parsed_data.get("episodes", []))
            season_number = (
                episode_parsed_data["seasons"][0]
                if episode_parsed_data.get("seasons")
                else None
            )
            if (
                season_number is None
                and metadata.get("seasons")
                and len(metadata["seasons"]) == 1
            ):
                season_number = metadata["seasons"][0]
            episode_number = (
                episode_parsed_data["episodes"][0]
                if episode_parsed_data.get("episodes")
                else None
            )
            if (
                episode_number is None
                and metadata.get("episodes")
                and len(metadata["episodes"]) == 1
            ):
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
                            groups.get("episode_name") or
                            groups.get("title") or
                            groups.get("name") or
                            groups.get("event")
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
            logging.warning(
                f"No video files found in torrent. Skipping. Found: {files}"
            )
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
    except Exception as e:
        logging.exception(f"Error occurred: {e}")
        if is_raise_error:
            raise ValueError(f"Failed to extract torrent metadata from torrent: {e}")
        return {}


def convert_info_hash_to_magnet(info_hash: str, trackers: list[str]) -> str:
    magnet_link = f"magnet:?xt=urn:btih:{info_hash}"
    for tracker in set(trackers) or TRACKERS:
        encoded_tracker = quote(tracker, safe="")
        magnet_link += f"&tr={encoded_tracker}"
    return magnet_link


T = TypeVar("T")


@asynccontextmanager
async def acollect(
    coros: Iterable[Awaitable[T]],
    limit: Optional[CapacityLimiter] = None,
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
    limit: Optional[CapacityLimiter],
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
    info_hashes: list[str], trackers: list[str], episode_name_parser: str = None
) -> list[dict]:
    torrents_data = []

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        logging.info("Fetching torrent metadata from P2P is disabled")
        return torrents_data

    demagnetizer = Demagnetizer()
    async with acollect(
        coros=[
            demagnetizer.demagnetize(Magnet(xt=info_hash, tr=trackers or TRACKERS))
            for info_hash in info_hashes
        ],
        limit=CapacityLimiter(10),
        timeout=60,
    ) as async_iterator:
        async for torrent_result in async_iterator:
            try:
                if isinstance(torrent_result, Exception):
                    pass
                else:
                    torrents_data.append(
                        extract_torrent_metadata(torrent_result.dump(), episode_name_parser=episode_name_parser)
                    )
            except Exception as e:
                logging.error(f"Error processing torrent: {e}")

    return torrents_data


async def init_best_trackers():
    # get the best trackers from https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url) as client:
            response = await client.get(
                "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
                timeout=30,
            )
            if response.status_code == 200:
                trackers = [tracker for tracker in response.text.split("\n") if tracker]
                utils.runtime_const.TRACKERS.extend(trackers)
                utils.runtime_const.TRACKERS = list(set(utils.runtime_const.TRACKERS))

                logging.info(
                    f"Loaded {len(trackers)} trackers. Total: {len(utils.runtime_const.TRACKERS)}"
                )
            else:
                logging.error(f"Failed to load trackers: {response.status_code}")
    except (httpx.ConnectTimeout, Exception) as e:
        logging.error(f"Failed to load trackers: {e}")


def parse_magnet(magnet_link: str) -> tuple[str, list[str]]:
    """
    Parse magnet link and return info hash and trackers
    """
    try:
        magnet = Magnet.from_string(magnet_link)
    except MagnetError:
        return "", []
    return magnet.infohash, magnet.tr


def get_info_hash_from_magnet(magnet_link: str) -> str:
    info_hash, _ = parse_magnet(magnet_link)
    return info_hash
