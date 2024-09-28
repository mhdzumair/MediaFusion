import asyncio
from datetime import timedelta, datetime
from typing import List, Dict, Any, AsyncGenerator, Literal

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

    def __init__(self):
        super().__init__(cache_key_prefix="prowlarr")
        self.base_url = f"{settings.prowlarr_url}/api/v1/search"

    @BaseScraper.cache(
        ttl=int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds())
    )
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        results = []
        processed_info_hashes: set[str] = set()

        try:
            async for stream in self._scrape_and_parse(
                processed_info_hashes,
                metadata,
                catalog_type,
                season,
                episode,
            ):
                results.append(stream)
        except Exception as e:
            self.logger.exception(f"An error occurred during scraping: {str(e)}")

        self.logger.info(
            f"Returning {len(results)} scraped streams for {metadata.title}"
        )
        return results

    async def _scrape_and_parse(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[TorrentStreams, None]:
        if catalog_type == "movie":
            async for stream in self.scrape_movie(processed_info_hashes, metadata):
                yield stream
        elif catalog_type == "series":
            async for stream in self.scrape_series(
                processed_info_hashes,
                metadata,
                season,
                episode,
            ):
                yield stream
        else:
            raise ValueError(f"Unsupported catalog type: {catalog_type}")

    async def scrape_movie(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
    ) -> AsyncGenerator[TorrentStreams, None]:
        imdb_streams_generator = self.scrape_movie_by_imdb(
            processed_info_hashes, metadata
        )
        title_streams_generators = []

        if settings.prowlarr_live_title_search:
            title_streams_generators.extend(
                self.scrape_movie_by_title(
                    processed_info_hashes,
                    metadata,
                    search_query=query.format(title=metadata.title, year=metadata.year),
                )
                for query in self.MOVIE_SEARCH_QUERY_TEMPLATES
            )

        async for stream in self.process_streams(
            imdb_streams_generator,
            *title_streams_generators,
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
    ) -> AsyncGenerator[TorrentStreams, None]:
        imdb_streams_generator = self.scrape_series_by_imdb(
            processed_info_hashes, metadata, season, episode
        )
        title_streams_generators = []

        if settings.prowlarr_live_title_search:
            title_streams_generators.extend(
                self.scrape_series_by_title(
                    processed_info_hashes,
                    metadata,
                    season,
                    episode,
                    search_query=query.format(
                        title=metadata.title,
                        season=season,
                        episode=episode,
                    ),
                )
                for query in self.SERIES_SEARCH_QUERY_TEMPLATES
            )

        async for stream in self.process_streams(
            imdb_streams_generator,
            *title_streams_generators,
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

        async def producer(gen: AsyncGenerator, generator_id: int):
            try:
                async for stream_item in gen:
                    await queue.put((stream_item, generator_id))
                    self.logger.debug(f"Generator {generator_id} produced a stream")
            except Exception as err:
                self.logger.exception(f"Error in generator {generator_id}: {err}")
            finally:
                await queue.put(("DONE", generator_id))
                self.logger.debug(f"Generator {generator_id} finished")

        async def queue_processor():
            nonlocal active_generators, streams_processed
            while active_generators > 0:
                item, gen_id = await queue.get()

                if item == "DONE":
                    active_generators -= 1
                    self.logger.debug(
                        f"Generator {gen_id} completed. {active_generators} generators remaining"
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
                    break

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
        except Exception as e:
            self.logger.error(f"An error occurred during stream processing: {e}")
        self.logger.info(
            f"Finished processing {streams_processed} streams from "
            f"{len(stream_generators)} generators"
        )

    async def fetch_stream_data(self, params: dict) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(
            headers={"X-Api-Key": settings.prowlarr_api_key}
        ) as client:
            response = await client.get(
                self.base_url,
                params=params,
                timeout=settings.prowlarr_search_query_timeout,
            )
            response.raise_for_status()
            return response.json()

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
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="movie",
            categories=[2000],
            catalog_type="movie",
        ):
            yield stream

    async def scrape_movie_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        search_query: str,
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="search",
            categories=[2000, 8000],
            catalog_type="movie",
            search_query=search_query,
        ):
            yield stream

    async def scrape_series_by_imdb(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
    ) -> AsyncGenerator[TorrentStreams, None]:
        async for stream in self.run_scrape_and_parse(
            processed_info_hashes=processed_info_hashes,
            metadata=metadata,
            search_type="tvsearch",
            categories=[5000],
            catalog_type="series",
            season=season,
            episode=episode,
        ):
            yield stream

    async def scrape_series_by_title(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        season: int,
        episode: int,
        search_query: str,
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
        ):
            yield stream

    async def run_scrape_and_parse(
        self,
        processed_info_hashes: set[str],
        metadata: MediaFusionMetaData,
        search_type: Literal["search", "tvsearch", "movie"],
        categories: list[int],
        catalog_type: str,
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
        search_results = await self.fetch_stream_data(params)
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
            self.logger.warning(
                f"Stream contains 18+ keywords: {stream_data.get('title')}"
            )
            return None

        if not self.validate_category_with_title(stream_data):
            self.logger.warning(
                f"Unable to validate Other category item title: {stream_data.get('title')}"
            )
            return None

        parsed_data = self.parse_title_data(stream_data.get("title"))

        if not self.validate_title_and_year(
            parsed_data["title"],
            parsed_data.get("year"),
            metadata,
            catalog_type,
            stream_data.get("title"),
        ):
            return None

        if catalog_type == "series" and len(parsed_data.get("seasons", [])) > 1:
            self.logger.warning(
                f"Series has multiple seasons: {parsed_data.get('title')} ({parsed_data.get('year')}) ({metadata.id}) : {parsed_data.get('seasons')}"
            )
            return None

        parsed_data = await self.parse_prowlarr_data(
            stream_data, catalog_type, parsed_data
        )
        if not parsed_data or parsed_data["info_hash"] in processed_info_hashes:
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
                episode_data = [Episode(episode_number=episode)]
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
                self.logger.warning(
                    f"Episode not found in stream: '{stream_data.get('title')}' Scraping for: S{season}E{episode}"
                )
                return None

        processed_info_hashes.add(parsed_data["info_hash"])
        self.logger.info(
            f"Successfully parsed stream: {parsed_data.get('title')} ({parsed_data.get('year')}) ({metadata.id}) info_hash: {parsed_data.get('info_hash')}"
        )
        return torrent_stream

    async def parse_prowlarr_data(
        self, prowlarr_data: dict, catalog_type: str, parsed_data: dict
    ) -> dict | None:
        download_url = prowlarr_data.get("downloadUrl") or prowlarr_data.get(
            "magnetUrl"
        )

        if not download_url:
            download_url = await self.get_download_url(prowlarr_data)

        try:
            torrent_data, is_torrent_downloaded = await self.get_torrent_data(
                download_url, prowlarr_data.get("indexer")
            )
        except httpx.TimeoutException:
            self.logger.warning("Timeout while getting torrent data")
            return None
        except Exception as e:
            self.logger.error(f"Error getting torrent data: {e}")
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
        if prowlarr_data.get("indexer") in [
            "Torlock",
            "YourBittorrent",
            "The Pirate Bay",
            "RuTracker.RU",
            "BitSearch",
            "BitRu",
            "iDope",
            "RuTor",
            "Internet Archive",
            "52BT",
        ]:
            return prowlarr_data.get("guid")
        else:
            if not prowlarr_data.get("magnetUrl") and not prowlarr_data.get(
                "downloadUrl", ""
            ).startswith("magnet:"):
                torrent_info_data = await torrent_info.get_torrent_info(
                    prowlarr_data.get("infoUrl"), prowlarr_data.get("indexer")
                )
                return torrent_info_data.get("magnetUrl") or torrent_info_data.get(
                    "downloadUrl"
                )
        return prowlarr_data.get("magnetUrl") or prowlarr_data.get("downloadUrl")

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
            return extract_torrent_metadata(response.content, is_parse_ptt=False), True

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
    time_limit=10 * 60 * 1000,  # 10 minutes
    min_backoff=2 * 60 * 1000,  # 2 minutes
    max_backoff=10 * 60 * 1000,  # 10 minutes
    priority=100,
)
async def background_movie_title_search(
    metadata_id: str,
):
    scraper = ProwlarrScraper()
    processed_info_hashes: set[str] = set()
    metadata = await MediaFusionMovieMetaData.get(metadata_id)
    if not metadata:
        return

    title_streams_generators = [
        scraper.scrape_movie_by_title(
            processed_info_hashes,
            metadata,
            search_query=query.format(title=metadata.title, year=metadata.year),
        )
        for query in scraper.MOVIE_SEARCH_QUERY_TEMPLATES
    ]

    async for stream in scraper.process_streams(*title_streams_generators):
        await scraper.store_streams([stream])

    scraper.logger.info(
        f"Background title search completed for {metadata.title} ({metadata.year})"
    )


@minimum_run_interval(hours=settings.prowlarr_search_interval_hour)
@dramatiq.actor(
    time_limit=10 * 60 * 1000,  # 10 minutes
    min_backoff=2 * 60 * 1000,  # 2 minutes
    max_backoff=10 * 60 * 1000,  # 10 minutes
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
        return

    title_streams_generators = [
        scraper.scrape_series_by_title(
            processed_info_hashes,
            metadata,
            season,
            episode,
            search_query=query.format(
                title=metadata.title, season=season, episode=episode
            ),
        )
        for query in scraper.SERIES_SEARCH_QUERY_TEMPLATES
    ]

    async for stream in scraper.process_streams(*title_streams_generators):
        await scraper.store_streams([stream])

    scraper.logger.info(
        f"Background title search completed for {metadata.title} S{season}E{episode}"
    )
