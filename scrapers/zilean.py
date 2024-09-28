import asyncio
from datetime import timedelta
from typing import List, Dict, Any

import PTT
from tenacity import RetryError

from db.config import settings
from db.models import TorrentStreams, Season, Episode, MediaFusionMetaData
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.parser import (
    is_contain_18_plus_keywords,
)


class ZileanScraper(BaseScraper):
    def __init__(self):
        super().__init__(cache_key_prefix="zilean")
        self.base_url = f"{settings.zilean_url}/dmm/search"
        self.semaphore = asyncio.Semaphore(10)

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
        try:
            stream_response = await self.make_request(
                self.base_url,
                method="POST",
                json={"queryText": metadata.title},
                timeout=10,
            )

            stream_data = stream_response.json()
            if not self.validate_response(stream_data):
                self.logger.warning(f"Invalid response received for {metadata.title}")
                return []

            return await self.parse_response(
                stream_data, metadata, catalog_type, season, episode
            )
        except (ScraperError, RetryError):
            return []
        except Exception as e:
            self.logger.exception(
                f"Error occurred while fetching {metadata.title}: {e}"
            )
            return []

    async def parse_response(
        self,
        response: List[Dict[str, Any]],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        tasks = [
            self.process_stream(stream, metadata, catalog_type, season, episode)
            for stream in response
        ]
        results = await asyncio.gather(*tasks)
        return [stream for stream in results if stream is not None]

    async def process_stream(
        self,
        stream: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TorrentStreams | None:
        async with self.semaphore:
            if is_contain_18_plus_keywords(stream["raw_title"]):
                self.logger.warning(
                    f"Stream contains 18+ keywords: {stream['filename']}"
                )
                return None

            torrent_data = PTT.parse_title(stream["raw_title"], True)
            if not self.validate_title_and_year(
                torrent_data.get("title"),
                torrent_data.get("year"),
                metadata,
                catalog_type,
                stream["raw_title"],
            ):
                return None

            torrent_stream = TorrentStreams(
                id=stream["info_hash"],
                meta_id=metadata.id,
                torrent_name=stream["raw_title"],
                announce_list=[],
                size=stream["size"],
                languages=torrent_data["languages"],
                resolution=torrent_data.get("resolution"),
                codec=torrent_data.get("codec"),
                quality=torrent_data.get("quality"),
                audio=torrent_data.get("audio"),
                source="Zilean DMM",
                catalog=["zilean_dmm_streams"],
            )

            if catalog_type == "movie":
                torrent_stream.catalog.append("zilean_dmm_movies")
            elif catalog_type == "series":
                torrent_stream.catalog.append("zilean_dmm_series")
                if seasons := torrent_data.get("seasons"):
                    if len(seasons) != 1:
                        return None
                    season_number = seasons[0]
                else:
                    return None

                if episodes := torrent_data.get("episodes"):
                    episode_data = [
                        Episode(episode_number=episode_number)
                        for episode_number in episodes
                    ]
                elif season in seasons:
                    episode_data = [Episode(episode_number=episode)]
                else:
                    return None

                torrent_stream.season = Season(
                    season_number=season_number,
                    episodes=episode_data,
                )

            return torrent_stream

    def validate_response(self, response: List[Dict[str, Any]]) -> bool:
        return isinstance(response, list) and len(response) > 0
