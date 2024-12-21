import abc
import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timedelta
from functools import wraps
from typing import Any, Literal, AsyncGenerator, AsyncIterable
from typing import Dict
from typing import List
from typing import Optional

import PTT
import httpx
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from torf import Magnet, MagnetError

from db.config import settings
from db.models import MediaFusionMovieMetaData, MediaFusionSeriesMetaData
from db.models import (
    TorrentStreams,
    MediaFusionMetaData,
    Episode,
    Season,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from scrapers import torrent_info
from scrapers.imdb_data import get_episode_by_date
from utils.network import batch_process_with_circuit_breaker, CircuitBreaker
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords
from utils.torrent import extract_torrent_metadata, info_hashes_to_torrent_metadata


@dataclass
class ScraperMetrics:
    scraper_name: str
    meta_data: MediaFusionMetaData = None
    season: int = None
    episode: int = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    total_items_found: int = 0
    total_items_processed: int = 0
    error_counts: Counter = field(default_factory=Counter)
    skip_reasons: Counter = field(default_factory=Counter)
    quality_stats: Counter = field(default_factory=Counter)
    source_stats: Counter = field(default_factory=Counter)
    skip_scraping: bool = False
    indexer_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def start(self):
        """Reset and start new metrics collection"""
        self.start_time = datetime.now()
        self.end_time = None
        self.total_items_found = 0
        self.total_items_processed = 0
        self.error_counts.clear()
        self.skip_reasons.clear()
        self.quality_stats.clear()
        self.source_stats.clear()
        self.skip_scraping = False

    def stop(self):
        """Stop metrics collection and record end time"""
        self.end_time = datetime.now()

    def record_found_items(self, count: int):
        """Record number of items initially found"""
        self.total_items_found += count

    def record_processed_item(self):
        """Record a successfully processed item"""
        self.total_items_processed += 1

    def record_error(self, error_type: str):
        """Record an error occurrence"""
        self.error_counts[error_type] += 1

    def record_skip(self, reason: str):
        """Record a skipped item and the reason"""
        self.skip_reasons[reason] += 1

    def record_quality(self, quality: str):
        """Record quality statistics"""
        self.quality_stats[str(quality)] += 1

    def record_source(self, source: str):
        """Record source statistics"""
        if source:
            self.source_stats[source] += 1

    def skip_scrape(self):
        """Skip scraping the item"""
        self.skip_scraping = True

    def record_indexer_success(self, indexer_name: str, results_count: int):
        """Record successful results from an indexer"""
        if indexer_name not in self.indexer_stats:
            self.indexer_stats[indexer_name] = {
                "success_count": 0,
                "error_count": 0,
                "results_count": 0,
                "errors": Counter(),
            }

        self.indexer_stats[indexer_name]["success_count"] += 1
        self.indexer_stats[indexer_name]["results_count"] += results_count

    def record_indexer_error(self, indexer_name: str, error: str):
        """Record an error from an indexer"""
        if indexer_name not in self.indexer_stats:
            self.indexer_stats[indexer_name] = {
                "success_count": 0,
                "error_count": 0,
                "results_count": 0,
                "errors": Counter(),
            }

        self.indexer_stats[indexer_name]["error_count"] += 1
        self.indexer_stats[indexer_name]["errors"][error] += 1

    def get_summary(self) -> Dict:
        """Generate a summary of the metrics"""
        duration = (self.end_time or datetime.now()) - self.start_time

        return {
            "scraper_name": self.scraper_name,
            "duration_seconds": duration.total_seconds(),
            "total_items": {
                "found": self.total_items_found,
                "processed": self.total_items_processed,
                "skipped": sum(self.skip_reasons.values()),
                "errors": sum(self.error_counts.values()),
            },
            "error_counts": dict(self.error_counts),
            "skip_reasons": dict(self.skip_reasons),
            "quality_distribution": dict(self.quality_stats),
            "source_distribution": dict(self.source_stats),
        }

    def format_summary(self) -> str:
        """Format the metrics summary as a nicely formatted string"""
        if self.skip_scraping:
            return f"{self.scraper_name} scraping was skipped due to recent scraping"

        summary = self.get_summary()
        lines = [""]

        # Header
        lines.extend(
            [
                "=" * 80,
                f"{self.scraper_name.upper()} Scraping Metrics Summary".center(80),
                "=" * 80,
                "",
            ]
        )

        if self.meta_data:
            lines.append(f"Meta ID: {self.meta_data.id}")
            lines.append(f"Title: {self.meta_data.title}")
            lines.append(f"Year: {self.meta_data.year}")

        if self.season:
            lines.append(f"Season: {self.season}")
            lines.append(f"Episode: {self.episode}")
        lines.append("")

        # Duration
        lines.append(f"Duration: {summary['duration_seconds']:.2f} seconds")
        lines.append("")

        # Items Summary
        lines.extend(
            [
                "Items:",
                f"  ├─ Found     : {summary['total_items']['found']}",
                f"  ├─ Processed : {summary['total_items']['processed']}",
                f"  ├─ Skipped   : {summary['total_items']['skipped']}",
                f"  └─ Errors    : {summary['total_items']['errors']}",
                "",
            ]
        )

        # Error Distribution
        if self.error_counts:
            lines.append("Error Distribution:")
            for i, (error_type, count) in enumerate(self.error_counts.most_common(), 1):
                prefix = "  └─" if i == len(self.error_counts) else "  ├─"
                lines.append(f"{prefix} {error_type:<20} : {count}")
            lines.append("")

        # Skip Reasons
        if self.skip_reasons:
            lines.append("Skip Reasons:")
            for i, (reason, count) in enumerate(self.skip_reasons.most_common(), 1):
                prefix = "  └─" if i == len(self.skip_reasons) else "  ├─"
                lines.append(f"{prefix} {reason:<20} : {count}")
            lines.append("")

        # Quality Distribution
        if self.quality_stats:
            lines.append("Quality Distribution:")
            for i, (quality, count) in enumerate(self.quality_stats.most_common(), 1):
                prefix = "  └─" if i == len(self.quality_stats) else "  ├─"
                lines.append(f"{prefix} {quality:<10} : {count:>4}")
            lines.append("")

        # Source Distribution
        if self.source_stats:
            lines.append("Source Distribution:")
            for i, (source, count) in enumerate(self.source_stats.most_common(), 1):
                prefix = "  └─" if i == len(self.source_stats) else "  ├─"
                lines.append(f"{prefix} {source:<15} : {count:>4} ")
            lines.append("")

        # Add Indexer Statistics section
        if self.indexer_stats:
            lines.extend(["Indexer Statistics:", ""])

            for indexer_name, stats in self.indexer_stats.items():

                lines.extend(
                    [
                        f"  {indexer_name}:",
                        f"    └─ Results :{stats['results_count']:>6}    Successes :{stats['success_count']:>6}    Errors :{stats['error_count']:>6}",
                    ]
                )

                if stats["errors"]:
                    lines.append("       Error Details:")
                    for error, count in stats["errors"].most_common():
                        lines.append(f"         └─ {error}: {count}")
                lines.append("")

        # Footer
        lines.extend(["=" * 80, ""])

        return "\n".join(lines)

    def log_summary(self, logger):
        """Log the metrics summary using the provided logger"""
        logger.info(self.format_summary())


class ScraperError(Exception):
    pass


class BaseScraper(abc.ABC):
    def __init__(self, cache_key_prefix: str, logger_name: str):
        self.logger = logging.getLogger(logger_name)
        self.http_client = httpx.AsyncClient(timeout=30)
        self.cache_key_prefix = cache_key_prefix
        self.metrics = ScraperMetrics(cache_key_prefix)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.http_client.aclose()

    async def scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """
        Scrape data and parse it into TorrentStreams objects.
        """
        self.metrics.start()
        self.metrics.meta_data = metadata
        self.metrics.season = season
        self.metrics.episode = episode
        try:
            result = await self._scrape_and_parse(
                metadata, catalog_type, season, episode
            )
            if isinstance(result, list):
                return result
            self.logger.error(
                f"Invalid result received from {self.cache_key_prefix}: {result}"
            )
            return []
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"An error occurred while scraping: {e}")
            return []
        finally:
            self.metrics.stop()
            self.metrics.log_summary(self.logger)

    async def process_streams(
        self,
        *stream_generators: AsyncGenerator[TorrentStreams, None],
        max_process: int = None,
        max_process_time: int = None,
        catalog_type: str = None,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreams, None]:
        """
        Process streams from multiple generators and yield them as they become available.
        """
        queue = asyncio.Queue()
        streams_processed = 0
        active_generators = len(stream_generators)
        processed_info_hashes = set()

        async def producer(gen: AsyncIterable, generator_id: int):
            try:
                async for stream_item in gen:
                    await queue.put((stream_item, generator_id))
                    self.logger.debug("Generator % produced a stream", generator_id)
            except Exception as err:
                self.logger.exception(f"Error in generator {generator_id}: {err}")
            finally:
                await queue.put(("DONE", generator_id))
                self.logger.debug("Generator %s finished", generator_id)

        async def queue_processor():
            nonlocal active_generators, streams_processed
            while active_generators > 0:
                item, gen_id = await queue.get()

                if item == "DONE":
                    active_generators -= 1
                    self.logger.debug(
                        "Generator %s completed. %s generators remaining",
                        gen_id,
                        active_generators,
                    )
                elif (
                    isinstance(item, TorrentStreams)
                    and item.id not in processed_info_hashes
                ):
                    processed_info_hashes.add(item.id)
                    if (
                        catalog_type != "series"
                        or item.get_episode(season, episode) is not None
                    ):
                        streams_processed += 1
                    self.logger.debug(
                        f"Processed stream from generator {gen_id}. Total streams processed: {streams_processed}"
                    )
                    yield item

                if max_process and streams_processed >= max_process:
                    self.logger.info(f"Reached max process limit of {max_process}")
                    raise MaxProcessLimitReached("Max process limit reached")

        try:
            async with asyncio.timeout(max_process_time):
                async with asyncio.TaskGroup() as tg:
                    # Create tasks for each stream generator
                    [
                        tg.create_task(producer(gen, i))
                        for i, gen in enumerate(stream_generators)
                    ]

                    # Yield items as they become available
                    async for stream in queue_processor():
                        yield stream
        except asyncio.TimeoutError:
            self.logger.warning(
                f"Stream processing timed out after {max_process_time} seconds. "
                f"Processed {streams_processed} streams"
            )
            self.metrics.record_skip("Max process time")
        except ExceptionGroup as eg:
            for e in eg.exceptions:
                if isinstance(e, MaxProcessLimitReached):
                    self.logger.info(
                        f"Stream processing cancelled after reaching max process limit of {max_process}"
                    )
                    self.metrics.record_skip("Max process limit")
                else:
                    self.logger.exception(
                        f"An error occurred during stream processing: {e}"
                    )
                    self.metrics.record_error(f"unexpected_stream_processing_error {e}")
        except Exception as e:
            self.logger.exception(f"An error occurred during stream processing: {e}")
            self.metrics.record_error(f"unexpected_stream_processing_error {e}")
        self.logger.info(
            f"Finished processing {streams_processed} streams from "
            f"{len(stream_generators)} generators"
        )

    @abc.abstractmethod
    async def _scrape_and_parse(self, *args, **kwargs) -> List[TorrentStreams]:
        """
        Internal method for actual scraping implementation.
        This should be implemented by each scraper.
        """
        pass

    @staticmethod
    def cache(ttl: int = 3600):
        """
        Decorator for caching the scraping status using Redis Sorted Sets with timestamps.
        :param ttl: Time to live for the cache in seconds
        """

        def decorator(func):
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                cache_key = self.get_cache_key(*args, **kwargs)
                current_time = int(time.time())

                # Check if the item has been scraped recently
                score = await REDIS_ASYNC_CLIENT.zscore(
                    self.cache_key_prefix, cache_key
                )

                if score and current_time - score < ttl:
                    self.metrics.skip_scrape()
                    return []  # Item has been scraped recently, no need to scrape again

                result = await func(self, *args, **kwargs)

                # Mark the item as scraped with the current timestamp
                await REDIS_ASYNC_CLIENT.zadd(
                    self.cache_key_prefix, {cache_key: current_time}
                )

                return result

            return wrapper

        return decorator

    @staticmethod
    def rate_limit(calls: int, period: timedelta):
        """
        Decorator for rate limiting method calls.
        :param calls: Number of calls allowed in the period
        :param period: Time period for the rate limit
        """

        def decorator(func):
            @sleep_and_retry
            @limits(calls=calls, period=period.total_seconds())
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                return await func(self, *args, **kwargs)

            return wrapper

        return decorator

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10)
    )
    async def make_request(
        self, url: str, method: str = "GET", is_expected_to_fail: bool = False, **kwargs
    ) -> httpx.Response:
        """
        Make an HTTP request with retry logic.
        """
        try:
            response = await self.http_client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and is_expected_to_fail:
                return e.response
            self.logger.error(f"HTTP error occurred: {e}")
            raise ScraperError(f"HTTP error occurred: {e}")
        except httpx.RequestError as e:
            self.logger.error(f"An error occurred while requesting {e.request.url!r}.")
            raise ScraperError(f"An error occurred while requesting {e.request.url!r}.")

    def validate_response(self, response: Dict[str, Any]) -> bool:
        """
        Validate the response from the scraper.
        :param response: Response dictionary
        :return: True if valid, False otherwise
        """
        pass

    async def parse_response(
        self,
        response: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """
        Parse the response into TorrentStreams objects.
        :param response: Response dictionary
        :param metadata: MediaFusionMetaData object
        :param catalog_type: Catalog type (movie, series)
        :param season: Season number (for series)
        :param episode: Episode number (for series)
        :return: List of TorrentStreams objects
        """
        pass

    def get_cache_key(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: str = None,
        episode: str = None,
        *_args,
        **_kwargs,
    ) -> str:
        """
        Generate a cache key for the given arguments.
        :return: Cache key string
        """
        if catalog_type == "movie":
            return f"{catalog_type}:{metadata.id}"

        return f"{catalog_type}:{metadata.id}:{season}:{episode}"

    def validate_title_and_year(
        self,
        parsed_data: dict,
        metadata: MediaFusionMovieMetaData | MediaFusionSeriesMetaData,
        catalog_type: str,
        torrent_title: str,
        expected_ratio: int = 87,
    ) -> bool:
        """
        Validate the title and year of the parsed data against the metadata.
        :param parsed_data: Parsed data dictionary
        :param metadata: MediaFusionMetaData object
        :param catalog_type: Catalog type (movie, series)
        :param torrent_title: Torrent title
        :param expected_ratio: Expected similarity ratio

        :return: True if valid, False otherwise
        """
        if settings.adult_content_filter_in_torrent_title and parsed_data.get("adult"):
            self.metrics.record_skip("Adult content")
            self.logger.debug(
                f"Torrent title contains adult content: '{torrent_title}'"
            )
            return False

        # Check similarity ratios
        max_similarity_ratio = calculate_max_similarity_ratio(
            parsed_data["title"], metadata.title, metadata.aka_titles
        )

        # Log and return False if similarity ratios is below the expected threshold
        if max_similarity_ratio < expected_ratio:
            self.metrics.record_skip("Title mismatch")
            self.logger.debug(
                f"Title mismatch: '{parsed_data['title']}' vs. '{metadata.title}'. Torrent title: '{torrent_title}'"
            )
            return False

        # Validate year based on a catalog type
        if catalog_type == "movie":
            if parsed_data.get("year") != metadata.year:
                self.metrics.record_skip("Year mismatch")
                self.logger.debug(
                    f"Year mismatch for movie: {parsed_data['title']} ({parsed_data.get('year')}) vs. {metadata.title} ({metadata.year}). Torrent title: '{torrent_title}'"
                )
                return False
            if parsed_data.get("season"):
                self.metrics.record_skip("Year mismatch")
                self.logger.debug(
                    f"Season found for movie: {parsed_data['title']} ({parsed_data.get('season')}). Torrent title: '{torrent_title}'"
                )
                return False

        parsed_year = parsed_data.get("year")
        if (
            catalog_type == "series"
            and parsed_year
            and (
                (
                    metadata.end_year
                    and metadata.year
                    and not (metadata.year <= parsed_year <= metadata.end_year)
                )
                or (
                    metadata.year
                    and not metadata.end_year
                    and parsed_year < metadata.year
                )
            )
        ):
            self.metrics.record_skip("Year mismatch")
            self.logger.debug(
                f"Year mismatch for series: {parsed_data['title']} ({parsed_year}) vs. {metadata.title} ({metadata.year} - {metadata.end_year}). Torrent title: '{torrent_title}'"
            )
            return False
        return True

    @staticmethod
    async def store_streams(streams: List[TorrentStreams]):
        """
        Store the parsed streams in the database.
        :param streams: List of TorrentStreams objects
        """
        from db.crud import store_new_torrent_streams

        await store_new_torrent_streams(streams)

    @staticmethod
    async def remove_expired_items(scraper_prefix: str, ttl: int = 3600):
        """
        Remove expired items from the cache.
        """
        current_time = int(time.time())
        await REDIS_ASYNC_CLIENT.zremrangebyscore(scraper_prefix, 0, current_time - ttl)
