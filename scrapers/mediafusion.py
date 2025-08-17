from datetime import timedelta
from typing import Dict, Any, Optional, List

import PTT
import httpx

from db.config import settings
from db.models import MediaFusionMetaData, TorrentStreams
from db.schemas import UserData
from scrapers.stremio_addons import StremioScraper
from utils.crypto import crypto_utils
from utils.parser import convert_size_to_bytes
from utils.runtime_const import MEDIAFUSION_SEARCH_TTL


class MediafusionScraper(StremioScraper):
    cache_key_prefix = "mediafusion"

    def __init__(self):
        super().__init__(
            cache_key_prefix=self.cache_key_prefix,
            base_url=settings.mediafusion_url,
            logger_name=__name__,
        )
        self.http_client = httpx.AsyncClient(
            timeout=30, proxy=settings.requests_proxy_url
        )

    def _generate_url(
        self,
        user_data: UserData,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> str:
        url = f"{self.base_url}/stream/{catalog_type}/{metadata.id}.json"
        if catalog_type == "series":
            url = f"{self.base_url}/stream/{catalog_type}/{metadata.id}:{season}:{episode}.json"
        upstream_user_data = UserData(
            api_password=settings.mediafusion_api_password,
            streaming_provider=user_data.streaming_provider,
            nudity_filter=user_data.nudity_filter,
            certification_filter=user_data.certification_filter,
        )
        self.http_client.headers.update(
            {"encoded_user_data": crypto_utils.encode_user_data(upstream_user_data)}
        )
        return url

    @StremioScraper.cache(ttl=MEDIAFUSION_SEARCH_TTL)
    @StremioScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MediaFusionMetaData,
        catalog_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[TorrentStreams]:
        return await super()._scrape_and_parse(
            user_data, metadata, catalog_type, season, episode
        )

    def get_adult_content_field(self, stream_data: Dict[str, Any]) -> str:
        return stream_data["description"]

    def get_scraper_name(self) -> str:
        return "Mediafusion"

    def parse_stream_title(self, stream: dict) -> tuple[dict, bool]:
        description = stream["description"].splitlines()
        torrent_name = description[0].removeprefix("📂 ").split(" ┈➤ ")[0]
        metadata = PTT.parse_title(torrent_name, True)
        source = stream["name"].split()[0].title()
        info_hash = stream.get("infoHash")
        if not info_hash:
            url = stream.get("url", "")
            if "/streaming_provider/" not in url:
                return {}, False
            info_hash = url.split("/")[6]
        is_cached = "⚡️" in stream["name"]

        metadata.update(
            {
                "source": source,
                "info_hash": info_hash,
                "size": convert_size_to_bytes(
                    self.extract_size_string(stream["description"])
                ),
                "torrent_name": torrent_name,
                "seeders": self.extract_seeders(stream["description"]),
                "filename": stream.get("behaviorHints", {}).get("filename"),
            }
        )

        return metadata, is_cached
