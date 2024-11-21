import logging
from typing import List, Optional

import dramatiq
import httpx

from db.config import settings
from db.crud import (
    get_movie_data_by_id,
    get_series_data_by_id,
    store_new_torrent_streams,
    get_or_create_metadata,
)
from scrapers.prowlarr import (
    ProwlarrScraper,
    MOVIE_CATEGORY_IDS,
    SERIES_CATEGORY_IDS,
    OTHER_CATEGORY_IDS,
)
from utils.crypto import get_text_hash
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import REDIS_ASYNC_CLIENT
from utils.wrappers import minimum_run_interval

logger = logging.getLogger(__name__)

REDIS_PROCESSED_ITEMS_KEY = "prowlarr_feed_scraper:processed_items"
PROCESSED_ITEMS_EXPIRY = 60 * 60 * 24 * 3  # 3 days


async def is_item_processed(item_id: str) -> bool:
    return bool(await REDIS_ASYNC_CLIENT.sismember(REDIS_PROCESSED_ITEMS_KEY, item_id))


async def mark_item_as_processed(item_id: str):
    await REDIS_ASYNC_CLIENT.sadd(REDIS_PROCESSED_ITEMS_KEY, item_id)
    await REDIS_ASYNC_CLIENT.expire(REDIS_PROCESSED_ITEMS_KEY, PROCESSED_ITEMS_EXPIRY)


async def scrape_prowlarr_feed():
    scraper = ProwlarrScraper()
    scraper.metrics.start()  # Start metrics collection

    try:
        # Get healthy indexers and split into chunks
        healthy_indexers = await scraper.get_healthy_indexers()
        if not healthy_indexers:
            logger.warning("No healthy indexers available for feed scraping")
            return

        indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))
        logger.info(
            f"Starting feed scraper with {len(healthy_indexers)} indexers "
            f"in {len(indexer_chunks)} chunks"
        )

        all_results = []
        for chunk in indexer_chunks:
            params = {
                "type": "search",
                "categories": [2000, 5000, 8000],  # Movies, TV, and Other categories
                "offset": 0,
                "limit": 100,
            }

            try:
                chunk_results = await scraper.fetch_search_results(
                    params,
                    indexer_ids=chunk,
                )
                scraper.metrics.record_found_items(len(chunk_results))
                all_results.extend(chunk_results)
                logger.info(
                    f"Scraped {len(chunk_results)} items from indexer chunk {[scraper.indexer_status[i]['name'] for i in chunk]}"
                )

            except httpx.ReadTimeout:
                scraper.metrics.record_error("timeout")
                logger.warning(
                    f"Timeout while fetching results from indexer chunk {chunk}"
                )
            except httpx.HTTPStatusError as e:
                scraper.metrics.record_error("http_error")
                logger.error(
                    f"HTTP error while fetching from chunk {chunk}: "
                    f"{e.response.text}, status code: {e.response.status_code}"
                )
            except Exception as e:
                scraper.metrics.record_error("unexpected_error")
                logger.exception(f"Error fetching from chunk {chunk}: {e}")

        logger.info(f"Total items scraped from all chunks: {len(all_results)}")

        # Process items with circuit breaker
        circuit_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=30, half_open_attempts=3
        )

        async for processed_item in batch_process_with_circuit_breaker(
            process_feed_item,
            all_results,
            batch_size=20,
            cb=circuit_breaker,
            rate_limit_delay=1,
            retry_exceptions=[httpx.HTTPStatusError],
            scraper=scraper,
        ):
            if processed_item:
                await mark_item_as_processed(processed_item)
                scraper.metrics.record_processed_item()
                logger.info(f"Successfully processed item: {processed_item}")

    except Exception as e:
        scraper.metrics.record_error("feed_scraping_error")
        logger.exception(f"Error scraping Prowlarr feed: {e}")
    finally:
        # Log final metrics and indexer status
        scraper.metrics.stop()
        scraper.metrics.log_summary(logger)
        await scraper.log_indexer_status()


async def process_feed_item(item: dict, scraper: ProwlarrScraper) -> Optional[str]:
    """Process a single feed item with enhanced metrics tracking"""
    try:
        item_id = item.get("infoHash") or get_text_hash(
            item.get("title"), full_hash=True
        )
        if await is_item_processed(item_id):
            scraper.metrics.record_skip("Already processed info_hash")
            logger.debug(f"Item {item_id} already processed, skipping")
            return None

        imdb_id = item.get("imdbId")
        category_ids = [category["id"] for category in item["categories"]]
        parsed_title_data = scraper.parse_title_data(item["title"])

        if is_contain_18_plus_keywords(item["title"]):
            scraper.metrics.record_skip("Adult Content")
            logger.warning(f"Item {item['title']} contains black listed keywords")
            return item_id

        # Determine media type
        media_type = get_media_type(category_ids, parsed_title_data, scraper, item)
        if not media_type:
            scraper.metrics.record_skip("Unsupported category")
            return item_id

        # Fetch or create metadata
        metadata = await get_item_metadata(imdb_id, media_type, parsed_title_data)
        if not metadata:
            scraper.metrics.record_skip("Unable to find or create metadata")
            logger.warning(f"Unable to find or create metadata for {item['title']}")
            return item_id

        # Process the stream
        stream = await scraper.process_stream(
            item, metadata, media_type, processed_info_hashes=set()
        )
        if stream:
            await store_new_torrent_streams([stream])
            scraper.metrics.record_quality(stream.quality)
            scraper.metrics.record_source(stream.source)
            return item_id
        else:
            scraper.metrics.record_skip("Stream processing failed")
            logger.warning(f"Failed to process stream for {item['title']}")
            return item_id

    except Exception as e:
        scraper.metrics.record_error("item_processing_error")
        logger.exception(f"Error processing feed item: {e}")
        return None


def get_media_type(
    category_ids: List[int],
    parsed_title_data: dict,
    scraper: ProwlarrScraper,
    item: dict,
) -> Optional[str]:
    """Determine media type from category IDs and parsed title data"""
    if any(category in category_ids for category in MOVIE_CATEGORY_IDS):
        return "movie"
    elif any(category in category_ids for category in SERIES_CATEGORY_IDS):
        return "series"
    elif any(category in category_ids for category in OTHER_CATEGORY_IDS):
        if not scraper.validate_category_with_title(
            item, category_ids, is_filter_with_blocklist=False
        ):
            return None
        return "series" if parsed_title_data.get("seasons") else "movie"
    return None


async def get_item_metadata(
    imdb_id: Optional[str], media_type: str, parsed_title_data: dict
):
    """Fetch or create metadata for an item"""
    if imdb_id:
        return await get_metadata_by_id(f"tt{imdb_id}", media_type)
    else:
        return await search_and_create_metadata(parsed_title_data, media_type)


async def get_metadata_by_id(imdb_id: str, media_type: str):
    if media_type == "movie":
        return await get_movie_data_by_id(imdb_id)
    else:
        return await get_series_data_by_id(imdb_id)


async def search_and_create_metadata(metadata: dict, media_type: str):
    metadata = await get_or_create_metadata(metadata, media_type, is_imdb=True)
    if not metadata["id"].startswith("tt"):
        # Only create metadata for IMDb movies and series
        return None

    # Fetch the newly created metadata
    return await get_metadata_by_id(metadata["id"], media_type)


@minimum_run_interval(hours=settings.prowlarr_feed_scrape_interval_hour)
@dramatiq.actor(
    time_limit=60 * 60 * 1000, priority=5, queue_name="scrapy"  # 60 minutes
)
async def run_prowlarr_feed_scraper(**kwargs):
    logger.info("Running Prowlarr feed scraper")
    await scrape_prowlarr_feed()
