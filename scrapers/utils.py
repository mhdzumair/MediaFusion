import asyncio
import logging

from db.config import settings
from db.models import TorrentStreams, MediaFusionMetaData
from scrapers.prowlarr import ProwlarrScraper
from scrapers.torrentio import TorrentioScraper
from scrapers.zilean import ZileanScraper


async def run_scrapers(
    metadata: MediaFusionMetaData,
    catalog_type: str,
    user_data,
    season: int = None,
    episode: int = None,
) -> set[TorrentStreams]:
    scraper_tasks = []

    if (
        settings.is_scrap_from_torrentio
        and "torrentio_streams" in user_data.selected_catalogs
    ):
        torrentio_scraper = TorrentioScraper()
        scraper_tasks.append(
            torrentio_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if settings.prowlarr_api_key and "prowlarr_streams" in user_data.selected_catalogs:
        prowlarr_scraper = ProwlarrScraper()
        scraper_tasks.append(
            prowlarr_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    if (
        settings.is_scrap_from_zilean
        and "zilean_dmm_streams" in user_data.selected_catalogs
    ):
        zilean_scraper = ZileanScraper()
        scraper_tasks.append(
            zilean_scraper.scrape_and_parse(metadata, catalog_type, season, episode)
        )

    scraped_streams = await asyncio.gather(*scraper_tasks)
    scraped_streams = [stream for sublist in scraped_streams for stream in sublist]
    unique_streams = set(scraped_streams)
    logging.info(f"Scraped {len(scraped_streams)} streams for {metadata.title}")
    return unique_streams
