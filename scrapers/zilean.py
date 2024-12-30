import asyncio
from datetime import timedelta
from typing import List, Dict, Any

import PTT
from httpx import Response
from tenacity import RetryError

from db.config import settings
from db.models import TorrentStreams, MediaFusionMetaData, EpisodeFile
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.parser import (
    is_contain_18_plus_keywords,
)
from utils.runtime_const import ZILEAN_SEARCH_TTL


class ZileanScraper(BaseScraper):
    cache_key_prefix = "zilean"

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)
        self.semaphore = asyncio.Semaphore(10)

    @BaseScraper.cache(ttl=ZILEAN_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
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
        if isinstance(search_response, Response):
            stream_data.extend(search_response.json())
        else:
            self.metrics.record_error("search_request_failed")
            self.logger.error(
                f"Error occurred while search {metadata.title}: {search_response}"
            )
        if isinstance(filtered_response, Response):
            stream_data.extend(filtered_response.json())
        else:
            self.metrics.record_error("filtered_request_failed")
            self.logger.error(
                f"Error occurred while filtering {metadata.title}: {filtered_response}"
            )

        self.metrics.record_found_items(len(stream_data))

        if not self.validate_response(stream_data):
            self.metrics.record_error("no_valid_streams")
            self.logger.error(f"No valid streams found for {metadata.title}")
            return []

        try:
            streams = await self.parse_response(
                stream_data, metadata, catalog_type, season, episode
            )
            return streams
        except (ScraperError, RetryError):
            self.metrics.record_error("parsing_failed")
        except Exception as e:
            self.metrics.record_error("unexpected_error")
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
            try:
                if is_contain_18_plus_keywords(stream["raw_title"]):
                    self.metrics.record_skip("Adult content")
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
                else:
                    torrent_stream.catalog.append("zilean_dmm_series")
                    seasons = torrent_data.get("seasons")
                    if not seasons:
                        self.metrics.record_skip("Missing season info")
                        return None

                    if episodes := torrent_data.get("episodes"):
                        episode_data = [
                            EpisodeFile(
                                season_number=seasons[0], episode_number=episode_number
                            )
                            for episode_number in episodes
                        ]
                    elif seasons:
                        episode_data = [
                            EpisodeFile(season_number=season_number, episode_number=1)
                            for season_number in seasons
                        ]
                    else:
                        self.metrics.record_skip("Missing episode info")
                        return None

                    torrent_stream.episode_files = episode_data

                # Record metrics for successful processing
                self.metrics.record_processed_item()
                self.metrics.record_quality(torrent_stream.quality)
                self.metrics.record_source(torrent_stream.source)

                return torrent_stream

            except Exception as e:
                self.metrics.record_error("stream_processing_error")
                self.logger.exception(f"Error processing stream: {e}")
                return None

    def validate_response(self, response: List[Dict[str, Any]]) -> bool:
        return isinstance(response, list) and len(response) > 0
