import scrapy
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from db.crud import is_torrent_stream_exists
from utils import torrent


class TorrentDownloadAndParsePipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        torrent_link = adapter.get("torrent_link")

        if not torrent_link:
            raise DropItem(f"No torrent link found in item: {item}")

        response = await maybe_deferred_to_future(
            spider.crawler.engine.download(
                scrapy.Request(torrent_link, callback=NO_CALLBACK)
            )
        )

        if response.status != 200:
            spider.logger.error(
                f"Failed to download torrent file: {response.url} with status {response.status}"
            )
            raise DropItem(f"Failed to download torrent file: {response.url}")

        # Validate the content-type of the response
        if "application/x-bittorrent" not in response.headers.get(
            "Content-Type", b""
        ).decode("utf-8", "ignore"):
            spider.logger.error(
                f"Unexpected Content-Type for {response.url}: {response.headers.get('Content-Type')}"
            )
            raise DropItem(f"Unexpected Content-Type for {response.url}")

        torrent_metadata = torrent.extract_torrent_metadata(
            response.body, item.get("is_parse_ptt", True)
        )

        if not torrent_metadata:
            raise DropItem(f"Failed to extract torrent metadata: {item}")

        item.update(torrent_metadata)
        return item


class MagnetDownloadAndParsePipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        magnet_link = adapter.get("magnet_link")

        if not magnet_link:
            raise DropItem(f"No magnet link found in item: {item}")

        info_hash, trackers = torrent.parse_magnet(magnet_link)
        if not info_hash:
            raise DropItem(f"Failed to parse info_hash from magnet link: {magnet_link}")
        if await is_torrent_stream_exists(info_hash):
            raise DropItem(f"Torrent stream already exists: {info_hash}")

        torrent_metadata = await torrent.info_hashes_to_torrent_metadata(
            [info_hash], trackers
        )

        if not torrent_metadata:
            raise DropItem(f"Failed to extract torrent metadata: {item}")

        item.update(torrent_metadata[0])
        return item
