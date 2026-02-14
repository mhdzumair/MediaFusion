"""
TorBox Search API Scraper

Scrapes both torrent and Usenet content from TorBox's search API.
Requires a TorBox API token for authentication.

API Documentation: https://search-api.torbox.app/openapi.json
"""

import asyncio
import logging

import PTT
import httpx

from db.schemas import MetadataData, StreamFileData, TorrentStreamData, UserData, UsenetStreamData
from scrapers.base_scraper import BaseScraper
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords

logger = logging.getLogger(__name__)


class TorBoxSearchScraper(BaseScraper):
    """Scraper for TorBox's search API supporting both torrents and Usenet."""

    cache_key_prefix = "torbox_search"
    BASE_URL = "https://search-api.torbox.app"

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)
        self.semaphore = asyncio.Semaphore(10)

    async def _scrape_and_parse(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        """Main scrape method - returns torrents. Use scrape_usenet for Usenet content."""
        return await self._scrape_torrents(user_data, metadata, catalog_type, season, episode)

    async def _scrape_torrents(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        """Scrape torrent content from TorBox Search API."""
        # Get API token from user's TorBox configuration
        api_token = self._get_api_token(user_data)
        if not api_token:
            self.logger.debug("No TorBox API token configured, skipping TorBox search")
            return []

        headers = {"Authorization": f"Bearer {api_token}"}
        results = []

        try:
            # Try IMDb search first (more accurate)
            imdb_id = metadata.get_imdb_id()
            if imdb_id:
                imdb_results = await self._search_torrents_by_id(imdb_id, headers, catalog_type, season, episode)
                results.extend(imdb_results)

            # Also try title search for additional results
            title_results = await self._search_torrents_by_query(
                metadata.title, headers, catalog_type, season, episode, metadata
            )
            results.extend(title_results)

            # Deduplicate by info_hash
            seen_hashes = set()
            unique_results = []
            for stream in results:
                if stream.info_hash not in seen_hashes:
                    seen_hashes.add(stream.info_hash)
                    unique_results.append(stream)

            self.logger.info(f"TorBox Search found {len(unique_results)} unique torrents for {metadata.title}")
            return unique_results

        except httpx.HTTPError as e:
            self.metrics.record_error("http_error")
            self.logger.error(f"HTTP error during TorBox search: {e}")
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"Error during TorBox search: {e}")

        return []

    async def scrape_usenet(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[UsenetStreamData]:
        """Scrape Usenet content from TorBox Search API."""
        # Get API token from user's TorBox configuration
        api_token = self._get_api_token(user_data)
        if not api_token:
            self.logger.debug("No TorBox API token configured, skipping TorBox Usenet search")
            return []

        headers = {"Authorization": f"Bearer {api_token}"}
        results = []

        try:
            # Try IMDb search first (more accurate)
            imdb_id = metadata.get_imdb_id()
            if imdb_id:
                imdb_results = await self._search_usenet_by_id(imdb_id, headers, catalog_type, season, episode)
                results.extend(imdb_results)

            # Also try title search for additional results
            title_results = await self._search_usenet_by_query(
                metadata.title, headers, catalog_type, season, episode, metadata
            )
            results.extend(title_results)

            # Deduplicate by nzb_guid
            seen_guids = set()
            unique_results = []
            for stream in results:
                if stream.nzb_guid not in seen_guids:
                    seen_guids.add(stream.nzb_guid)
                    unique_results.append(stream)

            self.logger.info(f"TorBox Search found {len(unique_results)} unique Usenet results for {metadata.title}")
            return unique_results

        except httpx.HTTPError as e:
            self.metrics.record_error("http_error")
            self.logger.error(f"HTTP error during TorBox Usenet search: {e}")
        except Exception as e:
            self.metrics.record_error("unexpected_error")
            self.logger.exception(f"Error during TorBox Usenet search: {e}")

        return []

    def _get_api_token(self, user_data: UserData) -> str | None:
        """Extract TorBox API token from user data.

        Checks both:
        - streaming_providers (list) - new multi-debrid support
        - streaming_provider (single) - legacy support
        """
        if not user_data:
            return None

        # First check streaming_providers list (new multi-debrid support)
        if user_data.streaming_providers:
            for sp in user_data.streaming_providers:
                if sp.service == "torbox" and sp.token:
                    return sp.token

        # Fallback to legacy single streaming_provider
        if user_data.streaming_provider:
            sp = user_data.streaming_provider
            if sp.service == "torbox" and sp.token:
                return sp.token

        return None

    async def _search_torrents_by_id(
        self,
        imdb_id: str,
        headers: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[TorrentStreamData]:
        """Search torrents by IMDb ID."""
        url = f"{self.BASE_URL}/torrents/{imdb_id}"
        params = {
            "metadata": "true",
            "check_cache": "false",
            "check_owned": "false",
        }

        if catalog_type == "series" and season is not None:
            params["season"] = season
            if episode is not None:
                params["episode"] = episode

        try:
            response = await self.http_client.get(url, headers=headers, params=params, timeout=15)

            # Handle non-success status codes gracefully
            if response.status_code in (404, 418):
                # 404 = Not found, 418 = Could not find metadata
                self.logger.debug(f"TorBox ID search returned {response.status_code} for {imdb_id}")
                return []

            response.raise_for_status()
            data = response.json()

            if not data.get("data") or not data["data"].get("torrents"):
                return []

            torrents = data["data"]["torrents"]
            self.metrics.record_found_items(len(torrents))

            return await self._parse_torrent_results(torrents, catalog_type, season, episode)

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 418):
                return []
            self.logger.warning(f"TorBox torrent search by ID failed: {e}")
            raise

    async def _search_torrents_by_query(
        self,
        query: str,
        headers: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> list[TorrentStreamData]:
        """Search torrents by title query."""
        # Build search query
        search_query = query
        if catalog_type == "series" and season is not None:
            search_query = f"{query} S{season:02d}"
            if episode is not None:
                search_query = f"{query} S{season:02d}E{episode:02d}"

        url = f"{self.BASE_URL}/torrents/search/{search_query}"
        params = {
            "metadata": "true",
            "check_cache": "false",
            "check_owned": "false",
        }

        try:
            response = await self.http_client.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data.get("data") or not data["data"].get("torrents"):
                return []

            torrents = data["data"]["torrents"]
            self.metrics.record_found_items(len(torrents))

            return await self._parse_torrent_results(torrents, catalog_type, season, episode, metadata)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            raise

    async def _search_usenet_by_id(
        self,
        imdb_id: str,
        headers: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[UsenetStreamData]:
        """Search Usenet by IMDb ID."""
        url = f"{self.BASE_URL}/usenet/{imdb_id}"
        params = {
            "metadata": "true",
            "check_cache": "false",
            "check_owned": "false",
        }

        if catalog_type == "series" and season is not None:
            params["season"] = season
            if episode is not None:
                params["episode"] = episode

        try:
            response = await self.http_client.get(url, headers=headers, params=params, timeout=15)

            # Handle non-success status codes gracefully
            if response.status_code in (404, 418):
                # 404 = Not found, 418 = Could not find metadata
                self.logger.debug(f"TorBox Usenet ID search returned {response.status_code} for {imdb_id}")
                return []

            response.raise_for_status()
            data = response.json()

            if not data.get("data") or not data["data"].get("nzbs"):
                return []

            nzbs = data["data"]["nzbs"]
            self.metrics.record_found_items(len(nzbs))

            return await self._parse_usenet_results(nzbs, catalog_type, season, episode)

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 418):
                return []
            self.logger.warning(f"TorBox Usenet search by ID failed: {e}")
            raise

    async def _search_usenet_by_query(
        self,
        query: str,
        headers: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> list[UsenetStreamData]:
        """Search Usenet by title query."""
        # Build search query
        search_query = query
        if catalog_type == "series" and season is not None:
            search_query = f"{query} S{season:02d}"
            if episode is not None:
                search_query = f"{query} S{season:02d}E{episode:02d}"

        url = f"{self.BASE_URL}/usenet/search/{search_query}"
        params = {
            "metadata": "true",
            "check_cache": "false",
            "check_owned": "false",
        }

        try:
            response = await self.http_client.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data.get("data") or not data["data"].get("nzbs"):
                return []

            nzbs = data["data"]["nzbs"]
            self.metrics.record_found_items(len(nzbs))

            return await self._parse_usenet_results(nzbs, catalog_type, season, episode, metadata)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            raise

    async def _parse_torrent_results(
        self,
        torrents: list[dict],
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> list[TorrentStreamData]:
        """Parse torrent results into TorrentStreamData objects."""
        results = []

        for torrent in torrents:
            try:
                stream = await self._parse_single_torrent(torrent, catalog_type, season, episode, metadata)
                if stream:
                    results.append(stream)
            except Exception as e:
                self.metrics.record_error("parse_error")
                self.logger.debug(f"Error parsing torrent: {e}")

        return results

    async def _parse_single_torrent(
        self,
        torrent: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> TorrentStreamData | None:
        """Parse a single torrent result."""
        raw_title = torrent.get("raw_title") or torrent.get("name", "")
        info_hash = torrent.get("hash", "").lower()

        if not raw_title or not info_hash:
            return None

        # Filter adult content
        if is_contain_18_plus_keywords(raw_title):
            self.metrics.record_skip("Adult content")
            return None

        # Parse title
        parsed = PTT.parse_title(raw_title, True)

        # Validate title match if metadata provided
        if metadata:
            max_ratio = calculate_max_similarity_ratio(parsed.get("title", ""), metadata.title, metadata.aka_titles)
            if max_ratio < 80:
                self.metrics.record_skip("Title mismatch")
                return None

        # Build files list for series
        files = []
        if catalog_type == "series":
            seasons = parsed.get("seasons", [])
            episodes = parsed.get("episodes", [])

            if seasons and episodes:
                for ep in episodes:
                    files.append(
                        StreamFileData(
                            file_index=0,
                            filename="",
                            file_type="video",
                            season_number=seasons[0],
                            episode_number=ep,
                        )
                    )
            elif seasons:
                # Season pack
                for s in seasons:
                    files.append(
                        StreamFileData(
                            file_index=0,
                            filename="",
                            file_type="video",
                            season_number=s,
                            episode_number=1,
                        )
                    )

            if not files:
                self.metrics.record_skip("Missing episode info")
                return None

        # Get size
        size = torrent.get("size", 0)
        if isinstance(size, str):
            try:
                size = int(size)
            except ValueError:
                size = 0

        stream = TorrentStreamData(
            info_hash=info_hash,
            meta_id=metadata.external_id if metadata else None,
            name=raw_title,
            size=size,
            source="TorBox Search",
            seeders=torrent.get("seeders", 0),
            announce_list=[],
            files=files,  # Empty list for movies, populated list for series
            # Quality attributes from PTT
            resolution=parsed.get("resolution"),
            codec=parsed.get("codec"),
            quality=parsed.get("quality"),
            bit_depth=parsed.get("bit_depth"),
            release_group=parsed.get("group"),
            audio_formats=parsed.get("audio", []) if isinstance(parsed.get("audio"), list) else [],
            channels=parsed.get("channels", []) if isinstance(parsed.get("channels"), list) else [],
            hdr_formats=parsed.get("hdr", []) if isinstance(parsed.get("hdr"), list) else [],
            languages=parsed.get("languages", []),
            is_proper=parsed.get("proper", False),
            is_repack=parsed.get("repack", False),
        )

        self.metrics.record_processed_item()
        self.metrics.record_quality(stream.quality)
        self.metrics.record_source(stream.source)

        return stream

    async def _parse_usenet_results(
        self,
        nzbs: list[dict],
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> list[UsenetStreamData]:
        """Parse Usenet results into UsenetStreamData objects."""
        results = []

        for nzb in nzbs:
            try:
                stream = await self._parse_single_usenet(nzb, catalog_type, season, episode, metadata)
                if stream:
                    results.append(stream)
            except Exception as e:
                self.metrics.record_error("parse_error")
                self.logger.debug(f"Error parsing NZB: {e}")

        return results

    async def _parse_single_usenet(
        self,
        nzb: dict,
        catalog_type: str,
        season: int = None,
        episode: int = None,
        metadata: MetadataData = None,
    ) -> UsenetStreamData | None:
        """Parse a single Usenet/NZB result."""
        raw_title = nzb.get("raw_title") or nzb.get("name", "")
        # TorBox uses 'hash' as the unique identifier for NZBs
        nzb_guid = nzb.get("hash") or nzb.get("id") or nzb.get("guid", "")
        # TorBox Search provides direct NZB download URL in the 'nzb' field
        nzb_url = nzb.get("nzb")

        if not raw_title or not nzb_guid:
            self.logger.debug(f"Skipping NZB - missing title or guid: {nzb.keys()}")
            return None

        # Filter adult content
        if is_contain_18_plus_keywords(raw_title):
            self.metrics.record_skip("Adult content")
            return None

        # Parse title
        parsed = PTT.parse_title(raw_title, True)

        # Validate title match if metadata provided
        if metadata:
            max_ratio = calculate_max_similarity_ratio(parsed.get("title", ""), metadata.title, metadata.aka_titles)
            if max_ratio < 80:
                self.metrics.record_skip("Title mismatch")
                return None

        # Build files list for series
        files = []
        if catalog_type == "series":
            seasons = parsed.get("seasons", [])
            episodes = parsed.get("episodes", [])

            if seasons and episodes:
                for ep in episodes:
                    files.append(
                        {
                            "filename": "",
                            "size": 0,
                            "index": 0,
                            "season_number": seasons[0],
                            "episode_number": ep,
                        }
                    )
            elif seasons:
                for s in seasons:
                    files.append(
                        {
                            "filename": "",
                            "size": 0,
                            "index": 0,
                            "season_number": s,
                            "episode_number": 1,
                        }
                    )

            if not files:
                self.metrics.record_skip("Missing episode info")
                return None

        # Get size
        size = nzb.get("size", 0)
        if isinstance(size, str):
            try:
                size = int(size)
            except ValueError:
                size = 0

        # Convert files to StreamFileData objects for series
        file_data = []
        if files:
            for f in files:
                file_data.append(
                    StreamFileData(
                        file_index=f.get("index", 0),
                        filename=f.get("filename", ""),
                        size=f.get("size", 0),
                        file_type="video",
                        season_number=f.get("season_number"),
                        episode_number=f.get("episode_number"),
                    )
                )

        stream = UsenetStreamData(
            nzb_guid=str(nzb_guid),
            nzb_url=nzb_url,  # TorBox Search provides direct NZB download URL
            name=raw_title,
            meta_id=metadata.get_canonical_id() if metadata else "",
            size=size,
            indexer="TorBox Search",
            source="TorBox Search",
            # Quality attributes
            resolution=parsed.get("resolution"),
            codec=parsed.get("codec"),
            quality=parsed.get("quality"),
            bit_depth=parsed.get("bit_depth"),
            release_group=parsed.get("group"),
            audio_formats=parsed.get("audio", []) if isinstance(parsed.get("audio"), list) else [],
            hdr_formats=parsed.get("hdr", []) if isinstance(parsed.get("hdr"), list) else [],
            languages=parsed.get("languages", []),
            files=file_data,
        )

        self.metrics.record_processed_item()
        self.metrics.record_quality(stream.quality)
        self.metrics.record_source(stream.source)

        return stream


# Singleton instance
torbox_search_scraper = TorBoxSearchScraper()
