import logging

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

    params = {
        "type": "search",
        "categories": [2000, 5000, 8000],  # Movies, TV, and Other categories
        "offset": 0,
        "limit": 100,
    }

    try:
        results = await scraper.fetch_stream_data(params)
        logger.info(f"Scraped {len(results)} items from Prowlarr feed")

        circuit_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=30, half_open_attempts=3
        )

        async for processed_item in batch_process_with_circuit_breaker(
            process_feed_item,
            results,
            batch_size=20,
            cb=circuit_breaker,
            rate_limit_delay=1,
            retry_exceptions=[httpx.HTTPStatusError],
            scraper=scraper,
        ):
            if processed_item:
                await mark_item_as_processed(processed_item)
                logger.info(f"Successfully processed item: {processed_item}")

    except Exception as e:
        logger.exception(f"Error scraping Prowlarr feed: {e}")


async def process_feed_item(item: dict, scraper: ProwlarrScraper):
    item_id = item.get("infoHash") or get_text_hash(item.get("title"), full_hash=True)
    if await is_item_processed(item_id):
        logger.debug(f"Item {item_id} already processed, skipping")
        return None

    imdb_id = item.get("imdbId")
    category_ids = [category["id"] for category in item["categories"]]
    parsed_title_data = scraper.parse_title_data(item["title"])
    if is_contain_18_plus_keywords(item["title"]):
        logger.warning(f"Item {item['title']} contains black listed keywords")
        return item_id

    # Determine media type
    if any(category in category_ids for category in MOVIE_CATEGORY_IDS):
        media_type = "movie"
    elif any(category in category_ids for category in SERIES_CATEGORY_IDS):
        media_type = "series"
    elif any(category in category_ids for category in OTHER_CATEGORY_IDS):
        if not scraper.validate_category_with_title(
            item, category_ids, is_filter_with_blocklist=False
        ):
            logger.warning(
                f"Category 8000 item {item['title']} does not match expected format"
            )
            return item_id
        media_type = "series" if parsed_title_data.get("seasons") else "movie"
    else:
        logger.warning(f"Unsupported category {category_ids} for item {item['title']}")
        return item_id

    # Fetch or create metadata
    if imdb_id:
        metadata = await get_metadata_by_id(f"tt{imdb_id}", media_type)
    else:
        metadata = await search_and_create_metadata(parsed_title_data, media_type)

    if not metadata:
        logger.warning(f"Unable to find or create metadata for {item['title']}")
        return item_id

    # Process the stream
    stream = await scraper.process_stream(
        item, metadata, media_type, processed_info_hashes=set()
    )
    if stream:
        await store_new_torrent_streams([stream])
        return item_id
    else:
        logger.warning(f"Failed to process stream for {item['title']}")
        return item_id


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


@minimum_run_interval(hours=settings.prowlarr_feed_scrape_interval)
@dramatiq.actor(
    time_limit=30 * 60 * 1000,  # 30 minutes
    max_retries=3,
    min_backoff=60000,
    max_backoff=3600000,
    priority=50,
)
async def run_prowlarr_feed_scraper():
    logger.info("Running Prowlarr feed scraper")
    await scrape_prowlarr_feed()
