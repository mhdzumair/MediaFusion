import re
from datetime import timedelta
from typing import Any

import httpx
import PTT

from db.config import settings
from db.schemas import MetadataData, TorrentStreamData, UserData
from scrapers.stremio_addons import StremioScraper
from utils import const
from utils.parser import (
    convert_size_to_bytes,
)
from utils.runtime_const import TORRENTIO_SEARCH_TTL

SUPPORTED_DEBRID_SERVICE = {
    "realdebrid",
    "premiumize",
    "alldebrid",
    "debridlink",
    "offcloud",
    "torbox",
}


def _is_torrentio_error_placeholder_url(url: str | None) -> bool:
    """True when Torrentio returns a dummy video URL instead of a real magnet/debrid link."""
    if not url:
        return False
    u = url.lower()
    # e.g. https://torrentio.strem.fun/videos/failed_access_v2.mp4
    if "failed_access" in u:
        return True
    if "/videos/" in u and u.endswith(".mp4") and ("error" in u or "unavailable" in u or "not_cached" in u):
        return True
    # Any plugin-hosted .mp4 under /videos/ is a status placeholder, not a torrent URL
    if "/videos/" in u and u.endswith(".mp4"):
        return True
    return False


def _torrentio_stream_url_candidates(stream: dict) -> list[str]:
    """Collect URL-like fields Stremio / Torrentio may use for the stream target."""
    out: list[str] = []
    for key in ("url", "externalUrl", "videoUrl"):
        val = stream.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
    sources = stream.get("sources")
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    return out


class TorrentioScraper(StremioScraper):
    cache_key_prefix = "torrentio"

    def __init__(self):
        super().__init__(
            cache_key_prefix=self.cache_key_prefix,
            base_url=settings.torrentio_url,
            logger_name=__name__,
        )
        self.http_client = httpx.AsyncClient(
            timeout=30,
            proxy=settings.requests_proxy_url,
            headers=const.UA_HEADER,
        )

    def _generate_url(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> str | None:
        # Torrentio requires IMDb ID
        imdb_id = metadata.get_imdb_id()
        if not imdb_id:
            return None

        primary_provider = user_data.get_primary_provider()
        user_data_str = (
            f"/{primary_provider.service}={primary_provider.token.rstrip('=')}"
            if primary_provider and primary_provider.service in SUPPORTED_DEBRID_SERVICE
            else ""
        )
        url = f"{self.base_url}{user_data_str}/stream/{catalog_type}/{imdb_id}.json"
        if catalog_type == "series":
            url = f"{self.base_url}{user_data_str}/stream/{catalog_type}/{imdb_id}:{season}:{episode}.json"
        return url

    @StremioScraper.cache(ttl=TORRENTIO_SEARCH_TTL)
    @StremioScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        if not metadata.get_imdb_id():
            self.logger.info(
                "Skipping Torrentio: no IMDb ID for %r (catalog_type=%s)",
                metadata.title,
                catalog_type,
            )
            self.metrics.record_skip("no_imdb_id")
            return []
        return await super()._scrape_and_parse(user_data, metadata, catalog_type, season, episode)

    def get_adult_content_field(self, stream_data: dict[str, Any]) -> str:
        title = stream_data.get("title")
        return str(title) if title is not None else ""

    def get_scraper_name(self) -> str:
        return "Torrentio"

    def parse_stream_title(self, stream: dict) -> tuple[dict, bool]:
        try:
            for candidate in _torrentio_stream_url_candidates(stream):
                if _is_torrentio_error_placeholder_url(candidate):
                    return None, False

            descriptions = stream.get("title")
            if not descriptions or not str(descriptions).strip():
                return None, False
            torrent_name = str(descriptions).splitlines()[0]
            metadata = PTT.parse_title(torrent_name, True)
            name_raw = stream.get("name")
            if not name_raw or not str(name_raw).strip():
                return None, False
            name_first_line = str(name_raw).splitlines()[0].split()
            if not name_first_line:
                return None, False
            source = name_first_line[-1]
            info_hash = stream.get("infoHash")
            if not info_hash:
                last_url = ""
                for candidate in _torrentio_stream_url_candidates(stream):
                    if _is_torrentio_error_placeholder_url(candidate):
                        return None, False
                    last_url = candidate
                    match = re.search(r"\b([a-fA-F0-9]{40})\b", candidate)
                    if match:
                        info_hash = match.group(1)
                        break
                if not info_hash:
                    self.logger.debug(
                        "Skipping Torrentio stream: no info_hash (url=%r, keys=%s)",
                        last_url,
                        list(stream.keys()),
                    )
                    return None, False

            is_cached = "+" in str(name_raw)
            metadata.update(
                {
                    "source": source,
                    "info_hash": info_hash,
                    "size": convert_size_to_bytes(self.extract_size_string(descriptions)),
                    "torrent_name": torrent_name,
                    "seeders": self.extract_seeders(descriptions),
                    "filename": stream.get("behaviorHints", {}).get("filename"),
                }
            )
            return metadata, is_cached
        except Exception as e:
            self.metrics.record_error("title_parsing_error")
            raise e
