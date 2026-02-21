import asyncio
import logging

import dramatiq
from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud
from db.config import settings
from db.database import get_background_session
from db.schemas import MetadataData
from scrapers.base_scraper import BackgroundScraperManager, IndexerBaseScraper
from scrapers.jackett import JackettScraper
from scrapers.prowlarr import ProwlarrScraper

logger = logging.getLogger(__name__)


def _is_shutdown_runtime_error(exc: BaseException) -> bool:
    """Return True for expected runtime errors during worker shutdown."""
    current: BaseException | None = exc
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, RuntimeError):
            message = str(current).lower()
            if (
                "cannot schedule new futures after shutdown" in message
                or "cannot schedule new futures after interpreter shutdown" in message
            ):
                return True
        next_exc = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        current = next_exc if isinstance(next_exc, BaseException) else None

    return False


def _is_expected_shutdown_error(exc: BaseException) -> bool:
    """Return True if exception (including groups) is shutdown-related only."""
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_is_expected_shutdown_error(sub_exc) for sub_exc in exc.exceptions)
    return _is_shutdown_runtime_error(exc)


async def _safe_session_rollback(session: AsyncSession) -> None:
    """Rollback session without surfacing expected shutdown failures."""
    try:
        await session.rollback()
    except BaseException as exc:
        if _is_expected_shutdown_error(exc):
            return
        logger.warning("Rollback failed in background search worker: %s", exc, exc_info=exc)


class BackgroundSearchWorker:
    def __init__(self):
        self.manager = BackgroundScraperManager()
        # Initialize scrapers based on settings
        self.scrapers: list[IndexerBaseScraper] = []
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

        async with get_background_session() as session:
            for item in pending_movies:
                meta_id = item["key"]
                await self.manager.mark_as_processing(meta_id)

                try:
                    metadata = None
                    media = await crud.get_movie_data_by_id(session, meta_id, load_relations=True)
                    if media:
                        metadata = MetadataData.from_db(media)
                    if not metadata:
                        continue

                    processed_info_hashes: set[str] = set()
                    for scraper in self.scrapers:
                        healthy_indexers = await scraper.get_healthy_indexers()
                        if not healthy_indexers:
                            continue

                        indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))

                        scraper.metrics.start()
                        scraper.metrics.meta_data = metadata

                        title_streams_generators = []
                        for chunk in indexer_chunks:
                            for query_template in scraper.MOVIE_SEARCH_QUERY_TEMPLATES:
                                search_query = query_template.format(title=metadata.title, year=metadata.year)
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

                        try:
                            async for stream in scraper.process_streams(
                                *title_streams_generators,
                                max_process=None,
                                max_process_time=None,
                            ):
                                await scraper.store_streams([stream], session=session)
                        finally:
                            scraper.metrics.stop()
                            scraper.metrics.log_summary(scraper.logger)

                except RuntimeError as e:
                    if _is_shutdown_runtime_error(e):
                        logger.warning("Event loop shutting down, aborting movie batch")
                        return
                    logger.exception(f"Error processing movie {meta_id}: {e}")
                    await _safe_session_rollback(session)
                except BaseExceptionGroup as e:
                    if _is_expected_shutdown_error(e):
                        logger.warning("Event loop shutting down, aborting movie batch")
                        return
                    await _safe_session_rollback(session)
                    raise
                except Exception as e:
                    if _is_expected_shutdown_error(e):
                        logger.warning("Event loop shutting down, aborting movie batch")
                        return
                    logger.exception(f"Error processing movie {meta_id}: {e}")
                    await _safe_session_rollback(session)
                finally:
                    await self.manager.mark_as_completed(meta_id, self.manager.movie_hash_key)

    async def process_series_batch(self):
        """Process a batch of pending series episodes with complete scraping"""
        if not self.scrapers:
            logger.warning("No scrapers enabled for background search")
            return

        pending_series = await self.manager.get_pending_items("series")
        logger.info(f"Background search found {len(pending_series)} series episodes to process")

        async with get_background_session() as session:
            for item in pending_series:
                key = item["key"]
                meta_id, season, episode = key.split(":")
                season = int(season)
                episode = int(episode)
                await self.manager.mark_as_processing(key)

                try:
                    metadata = None
                    media = await crud.get_series_data_by_id(session, meta_id, load_relations=True)
                    if media:
                        # Convert to MetadataData while session is active
                        metadata = MetadataData.from_db(media)
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
                        indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))

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
                                await scraper.store_streams([stream], session=session)
                        finally:
                            scraper.metrics.stop()
                            scraper.metrics.log_summary(scraper.logger)

                except RuntimeError as e:
                    if _is_shutdown_runtime_error(e):
                        logger.warning("Event loop shutting down, aborting series batch")
                        return
                    logger.exception(f"Error processing series {meta_id} S{season}E{episode}: {e}")
                    await _safe_session_rollback(session)
                except BaseExceptionGroup as e:
                    if _is_expected_shutdown_error(e):
                        logger.warning("Event loop shutting down, aborting series batch")
                        return
                    await _safe_session_rollback(session)
                    raise
                except Exception as e:
                    if _is_expected_shutdown_error(e):
                        logger.warning("Event loop shutting down, aborting series batch")
                        return
                    logger.exception(f"Error processing series {meta_id} S{season}E{episode}: {e}")
                    await _safe_session_rollback(session)
                finally:
                    await self.manager.mark_as_completed(key, self.manager.series_hash_key)


async def _run_background_search_async():
    """Async implementation of background search, run inside a fresh event loop."""
    try:
        worker = BackgroundSearchWorker()

        # Clean up any stale processing items
        await worker.manager.cleanup_stale_processing()

        results = await asyncio.gather(
            worker.process_movie_batch(),
            worker.process_series_batch(),
            return_exceptions=True,
        )
        for idx, result in enumerate(results):
            if isinstance(result, BaseException):
                batch_name = "movie" if idx == 0 else "series"
                if _is_expected_shutdown_error(result):
                    logger.warning("Background %s batch aborted during shutdown", batch_name)
                    continue
                logger.exception("Background %s batch failed: %s", batch_name, result, exc_info=result)
                if isinstance(result, BaseExceptionGroup):
                    raise result
    except BaseExceptionGroup as e:
        if _is_expected_shutdown_error(e):
            logger.warning("Background search aborted: event loop shutting down")
            return
        raise
    except RuntimeError as e:
        if _is_shutdown_runtime_error(e):
            logger.warning("Background search aborted: event loop shutting down")
            return
        raise


@dramatiq.actor(
    priority=10,
    max_retries=3,
    time_limit=60 * 60 * 1000,  # 1 hour
    min_backoff=600000,  # 10 minutes
    max_backoff=3600000,  # 1 hour
)
def run_background_search(**kwargs):
    """Scheduled task to run background searches"""
    asyncio.run(_run_background_search_async())
