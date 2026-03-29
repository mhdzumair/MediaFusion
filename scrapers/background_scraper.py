import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlmodel.ext.asyncio.session import AsyncSession

from api.task_queue import actor
from db import crud
from db.config import settings
from db.database import get_background_session
from db.retry_utils import run_db_operation_with_retry
from db.schemas import MetadataData
from scrapers.base_scraper import BackgroundScraperManager, IndexerBaseScraper
from scrapers.jackett import JackettScraper
from scrapers.prowlarr import ProwlarrScraper

logger = logging.getLogger(__name__)
T = TypeVar("T")


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


async def _rollback_session_before_retry(session: AsyncSession) -> None:
    """Clear failed transaction state; do not close (caller still holds the session)."""
    await _safe_session_rollback(session)


async def _run_with_db_retry(
    operation: Callable[[], Awaitable[T]],
    session: AsyncSession | None,
    *,
    operation_name: str,
    max_attempts: int = 3,
) -> T:
    """Run a DB operation with retry on transient disconnect errors.

    Pass ``session=None`` when each ``operation()`` call opens its own
    :func:`get_background_session` — required for retries after a dead connection,
    since closing the same session would leave the next attempt with a closed client.

    Pass a shared ``session`` only when the operation reuses it; then we rollback
    before retry (we never close it here).
    """

    async def _before_retry(attempt: int, total_attempts: int, exc: Exception) -> None:
        logger.warning(
            "Retryable DB error during %s (attempt %d/%d): %s",
            operation_name,
            attempt,
            total_attempts,
            exc,
        )
        if session is not None:
            await _rollback_session_before_retry(session)

    return await run_db_operation_with_retry(
        operation=operation,
        operation_name=operation_name,
        max_attempts=max_attempts,
        before_retry=_before_retry,
    )


class BackgroundSearchWorker:
    def __init__(self):
        self.manager = BackgroundScraperManager()
        # Initialize scrapers based on settings
        self.scrapers: list[IndexerBaseScraper] = []
        if settings.is_scrap_from_jackett:
            self.scrapers.append(JackettScraper())
        if settings.is_scrap_from_prowlarr:
            self.scrapers.append(ProwlarrScraper())

    @staticmethod
    def _is_anime_metadata(metadata: MetadataData) -> bool:
        return metadata.is_anime_metadata()

    async def close(self) -> None:
        """Close scraper HTTP clients to avoid file descriptor leaks."""
        for scraper in self.scrapers:
            try:
                await scraper.close()
            except Exception as exc:
                logger.warning("Failed to close scraper client %s: %s", scraper.cache_key_prefix, exc)

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

                async def _load_movie_metadata():
                    async with get_background_session() as read_session:
                        return await crud.get_movie_data_by_id(read_session, meta_id, load_relations=True)

                media = await _run_with_db_retry(
                    _load_movie_metadata,
                    None,
                    operation_name=f"loading movie metadata {meta_id}",
                )

                metadata = MetadataData.from_db(media) if media else None
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
                            await _run_with_db_retry(
                                lambda st=stream: scraper.store_streams([st]),
                                None,
                                operation_name=f"storing movie stream {meta_id}",
                            )
                    finally:
                        scraper.metrics.stop()
                        scraper.metrics.log_summary(scraper.logger)

            except RuntimeError as e:
                if _is_shutdown_runtime_error(e):
                    logger.warning("Event loop shutting down, aborting movie batch")
                    return
                logger.exception(f"Error processing movie {meta_id}: {e}")
            except BaseExceptionGroup as e:
                if _is_expected_shutdown_error(e):
                    logger.warning("Event loop shutting down, aborting movie batch")
                    return
                raise
            except Exception as e:
                if _is_expected_shutdown_error(e):
                    logger.warning("Event loop shutting down, aborting movie batch")
                    return
                logger.exception(f"Error processing movie {meta_id}: {e}")
            finally:
                await self.manager.mark_as_completed(meta_id, self.manager.movie_hash_key)

    async def process_series_batch(self):
        """Process a batch of pending series episodes with complete scraping"""
        if not self.scrapers:
            logger.warning("No scrapers enabled for background search")
            return

        pending_series = await self.manager.get_pending_items("series")
        logger.info(f"Background search found {len(pending_series)} series episodes to process")

        for item in pending_series:
            key = item["key"]
            parts = key.rsplit(":", 2)
            if len(parts) != 3:
                logger.warning(
                    "Skipping invalid series background-search key %r (expected meta_id:season:episode)",
                    key,
                )
                continue
            meta_id, season_raw, episode_raw = parts
            try:
                season = int(season_raw)
                episode = int(episode_raw)
            except ValueError:
                logger.warning(
                    "Skipping series background-search key %r: invalid season or episode",
                    key,
                )
                continue
            await self.manager.mark_as_processing(key)

            try:

                async def _load_series_metadata():
                    async with get_background_session() as read_session:
                        return await crud.get_series_data_by_id(read_session, meta_id, load_relations=True)

                media = await _run_with_db_retry(
                    _load_series_metadata,
                    None,
                    operation_name=f"loading series metadata {meta_id}",
                )

                metadata = MetadataData.from_db(media) if media else None
                if not metadata:
                    continue
                is_anime = self._is_anime_metadata(metadata)

                processed_info_hashes: set[str] = set()
                for scraper in self.scrapers:
                    healthy_indexers = await scraper.get_healthy_indexers()
                    if not healthy_indexers:
                        continue

                    indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))

                    scraper.metrics.start()
                    scraper.metrics.meta_data = metadata
                    scraper.metrics.season = season
                    scraper.metrics.episode = episode

                    title_streams_generators = []
                    for chunk in indexer_chunks:
                        if is_anime:
                            query_templates = (
                                "{title} - {episode:02d}",
                                "{title} {episode:02d}",
                                "{title} episode {episode}",
                                "{title}",
                            )
                        else:
                            query_templates = scraper.SERIES_SEARCH_QUERY_TEMPLATES

                        for query_template in query_templates:
                            search_query = query_template.format(title=metadata.title, season=season, episode=episode)
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
                        if is_anime and settings.scrape_with_aka_titles:
                            for aka_title in metadata.aka_titles:
                                if not isinstance(aka_title, str) or not aka_title.strip():
                                    continue
                                title_streams_generators.append(
                                    scraper.scrape_series_by_title(
                                        processed_info_hashes,
                                        metadata,
                                        season,
                                        episode,
                                        search_query=f"{aka_title.strip()} {episode:02d}",
                                        indexers=chunk,
                                    )
                                )

                    try:
                        async for stream in scraper.process_streams(
                            *title_streams_generators,
                            max_process=None,
                            max_process_time=None,
                            catalog_type="series",
                            season=season,
                            episode=episode,
                        ):
                            await _run_with_db_retry(
                                lambda st=stream: scraper.store_streams([st]),
                                None,
                                operation_name=f"storing series stream {meta_id} S{season}E{episode}",
                            )
                    finally:
                        scraper.metrics.stop()
                        scraper.metrics.log_summary(scraper.logger)

            except RuntimeError as e:
                if _is_shutdown_runtime_error(e):
                    logger.warning("Event loop shutting down, aborting series batch")
                    return
                logger.exception(f"Error processing series {meta_id} S{season}E{episode}: {e}")
            except BaseExceptionGroup as e:
                if _is_expected_shutdown_error(e):
                    logger.warning("Event loop shutting down, aborting series batch")
                    return
                raise
            except Exception as e:
                if _is_expected_shutdown_error(e):
                    logger.warning("Event loop shutting down, aborting series batch")
                    return
                logger.exception(f"Error processing series {meta_id} S{season}E{episode}: {e}")
            finally:
                await self.manager.mark_as_completed(key, self.manager.series_hash_key)


async def _run_background_search_async():
    """Async implementation of background search, run inside a fresh event loop."""
    worker = BackgroundSearchWorker()
    try:
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
    finally:
        await worker.close()


@actor(
    priority=10,
    max_retries=3,
    time_limit=2 * 60 * 60 * 1000,  # 2 hours
    min_backoff=600000,  # 10 minutes
    max_backoff=3600000,  # 1 hour
)
async def run_background_search(**kwargs):
    """Scheduled task to run background searches"""
    await _run_background_search_async()
