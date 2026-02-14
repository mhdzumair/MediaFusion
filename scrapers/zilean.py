import asyncio
from datetime import timedelta
from typing import Any

import PTT
from httpx import Response
from tenacity import RetryError

from db.config import settings
from db.schemas import MetadataData, StreamFileData, TorrentStreamData
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
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        search_task = asyncio.create_task(
            self.make_request(
                f"{settings.zilean_url}/dmm/search",
                method="POST",
                json={"queryText": metadata.title},
                timeout=10,
            )
        )

        params = {
            "Query": metadata.title,
        }
        if catalog_type == "movie":
            # For movies, we only need to search by title and year
            if metadata.year:
                params["Year"] = metadata.year
        elif catalog_type == "series":
            # For series, we need to search by title, season, and episode
            if season:
                params["Season"] = season
            if episode:
                params["Episode"] = episode

        filtered_task = asyncio.create_task(
            self.make_request(
                f"{settings.zilean_url}/dmm/filtered",
                method="GET",
                params=params,
                timeout=10,
            )
        )

        search_response, filtered_response = await asyncio.gather(search_task, filtered_task, return_exceptions=True)

        stream_data = []
        if isinstance(search_response, Response):
            stream_data.extend(search_response.json())
        else:
            self.metrics.record_error("search_request_failed")
            self.logger.error(f"Error occurred while search {metadata.title}: {search_response}")
        if isinstance(filtered_response, Response):
            stream_data.extend(filtered_response.json())
        else:
            self.metrics.record_error("filtered_request_failed")
            self.logger.error(f"Error occurred while filtering {metadata.title}: {filtered_response}")

        self.metrics.record_found_items(len(stream_data))

        if not self.validate_response(stream_data):
            self.metrics.record_error("no_valid_streams")
            self.logger.error(f"No valid streams found for {metadata.title}")
            return []

        try:
            streams = await self.parse_response(stream_data, user_data, metadata, catalog_type, season, episode)
            return streams
        except (ScraperError, RetryError):
            self.metrics.record_error("parsing_failed")
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"Error occurred while fetching {metadata.title}: {e}")
        return []

    async def parse_response(
        self,
        response: list[dict[str, Any]],
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        tasks = [self.process_stream(stream, metadata, catalog_type, season, episode) for stream in response]
        results = await asyncio.gather(*tasks)
        return [stream for stream in results if stream is not None]

    async def process_stream(
        self,
        stream: dict[str, Any],
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TorrentStreamData | None:
        async with self.semaphore:
            try:
                if is_contain_18_plus_keywords(stream["raw_title"]):
                    self.metrics.record_skip("Adult content")
                    self.logger.warning(f"Stream contains 18+ keywords: {stream['raw_title']}")
                    return None

                torrent_data = PTT.parse_title(stream["raw_title"], True)
                if not self.validate_title_and_year(
                    torrent_data,
                    metadata,
                    catalog_type,
                    stream["raw_title"],
                ):
                    return None

                torrent_stream = TorrentStreamData(
                    info_hash=stream["info_hash"],
                    meta_id=metadata.get_canonical_id(),
                    name=stream["raw_title"],
                    announce_list=[],
                    size=stream["size"],
                    source="Zilean DMM",
                    # Single-value quality attributes
                    resolution=torrent_data.get("resolution"),
                    codec=torrent_data.get("codec"),
                    quality=torrent_data.get("quality"),
                    bit_depth=torrent_data.get("bit_depth"),
                    release_group=torrent_data.get("group"),
                    # Multi-value quality attributes (from PTT)
                    audio_formats=torrent_data.get("audio", []) if isinstance(torrent_data.get("audio"), list) else [],
                    channels=torrent_data.get("channels", []) if isinstance(torrent_data.get("channels"), list) else [],
                    hdr_formats=torrent_data.get("hdr", []) if isinstance(torrent_data.get("hdr"), list) else [],
                    languages=torrent_data.get("languages", []),
                    # Release flags
                    is_remastered=torrent_data.get("remastered", False),
                    is_upscaled=torrent_data.get("upscaled", False),
                    is_proper=torrent_data.get("proper", False),
                    is_repack=torrent_data.get("repack", False),
                    is_extended=torrent_data.get("extended", False),
                    is_complete=torrent_data.get("complete", False),
                    is_dubbed=torrent_data.get("dubbed", False),
                    is_subbed=torrent_data.get("subbed", False),
                )

                if catalog_type == "movie":
                    # For the Movies, should not have seasons and episodes
                    if torrent_data.get("seasons") or torrent_data.get("episodes"):
                        self.metrics.record_skip("Unexpected season/episode info")
                        return None
                else:
                    # Series - add file entries with episode info
                    seasons = torrent_data.get("seasons")
                    if not seasons:
                        self.metrics.record_skip("Missing season info")
                        return None

                    files = []
                    if episodes := torrent_data.get("episodes"):
                        for episode_number in episodes:
                            files.append(
                                StreamFileData(
                                    file_index=0,
                                    filename="",
                                    file_type="video",
                                    season_number=seasons[0],
                                    episode_number=episode_number,
                                )
                            )
                    elif seasons:
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
                    else:
                        self.metrics.record_skip("Missing episode info")
                        return None

                    torrent_stream.files = files

                # Record metrics for successful processing
                self.metrics.record_processed_item()
                self.metrics.record_quality(torrent_stream.quality)
                self.metrics.record_source(torrent_stream.source)

                return torrent_stream

            except Exception as e:
                self.metrics.record_error("stream_processing_error")
                self.logger.exception(f"Error processing stream: {e}")
                return None

    def validate_response(self, response: list[dict[str, Any]]) -> bool:
        return isinstance(response, list) and len(response) > 0
