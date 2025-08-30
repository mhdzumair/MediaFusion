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
from db.enums import TorrentType
from db.models import (
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
    EpisodeFile,
    TorrentStreams,
    MediaFusionMetaData,
)
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
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
    # Blocklist & Allowlist of keywords to identify non-video files
    # fmt: off
    blocklist_keywords = [
        ".exe", ".zip", ".rar", ".iso", ".bin", ".tar", ".7z", ".pdf", ".xyz",
        ".epub", ".mobi", ".azw3", ".doc", ".docx", ".txt", ".rtf",
        "setup", "install", "crack", "patch", "trainer", "readme",
        "manual", "keygen", "license", "tutorial", "ebook", "software", "epub", "book",
    ]

    allowlist_keywords = [
        "mkv", "mp4", "avi", ".webm", ".mov", ".flv", "webdl", "web-dl", "webrip", "bluray",
        "brrip", "bdrip", "dvdrip", "hdtv", "hdcam", "hdrip", "1080p", "720p", "480p", "360p",
        "2160p", "4k", "x264", "x265", "hevc", "h264", "h265", "aac", "xvid", "movie", "series", "season",
    ]

    # fmt: on

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
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreams] | list | None:
        """
        Scrape data and parse it into TorrentStreams objects.
        """
        self.metrics.start()
        self.metrics.meta_data = metadata
        self.metrics.season = season
        self.metrics.episode = episode
        try:
            result = await self._scrape_and_parse(
                user_data, metadata, catalog_type, season, episode
            )
            if isinstance(result, list):
                return result
            self.logger.error(
                f"Invalid result received from {self.cache_key_prefix}: {result}"
            )
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"An error occurred while scraping: {e}")
        finally:
            self.metrics.stop()
            self.metrics.log_summary(self.logger)
        return []

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
        user_data,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        """
        Parse the response into TorrentStreams objects.
        :param response: Response dictionary
        :param user_data: UserData object
        :param metadata: MediaFusionMetaData object
        :param catalog_type: Catalog type (movie, series)
        :param season: Season number (for series)
        :param episode: Episode number (for series)
        :return: List of TorrentStreams objects
        """
        pass

    def get_cache_key(
        self,
        user_data,
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

    @staticmethod
    def parse_title_data(title: str) -> dict:
        """Parse torrent title using PTT"""
        parsed = PTT.parse_title(title, True)
        return {"torrent_name": title, **parsed}

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

    @property
    def search_query_timeout(self) -> int:
        """Timeout for search queries"""
        return 10

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

        try:
            response = await self.http_client.get(
                download_url,
                follow_redirects=False,
                timeout=self.search_query_timeout,
                headers=headers,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in [429, 500]:
                raise error
            self.logger.error(
                f"HTTP Error getting torrent data: {download_url}, status code: {error.response.status_code}"
            )
            return None, False
        except (httpx.TimeoutException, httpx.ConnectTimeout) as error:
            self.logger.warning(
                f"Timeout while getting torrent data for: {download_url}"
            )
            raise error
        except httpx.RequestError as error:
            self.logger.error(f"Request error getting torrent data: {error}")
            raise error
        except Exception as e:
            self.logger.exception(f"Error getting torrent data: {e}")
            return None, False

        if response.status_code in [301, 302, 303, 307, 308]:
            redirect_url = response.headers.get("Location")
            return await self.get_torrent_data(
                redirect_url, parsed_data, headers, episode_name_parser
            )

        response.raise_for_status()

        return (
            extract_torrent_metadata(
                response.content, parsed_data, episode_name_parser=episode_name_parser
            ),
            True,
        )

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        processed_info_hashes: set[str],
        season: int = None,
        episode: int = None,
    ) -> Optional[TorrentStreams]:
        """Common process stream implementation for all indexers"""
        try:
            torrent_title = self.get_title(stream_data)
            if not torrent_title:
                return None

            if is_contain_18_plus_keywords(torrent_title):
                self.logger.warning(
                    f"Adult content found in torrent title: {torrent_title}"
                )
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
            parsed_data = await self.parse_indexer_data(
                stream_data, catalog_type, parsed_data
            )
            if not parsed_data or parsed_data["info_hash"] in processed_info_hashes:
                self.metrics.record_skip("Duplicated info_hash")
                return None

            torrent_type = self.get_torrent_type(stream_data)

            torrent_stream = TorrentStreams(
                id=parsed_data["info_hash"],
                meta_id=metadata.id,
                torrent_name=parsed_data["torrent_name"],
                size=parsed_data["total_size"],
                filename=(
                    parsed_data.get("largest_file", {}).get("filename")
                    if catalog_type == "movie"
                    else None
                ),
                file_index=(
                    parsed_data.get("largest_file", {}).get("index")
                    if catalog_type == "movie"
                    else None
                ),
                languages=parsed_data.get("languages"),
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                audio=parsed_data.get("audio"),
                hdr=parsed_data.get("hdr"),
                source=parsed_data["source"],
                uploader=parsed_data.get("uploader"),
                catalog=parsed_data["catalog"],
                seeders=parsed_data["seeders"],
                created_at=parsed_data["created_at"],
                announce_list=parsed_data["announce_list"],
                torrent_type=torrent_type,
                torrent_file=(
                    parsed_data.get("torrent_file")
                    if torrent_type in [TorrentType.PRIVATE, TorrentType.SEMI_PRIVATE]
                    else None
                ),
            )

            if catalog_type == "series":
                seasons = parsed_data.get("seasons")
                episode_files = []
                if parsed_data.get("file_data"):
                    episode_files = [
                        EpisodeFile(
                            season_number=file["season_number"],
                            episode_number=file["episode_number"],
                            filename=file.get("filename"),
                            size=file.get("size"),
                            file_index=file.get("index"),
                        )
                        for file in parsed_data["file_data"]
                        if file.get("episode_number") is not None
                        and file.get("season_number") is not None
                    ]
                elif episodes := parsed_data.get("episodes") and seasons:
                    episode_files = [
                        EpisodeFile(season_number=seasons[0], episode_number=ep)
                        for ep in episodes
                    ]
                elif parsed_data.get("date"):
                    # search with date for episode
                    episode_date = datetime.strptime(parsed_data["date"], "%Y-%m-%d")
                    imdb_episode = await get_episode_by_date(
                        metadata.id,
                        parsed_data["title"],
                        episode_date.date(),
                    )
                    if imdb_episode and imdb_episode.season and imdb_episode.episode:
                        self.logger.info(
                            f"Episode found by {episode_date} date for {parsed_data.get('title')} ({metadata.id})"
                        )
                        episode_files = [
                            EpisodeFile(
                                season_number=int(imdb_episode.season),
                                episode_number=int(imdb_episode.episode),
                                title=imdb_episode.title,
                                released=episode_date,
                            )
                        ]
                else:
                    # if no episode data found, then try to get torrent metadata from trackers
                    torrent_data = await info_hashes_to_torrent_metadata(
                        [parsed_data["info_hash"]], parsed_data["announce_list"]
                    )
                    if torrent_data:
                        torrent_file_metadata = torrent_data[0]
                        episode_files = [
                            EpisodeFile(
                                season_number=file["season_number"],
                                episode_number=file["episode_number"],
                                filename=file.get("filename"),
                                size=file.get("size"),
                                file_index=file.get("index"),
                            )
                            for file in torrent_file_metadata.get("file_data", [])
                            if file.get("episode_number") is not None
                            and file.get("season_number") is not None
                        ]
                    elif seasons:
                        # Some pack contains few episodes. We can't determine exact episode number
                        episode_files = [
                            EpisodeFile(season_number=season_number, episode_number=1)
                            for season_number in seasons
                        ]

                if episode_files:
                    torrent_stream.episode_files = episode_files
                else:
                    self.metrics.record_skip("Missing episode info")
                    self.logger.warning(
                        f"Episode not found in stream: '{torrent_title}' "
                        f"Scraping for: S{season}E{episode}"
                    )
                    return None
            else:
                # For the Movies, should not have seasons and episodes
                if parsed_data.get("seasons") or parsed_data.get("episodes"):
                    self.metrics.record_skip("Unexpected season/episode info")
                    return None

            self.metrics.record_processed_item()
            self.metrics.record_quality(torrent_stream.quality)
            self.metrics.record_source(torrent_stream.source)

            processed_info_hashes.add(parsed_data["info_hash"])
            self.logger.info(
                f"Successfully parsed stream: {parsed_data.get('title')} "
                f"({parsed_data.get('year')}) ({metadata.id}) "
                f"info_hash: {parsed_data.get('info_hash')}"
            )
            return torrent_stream

        except httpx.ReadTimeout:
            self.metrics.record_error("timeout")
            self.logger.warning("Timeout while processing search result")
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

    async def add_series_to_queue(
        self, meta_id: str, season: int, episode: int
    ) -> None:
        """Add a series episode to the background search queue"""
        key = f"{meta_id}:{season}:{episode}"
        await REDIS_ASYNC_CLIENT.hset(
            self.series_hash_key,
            key,
            json.dumps({"last_scrape": None, "added_at": datetime.now().timestamp()}),
        )

    async def get_pending_items(self, item_type: str) -> List[Dict]:
        """Get items that need to be scraped"""
        hash_key = self.movie_hash_key if item_type == "movie" else self.series_hash_key
        cutoff_time = datetime.now() - timedelta(
            hours=settings.background_search_interval_hours
        )

        # Get all items
        all_items = await REDIS_ASYNC_CLIENT.hgetall(hash_key)
        pending_items = []

        for item_key, item_data in all_items.items():
            data = json.loads(item_data)
            last_scrape = data.get("last_scrape")

            # Check if item needs scraping
            if not last_scrape or datetime.fromtimestamp(last_scrape) < cutoff_time:

                # Check if not currently processing
                if not await REDIS_ASYNC_CLIENT.sismember(
                    self.processing_set_key, item_key
                ):
                    pending_items.append(
                        {"key": item_key.decode("utf-8"), "data": data}
                    )

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
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
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
        self.logger.info(
            f"Processing {len(healthy_indexers)} indexers in {len(indexer_chunks)} chunks"
        )

        try:
            if catalog_type == "movie":
                async for stream in self.scrape_movie(
                    processed_info_hashes, metadata, indexer_chunks
                ):
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

        self.logger.info(
            f"Returning {len(results)} scraped streams for {metadata.title}"
        )
        return results

    async def scrape_movie(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        indexer_chunks: List[List[dict]],
    ) -> AsyncGenerator[TorrentStreams, None]:
        """Common movie scraping logic"""
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(
                self.scrape_movie_by_imdb(processed_info_hashes, metadata, chunk)
            )

        # Add title-based searches if enabled
        if self.live_title_search_enabled:
            for chunk in indexer_chunks:
                for query_template in self.MOVIE_SEARCH_QUERY_TEMPLATES:
                    search_query = query_template.format(
                        title=metadata.title, year=metadata.year
                    )
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
            await self.background_scraper_manager.add_movie_to_queue(metadata.id)

    async def scrape_series(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        indexer_chunks: List[List[dict]],
    ) -> AsyncGenerator[TorrentStreams, None]:
        """Common series scraping logic"""
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(
                self.scrape_series_by_imdb(
                    processed_info_hashes, metadata, season, episode, chunk
                )
            )

        # Add title-based searches if enabled
        if self.live_title_search_enabled:
            for chunk in indexer_chunks:
                for query_template in self.SERIES_SEARCH_QUERY_TEMPLATES:
                    search_query = query_template.format(
                        title=metadata.title, season=season, episode=episode
                    )
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
            await self.background_scraper_manager.add_series_to_queue(
                metadata.id, season, episode
            )

    @abc.abstractmethod
    async def get_healthy_indexers(self) -> List[dict]:
        """Get list of healthy indexer IDs"""
        pass

    @abc.abstractmethod
    async def fetch_search_results(
        self, params: dict, indexer_ids: List[int], timeout: Optional[int] = None
    ) -> List[Dict[str, Any]]:
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
    async def parse_indexer_data(
        self, indexer_data: dict, catalog_type: str, parsed_data: dict
    ) -> dict:
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
    def get_category_ids(self, item: dict) -> List[int]:
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
        metadata: MediaFusionMetaData,
        indexers: List[dict],
    ) -> AsyncGenerator[TorrentStreams, None]:
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
        metadata: MediaFusionMetaData,
        search_query: str,
        indexers: List[dict],
    ) -> AsyncGenerator[TorrentStreams, None]:
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
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        indexers: List[dict],
    ) -> AsyncGenerator[TorrentStreams, None]:
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
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        search_query: str,
        indexers: List[dict],
    ) -> AsyncGenerator[TorrentStreams, None]:
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
        indexers: List[dict],
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        requires_imdb: bool = False,
    ) -> List[dict]:
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
        metadata: MediaFusionMetaData,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        catalog_type: str,
        indexers: List[dict],
        season: int = None,
        episode: int = None,
        search_query: str = None,
        requires_imdb: bool = False,
    ) -> AsyncGenerator[TorrentStreams, None]:
        """Common method to run scraping and parsing process"""
        # Filter indexers based on capabilities
        filtered_indexers = self.filter_indexers_by_capability(
            indexers, search_type, categories, requires_imdb
        )

        if not filtered_indexers:
            self.logger.warning(
                f"No indexers support {search_type} search with required capabilities, Requires IMDB: {requires_imdb}"
            )
            return

        self.logger.info(
            f"Found {len(filtered_indexers)} indexers supporting {search_type}."
        )

        params = await self.build_search_params(
            metadata.id,
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
        metadata: MediaFusionMetaData,
        search_results: List[Dict[str, Any]],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreams, None]:
        """Parse stream results with circuit breaker"""
        circuit_breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout=10, half_open_attempts=3
        )

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

        if not magnet_url.startswith("magnet:") and not download_url.startswith(
            "magnet:"
        ):
            torrent_info_data = await torrent_info.get_torrent_info(
                self.get_info_url(indexer_data), self.get_indexer(indexer_data)
            )
            return (
                torrent_info_data.get("magnetUrl")
                or torrent_info_data.get("downloadUrl")
                or magnet_url
                or download_url
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
            [category["id"] for category in indexer_data.get("categories", [])]
            if not category_ids
            else category_ids
        )

        if any(
            [
                category_id in category_ids
                for category_id in IndexerBaseScraper.OTHER_CATEGORY_IDS
            ]
        ):
            title = self.get_title(indexer_data).lower()

            if is_filter_with_blocklist:
                return not any(keyword in title for keyword in self.blocklist_keywords)
            else:
                return any(keyword in title for keyword in self.allowlist_keywords)

        return True

    @staticmethod
    def split_indexers_into_chunks(
        indexers: List[dict], chunk_size: int
    ) -> List[List[dict]]:
        """Split indexers into chunks of specified size"""
        return [
            indexers[i : i + chunk_size] for i in range(0, len(indexers), chunk_size)
        ]
