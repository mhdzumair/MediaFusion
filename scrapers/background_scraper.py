import asyncio
import logging
from datetime import timedelta
from typing import List

import dramatiq

from db.config import settings
from db.models import MediaFusionMovieMetaData, MediaFusionSeriesMetaData
from scrapers.base_scraper import IndexerBaseScraper, BackgroundScraperManager
from scrapers.jackett import JackettScraper
from scrapers.prowlarr import ProwlarrScraper

logger = logging.getLogger(__name__)


class BackgroundSearchWorker:
    def __init__(self):
        self.manager = BackgroundScraperManager()
        # Initialize scrapers based on settings
        self.scrapers: List[IndexerBaseScraper] = []
        if settings.is_scrap_from_jackett:
            self.scrapers.append(JackettScraper())
        if settings.is_scrap_from_prowlarr:
            self.scrapers.append(ProwlarrScraper())

    async def process_movie_batch(self):
        """Process a batch of pending movies with complete scraping"""
        if not self.scrapers:
            logger.warning("No scrapers enabled for background search")
            return

        pending_movies = await self.manager.get_pending_items("movie")
        logger.info(f"Background search found {len(pending_movies)} movies to process")

        for item in pending_movies:
            meta_id = item["key"]
            await self.manager.mark_as_processing(meta_id)

            try:
                metadata = await MediaFusionMovieMetaData.get(meta_id)
                if not metadata:
                    continue

                # Process each scraper sequentially for complete scraping
                processed_info_hashes: set[str] = set()
                for scraper in self.scrapers:
                    # Get healthy indexers
                    healthy_indexers = await scraper.get_healthy_indexers()
                    if not healthy_indexers:
                        continue

                    # Split indexers into chunks
                    indexer_chunks = list(
                        scraper.split_indexers_into_chunks(healthy_indexers, 3)
                    )

                    # Start metrics collection
                    scraper.metrics.start()
                    scraper.metrics.meta_data = metadata

                    # Create generators for title-based search
                    title_streams_generators = []
                    for chunk in indexer_chunks:
                        for query_template in scraper.MOVIE_SEARCH_QUERY_TEMPLATES:
                            search_query = query_template.format(
                                title=metadata.title, year=metadata.year
                            )
                            title_streams_generators.append(
                                scraper.scrape_movie_by_title(
                                    processed_info_hashes,
                                    metadata,
                                    search_query=search_query,
                                    indexers=chunk,
                                )
                            )
                        if settings.scrape_with_aka_titles:
                            for aka_title in metadata.aka_titles:
                                title_streams_generators.append(
                                    scraper.scrape_movie_by_title(
                                        processed_info_hashes,
                                        metadata,
                                        search_query=aka_title,
                                        indexers=chunk,
                                    )
                                )

                    # Process all streams without time limit
                    try:
                        async for stream in scraper.process_streams(
                            *title_streams_generators,
                            max_process=None,  # No limit for background
                            max_process_time=None,  # No time limit for background
                        ):
                            await scraper.store_streams([stream])
                    finally:
                        scraper.metrics.stop()
                        scraper.metrics.log_summary(scraper.logger)

            except Exception as e:
                logger.exception(f"Error processing movie {meta_id}: {e}")
            finally:
                await self.manager.mark_as_completed(
                    meta_id, self.manager.movie_hash_key
                )

    async def process_series_batch(self):
        """Process a batch of pending series episodes with complete scraping"""
        if not self.scrapers:
            logger.warning("No scrapers enabled for background search")
            return

        pending_series = await self.manager.get_pending_items("series")
        logger.info(
            f"Background search found {len(pending_series)} series episodes to process"
        )

        for item in pending_series:
            key = item["key"]
            meta_id, season, episode = key.split(":")
            season = int(season)
            episode = int(episode)
            await self.manager.mark_as_processing(key)

            try:
                metadata = await MediaFusionSeriesMetaData.get(meta_id)
                if not metadata:
                    continue

                # Process each scraper sequentially for complete scraping
                processed_info_hashes: set[str] = set()
                for scraper in self.scrapers:

                    # Get healthy indexers
                    healthy_indexers = await scraper.get_healthy_indexers()
                    if not healthy_indexers:
                        continue

                    # Split indexers into chunks
                    indexer_chunks = list(
                        scraper.split_indexers_into_chunks(healthy_indexers, 3)
                    )

                    # Start metrics collection
                    scraper.metrics.start()
                    scraper.metrics.meta_data = metadata
                    scraper.metrics.season = season
                    scraper.metrics.episode = episode

                    # Create generators for title-based search
                    title_streams_generators = []
                    for chunk in indexer_chunks:
                        for query_template in scraper.SERIES_SEARCH_QUERY_TEMPLATES:
                            search_query = query_template.format(
                                title=metadata.title, season=season, episode=episode
                            )
                            title_streams_generators.append(
                                scraper.scrape_series_by_title(
                                    processed_info_hashes,
                                    metadata,
                                    season,
                                    episode,
                                    search_query=search_query,
                                    indexers=chunk,
                                )
                            )

                    # Process all streams without time limit
                    try:
                        async for stream in scraper.process_streams(
                            *title_streams_generators,
                            max_process=None,  # No limit for background
                            max_process_time=None,  # No time limit for background
                            catalog_type="series",
                            season=season,
                            episode=episode,
                        ):
                            await scraper.store_streams([stream])
                    finally:
                        scraper.metrics.stop()
                        scraper.metrics.log_summary(scraper.logger)

            except Exception as e:
                logger.exception(
                    f"Error processing series {meta_id} S{season}E{episode}: {e}"
                )
            finally:
                await self.manager.mark_as_completed(key, self.manager.series_hash_key)


@dramatiq.actor(
    priority=10,
    max_retries=3,
    min_backoff=timedelta(minutes=10),
    max_backoff=timedelta(hours=1),
)
async def run_background_search(**kwargs):
    """Scheduled task to run background searches"""
    from db import database

    await database.init()
    worker = BackgroundSearchWorker()

    # Clean up any stale processing items
    await worker.manager.cleanup_stale_processing()

    # Process movies and series concurrently
    await asyncio.gather(worker.process_movie_batch(), worker.process_series_batch())
