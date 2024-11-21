import asyncio
from datetime import timedelta, datetime, timezone
from typing import List, Dict, Any, AsyncGenerator, Literal, AsyncIterable

import PTT
import dramatiq
import httpx
from torf import Magnet, MagnetError

from db.config import settings
from db.models import (
    TorrentStreams,
    Season,
    Episode,
    MediaFusionMetaData,
    MediaFusionMovieMetaData,
    MediaFusionSeriesMetaData,
)
from scrapers import torrent_info
from scrapers.base_scraper import BaseScraper
from scrapers.imdb_data import get_episode_by_date, get_season_episodes
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.parser import is_contain_18_plus_keywords
from utils.runtime_const import REDIS_ASYNC_CLIENT, PROWLARR_SEARCH_TTL
from utils.torrent import extract_torrent_metadata
from utils.wrappers import minimum_run_interval

MOVIE_CATEGORY_IDS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]
SERIES_CATEGORY_IDS = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080, 5090]
OTHER_CATEGORY_IDS = [8000, 8010, 8020]

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


class MaxProcessLimitReached(Exception):
    pass


class ProwlarrScraper(BaseScraper):
    MOVIE_SEARCH_QUERY_TEMPLATES = [
        "{title} ({year})",  # Exact match with year
        "{title} {year}",  # Title with year (without parentheses)
        "{title}",  # Title-only fallback
    ]
    SERIES_SEARCH_QUERY_TEMPLATES = [
        "{title} S{season:02d}E{episode:02d}",  # Standard SXXEYY format, with leading zeros
        "{title} Season {season} Episode {episode}",  # Verbose format
        "{title} {season}x{episode}",  # Alternate XXxYY format used by some indexes
        "{title} S{season:02d}",  # Short season search with leading zeros
        "{title}",  # Title-only fallback
    ]
    cache_key_prefix = "prowlarr"
    headers = {"X-Api-Key": settings.prowlarr_api_key}
    base_url = settings.prowlarr_url
    search_url = f"{base_url}/api/v1/search"

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)
        self.indexer_status = {}
        self.indexer_circuit_breakers = {}

    async def get_healthy_indexers(self) -> List[int]:
        """Fetch and return list of healthy indexer IDs with detailed health checks"""
        try:
            # Fetch both indexer configurations and their current status
            indexers_future = asyncio.create_task(self.fetch_indexers())
            statuses_future = asyncio.create_task(self.fetch_indexer_statuses())

            indexers, status_data = await asyncio.gather(
                indexers_future, statuses_future
            )

            current_time = datetime.now(timezone.utc)
            healthy_indexers = []

            for indexer in indexers:
                indexer_id = indexer.get("id")
                if not indexer_id:
                    continue

                # Get status information for this indexer
                status_info = status_data.get(indexer_id, {})
                disabled_till = status_info.get("disabled_till")

                # Convert disabled_till to datetime if it exists
                if disabled_till:
                    try:
                        disabled_till = datetime.fromisoformat(
                            disabled_till.replace("Z", "+00:00")
                        )
                    except ValueError:
                        disabled_till = None

                # Check if indexer is healthy
                is_healthy = all(
                    [
                        indexer.get("enable", False),  # Indexer is enabled
                        not disabled_till
                        or disabled_till < current_time,  # Not temporarily disabled
                    ]
                )

                self.indexer_status[indexer_id] = {
                    "is_healthy": is_healthy,
                    "name": indexer.get("name", "Unknown"),
                    "disabled_till": disabled_till,
                    "most_recent_failure": status_info.get("most_recent_failure"),
                    "initial_failure": status_info.get("initial_failure"),
                }

                if is_healthy:
                    healthy_indexers.append(indexer_id)
                    # Initialize or reset circuit breaker for healthy indexer
                    self.indexer_circuit_breakers[indexer_id] = CircuitBreaker(
                        failure_threshold=3,
                        recovery_timeout=300,  # 5 minutes
                        half_open_attempts=1,
                    )

            self.logger.info(
                f"Found {len(healthy_indexers)} healthy indexers out of {len(indexers)} total"
            )
            return healthy_indexers

        except Exception as e:
            self.logger.error(f"Failed to determine healthy indexers: {e}")
            return []

    def get_circuit_breaker(self, indexer_id: int) -> CircuitBreaker:
        """Get or create a circuit breaker for an indexer"""
        if indexer_id not in self.indexer_circuit_breakers:
            self.indexer_circuit_breakers[indexer_id] = CircuitBreaker(
                failure_threshold=3,
                recovery_timeout=300,  # 5 minutes
                half_open_attempts=1,
            )
        return self.indexer_circuit_breakers[indexer_id]

    async def log_indexer_status(self):
        """Log the current status of all indexers and their circuit breakers"""
        status_lines = ["Current Indexer Status:"]

        for indexer_id, status in self.indexer_status.items():
            circuit_breaker = self.indexer_circuit_breakers.get(indexer_id)
            if circuit_breaker:
                cb_status = circuit_breaker.get_status()
                status_lines.append(
                    f"Indexer {status.get('name', f'ID:{indexer_id}')}:\n"
                    f"  Health: {'Healthy' if status.get('is_healthy') else 'Unhealthy'}\n"
                    f"  Circuit Breaker: {cb_status['state']}\n"
                    f"  Failures: {cb_status['failures']}\n"
                    f"  Accepting Requests: {cb_status['is_accepting_requests']}"
                )

        self.logger.info("\n".join(status_lines))

    @BaseScraper.cache(ttl=PROWLARR_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
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
            async for stream in self.__scrape_and_parse(
                processed_info_hashes,
                indexer_chunks,
                metadata,
                catalog_type,
                season,
                episode,
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

    async def __scrape_and_parse(
        self,
        processed_info_hashes: set[str],
        indexer_chunks: List[List[int]],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreams, None]:
        if catalog_type == "movie":
            async for stream in self.scrape_movie(
                processed_info_hashes, metadata, indexer_chunks
            ):
                yield stream
        elif catalog_type == "series":
            async for stream in self.scrape_series(
                processed_info_hashes, metadata, season, episode, indexer_chunks
            ):
                yield stream
        else:
            raise ValueError(f"Unsupported catalog type: {catalog_type}")

    async def scrape_movie(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        indexer_chunks: List[List[int]],
    ) -> AsyncGenerator[TorrentStreams, None]:
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(
                self.scrape_movie_by_imdb(
                    processed_info_hashes, metadata, indexer_ids=chunk
                )
            )

        if settings.prowlarr_live_title_search:
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
                            indexer_ids=chunk,
                        )
                    )

        async for stream in self.process_streams(
            *search_generators,
            max_process=settings.prowlarr_immediate_max_process,
            max_process_time=settings.prowlarr_immediate_max_process_time,
        ):
            yield stream

        if settings.prowlarr_background_title_search:
            background_movie_title_search.send(
                metadata.id,
            )

    async def scrape_series(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        indexer_chunks: List[List[int]],
    ) -> AsyncGenerator[TorrentStreams, None]:
        search_generators = []

        # Add IMDB search for each chunk
        for chunk in indexer_chunks:
            search_generators.append(
                self.scrape_series_by_imdb(
                    processed_info_hashes, metadata, season, episode, indexer_ids=chunk
                )
            )

        # Add title-based searches if enabled
        if settings.prowlarr_live_title_search:
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
                            indexer_ids=chunk,
                        )
                    )

        async for stream in self.process_streams(
            *search_generators,
            max_process=settings.prowlarr_immediate_max_process,
            max_process_time=settings.prowlarr_immediate_max_process_time,
            catalog_type="series",
            season=season,
            episode=episode,
        ):
            yield stream

        if settings.prowlarr_background_title_search:
            background_series_title_search.send(
                metadata_id=metadata.id,
                season=str(season),
                episode=str(episode),
            )

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

    async def fetch_search_results(
        self,
        params: dict,
        indexer_ids: List[int],
        timeout: int = settings.prowlarr_search_query_timeout,
    ) -> List[Dict[str, Any]]:
        """Fetch search results for specific indexers with enhanced circuit breaker handling"""
        results = []

        for indexer_id in indexer_ids:
            indexer_status = self.indexer_status.get(indexer_id, {})
            if not indexer_status.get("is_healthy", False):
                continue

            circuit_breaker = self.get_circuit_breaker(indexer_id)
            indexer_name = indexer_status.get("name", f"ID:{indexer_id}")

            if circuit_breaker.is_closed():
                try:
                    search_params = {**params, "indexerIds": [indexer_id]}
                    async with httpx.AsyncClient(headers=self.headers) as client:
                        response = await client.get(
                            self.search_url,
                            params=search_params,
                            timeout=timeout,
                        )
                        response.raise_for_status()
                        indexer_results = response.json()

                        # Record success
                        circuit_breaker.record_success()
                        self.metrics.record_indexer_success(
                            indexer_name, len(indexer_results)
                        )
                        results.extend(indexer_results)

                except Exception as e:
                    error_msg = f"Error searching indexer {indexer_name}: {str(e)}"
                    self.logger.error(error_msg)

                    # Record failure
                    circuit_breaker.record_failure()
                    self.metrics.record_indexer_error(indexer_name, str(e))

                    # Update status if circuit breaker opens
                    if not circuit_breaker.is_closed():
                        self.logger.warning(
                            f"Circuit breaker opened for indexer {indexer_name}. "
                            f"Status: {circuit_breaker.get_status()}"
                        )
                        indexer_status["is_healthy"] = False
                        self.indexer_status[indexer_id] = indexer_status
            else:
                self.logger.debug(
                    f"Skipping indexer {indexer_name} - circuit breaker is {circuit_breaker.state}"
                )
                self.metrics.record_indexer_error(
                    indexer_name, f"Circuit breaker {circuit_breaker.state}"
                )

        return results

    @staticmethod
    async def build_search_params(
        video_id: str,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        search_query: str = None,
    ) -> dict:
        if search_type in ["movie", "tvsearch"]:
            search_query = f"{{IMDbId:{video_id}}}"

        return {
            "query": search_query,
            "categories": categories,
            "type": search_type,
        }

    async def scrape_movie_by_imdb(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        indexer_ids: List[int],
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="movie",
            categories=[2000],
            catalog_type="movie",
            indexer_ids=indexer_ids,
        ):
            yield stream

    async def scrape_movie_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        search_query: str,
        indexer_ids: List[int],
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="search",
            categories=[2000, 8000],
            catalog_type="movie",
            search_query=search_query,
            indexer_ids=indexer_ids,
        ):
            yield stream

    async def scrape_series_by_imdb(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        indexer_ids: List[int],
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="tvsearch",
            categories=[5000],
            catalog_type="series",
            season=season,
            episode=episode,
            indexer_ids=indexer_ids,
        ):
            yield stream

    async def scrape_series_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        search_query: str,
        indexer_ids: List[int],
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="search",
            categories=[5000, 8000],
            catalog_type="series",
            season=season,
            episode=episode,
            search_query=search_query,
            indexer_ids=indexer_ids,
        ):
            yield stream

    async def run_scrape_and_parse(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        catalog_type: str,
        indexer_ids: List[int],
        season: int = None,
        episode: int = None,
        search_query: str = None,
    ) -> AsyncGenerator[TorrentStreams, None]:
        params = await self.build_search_params(
            metadata.id,
            search_type,
            categories,
            search_query,
        )
        search_results = await self.fetch_search_results(
            params, indexer_ids=indexer_ids
        )
        self.metrics.record_found_items(len(search_results))
        self.logger.info(
            f"Found {len(search_results)} streams for {metadata.title} ({metadata.year}) with {search_type} Search, params: {params}"
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
        circuit_breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout=10, half_open_attempts=3
        )
        async for result in batch_process_with_circuit_breaker(
            self.process_stream,
            search_results,
            5,
            3,
            circuit_breaker,
            5,
            [httpx.HTTPError],
            metadata=metadata,
            catalog_type=catalog_type,
            season=season,
            episode=episode,
            processed_info_hashes=processed_info_hashes,
        ):
            if result is not None:
                yield result

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        processed_info_hashes: set[str],
        season: int = None,
        episode: int = None,
    ) -> TorrentStreams | None:
        if is_contain_18_plus_keywords(stream_data.get("title")):
            self.metrics.record_skip("Adult content")
            self.logger.warning(
                f"Stream contains 18+ keywords: {stream_data.get('title')}"
            )
            return None

        if not self.validate_category_with_title(stream_data):
            self.metrics.record_skip("Invalid category")
            self.logger.warning(
                f"Unable to validate Other category item title: {stream_data.get('title')}"
            )
            return None

        parsed_data = self.parse_title_data(stream_data.get("title"))

        if not self.validate_title_and_year(
            parsed_data,
            metadata,
            catalog_type,
            stream_data.get("title"),
        ):
            return None

        if catalog_type == "series" and len(parsed_data.get("seasons", [])) > 1:
            self.logger.warning(
                f"Series has multiple seasons: {parsed_data.get('title')} ({parsed_data.get('year')}) ({metadata.id}) : {parsed_data.get('seasons')}"
            )
            self.metrics.record_skip("Multiple seasons torrent")
            return None

        parsed_data = await self.parse_prowlarr_data(
            stream_data, catalog_type, parsed_data
        )
        if not parsed_data or parsed_data["info_hash"] in processed_info_hashes:
            self.metrics.record_skip("Duplicated info_hash")
            return None

        torrent_stream = TorrentStreams(
            id=parsed_data["info_hash"],
            meta_id=metadata.id,
            torrent_name=parsed_data["torrent_name"],
            size=parsed_data["total_size"],
            filename=parsed_data.get("largest_file", {}).get("file_name"),
            file_index=parsed_data.get("largest_file", {}).get("index"),
            languages=parsed_data.get("languages"),
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            audio=parsed_data.get("audio"),
            source=parsed_data["source"],
            catalog=parsed_data["catalog"],
            seeders=parsed_data["seeders"],
            created_at=parsed_data["created_at"],
            announce_list=parsed_data["announce_list"],
            indexer_flags=stream_data.get("indexerFlags", []),
        )

        if catalog_type == "series":
            season_number = (
                parsed_data["seasons"][0] if parsed_data.get("seasons") else None
            )
            # Prepare episode data based on detailed file data or basic episode numbers
            episode_data = []
            if parsed_data.get("file_data"):
                episode_data = [
                    Episode(
                        episode_number=file["episodes"][0],
                        filename=file.get("filename"),
                        size=file.get("size"),
                        file_index=file.get("index"),
                    )
                    for file in parsed_data["file_data"]
                    if file.get("episodes")
                ]
            elif episodes := parsed_data.get("episodes"):
                episode_data = [Episode(episode_number=ep) for ep in episodes]
            elif season and season_number == season:
                # Some pack contains few episodes. We can't determine exact episode number
                episode_data = [Episode(episode_number=1)]
            elif parsed_data.get("date"):
                # search with date for episode
                episode_date = datetime.strptime(parsed_data["date"], "%Y-%m-%d").date()
                imdb_episode = await get_episode_by_date(
                    metadata.id,
                    parsed_data["title"],
                    episode_date,
                )
                if imdb_episode and imdb_episode.season and imdb_episode.episode:
                    self.logger.info(
                        f"Episode found by {episode_date} date for {parsed_data.get('title')} ({metadata.id})"
                    )
                    season_number = int(imdb_episode.season)
                    episode_data = [Episode(episode_number=int(imdb_episode.episode))]
            elif season_number:
                # search with season for episodes
                imdb_season_episodes = await get_season_episodes(
                    metadata.id, parsed_data["title"], str(season_number)
                )
                if imdb_season_episodes:
                    episode_data = [
                        Episode(episode_number=int(ep.episode))
                        for ep in imdb_season_episodes
                    ]

            if episode_data and season_number:
                torrent_stream.season = Season(
                    season_number=season_number, episodes=episode_data
                )
            else:
                self.metrics.record_skip("Missing episode info")
                self.logger.warning(
                    f"Episode not found in stream: '{stream_data.get('title')}' Scraping for: S{season}E{episode}"
                )
                return None

        self.metrics.record_processed_item()
        self.metrics.record_quality(torrent_stream.quality)
        self.metrics.record_source(torrent_stream.source)

        processed_info_hashes.add(parsed_data["info_hash"])
        self.logger.info(
            f"Successfully parsed stream: {parsed_data.get('title')} ({parsed_data.get('year')}) ({metadata.id}) info_hash: {parsed_data.get('info_hash')}"
        )
        return torrent_stream

    async def parse_prowlarr_data(
        self, prowlarr_data: dict, catalog_type: str, parsed_data: dict
    ) -> dict | None:
        download_url = await self.get_download_url(prowlarr_data)
        if not download_url:
            return None

        try:
            torrent_data, is_torrent_downloaded = await self.get_torrent_data(
                download_url, prowlarr_data.get("indexer")
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in [429, 500]:
                raise error
            self.logger.error(
                f"HTTP Error getting torrent data: {error.response.text}, status code: {error.response.status_code}"
            )
            return None
        except httpx.TimeoutException as error:
            self.logger.warning("Timeout while getting torrent data")
            raise error
        except Exception as e:
            self.logger.exception(f"Error getting torrent data: {e}")
            return None

        info_hash = torrent_data.get("info_hash", "").lower()
        if not info_hash:
            return None

        torrent_data.update(
            {
                "seeders": prowlarr_data.get("seeders"),
                "created_at": prowlarr_data.get("publishDate"),
                "source": prowlarr_data.get("indexer"),
                "catalog": [
                    "prowlarr_streams",
                    f"{prowlarr_data.get('indexer').lower()}_{catalog_type}",
                    f"prowlarr_{catalog_type.rstrip('s')}s",
                ],
                "total_size": torrent_data.get("total_size")
                or prowlarr_data.get("size"),
                **parsed_data,
            }
        )

        return torrent_data

    @staticmethod
    async def get_download_url(prowlarr_data: dict) -> str:
        guid = prowlarr_data.get("guid") or ""
        magnet_url = prowlarr_data.get("magnetUrl") or ""
        download_url = prowlarr_data.get("downloadUrl") or ""

        if guid and guid.startswith("magnet:"):
            return guid

        if not magnet_url.startswith("magnet:") and not download_url.startswith(
            "magnet:"
        ):
            torrent_info_data = await torrent_info.get_torrent_info(
                prowlarr_data["infoUrl"], prowlarr_data["indexer"]
            )
            return torrent_info_data.get("magnetUrl") or torrent_info_data.get(
                "downloadUrl"
            )

        return magnet_url or download_url

    async def fetch_indexers(self):
        try:
            async with httpx.AsyncClient(headers=self.headers) as client:
                response = await client.get(
                    self.base_url + "/api/v1/indexer", timeout=10
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            self.logger.exception(f"Failed to fetch indexers: {e}")
            return []

    async def fetch_indexer_statuses(self) -> Dict[int, Dict]:
        """Fetch current status information for all indexers"""
        try:
            async with httpx.AsyncClient(headers=self.headers) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/indexerstatus", timeout=10
                )
                response.raise_for_status()

                # Create a mapping of indexerId to status info
                status_data = {}
                for status in response.json():
                    indexer_id = status.get("indexerId")
                    if indexer_id:
                        status_data[indexer_id] = {
                            "disabled_till": status.get("disabledTill"),
                            "most_recent_failure": status.get("mostRecentFailure"),
                            "initial_failure": status.get("initialFailure"),
                        }

                return status_data
        except Exception as e:
            self.logger.error(f"Failed to fetch indexer statuses: {e}")
            return {}

    @staticmethod
    def split_indexers_into_chunks(indexers, chunk_size):
        for i in range(0, len(indexers), chunk_size):
            yield indexers[i : i + chunk_size]

    async def get_torrent_data(
        self, download_url: str, indexer: str
    ) -> tuple[dict, bool]:
        if download_url.startswith("magnet:"):
            try:
                magnet = Magnet.from_string(download_url)
            except MagnetError:
                return {}, False
            return {"info_hash": magnet.infohash, "announce_list": magnet.tr}, False

        async with httpx.AsyncClient() as client:
            response = await client.get(
                download_url,
                follow_redirects=False,
                timeout=settings.prowlarr_search_query_timeout,
            )
            if response.status_code in [301, 302, 303, 307, 308]:
                redirect_url = response.headers.get("Location")
                return await self.get_torrent_data(redirect_url, indexer)
            response.raise_for_status()
            if response.headers.get("Content-Type") == "application/x-bittorrent":
                return (
                    extract_torrent_metadata(response.content, is_parse_ptt=False),
                    True,
                )
            return {}, False

    @staticmethod
    def parse_title_data(title: str) -> dict:
        parsed = PTT.parse_title(title, True)
        return {
            "torrent_name": title,
            **parsed,
        }

    @staticmethod
    def validate_category_with_title(
        prowlarr_data: dict,
        category_ids: list = None,
        is_filter_with_blocklist: bool = True,
    ) -> bool:
        category_ids = (
            [category["id"] for category in prowlarr_data.get("categories", [])]
            if not category_ids
            else category_ids
        )
        if any([category_id in category_ids for category_id in OTHER_CATEGORY_IDS]):

            # Extract the title or file name and convert to a lower case for comparison
            title = prowlarr_data.get(
                "fileName", prowlarr_data.get("title", "")
            ).lower()

            if is_filter_with_blocklist:
                # Check if the title contains any blocklisted keywords
                return not any(keyword in title for keyword in blocklist_keywords)
            else:
                # Check if the title contains any allowlisted keywords
                return any(keyword in title for keyword in allowlist_keywords)

        return True

    def should_retry_prowlarr_scrap(self, retries_so_far, exception) -> bool:
        should_retry = retries_so_far < 10 and isinstance(exception, httpx.HTTPError)
        if not should_retry:
            self.logger.error(f"Failed to fetch data from Prowlarr: {exception}")
        return should_retry


@minimum_run_interval(hours=settings.prowlarr_search_interval_hour)
@dramatiq.actor(
    time_limit=30 * 60 * 1000,  # 30 minutes
    priority=100,
)
async def background_movie_title_search(
    metadata_id: str,
):
    scraper = ProwlarrScraper()
    processed_info_hashes: set[str] = set()
    metadata = await MediaFusionMovieMetaData.get(metadata_id)
    if not metadata:
        scraper.logger.warning(f"Movie metadata not found for ID: {metadata_id}")
        return

    # Get healthy indexers and split into chunks
    healthy_indexers = await scraper.get_healthy_indexers()
    if not healthy_indexers:
        scraper.logger.warning("No healthy indexers available for background search")
        return

    indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))
    scraper.logger.info(
        f"Starting background movie search with {len(healthy_indexers)} indexers "
        f"in {len(indexer_chunks)} chunks"
    )

    scraper.metrics.start()
    scraper.metrics.meta_data = metadata

    # Create generators for each chunk and template combination
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
                    indexer_ids=chunk,
                )
            )

    try:
        async for stream in scraper.process_streams(
            *title_streams_generators,
        ):
            await scraper.store_streams([stream])

    except httpx.ReadTimeout:
        scraper.logger.warning(
            f"Timeout while fetching background results for movie "
            f"{metadata.title} ({metadata.year}), retrying later"
        )
        task_cache_key = f"background_tasks:background_movie_title_search:{metadata_id}"
        await REDIS_ASYNC_CLIENT.delete(task_cache_key)
        background_movie_title_search.send_with_options(
            kwargs={"metadata_id": metadata_id}, delay=timedelta(minutes=5)
        )

    except httpx.HTTPStatusError as e:
        scraper.metrics.record_error("http_error")
        scraper.logger.error(
            f"Error fetching background results: {e.response.text}, "
            f"status code: {e.response.status_code}"
        )
    except Exception as e:
        scraper.metrics.record_error("unexpected_error")
        scraper.logger.exception(
            f"Unexpected error during background movie search: {str(e)}"
        )
    finally:
        # Log final metrics and indexer status
        scraper.metrics.stop()
        scraper.metrics.log_summary(scraper.logger)
        await scraper.log_indexer_status()

        scraper.logger.info(
            f"Background title search completed for {metadata.title} ({metadata.year})"
        )


@minimum_run_interval(hours=settings.prowlarr_search_interval_hour)
@dramatiq.actor(
    time_limit=30 * 60 * 1000,  # 30 minutes
    priority=100,
)
async def background_series_title_search(
    metadata_id: str,
    season: str,
    episode: str,
):
    season = int(season)
    episode = int(episode)
    scraper = ProwlarrScraper()
    processed_info_hashes: set[str] = set()
    metadata = await MediaFusionSeriesMetaData.get(metadata_id)
    if not metadata:
        scraper.logger.warning(f"Series metadata not found for ID: {metadata_id}")
        return

    # Get healthy indexers and split into chunks
    healthy_indexers = await scraper.get_healthy_indexers()
    if not healthy_indexers:
        scraper.logger.warning("No healthy indexers available for background search")
        return

    indexer_chunks = list(scraper.split_indexers_into_chunks(healthy_indexers, 3))
    scraper.logger.info(
        f"Starting background series search with {len(healthy_indexers)} indexers "
        f"in {len(indexer_chunks)} chunks"
    )

    scraper.metrics.start()
    scraper.metrics.meta_data = metadata
    scraper.metrics.season = season
    scraper.metrics.episode = episode

    # Create generators for each chunk and template combination
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
                    indexer_ids=chunk,
                )
            )

    try:
        async for stream in scraper.process_streams(
            *title_streams_generators,
        ):
            await scraper.store_streams([stream])

    except httpx.ReadTimeout:
        scraper.logger.warning(
            f"Timeout while fetching background results for "
            f"{metadata.title} S{season}E{episode}, retrying later"
        )
        task_cache_key = (
            f"background_tasks:background_series_title_search:"
            f"metadata_id={metadata_id}_season={season}_episode={episode}"
        )
        await REDIS_ASYNC_CLIENT.delete(task_cache_key)
        background_series_title_search.send_with_options(
            kwargs={"metadata_id": metadata_id, "season": season, "episode": episode},
            delay=timedelta(minutes=5),
        )

    except httpx.HTTPStatusError as e:
        scraper.metrics.record_error("http_error")
        scraper.logger.error(
            f"Error fetching background results: {e.response.text}, "
            f"status code: {e.response.status_code}"
        )
    except Exception as e:
        scraper.metrics.record_error("unexpected_error")
        scraper.logger.exception(
            f"Unexpected error during background series search: {str(e)}"
        )
    finally:
        # Log final metrics and indexer status
        scraper.metrics.stop()
        scraper.metrics.log_summary(scraper.logger)
        await scraper.log_indexer_status()

        scraper.logger.info(
            f"Background title search completed for {metadata.title} S{season}E{episode}"
        )
