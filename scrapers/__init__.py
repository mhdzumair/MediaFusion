import asyncio

from db.config import settings
from db.models import TorrentStreams
from scrapers.prowlarr_v2 import ProwlarrScraper
from scrapers.torrentio import TorrentioScraper
from scrapers.zilean import ZileanScraper


async def run_scrapers(
    video_id: str,
    catalog_type: str,
    title: str,
    aka_titles: list[str],
    year: int,
    user_data,
    season: int = None,
    episode: int = None,
) -> list[TorrentStreams]:
    scraper_tasks = []

    if (
        settings.is_scrap_from_torrentio
        and "torrentio_streams" in user_data.selected_catalogs
    ):
        torrentio_scraper = TorrentioScraper()
        scraper_tasks.append(
            torrentio_scraper.scrape_and_parse(
                video_id, catalog_type, title, aka_titles, season, episode
            )
        )

    if settings.prowlarr_api_key and "prowlarr_streams" in user_data.selected_catalogs:
        prowlarr_scraper = ProwlarrScraper()
        scraper_tasks.append(
            prowlarr_scraper.scrape_and_parse(
                video_id, catalog_type, title, aka_titles, year, season, episode
            )
        )

    if (
        settings.is_scrap_from_zilean
        and "zilean_dmm_streams" in user_data.selected_catalogs
    ):
        zilean_scraper = ZileanScraper()
        scraper_tasks.append(
            zilean_scraper.scrape_and_parse(
                video_id, catalog_type, title, aka_titles, season, episode
            )
        )

    scraped_streams = await asyncio.gather(*scraper_tasks)
    return [
        stream for sublist in scraped_streams for stream in sublist
    ]  # Flatten the list of lists
