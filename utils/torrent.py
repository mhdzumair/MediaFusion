import hashlib
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Awaitable, Iterable, AsyncIterator, Optional, TypeVar
from urllib.parse import quote

import PTN
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
from torf import Magnet

# remove logging from demagnetize
logging.getLogger("demagnetize").setLevel(logging.CRITICAL)

TRACKERS = [
    "http://tracker3.itzmx.com:8080/announce",
    "udp://9.rarbg.me:2710/announce",
    "udp://9.rarbg.to:2710/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.pomf.se:80/announce",
    "udp://tracker.tiny-vps.com:6969/announce",
]


def extract_torrent_metadata(content: bytes) -> dict:
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

        for idx, file in enumerate(files):
            filename = (
                file[b"path"][0].decode()
                if b"files" in info
                else file[b"name"].decode()
            )
            parsed_data = PTN.parse(filename)
            file_data.append(
                {
                    "filename": filename,
                    "size": file[b"length"],
                    "index": idx,
                    "season": parsed_data.get("season"),
                    "episode": parsed_data.get("episode"),
                }
            )

        announce_list = [
            tracker[0].decode() for tracker in torrent_data.get(b"announce-list", [])
        ]
        torrent_name = info.get(b"name", b"").decode() or file_data[0]["filename"]
        largest_file = max(file_data, key=lambda x: x["size"])

        return {
            **PTN.parse(torrent_name),
            "info_hash": info_hash,
            "announce_list": announce_list,
            "total_size": total_size,
            "file_data": file_data,
            "torrent_name": torrent_name,
            "largest_file": largest_file,
        }
    except Exception as e:
        logging.error(f"Error occurred: {e}")
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
        [
            demagnetizer.demagnetize(Magnet(xt=info_hash, tr=trackers or TRACKERS))
            for info_hash in info_hashes
        ],
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
    global TRACKERS

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt"
        )
        if response.status_code == 200:
            trackers = [tracker for tracker in response.text.split("\n") if tracker]
            TRACKERS.extend(trackers)
            TRACKERS = list(set(TRACKERS))

            logging.info(f"Loaded {len(trackers)} trackers. Total: {len(TRACKERS)}")
        else:
            logging.error(f"Failed to load trackers: {response.status_code}")
