import re
from datetime import timedelta
from typing import Dict, Any, List

import PTT

from db.config import settings
from db.models import MediaFusionMetaData, TorrentStreams
from scrapers.stremio_addons import StremioScraper
from utils.parser import (
    convert_size_to_bytes,
)
from utils.runtime_const import TORRENTIO_SEARCH_TTL


class TorrentioScraper(StremioScraper):
    def __init__(self):
        super().__init__(
            cache_key_prefix="torrentio",
            base_url=settings.torrentio_url,
            logger_name=__name__,
        )

    @StremioScraper.cache(ttl=TORRENTIO_SEARCH_TTL)
    @StremioScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> List[TorrentStreams]:
        return await super()._scrape_and_parse(metadata, catalog_type, season, episode)

    def get_adult_content_field(self, stream_data: Dict[str, Any]) -> str:
        return stream_data["title"]

    def get_scraper_name(self) -> str:
        return "Torrentio"

    def parse_stream_title(self, stream: dict) -> dict:
        try:
            descriptions = stream.get("title")
            torrent_name = descriptions.splitlines()[0]
            metadata = PTT.parse_title(torrent_name, True)

            addon_name = stream["name"].split()[0].title()
            source_match = re.search(r"⚙️.*", descriptions)
            source = (
                source_match.group(0).removeprefix("⚙️ ").strip() if source_match else ""
            )
            if addon_name not in source:
                source = f"{addon_name} | {source}"

            return {
                "torrent_name": torrent_name,
                "title": metadata.get("title"),
                "size": convert_size_to_bytes(self.extract_size_string(descriptions)),
                "seeders": self.extract_seeders(descriptions),
                "languages": metadata["languages"],
                "metadata": metadata,
                "file_name": stream.get("behaviorHints", {}).get("filename"),
                "source": source,
            }
        except Exception as e:
            self.metrics.record_error("title_parsing_error")
            raise e
