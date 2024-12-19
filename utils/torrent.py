import hashlib
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Awaitable, Iterable, AsyncIterator, Optional, TypeVar
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
    content: bytes, is_parse_ptt: bool = True, is_raise_error: bool = False
) -> dict:
    try:
        torrent_data = bencodepy.decode(content)
        info = torrent_data[b"info"]
        info_encoded = bencodepy.encode(info)
        m = hashlib.sha1()
        m.update(info_encoded)
        info_hash = m.hexdigest()

        # Extract file size, file list, and announce list
        files = info[b"files"] if b"files" in info else [info]
        total_size = sum(file[b"length"] for file in files)
        file_data = []
        seasons = set()
        episodes = set()

        for idx, file in enumerate(files):
            filename = (
                "/".join([p.decode() for p in file[b"path"]])
                if b"files" in info
                else file[b"name"].decode()
            )
            if not is_video_file(filename):
                continue
            parsed_data = PTT.parse_title(filename)
            seasons.update(parsed_data.get("seasons", []))
            episodes.update(parsed_data.get("episodes", []))
            file_data.append(
                {
                    "filename": filename,
                    "size": file[b"length"],
                    "index": idx,
                    "seasons": parsed_data.get("seasons"),
                    "episodes": parsed_data.get("episodes"),
                }
            )
        if not file_data:
            logging.warning("No video files found in torrent. Skipping")
            if is_raise_error:
                raise ValueError("No video files found in torrent")
            return {}

        announce_list = [
            tracker[0].decode() for tracker in torrent_data.get(b"announce-list", [])
        ]
        torrent_name = info.get(b"name", b"").decode() or file_data[0]["filename"]
        if is_contain_18_plus_keywords(torrent_name):
            logging.warning(
                f"Torrent name contains 18+ keywords: {torrent_name}. Skipping"
            )
            if is_raise_error:
                raise ValueError("Torrent name contains 18+ keywords")
            return {}

        largest_file = max(file_data, key=lambda x: x["size"])

        metadata = {
            "info_hash": info_hash,
            "announce_list": announce_list,
            "total_size": total_size,
            "file_data": file_data,
            "torrent_name": torrent_name,
            "largest_file": largest_file,
        }
        if is_parse_ptt:
            metadata.update(PTT.parse_title(torrent_name, True))
            if not metadata["seasons"]:
                metadata["seasons"] = list(seasons)
            if not metadata["episodes"]:
                metadata["episodes"] = list(episodes)
        return metadata
    except Exception as e:
        logging.exception(f"Error occurred: {e}")
        if is_raise_error:
            raise ValueError(f"Failed to extract torrent metadata from torrent: {e}")
        return {}


async def convert_info_hash_to_magnet(info_hash: str, trackers: list[str]) -> str:
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
    info_hashes: list[str], trackers: list[str]
) -> list[dict]:
    torrents_data = []

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
                        extract_torrent_metadata(torrent_result.dump())
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
