import logging

import scrapy
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK

from db import crud
from db.database import get_async_session
from utils import torrent


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
            raise DropItem(f"No torrent link found in item: {item}")

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
        return item


class MagnetDownloadAndParsePipeline:
    async def process_item(self, item):
        magnet_link = item.get("magnet_link")

        if not magnet_link:
            raise DropItem(f"No magnet link found in item: {item}")

        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise DropItem(f"Failed to parse info_hash from magnet link: {magnet_link}")

        async for session in get_async_session():
            torrent_stream = await crud.get_stream_by_info_hash(session, info_hash)

        if torrent_stream:
            if item.get("expected_sources") and torrent_stream.source not in item["expected_sources"]:
                logging.info(
                    "Source mismatch for %s: %s != %s. Trying to re-create the data",
                    torrent_stream.name,
                    item["source"],
                    torrent_stream.source,
                )
                async for session in get_async_session():
                    await crud.delete_torrent_stream(session, info_hash)
            else:
                raise DropItem(f"Torrent stream already exists: {torrent_stream.name} from {torrent_stream.source}")

        torrent_metadata = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers)

        if not torrent_metadata:
            if item.get("file_data"):
                return item
            raise DropItem(f"Failed to extract torrent metadata: {item}")

        item.update(torrent_metadata[0])
        return item
