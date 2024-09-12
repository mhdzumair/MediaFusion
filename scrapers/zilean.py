import asyncio
from datetime import timedelta
from typing import List, Dict, Any

import PTT
import httpx

from db.config import settings
from db.models import TorrentStreams, Season, Episode
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.const import UA_HEADER
from utils.parser import (
    is_contain_18_plus_keywords,
    calculate_max_similarity_ratio,
)
from utils.runtime_const import REDIS_ASYNC_CLIENT


class ZileanScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.base_url = f"{settings.zilean_url}/dmm/search"
        self.semaphore = asyncio.Semaphore(10)

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
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        try:
            stream_response = await self.make_request(
                self.base_url, json={"queryText": title}, timeout=10
            )
        except ScraperError:
            return []

        stream_data = stream_response.json()
        if not self.validate_response(stream_data):
            self.logger.warning(f"Invalid response received for {title}")
            return []

        return await self.parse_response(
            stream_data, video_id, title, aka_titles, catalog_type, season, episode
        )

    def get_cache_key(
        self,
        video_id: str,
        catalog_type: str,
        title: str,
        aka_titles: list[str],
        season: int = None,
        episode: int = None,
    ) -> str:
        return f"zilean:{catalog_type}:{video_id}:{season}:{episode}"

    async def fetch_stream_data(self, title: str) -> list:
        """Fetch stream data asynchronously."""
        async with httpx.AsyncClient(
            headers=UA_HEADER, proxy=settings.scraper_proxy_url
        ) as client:
            response = await client.post(
                self.base_url, timeout=10, json={"queryText": title}
            )
            response.raise_for_status()
            self.logger.info(
                f"Zilean DMM found {len(response.json())} streams for {title}"
            )
            return response.json()

    async def parse_response(
        self,
        response: List[Dict[str, Any]],
        video_id: str,
        title: str,
        aka_titles: list[str],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        tasks = [
            self.process_stream(
                stream, video_id, title, aka_titles, catalog_type, season, episode
            )
            for stream in response
        ]
        results = await asyncio.gather(*tasks)
        return [stream for stream in results if stream is not None]

    async def process_stream(
        self,
        stream: Dict[str, Any],
        video_id: str,
        title: str,
        aka_titles: list[str],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TorrentStreams | None:
        async with self.semaphore:
            if is_contain_18_plus_keywords(stream["filename"]):
                self.logger.warning(
                    f"Stream contains 18+ keywords: {stream['filename']}"
                )
                return None

            metadata = PTT.parse_title(stream["filename"], True)
            max_similarity_ratio = calculate_max_similarity_ratio(
                metadata.get("title"), title, aka_titles
            )
            if max_similarity_ratio < 85:
                self.logger.error(
                    f"Title mismatch: '{title}' != '{metadata.get('title')}' ratio: {max_similarity_ratio}, fullname: {stream['filename']}"
                )
                return None

            torrent_stream = TorrentStreams(
                id=stream["info_hash"],
                meta_id=video_id,
                torrent_name=stream["filename"],
                announce_list=[],
                size=stream["filesize"],
                languages=metadata["languages"],
                resolution=metadata.get("resolution"),
                codec=metadata.get("codec"),
                quality=metadata.get("quality"),
                audio=metadata.get("audio"),
                source="Zilean DMM",
                catalog=["zilean_dmm_streams"],
            )

            if catalog_type == "movie":
                torrent_stream.catalog.append("zilean_dmm_movies")
            elif catalog_type == "series":
                torrent_stream.catalog.append("zilean_dmm_series")
                if seasons := metadata.get("seasons"):
                    if len(seasons) != 1:
                        return None
                    season_number = seasons[0]
                else:
                    return None

                if episodes := metadata.get("episodes"):
                    episode_data = [
                        Episode(episode_number=episode_number)
                        for episode_number in episodes
                    ]
                else:
                    return None

                torrent_stream.season = Season(
                    season_number=season_number,
                    episodes=episode_data,
                )

            return torrent_stream

    def validate_response(self, response: List[Dict[str, Any]]) -> bool:
        return isinstance(response, list) and len(response) > 0
