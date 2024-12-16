import asyncio
import logging

import dramatiq

from db.config import settings
from db.models import TorrentStreams, MediaFusionMetaData
from scrapers.base_scraper import BaseScraper
from scrapers.prowlarr import ProwlarrScraper
from scrapers.torrentio import TorrentioScraper
from scrapers.mediafusion import MediafusionScraper
from scrapers.yts import YTSScraper
from scrapers.zilean import ZileanScraper
from scrapers.bt4g import BT4GScraper
from utils.runtime_const import (
    ZILEAN_SEARCH_TTL,
    TORRENTIO_SEARCH_TTL,
    PROWLARR_SEARCH_TTL,
    MEDIAFUSION_SEARCH_TTL,
    YTS_SEARCH_TTL,
    BT4G_SEARCH_TTL,
)


async def run_scrapers(
    metadata: MediaFusionMetaData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
) -> set[TorrentStreams]:
    scraper_tasks = []

    if settings.prowlarr_api_key:
        prowlarr_scraper = ProwlarrScraper()
        scraper_tasks.append(
            prowlarr_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if settings.is_scrap_from_zilean:
        zilean_scraper = ZileanScraper()
        scraper_tasks.append(
            zilean_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if settings.is_scrap_from_torrentio:
        torrentio_scraper = TorrentioScraper()
        scraper_tasks.append(
            torrentio_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if settings.is_scrap_from_mediafusion:
        mediafusion_scraper = MediafusionScraper()
        scraper_tasks.append(
            mediafusion_scraper.scrape_and_parse(
                metadata, catalog_type, season, episode
            )
        )

    if settings.is_scrap_from_yts:
        yts_scraper = YTSScraper()
        scraper_tasks.append(
            yts_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if settings.is_scrap_from_bt4g:
        bt4g_scraper = BT4GScraper()
        scraper_tasks.append(
            bt4g_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    scraped_streams = await asyncio.gather(*scraper_tasks)
    scraped_streams = [stream for sublist in scraped_streams for stream in sublist]
    unique_streams = set(scraped_streams)
    logging.info(f"Scraped {len(scraped_streams)} streams for {metadata.title}")
    return unique_streams


@dramatiq.actor(
    time_limit=5 * 60 * 1000,  # 5 minutes
    priority=20,
)
async def cleanup_expired_scraper_task(**kwargs):
    await BaseScraper.remove_expired_items(
        ProwlarrScraper.cache_key_prefix, PROWLARR_SEARCH_TTL
    )
    await BaseScraper.remove_expired_items(
        TorrentioScraper.cache_key_prefix, TORRENTIO_SEARCH_TTL
    )
    await BaseScraper.remove_expired_items(
        ZileanScraper.cache_key_prefix, ZILEAN_SEARCH_TTL
    )
    await BaseScraper.remove_expired_items(
        MediafusionScraper.cache_key_prefix, MEDIAFUSION_SEARCH_TTL
    )
    await BaseScraper.remove_expired_items(YTSScraper.cache_key_prefix, YTS_SEARCH_TTL)
    await BaseScraper.remove_expired_items(
        BT4GScraper.cache_key_prefix, BT4G_SEARCH_TTL
    )
