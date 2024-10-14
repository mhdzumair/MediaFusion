import asyncio
from datetime import timedelta
from typing import List, Dict, Any

import PTT
from httpx import Response
from tenacity import RetryError
from scrapeops_python_requests.scrapeops_requests import ScrapeOpsRequests
from db.config import settings
from db.models import TorrentStreams, Season, Episode, MediaFusionMetaData
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.parser import (
    is_contain_18_plus_keywords,
)
from utils.runtime_const import ZILEAN_SEARCH_TTL


class ZileanScraper(BaseScraper):
    cache_key_prefix = "zilean"

    def __init__(self):
        super().__init__(
            cache_key_prefix=self.cache_key_prefix, logger_name=self.__class__.__name__
        )
        self.semaphore = asyncio.Semaphore(10)

    @BaseScraper.cache(ttl=ZILEAN_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        job_name = f"{metadata.title}:{metadata.id}"
        if catalog_type == "series":
            job_name += f":{season}:{episode}"

        scrapeops_logger = None
        if settings.scrapeops_api_key:
            scrapeops_logger = ScrapeOpsRequests(
                scrapeops_api_key=settings.scrapeops_api_key,
                spider_name="Zilean Scraper",
                job_name=job_name,
            )

        search_task = asyncio.create_task(
            self.make_request(
                f"{settings.zilean_url}/dmm/search",
                method="POST",
                json={"queryText": metadata.title},
                timeout=10,
            )
        )

        if metadata.type == "movie":
            params = {
                "Query": metadata.title,
                "Year": metadata.year,
            }
        else:
            params = {
                "Query": metadata.title,
                "Season": season,
                "Episode": episode,
            }

        filtered_task = asyncio.create_task(
            self.make_request(
                f"{settings.zilean_url}/dmm/filtered",
                method="GET",
                params=params,
                timeout=10,
            )
        )

        search_response, filtered_response = await asyncio.gather(
            search_task, filtered_task, return_exceptions=True
        )

        stream_data = []
        response = None
        if isinstance(search_response, Response):
            response = search_response
            stream_data.extend(search_response.json())
        else:
            self.logger.error(
                f"Error occurred while search {metadata.title}: {search_response}"
            )
        if isinstance(filtered_response, Response):
            response = filtered_response
            stream_data.extend(filtered_response.json())
        else:
            self.logger.error(
                f"Error occurred while filtering {metadata.title}: {filtered_response}"
            )

        if not self.validate_response(stream_data):
            self.logger.error(f"No valid streams found for {metadata.title}")
            if scrapeops_logger:
                scrapeops_logger.logger.close_sdk()
            return []

        try:
            streams = await self.parse_response(
                stream_data, metadata, catalog_type, season, episode
            )
            if scrapeops_logger:
                for stream in streams:
                    scrapeops_logger.item_scraped(
                        item=stream.model_dump(include={"id"}),
                        response=response,
                    )
            return streams
        except (ScraperError, RetryError):
            return []
        except Exception as e:
            self.logger.exception(
                f"Error occurred while fetching {metadata.title}: {e}"
            )
            return []
        finally:
            if scrapeops_logger:
                scrapeops_logger.logger.close_sdk()

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
                    f"Stream contains 18+ keywords: {stream['raw_title']}"
                )
                return None

            torrent_data = PTT.parse_title(stream["raw_title"], True)
            if not self.validate_title_and_year(
                torrent_data,
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
