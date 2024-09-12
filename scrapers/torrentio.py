import asyncio
from typing import List, Dict, Any
from os import path
import PTT
import re
from datetime import timedelta

from db.models import TorrentStreams, Season, Episode
from db.config import settings
from scrapers.base_scraper import BaseScraper, ScraperError
from utils.parser import (
    convert_size_to_bytes,
    is_contain_18_plus_keywords,
    calculate_max_similarity_ratio,
)
from utils.runtime_const import REDIS_ASYNC_CLIENT
from utils.validation_helper import is_video_file


class TorrentioScraper(BaseScraper):
    def __init__(self):
        super().__init__()
        self.base_url = settings.torrentio_url
        self.semaphore = asyncio.Semaphore(10)

    @BaseScraper.cache(
        ttl=int(timedelta(days=settings.torrentio_search_interval_days).total_seconds())
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
        url = f"{self.base_url}/stream/{catalog_type}/{video_id}.json"
        if catalog_type == "series":
            url = f"{self.base_url}/stream/{catalog_type}/{video_id}:{season}:{episode}.json"

        try:
            response = await self.make_request(url)
            data = response.json()
        except ScraperError:
            return []

        if not self.validate_response(data):
            self.logger.warning(f"Invalid response received for {url}")
            return []

        return await self.parse_response(
            data, video_id, title, aka_titles, catalog_type, season, episode
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
        return f"torrentio:{catalog_type}:{video_id}:{season}:{episode}"

    def validate_response(self, response: Dict[str, Any]) -> bool:
        return "streams" in response and isinstance(response["streams"], list)

    async def parse_response(
        self,
        response: Dict[str, Any],
        video_id: str,
        title: str,
        aka_titles: list[str],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        tasks = [
            self.process_stream(
                stream_data, video_id, title, aka_titles, catalog_type, season, episode
            )
            for stream_data in response.get("streams", [])
        ]
        results = await asyncio.gather(*tasks)
        return [stream for stream in results if stream is not None]

    async def process_stream(
        self,
        stream_data: Dict[str, Any],
        video_id: str,
        title: str,
        aka_titles: list[str],
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> TorrentStreams | None:
        async with self.semaphore:
            try:
                if is_contain_18_plus_keywords(stream_data["title"]):
                    self.logger.warning(
                        f"Stream contains 18+ keywords: {stream_data['title']}"
                    )
                    return None

                parsed_data = self.parse_stream_title(stream_data)
                source = (
                    stream_data["name"].split()[0].title()
                    if stream_data.get("name")
                    else "Torrentio"
                )

                if source != "Torrentio":
                    max_similarity_ratio = calculate_max_similarity_ratio(
                        parsed_data.get("title"), title, aka_titles
                    )
                    if max_similarity_ratio < 85:
                        self.logger.error(
                            f"Title mismatch: '{title}' != '{parsed_data.get('title')}' ratio: {max_similarity_ratio}"
                        )
                        return None

                stream = TorrentStreams(
                    id=stream_data["infoHash"],
                    meta_id=video_id,
                    torrent_name=parsed_data["torrent_name"],
                    size=parsed_data["size"],
                    filename=parsed_data["file_name"],
                    file_index=stream_data.get("fileIdx"),
                    languages=parsed_data["languages"],
                    resolution=parsed_data["metadata"].get("resolution"),
                    codec=parsed_data["metadata"].get("codec"),
                    quality=parsed_data["metadata"].get("quality"),
                    audio=parsed_data["metadata"].get("audio"),
                    source=source,
                    catalog=["torrentio_streams"],
                    seeders=parsed_data["seeders"],
                    announce_list=[
                        tracker.lstrip("tracker:")
                        for tracker in stream_data.get("sources", [])
                        if "tracker:" in tracker
                    ],
                )

                if catalog_type == "series":
                    if seasons := parsed_data["metadata"].get("seasons"):
                        if len(seasons) == 1:
                            season_number = seasons[0]
                        else:
                            # Skip This Stream due to multiple seasons in one torrent.
                            return None
                    else:
                        season_number = season

                    if parsed_data["metadata"].get("episodes"):
                        episode_data = [
                            Episode(
                                episode_number=episode_number,
                                file_index=(
                                    stream.get("fileIdx")
                                    if episode_number == episode
                                    else None
                                ),
                            )
                            for episode_number in parsed_data["metadata"]["episodes"]
                        ]
                    else:
                        episode_data = [
                            Episode(
                                episode_number=episode, file_index=stream.get("fileIdx")
                            )
                        ]

                    stream.season = Season(
                        season_number=season_number,
                        episodes=episode_data,
                    )
                    stream.filename = None

                return stream
            except Exception as e:
                self.logger.error(f"Error processing stream: {e}")
                return None

    def parse_stream_title(self, stream: dict) -> dict:
        torrent_name, file_name = stream["title"].splitlines()[:2]
        metadata = PTT.parse_title(torrent_name, True)

        return {
            "torrent_name": torrent_name,
            "title": metadata.get("title"),
            "size": convert_size_to_bytes(self.extract_size_string(stream["title"])),
            "seeders": self.extract_seeders(stream["title"]),
            "languages": self.extract_languages(metadata, stream["title"]),
            "metadata": metadata,
            "file_name": (
                stream.get("behaviorHints", {}).get("filename")
                or path.basename(file_name)
                if is_video_file(file_name)
                else None
            ),
        }

    @staticmethod
    def extract_seeders(details: str) -> int:
        seeders_match = re.search(r"👤 (\d+)", details)
        return int(seeders_match.group(1)) if seeders_match else None

    @staticmethod
    def extract_languages_from_title(title: str) -> list:
        languages = []
        if "Multi Audio" in title or "Multi Language" in title:
            languages.append("Multi Language")
        elif "Dual Audio" in title or "Dual Language" in title:
            languages.append("Dual Language")

        flag_emojis = re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", title)
        if flag_emojis:
            languages.extend(flag_emojis)

        return languages

    def extract_languages(self, metadata: dict, title: str) -> list:
        languages = metadata.get("languages", [])
        if languages:
            return languages
        return self.extract_languages_from_title(title)

    @staticmethod
    def extract_size_string(details: str) -> str:
        size_match = re.search(r"💾 (\d+(?:\.\d+)?\s*(GB|MB))", details, re.IGNORECASE)
        return size_match.group(1) if size_match else ""
