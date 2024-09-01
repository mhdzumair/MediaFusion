import asyncio
import logging
from uuid import uuid4

from beanie import WriteRules
from scrapy import signals
from scrapy.exceptions import DropItem

from db import crud
from db.models import (
    TorrentStreams,
    Season,
    MediaFusionSeriesMetaData,
)
from db.schemas import TVMetaData
from utils.runtime_const import REDIS_ASYNC_CLIENT


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

        series = await MediaFusionSeriesMetaData.find_one(
            {"title": item["title"]}, fetch_links=True
        )

        if not series:
            meta_id = f"mf{uuid4().fields[-1]}"
            poster = item.get("poster")
            background = item.get("background")

            # Create an initial entry for the series
            series = MediaFusionSeriesMetaData(
                id=meta_id,
                title=item["title"],
                year=item["year"],
                poster=poster,
                background=background,
                streams=[],
                is_poster_working=bool(poster),
                is_add_title_to_poster=item.get("is_add_title_to_poster", False),
            )
            await series.insert()
            logging.info("Added series %s", series.title)

        meta_id = series.id

        stream = next((s for s in series.streams if s.id == item["info_hash"]), None)
        if stream is None:
            # Create the stream
            stream = TorrentStreams(
                id=item["info_hash"],
                torrent_name=item["torrent_name"],
                announce_list=item["announce_list"],
                size=item["total_size"],
                languages=item["languages"],
                resolution=item.get("resolution"),
                codec=item.get("codec"),
                quality=item.get("quality"),
                audio=item.get("audio"),
                source=item["source"],
                catalog=item["catalog"],
                created_at=item["created_at"],
                season=Season(season_number=1, episodes=item["episodes"]),
                meta_id=meta_id,
                seeders=item["seeders"],
            )
            # Add the stream to the series
            series.streams.append(stream)
            logging.info(
                "Added stream %s to series %s", stream.torrent_name, series.title
            )

        self.organize_episodes(series)

        await series.save(link_rule=WriteRules.WRITE)
        logging.info("Updated series %s", series.title)
        await self.redis.sadd(item["scraped_info_hash_key"], item["info_hash"])

        return item

    def organize_episodes(self, series):
        # Flatten all episodes from all streams and sort by release date
        all_episodes = sorted(
            (
                episode
                for stream in series.streams
                for episode in stream.season.episodes
            ),
            key=lambda e: (
                e.released.date(),
                e.filename,
            ),  # Sort primarily by released date, then by filename
        )

        # Assign episode numbers, ensuring the same title across different qualities gets the same number
        episode_number = 1
        last_title = None
        for episode in all_episodes:
            if episode.title != last_title:
                if last_title:
                    episode_number = episode_number + 1
                last_title = episode.title

            episode.episode_number = episode_number

        # Now distribute episodes back to their respective streams, ensuring they are in the correct order
        for stream in series.streams:
            stream.season.episodes.sort(
                key=lambda e: e.episode_number
            )  # Ensure episodes are ordered by episode number


class TVStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            logging.warning(f"title not found in item: {item}")
            raise DropItem(f"title not found in item: {item}")

        tv_metadata = TVMetaData.model_validate(item)
        await crud.save_tv_channel_metadata(tv_metadata)
        return item


class MovieStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "movie":
            return item

        await crud.save_movie_metadata(item, item.get("is_imdb", True))
        return item


class SeriesStorePipeline(QueueBasedPipeline):
    async def parse_item(self, item, spider):
        if "title" not in item:
            return item

        if item.get("type") != "series":
            return item
        await crud.save_series_metadata(item)
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

        await crud.save_events_data(item)
        return item
