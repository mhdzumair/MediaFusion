"""
TorznabScraper - Direct Torznab API scraper for user-configured endpoints.

This scraper allows users to configure their own Torznab-compatible indexer
endpoints without requiring Prowlarr or Jackett as an intermediary.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Any
from xml.etree import ElementTree

import httpx
import PTT

from db.config import settings
from db.enums import TorrentType
from db.schemas import MetadataData, TorrentStreamData
from db.schemas.config import TorznabEndpointConfig
from scrapers.base_scraper import BaseScraper
from utils.network import CircuitBreaker
from utils.parser import convert_size_to_bytes, is_contain_18_plus_keywords
from utils.runtime_const import TORZNAB_SEARCH_TTL


class TorznabScraper(BaseScraper):
    """Scraper for direct Torznab API endpoints (without Prowlarr/Jackett)."""

    MOVIE_CATEGORY_IDS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070]
    SERIES_CATEGORY_IDS = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070]

    def __init__(self, endpoints: list[TorznabEndpointConfig]):
        """Initialize TorznabScraper with user-configured endpoints.

        Args:
            endpoints: List of Torznab endpoint configurations from user profile.
        """
        # Generate unique cache prefix based on endpoint URLs
        cache_prefix = self._generate_cache_prefix(endpoints)
        super().__init__(cache_key_prefix=cache_prefix, logger_name=__name__)

        self.endpoints = [e for e in endpoints if e.enabled]
        self.semaphore = asyncio.Semaphore(5)  # Limit concurrent requests
        self.endpoint_circuit_breakers: dict[str, CircuitBreaker] = {}

        # Initialize circuit breakers for each endpoint
        for endpoint in self.endpoints:
            self.endpoint_circuit_breakers[endpoint.id] = CircuitBreaker(
                failure_threshold=3,
                recovery_timeout=300,
                half_open_attempts=1,
            )

        self.http_client = httpx.AsyncClient(
            proxy=settings.requests_proxy_url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MediaFusion/1.0)",
            },
        )

    def _generate_cache_prefix(self, endpoints: list[TorznabEndpointConfig]) -> str:
        """Generate unique cache prefix based on endpoint URLs."""
        if not endpoints:
            return "torznab"
        # Create hash from sorted endpoint URLs for consistent caching
        urls = sorted(e.url for e in endpoints if e.enabled)
        url_hash = hashlib.md5(":".join(urls).encode()).hexdigest()[:8]
        return f"torznab:{url_hash}"

    @BaseScraper.cache(ttl=TORZNAB_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=5, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentStreamData]:
        """Scrape all configured Torznab endpoints and combine results."""
        if not self.endpoints:
            self.logger.debug("No Torznab endpoints configured")
            return []

        results = []
        processed_info_hashes: set[str] = set()

        # Create tasks for each endpoint
        tasks = []
        for endpoint in self.endpoints:
            tasks.append(
                self._query_endpoint(
                    endpoint,
                    metadata,
                    catalog_type,
                    season,
                    episode,
                    processed_info_hashes,
                )
            )

        # Run all endpoint queries concurrently
        endpoint_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(endpoint_results):
            if isinstance(result, Exception):
                self.logger.error(f"Error querying endpoint {self.endpoints[i].name}: {result}")
                self.metrics.record_error(f"endpoint_error_{self.endpoints[i].name}")
            elif result:
                results.extend(result)

        self.logger.info(f"TorznabScraper found {len(results)} streams for {metadata.title}")
        return results

    async def _query_endpoint(
        self,
        endpoint: TorznabEndpointConfig,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        processed_info_hashes: set[str],
    ) -> list[TorrentStreamData]:
        """Query a single Torznab endpoint."""
        circuit_breaker = self.endpoint_circuit_breakers.get(endpoint.id)
        if circuit_breaker and not circuit_breaker.is_closed():
            self.logger.debug(f"Skipping endpoint {endpoint.name} - circuit breaker is {circuit_breaker.state}")
            return []

        async with self.semaphore:
            try:
                # Add small delay to avoid hammering endpoints
                await asyncio.sleep(0.2)

                # Build search parameters
                params = self._build_search_params(metadata, catalog_type, season, episode, endpoint.categories)

                # Build headers (merge endpoint headers with default)
                headers = {"User-Agent": "Mozilla/5.0 (compatible; MediaFusion/1.0)"}
                if endpoint.headers:
                    headers.update(endpoint.headers)

                # Make request
                response = await self.http_client.get(
                    endpoint.url,
                    params=params,
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()

                # Parse XML response
                streams = await self._parse_torznab_response(
                    response.text,
                    endpoint,
                    metadata,
                    catalog_type,
                    season,
                    episode,
                    processed_info_hashes,
                )

                if circuit_breaker:
                    circuit_breaker.record_success()

                self.logger.info(f"Endpoint {endpoint.name} returned {len(streams)} streams")
                return streams

            except Exception as e:
                self.logger.error(f"Error querying endpoint {endpoint.name}: {e}")
                if circuit_breaker:
                    circuit_breaker.record_failure()
                return []

    def _build_search_params(
        self,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        categories: list[int] | None,
    ) -> dict[str, Any]:
        """Build Torznab API search parameters."""
        params: dict[str, Any] = {}

        # Determine search type and categories
        if catalog_type == "movie":
            params["t"] = "movie"
            cat_list = categories or self.MOVIE_CATEGORY_IDS
        else:
            params["t"] = "tvsearch"
            cat_list = categories or self.SERIES_CATEGORY_IDS
            if season is not None:
                params["season"] = season
            if episode is not None:
                params["ep"] = episode

        params["cat"] = ",".join(str(c) for c in cat_list)

        # Try IMDb ID first, fall back to title search
        imdb_id = metadata.get_imdb_id()
        if imdb_id:
            params["imdbid"] = imdb_id
        else:
            # Fall back to title-based search
            params["t"] = "search"
            search_query = metadata.title
            if catalog_type == "movie" and metadata.year:
                search_query = f"{metadata.title} {metadata.year}"
            elif catalog_type == "series" and season is not None:
                search_query = f"{metadata.title} S{season:02d}"
                if episode is not None:
                    search_query = f"{metadata.title} S{season:02d}E{episode:02d}"
            params["q"] = search_query

        return params

    async def _parse_torznab_response(
        self,
        xml_text: str,
        endpoint: TorznabEndpointConfig,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        processed_info_hashes: set[str],
    ) -> list[TorrentStreamData]:
        """Parse Torznab XML response into TorrentStreamData objects."""
        streams = []

        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as e:
            self.logger.error(f"Failed to parse XML from {endpoint.name}: {e}")
            return []

        # Find all items in the RSS feed
        channel = root.find("channel")
        if channel is None:
            self.logger.warning(f"No channel element found in response from {endpoint.name}")
            return []

        items = channel.findall("item")
        self.logger.debug(f"Found {len(items)} items in XML from {endpoint.name}")
        self.metrics.record_found_items(len(items))

        for item in items:
            try:
                stream = await self._parse_item(
                    item,
                    endpoint,
                    metadata,
                    catalog_type,
                    season,
                    episode,
                    processed_info_hashes,
                )
                if stream:
                    streams.append(stream)
            except Exception as e:
                self.metrics.record_error(str(e)[:50])
                self.logger.debug(f"Error parsing item from {endpoint.name}: {e}")
                continue

        return streams

    async def _parse_item(
        self,
        item: ElementTree.Element,
        endpoint: TorznabEndpointConfig,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        processed_info_hashes: set[str],
    ) -> TorrentStreamData | None:
        """Parse a single RSS item into TorrentStreamData."""
        # Extract basic info
        title_elem = item.find("title")
        if title_elem is None or not title_elem.text:
            self.metrics.record_skip("No title")
            return None
        title = title_elem.text

        # Check for adult content
        if is_contain_18_plus_keywords(title):
            self.metrics.record_skip("Adult content")
            return None

        # Get info_hash from torznab attributes
        info_hash = None
        magnet_link = None
        size = 0
        seeders = 0
        pub_date = None

        # Parse torznab:attr elements
        for attr in item.findall("{http://torznab.com/schemas/2015/feed}attr"):
            name = attr.get("name")
            value = attr.get("value")
            if name == "infohash" and value:
                info_hash = value.lower()
            elif name == "magneturl" and value:
                magnet_link = value
            elif name == "size" and value:
                try:
                    size = int(value)
                except ValueError:
                    size = convert_size_to_bytes(value) or 0
            elif name == "seeders" and value:
                try:
                    seeders = int(value)
                except ValueError:
                    pass

        # Also check standard RSS elements
        if not size:
            size_elem = item.find("size")
            if size_elem is not None and size_elem.text:
                try:
                    size = int(size_elem.text)
                except ValueError:
                    size = convert_size_to_bytes(size_elem.text) or 0

        # Get enclosure for size/link
        enclosure = item.find("enclosure")
        if enclosure is not None:
            if not size:
                try:
                    size = int(enclosure.get("length", 0))
                except ValueError:
                    pass

        # Get publish date
        pub_date_elem = item.find("pubDate")
        if pub_date_elem is not None and pub_date_elem.text:
            try:
                pub_date = datetime.strptime(pub_date_elem.text, "%a, %d %b %Y %H:%M:%S %z")
            except ValueError:
                pass

        # Extract info_hash from magnet link if not found
        if not info_hash and magnet_link:
            import re

            match = re.search(r"btih:([a-fA-F0-9]{40})", magnet_link)
            if match:
                info_hash = match.group(1).lower()

        # Also try to get info_hash from link element (some indexers put magnet there)
        if not info_hash:
            link_elem = item.find("link")
            if link_elem is not None and link_elem.text and "magnet:" in link_elem.text:
                import re

                match = re.search(r"btih:([a-fA-F0-9]{40})", link_elem.text, re.IGNORECASE)
                if match:
                    info_hash = match.group(1).lower()

        if not info_hash:
            self.metrics.record_skip("No info_hash")
            self.logger.debug(f"No info_hash found for item: {title[:50]}...")
            return None

        # Skip duplicates
        if info_hash in processed_info_hashes:
            self.metrics.record_skip("Duplicate")
            return None
        processed_info_hashes.add(info_hash)

        # Parse title with PTT
        parsed_data = PTT.parse_title(title, True)

        # Validate title and year
        if not self.validate_title_and_year(parsed_data, metadata, catalog_type, title, expected_ratio=70):
            return None

        # Build stream data
        meta_id = metadata.get_canonical_id()

        stream = TorrentStreamData(
            id=info_hash,
            info_hash=info_hash,
            meta_id=meta_id,
            name=title,  # Required field - stream display name
            size=size,
            seeders=seeders,
            created_at=pub_date.isoformat() if pub_date else None,
            source=endpoint.name,
            catalog=[f"torznab_{catalog_type}s"],
            torrent_type=TorrentType.PUBLIC,
            resolution=parsed_data.get("resolution"),
            codec=parsed_data.get("codec"),
            quality=parsed_data.get("quality"),
            audio=parsed_data.get("audio"),
            hdr=parsed_data.get("hdr"),
            uploader=None,
            languages=parsed_data.get("languages", []),
        )

        # Add episode info for series
        if catalog_type == "series":
            stream.season = season
            stream.episode = episode

        return stream

    def validate_response(self, response: dict[str, Any]) -> bool:
        """Validate the response structure."""
        return True  # XML parsing handles validation

    async def parse_response(
        self,
        response: dict[str, Any],
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentStreamData]:
        """Parse response - not used for Torznab (XML parsing done elsewhere)."""
        return []
