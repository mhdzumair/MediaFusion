import asyncio
import re
from abc import abstractmethod
from typing import List, Dict, Any, Optional

from tenacity import RetryError

from db.models import TorrentStreams, MediaFusionMetaData, EpisodeFile
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.parser import is_contain_18_plus_keywords


class StremioScraper(BaseScraper):
    def __init__(self, cache_key_prefix: str, base_url: str, logger_name: str):
        super().__init__(cache_key_prefix=cache_key_prefix, logger_name=logger_name)
        self.base_url = base_url
        self.semaphore = asyncio.Semaphore(10)

    async def _scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[TorrentStreams]:
        url = f"{self.base_url}/stream/{catalog_type}/{metadata.id}.json"
        job_name = f"{metadata.title}:{metadata.id}"
        if catalog_type == "series":
            url = f"{self.base_url}/stream/{catalog_type}/{metadata.id}:{season}:{episode}.json"
            job_name += f":{season}:{episode}"

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
                data, metadata, catalog_type, season, episode
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
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[TorrentStreams]:
        tasks = [
            self.process_stream(stream_data, metadata, catalog_type, season, episode)
            for stream_data in response.get("streams", [])
        ]
        results = await asyncio.gather(*tasks)
        return [stream for stream in results if stream is not None]

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> Optional[TorrentStreams]:
        async with self.semaphore:
            try:
                adult_content_field = self.get_adult_content_field(stream_data)
                if is_contain_18_plus_keywords(adult_content_field):
                    self.metrics.record_skip("Adult Content")
                    self.logger.warning(
                        f"Stream contains 18+ keywords: {adult_content_field}"
                    )
                    return None

                parsed_data = self.parse_stream_title(stream_data)
                source = parsed_data["source"]

                if self.get_scraper_name() not in source:
                    if not self.validate_title_and_year(
                        parsed_data,
                        metadata,
                        catalog_type,
                        parsed_data.get("torrent_name"),
                    ):
                        return None

                stream = self.create_torrent_stream(stream_data, parsed_data, metadata)

                if catalog_type == "series":
                    episodes = parsed_data.get("episodes")

                    if season not in episodes:
                        self.metrics.record_skip("Season not found")
                        return None

                    if episode not in episodes:
                        self.metrics.record_skip("Episode not found")
                        return None

                    if not self.process_series_data(stream, parsed_data, season, episode, stream_data):
                        return None

                # Record metrics for successful processing
                self.metrics.record_processed_item()
                self.metrics.record_quality(stream.quality)
                self.metrics.record_source(source)

                return stream
            except Exception as e:
                self.metrics.record_error("stream_processing_error")
                self.logger.exception(f"Error processing stream: {e}")
                return None

    def create_torrent_stream(
        self,
        stream_data: Dict[str, Any],
        parsed_data: Dict[str, Any],
        metadata: MediaFusionMetaData,
    ) -> TorrentStreams:
        return TorrentStreams(
            id=stream_data["infoHash"],
            meta_id=metadata.id,
            torrent_name=parsed_data["torrent_name"],
            size=parsed_data["size"],
            filename=parsed_data["filename"],
            file_index=stream_data.get("fileIdx"),
            languages=parsed_data["languages"],
            resolution=parsed_data["metadata"].get("resolution"),
            codec=parsed_data["metadata"].get("codec"),
            quality=parsed_data["metadata"].get("quality"),
            audio=parsed_data["metadata"].get("audio"),
            hdr=parsed_data["metadata"].get("hdr"),
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
        stream: TorrentStreams,
        parsed_data: Dict[str, Any],
        season: int,
        episode: int,
        stream_data: Dict[str, Any],
    ) -> bool:
        season_number = season

        if parsed_data["metadata"].get("episodes"):
            episode_data = [
                EpisodeFile(
                    season_number=season_number,
                    episode_number=episode_number,
                    file_index=(
                        stream_data.get("fileIdx")
                        if episode_number == episode
                        else None
                    ),
                )
                for episode_number in parsed_data["metadata"]["episodes"]
            ]
        else:
            episode_data = [
                EpisodeFile(
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
    def parse_stream_title(self, stream: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_scraper_name(self) -> str:
        raise NotImplementedError
