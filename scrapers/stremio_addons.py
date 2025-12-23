import asyncio
import logging
import re
from abc import abstractmethod
from typing import List, Dict, Any, Optional

from tenacity import RetryError

from db.schemas import TorrentStreamData, MetadataData, EpisodeFileData
from db.schemas import UserData
from scrapers.base_scraper import BaseScraper, ScraperError
from streaming_providers.cache_helpers import store_cached_info_hashes
from utils.parser import is_contain_18_plus_keywords


class StremioScraper(BaseScraper):
    def __init__(self, cache_key_prefix: str, base_url: str, logger_name: str):
        super().__init__(cache_key_prefix=cache_key_prefix, logger_name=logger_name)
        self.base_url = base_url
        self.semaphore = asyncio.Semaphore(10)

    def _generate_url(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> str:
        pass

    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[TorrentStreamData]:
        url = self._generate_url(user_data, metadata, catalog_type, season, episode)
        try:
            response = await self.make_request(url)
            response.raise_for_status()
            data = response.json()

            if not self.validate_response(data):
                self.metrics.record_error("invalid_response")
                self.logger.warning(f"Invalid response received for {url}")
                return []

            self.metrics.record_found_items(len(data.get("streams", [])))
            return await self.parse_response(
                data, user_data, metadata, catalog_type, season, episode
            )
        except (ScraperError, RetryError):
            self.metrics.record_error("request_failed")
            return []
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"Error occurred while fetching {url}: {e}")
            return []

    def validate_response(self, response: Dict[str, Any]) -> bool:
        return "streams" in response and isinstance(response["streams"], list)

    async def parse_response(
        self,
        response: Dict[str, Any],
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[TorrentStreamData]:
        tasks = [
            self.process_stream(stream_data, metadata, catalog_type, season, episode)
            for stream_data in response.get("streams", [])
        ]
        results = await asyncio.gather(*tasks)
        streams = []
        cached_info_hashes = []
        for stream, is_cached in results:
            if stream:
                streams.append(stream)
                if is_cached:
                    cached_info_hashes.append(stream.id)

        logging.info(
            f"Found {len(streams)} streams for {metadata.title} on {self.get_scraper_name()} with {len(cached_info_hashes)} cached streams for {user_data.streaming_provider.service}"
        )
        await store_cached_info_hashes(user_data.streaming_provider, cached_info_hashes)
        return streams

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        metadata: MetadataData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> tuple[Optional[TorrentStreamData], bool]:
        async with self.semaphore:
            try:
                adult_content_field = self.get_adult_content_field(stream_data)
                if is_contain_18_plus_keywords(adult_content_field):
                    self.metrics.record_skip("Adult Content")
                    self.logger.warning(
                        f"Stream contains 18+ keywords: {adult_content_field}"
                    )
                    return None, False

                parsed_data, is_cached = self.parse_stream_title(stream_data)
                if not parsed_data:
                    self.metrics.record_skip("Invalid Data")
                    return None, False
                source = parsed_data["source"]

                if self.get_scraper_name() not in source:
                    if not self.validate_title_and_year(
                        parsed_data,
                        metadata,
                        catalog_type,
                        parsed_data.get("torrent_name"),
                    ):
                        return None, False

                stream = self.create_torrent_stream(stream_data, parsed_data, metadata)

                if catalog_type == "series":
                    if not self.process_series_data(
                        stream, parsed_data, season, episode, stream_data
                    ):
                        return None, False

                # Record metrics for successful processing
                self.metrics.record_processed_item()
                self.metrics.record_quality(stream.quality)
                self.metrics.record_source(source)

                return stream, is_cached
            except Exception as e:
                self.metrics.record_error("stream_processing_error")
                self.logger.exception(f"Error processing stream: {e}")
                return None, False

    def create_torrent_stream(
        self,
        stream_data: Dict[str, Any],
        parsed_data: Dict[str, Any],
        metadata: MetadataData,
    ) -> TorrentStreamData:
        return TorrentStreamData(
            id=parsed_data["info_hash"],
            meta_id=metadata.id,
            torrent_name=parsed_data["torrent_name"],
            size=parsed_data["size"],
            filename=parsed_data["filename"],
            file_index=stream_data.get("fileIdx"),
            languages=parsed_data["languages"],
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            audio=parsed_data.get("audio"),
            hdr=parsed_data.get("hdr"),
            source=parsed_data["source"],
            uploader=parsed_data.get("uploader"),
            catalog=[f"{self.cache_key_prefix}_streams"],
            seeders=parsed_data["seeders"],
            announce_list=[
                tracker.removeprefix("tracker:")
                for tracker in stream_data.get("sources", [])
                if "tracker:" in tracker
            ],
        )

    def process_series_data(
        self,
        stream: TorrentStreamData,
        parsed_data: Dict[str, Any],
        season: int,
        episode: int,
        stream_data: Dict[str, Any],
    ) -> bool:
        season_number = season

        if parsed_data.get("episodes"):
            episode_data = [
                EpisodeFileData(
                    season_number=season_number,
                    episode_number=episode_number,
                    file_index=(
                        stream_data.get("fileIdx")
                        if episode_number == episode
                        else None
                    ),
                )
                for episode_number in parsed_data["episodes"]
            ]
        else:
            episode_data = [
                EpisodeFileData(
                    season_number=season_number,
                    episode_number=episode,
                    file_index=stream_data.get("fileIdx"),
                )
            ]

        stream.episode_files = episode_data
        stream.filename = None
        return True

    @staticmethod
    def extract_seeders(details: str) -> int:
        seeders_match = re.search(r"👤 (\d+)", details)
        return int(seeders_match.group(1)) if seeders_match else 0

    @staticmethod
    def extract_size_string(details: str) -> str:
        size_match = re.search(r"💾 (\d+(?:\.\d+)?\s*(GB|MB))", details, re.IGNORECASE)
        return size_match.group(1) if size_match else ""

    @abstractmethod
    def get_adult_content_field(self, stream_data: Dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def parse_stream_title(self, stream: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        raise NotImplementedError

    @abstractmethod
    def get_scraper_name(self) -> str:
        raise NotImplementedError
