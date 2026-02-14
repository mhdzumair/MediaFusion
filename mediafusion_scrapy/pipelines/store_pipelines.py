import asyncio
import logging

from scrapy import signals
from scrapy.exceptions import DropItem

from db import crud
from db.database import get_async_session
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import TVMetaData


class QueueBasedPipeline:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.processing_task = None

    async def init(self):
        self.processing_task = asyncio.create_task(self.process_queue())

    async def close(self):
        await self.queue.join()
        self.processing_task.cancel()

    @classmethod
    def from_crawler(cls, crawler):
        p = cls()
        crawler.signals.connect(p.init, signal=signals.spider_opened)
        crawler.signals.connect(p.close, signal=signals.spider_closed)
        return p

    async def process_item(self, item, spider):
        # Instead of processing the item directly, add it to the queue
        await self.queue.put((item, spider))
        return item

    async def process_queue(self):
        logging.info("Starting processing queue")
        while True:
            item, spider = await self.queue.get()
            try:
                await self.parse_item(item, spider)
            except Exception as e:
                logging.error(f"Error processing item: {e}", exc_info=True)
            finally:
                self.queue.task_done()

    async def parse_item(self, item, spider):
        raise NotImplementedError


class EventSeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        async for session in get_async_session():
            # Get or create series metadata
            metadata = {
                "id": None,
                "title": item["title"],
                "year": item["year"],
                "poster": item.get("poster"),
                "background": item.get("background"),
                "is_poster_working": bool(item.get("poster")),
                "is_add_title_to_poster": item.get("is_add_title_to_poster", False),
            }

            series_result = await crud.get_or_create_metadata(session, metadata, "series", is_search_imdb_title=False)

            if not series_result:
                raise DropItem(f"Failed to create series metadata for: {item['title']}")

            meta_id = series_result["id"]
            logging.info("Using series %s with id %s", item["title"], meta_id)

            # Check if torrent stream exists
            existing_stream = await crud.get_stream_by_info_hash(session, item["info_hash"])

            if not existing_stream:
                # Prepare episode files
                episode_files = []
                if item.get("episodes"):
                    for ep in item["episodes"]:
                        episode_files.append(
                            {
                                "season_number": ep.get("season_number", 1),
                                "episode_number": ep.get("episode_number", 1),
                                "filename": ep.get("filename"),
                                "size": ep.get("size"),
                                "file_index": ep.get("file_index"),
                            }
                        )

                # Create torrent stream
                stream_data = {
                    "id": item["info_hash"],
                    "meta_id": meta_id,
                    "torrent_name": item["torrent_name"],
                    "announce_urls": item.get("announce_list", []),
                    "size": item["total_size"],
                    "languages": item.get("languages", []),
                    "resolution": item.get("resolution"),
                    "codec": item.get("codec"),
                    "quality": item.get("quality"),
                    "audio": item.get("audio"),
                    "hdr": item.get("hdr"),
                    "source": item["source"],
                    "uploader": item.get("uploader"),
                    "catalogs": item.get("catalog", []),
                    "created_at": item.get("created_at"),
                    "seeders": item.get("seeders"),
                    "episode_files": episode_files,
                }

                await crud.store_new_torrent_streams(session, [stream_data])
                logging.info(
                    "Added torrent stream %s for series %s",
                    item["torrent_name"],
                    item["title"],
                )

            # Organize episodes
            await crud.organize_episodes(session, meta_id)

        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item


class TVStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        async for session in get_async_session():
            await crud.save_tv_channel_metadata(session, tv_metadata)
        return item


class MovieStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "movie":
            return item

        async for session in get_async_session():
            await crud.scraper_save_movie_metadata(session, item, item.get("is_search_imdb_title", True))
        return item


class SeriesStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "series":
            return item
        async for session in get_async_session():
            await crud.scraper_save_series_metadata(session, item)
        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item


class LiveEventStorePipeline(QueueBasedPipeline):
    def __init__(self):
        super().__init__()
        self.redis = REDIS_ASYNC_CLIENT

    async def close(self):
        await super().close()
        await self.redis.aclose()

    async def parse_item(self, item, spider):
        if "title" not in item:
            raise DropItem(f"name not found in item: {item}")

        async for session in get_async_session():
            await crud.save_events_data(session, item)
        if "scraped_info_hash_key" in item:
            await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])
        return item
