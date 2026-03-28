import logging

import scrapy
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK

from db import crud
from db.database import get_async_session_context
from utils import torrent
from utils.validation_helper import is_video_file

COMMON_TAMIL_SOURCES = {"TamilMV", "TamilBlasters"}


def _video_file_data_only(item: dict) -> None:
    """Keep only playable video entries in ``file_data`` (drops HTML-parsed FLAC/images/etc.)."""
    file_data = item.get("file_data")
    if not file_data:
        return
    filtered = [
        entry for entry in file_data if isinstance(entry, dict) and is_video_file((entry.get("filename") or ""))
    ]
    if filtered:
        item["file_data"] = filtered
    else:
        item.pop("file_data", None)


def _has_episode_signals(item: dict) -> bool:
    if item.get("seasons") or item.get("episodes"):
        return True

    file_data = item.get("file_data") or []
    return any(
        isinstance(file_info, dict)
        and (file_info.get("season_number") is not None or file_info.get("episode_number") is not None)
        for file_info in file_data
    )


def _reconcile_common_tamil_type(item: dict) -> None:
    """Fix obvious movie/series misclassifications for common Tamil spiders."""
    source = item.get("source")
    declared_type = item.get("type")
    if source not in COMMON_TAMIL_SOURCES or declared_type not in {"movie", "series"}:
        return

    file_data = item.get("file_data") or []
    video_file_count = sum(1 for file_info in file_data if isinstance(file_info, dict))
    has_episode_signals = _has_episode_signals(item)

    corrected_type = None
    reason = None

    # Strong movie signal: tagged as series but no episode markers and tiny video set.
    if declared_type == "series" and not has_episode_signals and video_file_count <= 2:
        corrected_type = "movie"
        reason = f"no episode signals and only {video_file_count} video file(s)"
    # Strong series signal: tagged as movie but has episodes or many episodic files.
    elif declared_type == "movie" and (has_episode_signals or video_file_count >= 4):
        corrected_type = "series"
        reason = (
            "episode signals detected"
            if has_episode_signals
            else f"{video_file_count} video files suggest episodic content"
        )

    if not corrected_type or corrected_type == declared_type:
        return

    item["type"] = corrected_type
    if corrected_type == "series":
        item["video_type"] = "series"
    elif item.get("video_type") == "series":
        # Keep movie torrents out of series catalogs when forum placement is wrong.
        item["video_type"] = "hdrip"

    logging.warning(
        "Reconciled %s type from %s to %s for '%s' (%s)",
        source,
        declared_type,
        corrected_type,
        item.get("torrent_name") or item.get("webpage_url"),
        reason,
    )


class TorrentDownloadAndParsePipeline:
    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        pipeline.crawler = crawler
        return pipeline

    async def process_item(self, item):
        adapter = ItemAdapter(item)
        torrent_link = adapter.get("torrent_link")

        if not torrent_link:
            raise DropItem("No torrent link found in item.")

        headers = {"Referer": item.get("webpage_url")}

        response = await self.crawler.engine.download_async(
            scrapy.Request(torrent_link, callback=NO_CALLBACK, headers=headers),
        )

        if response.status != 200:
            logging.error("Failed to download torrent file: %s with status %s", response.url, response.status)
            return item

        # Validate the content-type of the response
        if "application/x-bittorrent" not in response.headers.get("Content-Type", b"").decode("utf-8", "ignore"):
            logging.error("Unexpected Content-Type for %s: %s", response.url, response.headers.get("Content-Type"))
            return item

        torrent_metadata = torrent.extract_torrent_metadata(response.body, item.get("parsed_data"))

        if not torrent_metadata:
            return item

        item.update(torrent_metadata)
        _reconcile_common_tamil_type(item)
        return item


class MagnetDownloadAndParsePipeline:
    async def process_item(self, item):
        magnet_link = item.get("magnet_link")

        if not magnet_link:
            raise DropItem("No magnet link found in item.")

        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise DropItem(f"Failed to parse info_hash from magnet link: {magnet_link}")

        async with get_async_session_context() as session:
            torrent_stream = await crud.get_stream_by_info_hash(session, info_hash, load_relations=True)

        if torrent_stream:
            stream = torrent_stream.stream
            stream_source = stream.source if stream else None
            stream_name = stream.name if stream else None
            if item.get("expected_sources") and stream_source not in item["expected_sources"]:
                logging.info(
                    "Source mismatch for %s: %s != %s. Trying to re-create the data",
                    stream_name,
                    item["source"],
                    stream_source,
                )
                async with get_async_session_context() as session:
                    await crud.delete_torrent_stream(session, info_hash)
            else:
                raise DropItem(f"Torrent stream already exists: {stream_name} from {stream_source}")

        torrent_metadata = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)

        if not torrent_metadata:
            _video_file_data_only(item)
            if not item.get("file_data"):
                raise DropItem("Failed to extract torrent metadata.")
            return item

        magnet_meta = torrent_metadata[0] or {}
        item.update(magnet_meta)

        if not magnet_meta.get("file_data"):
            _video_file_data_only(item)
            if not item.get("file_data"):
                raise DropItem("No video files in torrent; skipping non-video release.")
        return item
