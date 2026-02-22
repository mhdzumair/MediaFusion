import abc
import asyncio
import json
import logging
import re
import time
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Literal

import httpx
import PTT
from ratelimit import limits, sleep_and_retry
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential
from torf import Magnet, MagnetError

from db import crud
from db.config import settings
from db.database import get_background_session
from db.enums import TorrentType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import MetadataData, StreamFileData, TorrentStreamData, UserData
from scrapers import torrent_info
from scrapers.imdb_data import get_episode_by_date
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import (
    NON_VIDEO_BLOCKLIST_KEYWORDS,
    VIDEO_ALLOWLIST_KEYWORDS,
    calculate_max_similarity_ratio,
    is_contain_18_plus_keywords,
)
from utils.torrent import extract_torrent_metadata, info_hashes_to_torrent_metadata

# Redis key constants for scraper metrics storage
SCRAPER_METRICS_KEY_PREFIX = "scraper_metrics:"
SCRAPER_METRICS_HISTORY_KEY = "scraper_metrics_history:"
SCRAPER_METRICS_LATEST_KEY = "scraper_metrics_latest:"
SCRAPER_METRICS_AGGREGATED_KEY = "scraper_metrics_aggregated:"
SCRAPER_METRICS_TTL = 86400 * 7  # 7 days TTL for individual metrics
SCRAPER_METRICS_HISTORY_MAX = 100  # Keep last 100 runs per scraper


@dataclass
class ScraperMetrics:
    scraper_name: str
    meta_data: MetadataData = None
    season: int = None
    episode: int = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    total_items_found: int = 0
    total_items_processed: int = 0
    error_counts: Counter = field(default_factory=Counter)
    skip_reasons: Counter = field(default_factory=Counter)
    quality_stats: Counter = field(default_factory=Counter)
    source_stats: Counter = field(default_factory=Counter)
    skip_scraping: bool = False
    indexer_stats: dict[str, dict[str, Any]] = field(default_factory=dict)

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

    def get_summary(self) -> dict:
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
            lines.append(f"Meta ID: {self.meta_data.get_canonical_id()}")
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

    def get_full_summary(self) -> dict:
        """Generate a comprehensive summary of the metrics for storage"""
        duration = (self.end_time or datetime.now()) - self.start_time

        # Convert indexer stats errors from Counter to dict
        indexer_stats_serializable = {}
        for indexer_name, stats in self.indexer_stats.items():
            indexer_stats_serializable[indexer_name] = {
                "success_count": stats["success_count"],
                "error_count": stats["error_count"],
                "results_count": stats["results_count"],
                "errors": dict(stats["errors"]) if isinstance(stats["errors"], Counter) else stats["errors"],
            }

        return {
            "scraper_name": self.scraper_name,
            "timestamp": self.start_time.isoformat(),
            "end_timestamp": (self.end_time or datetime.now()).isoformat(),
            "duration_seconds": duration.total_seconds(),
            "meta_id": self.meta_data.id if self.meta_data else None,
            "meta_title": self.meta_data.title if self.meta_data else None,
            "season": self.season,
            "episode": self.episode,
            "skip_scraping": self.skip_scraping,
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
            "indexer_stats": indexer_stats_serializable,
        }

    async def save_to_redis(self) -> bool:
        """
        Save metrics to Redis for later retrieval.
        Stores:
        - Latest metrics for the scraper
        - History of runs (capped to SCRAPER_METRICS_HISTORY_MAX)
        - Aggregated statistics
        """
        try:
            summary = self.get_full_summary()
            summary_json = json.dumps(summary)

            # Save latest metrics for this scraper
            latest_key = f"{SCRAPER_METRICS_LATEST_KEY}{self.scraper_name}"
            await REDIS_ASYNC_CLIENT.set(latest_key, summary_json, ex=SCRAPER_METRICS_TTL)

            # Add to history list (prepend to beginning)
            history_key = f"{SCRAPER_METRICS_HISTORY_KEY}{self.scraper_name}"
            pipeline = await REDIS_ASYNC_CLIENT.client.pipeline()
            pipeline.lpush(history_key, summary_json)
            pipeline.ltrim(history_key, 0, SCRAPER_METRICS_HISTORY_MAX - 1)
            pipeline.expire(history_key, SCRAPER_METRICS_TTL)
            await pipeline.execute()

            # Update aggregated statistics
            await self._update_aggregated_stats(summary)

            return True
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to save scraper metrics to Redis: {e}")
            return False

    async def _update_aggregated_stats(self, summary: dict):
        """Update aggregated statistics for the scraper"""
        try:
            agg_key = f"{SCRAPER_METRICS_AGGREGATED_KEY}{self.scraper_name}"
            existing = await REDIS_ASYNC_CLIENT.get(agg_key)

            if existing:
                agg_stats = json.loads(existing)
            else:
                agg_stats = {
                    "scraper_name": self.scraper_name,
                    "total_runs": 0,
                    "total_items_found": 0,
                    "total_items_processed": 0,
                    "total_items_skipped": 0,
                    "total_errors": 0,
                    "total_duration_seconds": 0,
                    "successful_runs": 0,
                    "failed_runs": 0,
                    "skipped_runs": 0,
                    "error_distribution": {},
                    "skip_reason_distribution": {},
                    "quality_distribution": {},
                    "source_distribution": {},
                    "last_run": None,
                    "last_successful_run": None,
                }

            # Update counters
            agg_stats["total_runs"] += 1
            agg_stats["total_items_found"] += summary["total_items"]["found"]
            agg_stats["total_items_processed"] += summary["total_items"]["processed"]
            agg_stats["total_items_skipped"] += summary["total_items"]["skipped"]
            agg_stats["total_errors"] += summary["total_items"]["errors"]
            agg_stats["total_duration_seconds"] += summary["duration_seconds"]
            agg_stats["last_run"] = summary["timestamp"]

            # Track run outcomes
            if summary["skip_scraping"]:
                agg_stats["skipped_runs"] += 1
            elif summary["total_items"]["errors"] > 0:
                agg_stats["failed_runs"] += 1
            else:
                agg_stats["successful_runs"] += 1
                agg_stats["last_successful_run"] = summary["timestamp"]

            # Aggregate distributions
            for error_type, count in summary["error_counts"].items():
                agg_stats["error_distribution"][error_type] = agg_stats["error_distribution"].get(error_type, 0) + count

            for reason, count in summary["skip_reasons"].items():
                agg_stats["skip_reason_distribution"][reason] = (
                    agg_stats["skip_reason_distribution"].get(reason, 0) + count
                )

            for quality, count in summary["quality_distribution"].items():
                agg_stats["quality_distribution"][quality] = agg_stats["quality_distribution"].get(quality, 0) + count

            for source, count in summary["source_distribution"].items():
                agg_stats["source_distribution"][source] = agg_stats["source_distribution"].get(source, 0) + count

            # Save updated aggregated stats
            await REDIS_ASYNC_CLIENT.set(
                agg_key,
                json.dumps(agg_stats),
                ex=SCRAPER_METRICS_TTL * 4,  # Keep aggregated stats longer (28 days)
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to update aggregated scraper metrics: {e}")


class ScraperError(Exception):
    pass


class BaseScraper(abc.ABC):
    blocklist_keywords = NON_VIDEO_BLOCKLIST_KEYWORDS
    allowlist_keywords = VIDEO_ALLOWLIST_KEYWORDS

    # Sports-broadcaster tags that only appear in live sporting-event uploads.
    # A real movie torrent would never contain "SkyF1HD" or "F1TV".
    _sports_broadcaster_re = re.compile(
        r"Sky\s*F1(?:UHD|HD)?|Sky\s*Sports|F1TV|V\s*Sport|MotoGP\s*VideoPass",
        re.IGNORECASE,
    )

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
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData] | list | None:
        """
        Scrape data and parse it into TorrentStreamData objects.
        """
        self.metrics.start()
        self.metrics.meta_data = metadata
        self.metrics.season = season
        self.metrics.episode = episode
        try:
            result = await self._scrape_and_parse(user_data, metadata, catalog_type, season, episode)
            if isinstance(result, list):
                return result
            self.logger.error(f"Invalid result received from {self.cache_key_prefix}: {result}")
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"An error occurred while scraping: {e}")
        finally:
            self.metrics.stop()
            self.metrics.log_summary(self.logger)
            # Persist metrics to Redis for later retrieval
            await self.metrics.save_to_redis()
        return []

    async def process_streams(
        self,
        *stream_generators: AsyncGenerator[TorrentStreamData, None],
        max_process: int = None,
        max_process_time: int = None,
        catalog_type: str = None,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreamData, None]:
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
                elif isinstance(item, TorrentStreamData) and item.info_hash not in processed_info_hashes:
                    processed_info_hashes.add(item.info_hash)
                    if catalog_type != "series" or item.get_episodes(season, episode):
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
                    [tg.create_task(producer(gen, i)) for i, gen in enumerate(stream_generators)]

                    # Yield items as they become available
                    async for stream in queue_processor():
                        try:
                            yield stream
                        except (GeneratorExit, asyncio.CancelledError):
                            self.logger.debug("Stream processing consumer stopped early")
                            return
        except TimeoutError:
            self.logger.warning(
                f"Stream processing timed out after {max_process_time} seconds. Processed {streams_processed} streams"
            )
            self.metrics.record_skip("Max process time")
        except BaseExceptionGroup as eg:
            for e in eg.exceptions:
                if isinstance(e, (GeneratorExit, asyncio.CancelledError)):
                    self.logger.debug("Stream processing stopped due to consumer cancellation")
                    continue
                if isinstance(e, MaxProcessLimitReached):
                    self.logger.info(f"Stream processing cancelled after reaching max process limit of {max_process}")
                    self.metrics.record_skip("Max process limit")
                    continue
                self.logger.exception(f"An error occurred during stream processing: {e}")
                self.metrics.record_error(f"unexpected_stream_processing_error {e}")
        except Exception as e:
            self.logger.exception(f"An error occurred during stream processing: {e}")
            self.metrics.record_error(f"unexpected_stream_processing_error {e}")
        self.logger.info(f"Finished processing {streams_processed} streams from {len(stream_generators)} generators")

    @abc.abstractmethod
    async def _scrape_and_parse(self, *args, **kwargs) -> list[TorrentStreamData]:
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
                score = await REDIS_ASYNC_CLIENT.zscore(self.cache_key_prefix, cache_key)

                if score and current_time - score < ttl:
                    self.metrics.skip_scrape()
                    return []  # Item has been scraped recently, no need to scrape again

                result = await func(self, *args, **kwargs)

                # Mark the item as scraped with the current timestamp
                await REDIS_ASYNC_CLIENT.zadd(self.cache_key_prefix, {cache_key: current_time})

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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
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

    def validate_response(self, response: dict[str, Any]) -> bool:
        """
        Validate the response from the scraper.
        :param response: Response dictionary
        :return: True if valid, False otherwise
        """
        pass

    async def parse_response(
        self,
        response: dict[str, Any],
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        """
        Parse the response into TorrentStreamData objects.
        :param response: Response dictionary
        :param user_data: UserData object
        :param metadata: MetadataData object
        :param catalog_type: Catalog type (movie, series)
        :param season: Season number (for series)
        :param episode: Episode number (for series)
        :return: List of TorrentStreamData objects
        """
        pass

    def get_cache_key(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: str = None,
        episode: str = None,
        *_args,
        **_kwargs,
    ) -> str:
        """
        Generate a cache key for the given arguments.
        Uses canonical external ID (IMDb > TMDB > TVDB > external_id).
        :return: Cache key string
        """
        canonical_id = metadata.get_canonical_id()
        if catalog_type == "movie":
            return f"{catalog_type}:{canonical_id}"

        return f"{catalog_type}:{canonical_id}:{season}:{episode}"

    @staticmethod
    def parse_title_data(title: str) -> dict:
        """Parse torrent title using PTT"""
        parsed = PTT.parse_title(title, True)
        return {"torrent_name": title, **parsed}

    def validate_title_and_year(
        self,
        parsed_data: dict,
        metadata: MetadataData,
        catalog_type: str,
        torrent_title: str,
        expected_ratio: int = 87,
    ) -> bool:
        """
        Validate the title and year of the parsed data against the metadata.
        :param parsed_data: Parsed data dictionary
        :param metadata: MetadataData object
        :param catalog_type: Catalog type (movie, series)
        :param torrent_title: Torrent title
        :param expected_ratio: Expected similarity ratio

        :return: True if valid, False otherwise
        """
        parsed_title = parsed_data["title"]

        # Guard: reject sports-broadcast torrents when scraping for a movie.
        # Torrents from live sporting events embed broadcaster tags like
        # "SkyF1HD", "SkyUHD", "F1TV" etc. that never appear in movie
        # releases.  This prevents e.g. "F1 2025 R01 Australian Grand Prix
        # SkyF1HD 1080P" from being matched to the Brad Pitt "F1" movie.
        if catalog_type == "movie" and self._sports_broadcaster_re.search(torrent_title):
            self.metrics.record_skip("Sports broadcast mismatch")
            self.logger.debug(
                "Rejecting sports-broadcast torrent for movie %r: %s",
                metadata.title,
                torrent_title,
            )
            return False

        # Check similarity ratios
        max_similarity_ratio = calculate_max_similarity_ratio(parsed_title, metadata.title, metadata.aka_titles)

        # Log and return False if similarity ratios is below the expected threshold
        if max_similarity_ratio < expected_ratio:
            self.metrics.record_skip("Title mismatch")
            self.logger.debug(
                f"Title mismatch: '{parsed_title}' vs. '{metadata.title}'. Torrent title: '{torrent_title}'"
            )
            return False

        # Validate year based on a catalog type
        if catalog_type == "movie":
            if parsed_data.get("year") != metadata.year:
                self.metrics.record_skip("Year mismatch")
                self.logger.debug(
                    f"Year mismatch for movie: {parsed_title} ({parsed_data.get('year')}) vs. {metadata.title} ({metadata.year}). Torrent title: '{torrent_title}'"
                )
                return False
            if parsed_data.get("season"):
                self.metrics.record_skip("Year mismatch")
                self.logger.debug(
                    f"Season found for movie: {parsed_title} ({parsed_data.get('season')}). Torrent title: '{torrent_title}'"
                )
                return False

        parsed_year = parsed_data.get("year")
        if (
            catalog_type == "series"
            and parsed_year
            and (
                (metadata.end_year and metadata.year and not (metadata.year <= parsed_year <= metadata.end_year))
                or (metadata.year and not metadata.end_year and parsed_year < metadata.year)
            )
        ):
            self.metrics.record_skip("Year mismatch")
            self.logger.debug(
                f"Year mismatch for series: {parsed_title} ({parsed_year}) vs. {metadata.title} ({metadata.year} - {metadata.end_year}). Torrent title: '{torrent_title}'"
            )
            return False
        return True

    @staticmethod
    async def store_streams(streams: list[TorrentStreamData], session: AsyncSession | None = None):
        """
        Store the parsed streams in the database.
        :param streams: List of TorrentStreamData objects
        :param session: Optional existing session to reuse
        """
        if session is None:
            async with get_background_session() as managed_session:
                await crud.store_new_torrent_streams(managed_session, [s.model_dump(by_alias=True) for s in streams])
                await managed_session.commit()
            return

        await crud.store_new_torrent_streams(session, [s.model_dump(by_alias=True) for s in streams])
        await session.commit()

    @property
    def search_query_timeout(self) -> int:
        """Timeout for search queries"""
        return 10

    @property
    def torrent_download_timeout(self) -> int:
        """Timeout for downloading torrent files (longer than search since it may involve redirects)"""
        return 30

    @staticmethod
    async def remove_expired_items(scraper_prefix: str, ttl: int = 3600):
        """
        Remove expired items from the cache.
        """
        current_time = int(time.time())
        await REDIS_ASYNC_CLIENT.zremrangebyscore(scraper_prefix, 0, current_time - ttl)

    async def get_torrent_data(
        self,
        download_url: str,
        parsed_data: dict,
        headers: dict = None,
        episode_name_parser: str = None,
    ) -> tuple[dict | None, bool]:
        """Common method to get torrent data from magnet or URL"""
        if download_url.startswith("magnet:"):
            try:
                magnet = Magnet.from_string(download_url)
            except MagnetError:
                return None, False
            return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, True

        max_5xx_retries = 2
        for attempt in range(max_5xx_retries + 1):
            try:
                response = await self.http_client.get(
                    download_url,
                    follow_redirects=False,
                    timeout=self.torrent_download_timeout,
                    headers=headers,
                )

                if response.status_code in [301, 302, 303, 307, 308]:
                    redirect_url = response.headers.get("Location")
                    if not redirect_url:
                        self.logger.warning("Redirect without location while fetching torrent data: %s", download_url)
                        return None, False
                    return await self.get_torrent_data(redirect_url, parsed_data, headers, episode_name_parser)

                response.raise_for_status()
                return (
                    extract_torrent_metadata(response.content, parsed_data, episode_name_parser=episode_name_parser),
                    True,
                )
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                if status_code == 429:
                    raise
                if 500 <= status_code < 600:
                    if attempt < max_5xx_retries:
                        self.logger.warning(
                            "Transient %d while getting torrent data (attempt %d/%d): %s",
                            status_code,
                            attempt + 1,
                            max_5xx_retries + 1,
                            download_url,
                        )
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    self.logger.warning("Repeated %d while getting torrent data: %s", status_code, download_url)
                    return None, False
                self.logger.error("HTTP error getting torrent data: %s, status code: %d", download_url, status_code)
                return None, False
            except (httpx.TimeoutException, httpx.ConnectTimeout):
                self.logger.warning(f"Timeout while getting torrent data for: {download_url}")
                raise
            except httpx.RequestError as error:
                self.logger.error(f"Request error getting torrent data: {error}")
                raise
            except Exception as e:
                self.logger.exception(f"Error getting torrent data: {e}")
                return None, False

        return None, False

    async def process_stream(
        self,
        stream_data: dict[str, Any],
        metadata: MetadataData,
        catalog_type: str,
        processed_info_hashes: set[str],
        season: int = None,
        episode: int = None,
    ) -> TorrentStreamData | None:
        """Common process stream implementation for all indexers"""
        try:
            torrent_title = self.get_title(stream_data)
            if not torrent_title:
                return None

            if is_contain_18_plus_keywords(torrent_title):
                self.logger.warning(f"Adult content found in torrent title: {torrent_title}")
                self.metrics.record_skip("Adult content")
                return None

            parsed_data = self.parse_title_data(torrent_title)
            if not self.validate_title_and_year(
                parsed_data,
                metadata,
                catalog_type,
                torrent_title,
            ):
                return None

            # Get indexer-specific parsed data
            parsed_data = await self.parse_indexer_data(stream_data, catalog_type, parsed_data)
            if not parsed_data or parsed_data["info_hash"] in processed_info_hashes:
                self.metrics.record_skip("Duplicated info_hash")
                return None

            torrent_type = self.get_torrent_type(stream_data)

            # Build files list using new StreamFileData schema
            files: list[StreamFileData] = []

            if catalog_type == "series":
                # Handle series - try to get episode info from various sources
                seasons = parsed_data.get("seasons")

                if parsed_data.get("file_data"):
                    # Use parsed file data with episode info
                    for file in parsed_data["file_data"]:
                        if file.get("episode_number") is not None and file.get("season_number") is not None:
                            files.append(
                                StreamFileData(
                                    file_index=file.get("index", 0),
                                    filename=file.get("filename", ""),
                                    size=file.get("size", 0),
                                    file_type="video",
                                    season_number=file["season_number"],
                                    episode_number=file["episode_number"],
                                )
                            )
                elif (episodes := parsed_data.get("episodes")) and seasons:
                    # Simple case - episodes list with season info
                    for ep in episodes:
                        files.append(
                            StreamFileData(
                                file_index=0,
                                filename="",
                                file_type="video",
                                season_number=seasons[0],
                                episode_number=ep,
                            )
                        )
                elif parsed_data.get("date"):
                    # Search with date for episode - requires IMDb ID
                    imdb_id = metadata.get_imdb_id()
                    if imdb_id:
                        episode_date = datetime.strptime(parsed_data["date"], "%Y-%m-%d")
                        imdb_episode = await get_episode_by_date(
                            imdb_id,
                            parsed_data["title"],
                            episode_date.date(),
                        )
                        if imdb_episode and imdb_episode.season and imdb_episode.episode:
                            self.logger.info(
                                f"Episode found by {episode_date} date for {parsed_data.get('title')} ({imdb_id})"
                            )
                            files.append(
                                StreamFileData(
                                    file_index=0,
                                    filename=imdb_episode.title or "",
                                    file_type="video",
                                    season_number=int(imdb_episode.season),
                                    episode_number=int(imdb_episode.episode),
                                )
                            )
                else:
                    # Try to get torrent metadata from trackers
                    torrent_data = await info_hashes_to_torrent_metadata(
                        [parsed_data["info_hash"]], parsed_data["announce_list"]
                    )
                    if torrent_data:
                        torrent_file_metadata = torrent_data[0]
                        for file in torrent_file_metadata.get("file_data", []):
                            if file.get("episode_number") is not None and file.get("season_number") is not None:
                                files.append(
                                    StreamFileData(
                                        file_index=file.get("index", 0),
                                        filename=file.get("filename", ""),
                                        size=file.get("size", 0),
                                        file_type="video",
                                        season_number=file["season_number"],
                                        episode_number=file["episode_number"],
                                    )
                                )
                    elif seasons:
                        # Some pack contains few episodes - can't determine exact episode
                        for season_number in seasons:
                            files.append(
                                StreamFileData(
                                    file_index=0,
                                    filename="",
                                    file_type="video",
                                    season_number=season_number,
                                    episode_number=1,
                                )
                            )

                if not files:
                    self.metrics.record_skip("Missing episode info")
                    self.logger.warning(
                        f"Episode not found in stream: '{torrent_title}' Scraping for: S{season}E{episode}"
                    )
                    return None
            else:
                # For movies, use the largest file
                largest_file = parsed_data.get("largest_file", {})
                if largest_file:
                    files.append(
                        StreamFileData(
                            file_index=largest_file.get("index", 0),
                            filename=largest_file.get("filename", ""),
                            size=largest_file.get("size", 0),
                            file_type="video",
                        )
                    )

            torrent_stream = TorrentStreamData(
                info_hash=parsed_data["info_hash"],
                meta_id=metadata.external_id,
                name=parsed_data["torrent_name"],
                size=parsed_data["total_size"],
                source=parsed_data["source"],
                uploader=parsed_data.get("uploader"),
                seeders=parsed_data["seeders"],
                created_at=parsed_data["created_at"],
                announce_list=parsed_data["announce_list"],
                torrent_type=torrent_type,
                torrent_file=(
                    parsed_data.get("torrent_file")
                    if torrent_type in [TorrentType.PRIVATE, TorrentType.SEMI_PRIVATE]
                    else None
                ),
                files=files,
                # Single-value quality attributes
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                bit_depth=parsed_data.get("bit_depth"),
                release_group=parsed_data.get("group"),
                # Multi-value quality attributes (from PTT)
                audio_formats=parsed_data.get("audio", []) if isinstance(parsed_data.get("audio"), list) else [],
                channels=parsed_data.get("channels", []) if isinstance(parsed_data.get("channels"), list) else [],
                hdr_formats=parsed_data.get("hdr", []) if isinstance(parsed_data.get("hdr"), list) else [],
                languages=parsed_data.get("languages", []),
                # Release flags
                is_remastered=parsed_data.get("remastered", False),
                is_upscaled=parsed_data.get("upscaled", False),
                is_proper=parsed_data.get("proper", False),
                is_repack=parsed_data.get("repack", False),
                is_extended=parsed_data.get("extended", False),
                is_complete=parsed_data.get("complete", False),
                is_dubbed=parsed_data.get("dubbed", False),
                is_subbed=parsed_data.get("subbed", False),
            )

            self.metrics.record_processed_item()
            self.metrics.record_quality(torrent_stream.quality)
            self.metrics.record_source(torrent_stream.source)

            processed_info_hashes.add(parsed_data["info_hash"])
            self.logger.info(
                f"Successfully parsed stream: {parsed_data.get('title')} "
                f"({parsed_data.get('year')}) ({metadata.get_canonical_id()}) "
                f"info_hash: {parsed_data.get('info_hash')}"
            )
            return torrent_stream

        except httpx.ReadTimeout:
            self.metrics.record_error("timeout")
            self.logger.warning("Timeout while processing search result")
            return None
        except httpx.HTTPStatusError as e:
            self.metrics.record_error("http_status_error")
            status_code = e.response.status_code if e.response else "unknown"
            self.logger.warning("HTTP status error while processing search result (%s): %s", status_code, e)
            return None
        except Exception as e:
            self.metrics.record_error("result_processing_error")
            self.logger.exception(f"Error processing search result: {e}")
            return None


class BackgroundScraperManager:
    def __init__(self):
        self.movie_hash_key = "background_search:movies"
        self.series_hash_key = "background_search:series"
        self.processing_set_key = "background_search:processing"
        self.batch_size = 10  # Number of items to process in each batch

    async def add_movie_to_queue(self, meta_id: str) -> None:
        """Add a movie to the background search queue"""
        await REDIS_ASYNC_CLIENT.hset(
            self.movie_hash_key,
            meta_id,
            json.dumps({"last_scrape": None, "added_at": datetime.now().timestamp()}),
        )

    async def add_series_to_queue(self, meta_id: str, season: int, episode: int) -> None:
        """Add a series episode to the background search queue"""
        key = f"{meta_id}:{season}:{episode}"
        await REDIS_ASYNC_CLIENT.hset(
            self.series_hash_key,
            key,
            json.dumps({"last_scrape": None, "added_at": datetime.now().timestamp()}),
        )

    async def get_pending_items(self, item_type: str) -> list[dict]:
        """Get items that need to be scraped"""
        hash_key = self.movie_hash_key if item_type == "movie" else self.series_hash_key
        cutoff_time = datetime.now() - timedelta(hours=settings.background_search_interval_hours)

        # Get all items
        all_items = await REDIS_ASYNC_CLIENT.hgetall(hash_key)
        pending_items = []

        for item_key, item_data in all_items.items():
            data = json.loads(item_data)
            last_scrape = data.get("last_scrape")

            # Check if item needs scraping
            if not last_scrape or datetime.fromtimestamp(last_scrape) < cutoff_time:
                # Check if not currently processing
                if not await REDIS_ASYNC_CLIENT.sismember(self.processing_set_key, item_key):
                    pending_items.append({"key": item_key.decode("utf-8"), "data": data})

        return pending_items[: self.batch_size]

    async def mark_as_processing(self, item_key: str) -> None:
        """Mark an item as currently being processed"""
        await REDIS_ASYNC_CLIENT.sadd(self.processing_set_key, item_key)

    async def mark_as_completed(self, item_key: str, hash_key: str) -> None:
        """Mark an item as completed and update last scrape time"""
        # Update last scrape time
        item_data = await REDIS_ASYNC_CLIENT.hget(hash_key, item_key)
        if item_data:
            data = json.loads(item_data)
            data["last_scrape"] = datetime.now().timestamp()
            await REDIS_ASYNC_CLIENT.hset(hash_key, item_key, json.dumps(data))

        # Remove from processing set
        await REDIS_ASYNC_CLIENT.srem(self.processing_set_key, item_key)

    async def cleanup_stale_processing(self, max_processing_time: int = 3600) -> None:
        """Clean up items stuck in processing state"""
        processing_items = await REDIS_ASYNC_CLIENT.smembers(self.processing_set_key)
        for item_key in processing_items:
            await REDIS_ASYNC_CLIENT.srem(self.processing_set_key, item_key)


class MaxProcessLimitReached(Exception):
    pass


class IndexerBaseScraper(BaseScraper, abc.ABC):
    """Base class for indexer-based scrapers (Prowlarr, Jackett)"""

    MOVIE_SEARCH_QUERY_TEMPLATES = [
        "{title} ({year})",  # Exact match with year
        "{title} {year}",  # Title with year (without parentheses)
        "{title}",  # Title-only fallback
    ]

    SERIES_SEARCH_QUERY_TEMPLATES = [
        "{title} S{season:02d}E{episode:02d}",  # Standard SXXEYY format
        "{title} Season {season} Episode {episode}",  # Verbose format
        "{title} {season}x{episode}",  # Alternate XXxYY format
        "{title} S{season:02d}",  # Season search
        "{title}",  # Title-only fallback
    ]

    MOVIE_CATEGORY_IDS = [
        2000,
        2010,
        2020,
        2030,
        2040,
        2045,
        2050,
        2060,
        2070,
        2080,
        2090,
    ]
    SERIES_CATEGORY_IDS = [
        5000,
        5010,
        5020,
        5030,
        5040,
        5045,
        5050,
        5060,
        5070,
        5080,
        5090,
    ]
    OTHER_CATEGORY_IDS = [8000, 8010, 8020]

    def __init__(self, cache_key_prefix: str, base_url: str):
        super().__init__(cache_key_prefix=cache_key_prefix, logger_name=__name__)
        self.base_url = base_url
        self.indexer_status = {}
        self.indexer_circuit_breakers = {}
        self.background_scraper_manager = BackgroundScraperManager()

    async def _scrape_and_parse(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        results = []
        processed_info_hashes: set[str] = set()

        # Get list of healthy indexers
        healthy_indexers = await self.get_healthy_indexers()
        if not healthy_indexers:
            self.metrics.record_error("No healthy indexers")
            self.logger.warning("No healthy indexers available")
            return results

        # Split indexers into chunks of 3
        indexer_chunks = list(self.split_indexers_into_chunks(healthy_indexers, 3))
        self.logger.info(f"Processing {len(healthy_indexers)} indexers in {len(indexer_chunks)} chunks")

        try:
            if catalog_type == "movie":
                async for stream in self.scrape_movie(processed_info_hashes, metadata, indexer_chunks):
                    results.append(stream)
            elif catalog_type == "series":
                async for stream in self.scrape_series(
                    processed_info_hashes, metadata, season, episode, indexer_chunks
                ):
                    results.append(stream)

        except httpx.ReadTimeout:
            self.metrics.record_error("timeout")
            self.logger.warning("Timeout while fetching search results")
        except httpx.HTTPStatusError as e:
            self.metrics.record_error("http_error")
            self.logger.error(
                f"Error fetching search results: {e.response.text}, status code: {e.response.status_code}"
            )
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"An error occurred during scraping: {str(e)}")

        self.logger.info(f"Returning {len(results)} scraped streams for {metadata.title}")
        return results

    async def scrape_movie(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        indexer_chunks: list[list[dict]],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Common movie scraping logic"""
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(self.scrape_movie_by_imdb(processed_info_hashes, metadata, chunk))

        # Add title-based searches if enabled
        if self.live_title_search_enabled:
            for chunk in indexer_chunks:
                for query_template in self.MOVIE_SEARCH_QUERY_TEMPLATES:
                    search_query = query_template.format(title=metadata.title, year=metadata.year)
                    search_generators.append(
                        self.scrape_movie_by_title(
                            processed_info_hashes,
                            metadata,
                            search_query=search_query,
                            indexers=chunk,
                        )
                    )
                if settings.scrape_with_aka_titles:
                    for aka_title in metadata.aka_titles:
                        search_generators.append(
                            self.scrape_movie_by_title(
                                processed_info_hashes,
                                metadata,
                                search_query=aka_title,
                                indexers=chunk,
                            )
                        )

        async for stream in self.process_streams(
            *search_generators,
            max_process=self.immediate_max_process,
            max_process_time=self.immediate_max_process_time,
        ):
            yield stream

        if self.background_title_search_enabled:
            await self.background_scraper_manager.add_movie_to_queue(metadata.get_canonical_id())

    async def scrape_series(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        season: int,
        episode: int,
        indexer_chunks: list[list[dict]],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Common series scraping logic"""
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(
                self.scrape_series_by_imdb(processed_info_hashes, metadata, season, episode, chunk)
            )

        # Add title-based searches if enabled
        if self.live_title_search_enabled:
            for chunk in indexer_chunks:
                for query_template in self.SERIES_SEARCH_QUERY_TEMPLATES:
                    search_query = query_template.format(title=metadata.title, season=season, episode=episode)
                    search_generators.append(
                        self.scrape_series_by_title(
                            processed_info_hashes,
                            metadata,
                            season,
                            episode,
                            search_query=search_query,
                            indexers=chunk,
                        )
                    )
                if settings.scrape_with_aka_titles:
                    for aka_title in metadata.aka_titles:
                        search_generators.append(
                            self.scrape_series_by_title(
                                processed_info_hashes,
                                metadata,
                                season,
                                episode,
                                search_query=aka_title,
                                indexers=chunk,
                            )
                        )

        async for stream in self.process_streams(
            *search_generators,
            max_process=self.immediate_max_process,
            max_process_time=self.immediate_max_process_time,
            catalog_type="series",
            season=season,
            episode=episode,
        ):
            yield stream

        if self.background_title_search_enabled:
            await self.background_scraper_manager.add_series_to_queue(metadata.get_canonical_id(), season, episode)

    @abc.abstractmethod
    async def get_healthy_indexers(self) -> list[dict]:
        """Get list of healthy indexer IDs"""
        pass

    @abc.abstractmethod
    async def fetch_search_results(
        self, params: dict, indexer_ids: list[int], timeout: int | None = None
    ) -> list[dict[str, Any]]:
        """Fetch search results from the indexer"""
        pass

    @abc.abstractmethod
    async def build_search_params(
        self,
        video_id: str,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        search_query: str = None,
    ) -> dict:
        """Build search parameters for the indexer"""
        pass

    @abc.abstractmethod
    async def parse_indexer_data(self, indexer_data: dict, catalog_type: str, parsed_data: dict) -> dict:
        """Parse indexer-specific data"""
        pass

    @property
    @abc.abstractmethod
    def live_title_search_enabled(self) -> bool:
        """Whether live title search is enabled"""
        pass

    @property
    @abc.abstractmethod
    def background_title_search_enabled(self) -> bool:
        """Whether background title search is enabled"""
        pass

    @property
    @abc.abstractmethod
    def immediate_max_process(self) -> int:
        """Maximum number of items to process immediately"""
        pass

    @property
    @abc.abstractmethod
    def immediate_max_process_time(self) -> int:
        """Maximum time to spend processing items immediately"""
        pass

    @abc.abstractmethod
    def get_info_hash(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_guid(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_title(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_imdb_id(self, item: dict) -> str | None:
        pass

    @abc.abstractmethod
    def get_category_ids(self, item: dict) -> list[int]:
        pass

    @abc.abstractmethod
    def get_magent_link(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_download_link(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_info_url(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_indexer(self, item: dict) -> str:
        pass

    @abc.abstractmethod
    def get_torrent_type(self, item: dict) -> TorrentType:
        pass

    @abc.abstractmethod
    def get_created_at(self, item: dict) -> datetime:
        pass

    async def scrape_movie_by_imdb(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        indexers: list[dict],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Scrape movie using IMDB ID"""
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="movie",
            categories=self.MOVIE_CATEGORY_IDS,
            catalog_type="movie",
            indexers=indexers,
            requires_imdb=True,
        ):
            yield stream

    async def scrape_movie_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        search_query: str,
        indexers: list[dict],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Scrape movie using title search"""
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="search",
            categories=self.MOVIE_CATEGORY_IDS + self.OTHER_CATEGORY_IDS,
            catalog_type="movie",
            search_query=search_query,
            indexers=indexers,
            requires_imdb=False,
        ):
            yield stream

    async def scrape_series_by_imdb(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        season: int,
        episode: int,
        indexers: list[dict],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Scrape series using IMDB ID"""
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="tvsearch",
            categories=self.SERIES_CATEGORY_IDS,
            catalog_type="series",
            season=season,
            episode=episode,
            indexers=indexers,
            requires_imdb=True,
        ):
            yield stream

    async def scrape_series_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        season: int,
        episode: int,
        search_query: str,
        indexers: list[dict],
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Scrape series using title search"""
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="search",
            categories=self.SERIES_CATEGORY_IDS + self.OTHER_CATEGORY_IDS,
            catalog_type="series",
            season=season,
            episode=episode,
            search_query=search_query,
            indexers=indexers,
            requires_imdb=False,
        ):
            yield stream

    def filter_indexers_by_capability(
        self,
        indexers: list[dict],
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        requires_imdb: bool = False,
    ) -> list[dict]:
        """Filter indexers based on their capabilities"""
        filtered_indexers = []

        # Map our search types to indexer search types
        search_type_map = {
            "search": "search",
            "tvsearch": "tv-search",
            "movie": "movie-search",
        }
        indexer_search_type = search_type_map[search_type]

        for indexer in indexers:
            # Check if indexer supports the required search type
            search_caps = indexer["search_capabilities"]
            if indexer_search_type not in search_caps:
                continue

            # If we need IMDB support, check if it's available
            if requires_imdb and "imdbid" not in search_caps[indexer_search_type]:
                continue

            # Check if indexer supports any of the required categories
            if not any(cat in indexer["categories"] for cat in categories):
                continue

            filtered_indexers.append(indexer)

        return filtered_indexers

    async def run_scrape_and_parse(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        catalog_type: str,
        indexers: list[dict],
        season: int = None,
        episode: int = None,
        search_query: str = None,
        requires_imdb: bool = False,
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Common method to run scraping and parsing process"""
        # Filter indexers based on capabilities
        filtered_indexers = self.filter_indexers_by_capability(indexers, search_type, categories, requires_imdb)

        if not filtered_indexers:
            self.logger.warning(
                f"No indexers support {search_type} search with required capabilities, Requires IMDB: {requires_imdb}"
            )
            return

        self.logger.info(f"Found {len(filtered_indexers)} indexers supporting {search_type}.")

        # Use IMDb ID for indexer search (Prowlarr/Jackett expect IMDb format)
        video_id = metadata.get_imdb_id() or metadata.get_canonical_id()
        params = await self.build_search_params(
            video_id,
            search_type,
            categories,
            search_query,
        )

        # Use only the IDs from filtered indexers
        indexer_ids = [indexer["id"] for indexer in filtered_indexers]
        search_results = await self.fetch_search_results(
            params, indexer_ids=indexer_ids, timeout=self.search_query_timeout
        )

        self.metrics.record_found_items(len(search_results))
        self.logger.info(
            f"Found {len(search_results)} streams for {metadata.title} ({metadata.year}) "
            f"with {search_type} Search, params: {params}"
        )

        async for stream in self.parse_streams(
            processed_info_hashes,
            metadata,
            search_results,
            catalog_type,
            season,
            episode,
        ):
            yield stream

    async def parse_streams(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        search_results: list[dict[str, Any]],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Parse stream results with circuit breaker"""
        circuit_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=10, half_open_attempts=3)

        async for result in batch_process_with_circuit_breaker(
            self.process_stream,
            search_results,
            5,  # batch_size
            3,  # max_concurrent_batches
            circuit_breaker,
            5,  # max_retries
            metadata=metadata,
            catalog_type=catalog_type,
            season=season,
            episode=episode,
            processed_info_hashes=processed_info_hashes,
        ):
            if result is not None:
                yield result

    async def get_download_url(self, indexer_data):
        """Get download URL from Jackett indexer data"""
        guid = self.get_guid(indexer_data) or ""
        magnet_url = self.get_magent_link(indexer_data) or ""
        download_url = self.get_download_link(indexer_data) or ""
        torrent_type = self.get_torrent_type(indexer_data)

        if torrent_type in [TorrentType.PRIVATE, TorrentType.SEMI_PRIVATE]:
            return download_url

        if guid and guid.startswith("magnet:"):
            return guid

        if not magnet_url.startswith("magnet:") and not download_url.startswith("magnet:"):
            torrent_info_data = await torrent_info.get_torrent_info(
                self.get_info_url(indexer_data), self.get_indexer(indexer_data)
            )
            return (
                torrent_info_data.get("magnetUrl") or torrent_info_data.get("downloadUrl") or magnet_url or download_url
            )

        return magnet_url or download_url

    def validate_category_with_title(
        self,
        indexer_data: dict,
        category_ids: list = None,
        is_filter_with_blocklist: bool = True,
    ) -> bool:
        """Validate category against title"""
        category_ids = (
            [category["id"] for category in indexer_data.get("categories", [])] if not category_ids else category_ids
        )

        if any([category_id in category_ids for category_id in IndexerBaseScraper.OTHER_CATEGORY_IDS]):
            title = self.get_title(indexer_data).lower()

            if is_filter_with_blocklist:
                return not any(keyword in title for keyword in self.blocklist_keywords)
            else:
                return any(keyword in title for keyword in self.allowlist_keywords)

        return True

    @staticmethod
    def split_indexers_into_chunks(indexers: list[dict], chunk_size: int) -> list[list[dict]]:
        """Split indexers into chunks of specified size"""
        return [indexers[i : i + chunk_size] for i in range(0, len(indexers), chunk_size)]
