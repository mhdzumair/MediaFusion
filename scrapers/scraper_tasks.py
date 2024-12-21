import asyncio
import logging

import dramatiq

from db.config import settings
from db.models import TorrentStreams, MediaFusionMetaData
from scrapers.base_scraper import BaseScraper
from scrapers.jackett import JackettScraper
from scrapers.prowlarr import ProwlarrScraper
from scrapers.torrentio import TorrentioScraper
from scrapers.mediafusion import MediafusionScraper
from scrapers.yts import YTSScraper
from scrapers.zilean import ZileanScraper
from scrapers.bt4g import BT4GScraper
from utils import runtime_const

SCRAPERS = [
    (settings.is_scrap_from_prowlarr, ProwlarrScraper),
    (settings.is_scrap_from_zilean, ZileanScraper),
    (settings.is_scrap_from_torrentio, TorrentioScraper),
    (settings.is_scrap_from_mediafusion, MediafusionScraper),
    (settings.is_scrap_from_yts, YTSScraper),
    (settings.is_scrap_from_bt4g, BT4GScraper),
    (settings.is_scrap_from_jackett, JackettScraper),
]

CACHED_DATA = [
    (ProwlarrScraper.cache_key_prefix, runtime_const.PROWLARR_SEARCH_TTL),
    (TorrentioScraper.cache_key_prefix, runtime_const.TORRENTIO_SEARCH_TTL),
    (ZileanScraper.cache_key_prefix, runtime_const.ZILEAN_SEARCH_TTL),
    (MediafusionScraper.cache_key_prefix, runtime_const.MEDIAFUSION_SEARCH_TTL),
    (YTSScraper.cache_key_prefix, runtime_const.YTS_SEARCH_TTL),
    (BT4GScraper.cache_key_prefix, runtime_const.BT4G_SEARCH_TTL),
    (JackettScraper.cache_key_prefix, runtime_const.JACKETT_SEARCH_TTL),
]


async def run_scrapers(
    metadata: MediaFusionMetaData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
) -> set[TorrentStreams]:
    """Run all enabled scrapers and return unique streams"""
    all_streams = []
    failed_scrapers = []

    async with asyncio.TaskGroup() as tg:
        # Create tasks for enabled scrapers
        tasks = [
            tg.create_task(
                scraper_cls().scrape_and_parse(metadata, catalog_type, season, episode),
                name=f"{scraper_cls.__name__}",
            )
            for is_enabled, scraper_cls in SCRAPERS
            if is_enabled
        ]

    # Process results after all tasks complete
    for task in tasks:
        try:
            streams = task.result()
            all_streams.extend(streams)
            logging.info(
                f"Successfully scraped {len(streams)} streams from {task.get_name()}"
            )
        except Exception as exc:
            # Log the error and keep track of failed scrapers
            failed_scrapers.append(task.get_name())
            logging.error(f"Error in scraper {task.get_name()}: {str(exc)}")

    # Log summary of failures if any occurred
    if failed_scrapers:
        logging.error(f"Failed scrapers: {', '.join(failed_scrapers)}")

    unique_streams = set(all_streams)
    logging.info(
        f"Successfully scraped {len(all_streams)} total streams "
        f"({len(unique_streams)} unique) for {metadata.title}"
    )
    return unique_streams


@dramatiq.actor(
    time_limit=5 * 60 * 1000,  # 5 minutes
    priority=20,
)
async def cleanup_expired_scraper_task(**kwargs):
    """Cleanup expired items from all scrapers"""
    logging.info("Cleaning up expired scraper items")
    for cache_key_prefix, ttl in CACHED_DATA:
        await BaseScraper.remove_expired_items(cache_key_prefix, ttl)
