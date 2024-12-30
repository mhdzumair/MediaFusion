import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Type

import dramatiq
import httpx

from db.config import settings
from db.crud import (
    get_movie_data_by_id,
    get_series_data_by_id,
    store_new_torrent_streams,
    get_or_create_metadata,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers.base_scraper import IndexerBaseScraper
from scrapers.jackett import JackettScraper
from scrapers.prowlarr import ProwlarrScraper
from utils.crypto import get_text_hash
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import is_contain_18_plus_keywords
from utils.wrappers import minimum_run_interval

logger = logging.getLogger(__name__)


class FeedScraper(ABC):
    def __init__(
        self, name: str, scraper_class: Type[ProwlarrScraper | JackettScraper]
    ):
        self.name = name
        self.scraper = scraper_class()
        self.processed_items_key = f"{name}_feed_scraper:processed_items"
        self.processed_items_expiry = 60 * 60 * 24 * 3  # 3 days

    async def is_item_processed(self, item_id: str) -> bool:
        return bool(
            await REDIS_ASYNC_CLIENT.sismember(self.processed_items_key, item_id)
        )

    async def mark_item_as_processed(self, item_id: str):
        await REDIS_ASYNC_CLIENT.sadd(self.processed_items_key, item_id)
        await REDIS_ASYNC_CLIENT.expire(
            self.processed_items_key, self.processed_items_expiry
        )

    async def scrape_feed(self):
        """Main feed scraping logic"""
        self.scraper.metrics.start()

        try:
            healthy_indexers = await self.scraper.get_healthy_indexers()
            if not healthy_indexers:
                logger.warning(
                    f"No healthy indexers available for {self.name} feed scraping"
                )
                return

            indexer_chunks = list(
                self.scraper.split_indexers_into_chunks(healthy_indexers, 3)
            )
            logger.info(
                f"Starting {self.name} feed scraper with {len(healthy_indexers)} indexers "
                f"in {len(indexer_chunks)} chunks"
            )

            all_results = []
            processed_info_hashes = set()
            for chunk in indexer_chunks:
                indexer_ids = [indexer["id"] for indexer in chunk]
                try:
                    chunk_results = await self.get_chunk_results(indexer_ids)
                    self.scraper.metrics.record_found_items(len(chunk_results))
                    all_results.extend(chunk_results)
                    logger.info(
                        f"Scraped {len(chunk_results)} items from {self.name} indexer chunk "
                        f"{[self.scraper.indexer_status[i]['name'] for i in indexer_ids]}"
                    )
                except Exception as e:
                    self.handle_chunk_error(e, chunk)

            logger.info(f"Total items scraped from all chunks: {len(all_results)}")

            # Process items with circuit breaker
            circuit_breaker = CircuitBreaker(
                failure_threshold=5, recovery_timeout=30, half_open_attempts=3
            )

            async for processed_item in batch_process_with_circuit_breaker(
                self.process_feed_item,
                all_results,
                batch_size=20,
                cb=circuit_breaker,
                rate_limit_delay=1,
                retry_exceptions=[httpx.HTTPStatusError],
                scraper=self.scraper,
                processed_info_hashes=processed_info_hashes,
            ):
                if processed_item:
                    await self.mark_item_as_processed(processed_item)
                    self.scraper.metrics.record_processed_item()
                    logger.info(
                        f"Successfully processed {self.name} feed item: {processed_item}"
                    )

        except Exception as e:
            self.scraper.metrics.record_error("feed_scraping_error")
            logger.exception(f"Error scraping {self.name} feed: {e}")
        finally:
            self.scraper.metrics.stop()
            self.scraper.metrics.log_summary(logger)

    @abstractmethod
    async def get_chunk_results(self, indexer_ids: List) -> List[dict]:
        """Get results for a specific indexer chunk"""
        pass

    def handle_chunk_error(self, error: Exception, chunk: List):
        """Handle errors during chunk processing"""
        if isinstance(error, httpx.ReadTimeout):
            self.scraper.metrics.record_error("timeout")
            logger.warning(
                f"Timeout while fetching results from {self.name} indexer chunk {chunk}"
            )
        elif isinstance(error, httpx.HTTPStatusError):
            self.scraper.metrics.record_error("http_error")
            logger.error(
                f"HTTP error while fetching from {self.name} chunk {chunk}: "
                f"{error.response.text}, status code: {error.response.status_code}"
            )
        else:
            self.scraper.metrics.record_error("unexpected_error")
            logger.exception(f"Error fetching from {self.name} chunk {chunk}: {error}")

    async def process_feed_item(
        self, item: dict, scraper: IndexerBaseScraper, processed_info_hashes: set
    ) -> Optional[str]:
        """Process a single feed item"""
        try:
            item_id = scraper.get_info_hash(item) or get_text_hash(
                scraper.get_guid(item), full_hash=True
            )
            title = scraper.get_title(item)

            if await self.is_item_processed(item_id):
                logger.info(f"Skipping already processed {title} feed item")
                scraper.metrics.record_skip("Already processed info_hash")
                return None

            parsed_title_data = scraper.parse_title_data(title)

            if not self.validate_item(title, parsed_title_data, scraper):
                return item_id

            media_type = self.get_media_type(item, parsed_title_data, scraper)
            if not media_type:
                logger.info(f"Skipping unsupported category for {title}")
                scraper.metrics.record_skip("Unsupported category")
                return item_id

            metadata = await self.get_item_metadata(
                item, parsed_title_data, media_type, scraper
            )
            if not metadata:
                logger.info(f"Unable to find or create metadata for {title}")
                scraper.metrics.record_skip("Unable to find or create metadata")
                return item_id

            stream = await scraper.process_stream(
                item, metadata, media_type, processed_info_hashes=processed_info_hashes
            )
            if stream:
                await store_new_torrent_streams([stream])
                scraper.metrics.record_quality(stream.quality)
                scraper.metrics.record_source(stream.source)
                return item_id

            return item_id

        except Exception as e:
            scraper.metrics.record_error("item_processing_error")
            logger.exception(f"Error processing {self.name} feed item: {e}")
            return None

    def validate_item(
        self, title: str, parsed_title_data: dict, scraper: IndexerBaseScraper
    ) -> bool:
        """Validate feed item"""

        if is_contain_18_plus_keywords(title) or (
            settings.adult_content_filter_in_torrent_title
            and parsed_title_data.get("adult")
        ):
            logger.info(f"Skipping adult content: {title}")
            scraper.metrics.record_skip("Adult Content")
            return False
        return True

    def get_media_type(
        self, item: dict, parsed_title_data: dict, scraper: IndexerBaseScraper
    ) -> Optional[str]:
        category_ids = scraper.get_category_ids(item)

        if any(cat in category_ids for cat in self.scraper.MOVIE_CATEGORY_IDS):
            return "movie"
        elif any(cat in category_ids for cat in self.scraper.SERIES_CATEGORY_IDS):
            return "series"
        elif any(cat in category_ids for cat in self.scraper.OTHER_CATEGORY_IDS):
            if not scraper.validate_category_with_title(
                item, category_ids, is_filter_with_blocklist=False
            ):
                return None
            return "series" if parsed_title_data.get("seasons") else "movie"
        return None

    async def get_item_metadata(
        self,
        item: dict,
        parsed_title_data: dict,
        media_type: str,
        scraper: IndexerBaseScraper,
    ):
        """Get or create metadata for item"""
        imdb_id = scraper.get_imdb_id(item)
        if imdb_id:
            return await get_metadata_by_id(imdb_id, media_type)

        parsed_title_data["created_at"] = scraper.get_created_at(item)

        metadata = await get_or_create_metadata(
            parsed_title_data, media_type, is_search_imdb_title=True, is_imdb_only=True
        )

        if not metadata:
            return None

        return await get_metadata_by_id(metadata["id"], media_type)


class ProwlarrFeedScraper(FeedScraper):
    def __init__(self):
        super().__init__("prowlarr", ProwlarrScraper)

    async def get_chunk_results(self, indexer_ids: List) -> List[dict]:
        params = {
            "type": "search",
            "categories": [2000, 5000, 8000],  # Movies, TV, and Other categories
            "offset": 0,
            "limit": 100,
        }
        return await self.scraper.fetch_search_results(params, indexer_ids=indexer_ids)


class JackettFeedScraper(FeedScraper):
    def __init__(self):
        super().__init__("jackett", JackettScraper)

    async def get_chunk_results(self, indexer_ids: List) -> List[dict]:
        params = {
            "t": "search",
            "cat": [2000, 5000, 8000],  # Movies, TV, and Other categories
            "offset": 0,
            "limit": 100,
        }
        return await self.scraper.fetch_search_results(params, indexer_ids=indexer_ids)


async def get_metadata_by_id(imdb_id: str, media_type: str):
    """Get metadata by IMDB ID"""
    if media_type == "movie":
        return await get_movie_data_by_id(imdb_id)
    return await get_series_data_by_id(imdb_id)


# Dramatiq actors for scheduling
@dramatiq.actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
@minimum_run_interval(hours=settings.prowlarr_feed_scrape_interval_hour)
async def run_prowlarr_feed_scraper(**kwargs):
    if not settings.is_scrap_from_prowlarr:
        return
    logger.info("Running Prowlarr feed scraper")
    scraper = ProwlarrFeedScraper()
    await scraper.scrape_feed()


@dramatiq.actor(time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy")
@minimum_run_interval(hours=settings.jackett_feed_scrape_interval_hour)
async def run_jackett_feed_scraper(**kwargs):
    if not settings.is_scrap_from_jackett:
        return
    logger.info("Running Jackett feed scraper")
    scraper = JackettFeedScraper()
    await scraper.scrape_feed()
