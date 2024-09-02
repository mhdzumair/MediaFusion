import asyncio
from typing import List, Dict, Any
from datetime import timedelta
import logging

import PTT
import httpx
import dramatiq
from torf import Magnet, MagnetError

from db.models import TorrentStreams, Season, Episode
from db.config import settings
from scrapers.base_scraper import BaseScraper
from utils.parser import is_contain_18_plus_keywords, calculate_max_similarity_ratio
from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
from utils.torrent import extract_torrent_metadata
from utils.wrappers import minimum_run_interval
from scrapers import torrent_info


class ProwlarrScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.base_url = f"{settings.prowlarr_url}/api/v1/search"
        self.semaphore = asyncio.Semaphore(10)  # Limit concurrent processing

    @BaseScraper.cache(
        ttl=int(timedelta(hours=settings.prowlarr_search_interval_hour).total_seconds())
    )
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def scrape_and_parse(
        self,
        video_id: str,
        catalog_type: str,
        title: str,
        aka_titles: list[str],
        year: int,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        if catalog_type == "movie":
            return await self.scrape_movie(video_id, title, aka_titles, year)
        elif catalog_type == "series":
            return await self.scrape_series(
                video_id, title, aka_titles, year, season, episode
            )
        else:
            raise ValueError(f"Unsupported catalog type: {catalog_type}")

    async def scrape_movie(
        self, video_id: str, title: str, aka_titles: list[str], year: int
    ) -> List[TorrentStreams]:
        imdb_streams = await self.scrape_movie_by_imdb(video_id, title, year)
        title_streams = []

        if settings.prowlarr_live_title_search:
            max_process = settings.prowlarr_immediate_max_process - len(imdb_streams)
            if max_process > 0:
                title_streams = await self.scrape_movie_by_title(
                    video_id, title, aka_titles, year, max_process
                )

        if settings.prowlarr_background_title_search:
            self.background_movie_title_search.send(
                video_id=video_id, title=title, aka_titles=aka_titles, year=year
            )

        return imdb_streams + title_streams

    async def scrape_series(
        self,
        video_id: str,
        title: str,
        aka_titles: list[str],
        year: int,
        season: int,
        episode: int,
    ) -> List[TorrentStreams]:
        imdb_streams = await self.scrape_series_by_imdb(
            video_id, title, year, season, episode
        )
        title_streams = []

        if settings.prowlarr_live_title_search:
            max_process = settings.prowlarr_immediate_max_process - len(imdb_streams)
            if max_process > 0:
                title_streams = await self.scrape_series_by_title(
                    video_id, title, aka_titles, year, season, episode, max_process
                )

        if settings.prowlarr_background_title_search:
            self.background_series_title_search.send(
                video_id=video_id,
                title=title,
                aka_titles=aka_titles,
                year=year,
                season=season,
                episode=episode,
            )

        return imdb_streams + title_streams

    async def scrape_movie_by_imdb(
        self, video_id: str, title: str, year: int
    ) -> List[TorrentStreams]:
        params = {
            "query": f"{{ImdbId:{video_id}}}",
            "categories": [2000],  # Movies
            "type": "movie",
        }
        search_results = await self.fetch_stream_data(params)
        return await self.parse_movie_streams(video_id, title, year, search_results)

    async def scrape_movie_by_title(
        self,
        video_id: str,
        title: str,
        aka_titles: list[str],
        year: int,
        max_process: int,
    ) -> List[TorrentStreams]:
        params = {
            "query": f"{title} ({year})",
            "categories": [2000, 8000],  # Movies & Others
            "type": "search",
        }
        search_results = await self.fetch_stream_data(params)
        return await self.parse_movie_streams(
            video_id, title, year, search_results[:max_process], aka_titles
        )

    async def scrape_series_by_imdb(
        self, video_id: str, title: str, year: int, season: int, episode: int
    ) -> List[TorrentStreams]:
        params = {
            "query": f"{{ImdbId:{video_id}}}{{Season:{season}}}{{Episode:{episode}}}",
            "categories": [5000],  # TV
            "type": "tvsearch",
        }
        search_results = await self.fetch_stream_data(params)
        return await self.parse_series_streams(
            video_id, title, year, season, episode, search_results
        )

    async def scrape_series_by_title(
        self,
        video_id: str,
        title: str,
        aka_titles: list[str],
        year: int,
        season: int,
        episode: int,
        max_process: int,
    ) -> List[TorrentStreams]:
        params = {
            "query": title,
            "categories": [5000, 8000],  # TV & Others
            "type": "search",
        }
        search_results = await self.fetch_stream_data(params)
        return await self.parse_series_streams(
            video_id,
            title,
            year,
            season,
            episode,
            search_results[:max_process],
            aka_titles,
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

    async def parse_movie_streams(
        self,
        video_id: str,
        title: str,
        year: int,
        search_results: List[Dict[str, Any]],
        aka_titles: list[str] = None,
    ) -> List[TorrentStreams]:
        circuit_breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout=10, half_open_attempts=2
        )
        parsed_results = await batch_process_with_circuit_breaker(
            self.process_stream,
            search_results,
            max(len(search_results) // 5, 10),
            3,
            circuit_breaker,
            5,
            [httpx.HTTPError],
            video_id=video_id,
            title=title,
            aka_titles=aka_titles,
            year=year,
            catalog_type="movie",
        )
        return [stream for stream in parsed_results if stream is not None]

    async def parse_series_streams(
        self,
        video_id: str,
        title: str,
        year: int,
        season: int,
        episode: int,
        search_results: List[Dict[str, Any]],
        aka_titles: list[str] = None,
    ) -> List[TorrentStreams]:
        circuit_breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout=10, half_open_attempts=2
        )
        parsed_results = await batch_process_with_circuit_breaker(
            self.process_stream,
            search_results,
            max(len(search_results) // 5, 10),
            3,
            circuit_breaker,
            5,
            [httpx.HTTPError],
            video_id=video_id,
            title=title,
            aka_titles=aka_titles,
            year=year,
            catalog_type="series",
            season=season,
            episode=episode,
        )
        return [stream for stream in parsed_results if stream is not None]

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        video_id: str,
        title: str,
        aka_titles: list[str],
        year: int,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TorrentStreams | None:
        async with self.semaphore:
            if is_contain_18_plus_keywords(stream_data.get("title")):
                self.logger.warning(
                    f"Stream contains 18+ keywords: {stream_data.get('title')}"
                )
                return None

            if not self.validate_category_with_title(stream_data):
                self.logger.warning(
                    f"Invalid video category: {stream_data.get('title')}"
                )
                return None

            parsed_data = await self.parse_prowlarr_data(
                stream_data, video_id, catalog_type
            )
            if not parsed_data:
                return None

            max_similarity_ratio = calculate_max_similarity_ratio(
                parsed_data.get("title", ""), title, aka_titles or []
            )
            if max_similarity_ratio < 85 or (
                catalog_type == "movie" and parsed_data.get("year") != year
            ):
                self.logger.warning(
                    f"Title or year mismatch: {parsed_data.get('title')} ({parsed_data.get('year')}) != {title} ({year})"
                )
                return None

            torrent_stream = TorrentStreams(
                id=parsed_data["info_hash"],
                meta_id=video_id,
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
            )

            if catalog_type == "series":
                if "seasons" in parsed_data and len(parsed_data["seasons"]) == 1:
                    season_number = parsed_data["seasons"][0]
                    episode_data = [
                        Episode(
                            episode_number=ep,
                            filename=file.get("filename"),
                            size=file.get("size"),
                            file_index=file.get("index"),
                        )
                        for file in parsed_data.get("file_data", [])
                        for ep in file.get("episodes", [])
                        if ep == episode
                    ]
                    if episode_data:
                        torrent_stream.season = Season(
                            season_number=season_number, episodes=episode_data
                        )
                    else:
                        return None
                else:
                    return None

            return torrent_stream

    async def parse_prowlarr_data(
        self, meta_data: dict, video_id: str, catalog_type: str
    ) -> dict | None:
        download_url = meta_data.get("downloadUrl") or meta_data.get("magnetUrl")

        if not download_url:
            download_url = await self.get_download_url(meta_data)

        try:
            torrent_data, is_torrent_downloaded = await self.get_torrent_data(
                download_url, meta_data.get("indexer")
            )
        except Exception as e:
            self.logger.error(f"Error getting torrent data: {e}")
            return None

        info_hash = torrent_data.get("info_hash", "").lower()
        if not info_hash:
            return None

        if not is_torrent_downloaded:
            torrent_data.update(self.parse_title_data(meta_data.get("title")))

        torrent_data.update(
            {
                "seeders": meta_data.get("seeders"),
                "created_at": meta_data.get("publishDate"),
                "source": meta_data.get("indexer"),
                "catalog": [
                    "prowlarr_streams",
                    f"{meta_data.get('indexer').lower()}_{catalog_type}",
                    f"prowlarr_{catalog_type}",
                ],
            }
        )

        return torrent_data

    @staticmethod
    async def get_download_url(meta_data: dict) -> str:
        if meta_data.get("indexer") in [
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
            return meta_data.get("guid")
        else:
            if not meta_data.get("magnetUrl") and not meta_data.get(
                "downloadUrl", ""
            ).startswith("magnet:"):
                torrent_info_data = await torrent_info.get_torrent_info(
                    meta_data.get("infoUrl"), meta_data.get("indexer")
                )
                return torrent_info_data.get("magnetUrl") or torrent_info_data.get(
                    "downloadUrl"
                )
        return meta_data.get("magnetUrl") or meta_data.get("downloadUrl")

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
            response = await client.get(download_url, follow_redirects=True, timeout=20)
            response.raise_for_status()
            return extract_torrent_metadata(response.content), True

    @staticmethod
    def parse_title_data(title: str) -> dict:
        parsed = PTT.parse_title(title, True)
        return {
            "torrent_name": title,
            "title": parsed.get("title"),
            "year": parsed.get("year"),
            "resolution": parsed.get("resolution"),
            "codec": parsed.get("codec"),
            "quality": parsed.get("quality"),
            "audio": parsed.get("audio"),
            "languages": parsed.get("languages", []),
        }

    @staticmethod
    def validate_category_with_title(meta_data: dict) -> bool:
        if meta_data.get("categories") and meta_data.get("categories")[0]["id"] == 8000:
            # Extra caution with category 8000 (Others) as it may contain non-movie torrents
            video_keywords = [
                "mkv",
                "mp4",
                "avi",
                ".webm",
                ".mov",
                ".flv",
                "webdl",
                "web-dl",
                "webrip",
                "bluray",
                "brrip",
                "bdrip",
                "dvdrip",
                "hdtv",
                "hdcam",
                "hdrip",
                "1080p",
                "720p",
                "480p",
                "360p",
                "2160p",
                "4k",
                "x264",
                "x265",
                "hevc",
                "h264",
                "h265",
                "aac",
                "xvid",
            ]
            title = meta_data.get("fileName", meta_data.get("title", "")).lower()
            return any(keyword in title for keyword in video_keywords)
        return True

    def get_cache_key(
        self,
        video_id: str,
        catalog_type: str,
        title: str,
        aka_titles: list[str],
        year: int,
        season: int = None,
        episode: int = None,
    ) -> str:
        return f"prowlarr:{catalog_type}:{video_id}:{year}:{season}:{episode}"

    @minimum_run_interval(hours=settings.prowlarr_search_interval_hour)
    @dramatiq.actor(
        time_limit=60 * 60 * 1000,  # 60 minutes
        min_backoff=2 * 60 * 1000,  # 2 minutes
        max_backoff=60 * 60 * 1000,  # 60 minutes
        priority=100,
    )
    async def background_movie_title_search(
        self, video_id: str, title: str, aka_titles: list[str], year: int
    ):
        await self.scrape_movie_by_title(
            video_id, title, aka_titles, year, max_process=None
        )
        self.logger.info(f"Background title search completed for {title} ({year})")

    @minimum_run_interval(hours=settings.prowlarr_search_interval_hour)
    @dramatiq.actor(
        time_limit=60 * 60 * 1000,  # 60 minutes
        min_backoff=2 * 60 * 1000,  # 2 minutes
        max_backoff=60 * 60 * 1000,  # 60 minutes
        priority=100,
    )
    async def background_series_title_search(
        self,
        video_id: str,
        title: str,
        aka_titles: list[str],
        year: int,
        season: int,
        episode: int,
    ):
        await self.scrape_series_by_title(
            video_id, title, aka_titles, year, season, episode, max_process=None
        )
        self.logger.info(
            f"Background title search completed for {title} S{season}E{episode}"
        )

    def should_retry_prowlarr_scrap(self, retries_so_far, exception) -> bool:
        should_retry = retries_so_far < 10 and isinstance(exception, httpx.HTTPError)
        if not should_retry:
            self.logger.error(f"Failed to fetch data from Prowlarr: {exception}")
        return should_retry
