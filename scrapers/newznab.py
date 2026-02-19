"""Newznab API scraper for NZB indexers.

This scraper implements the Newznab API standard for searching NZB indexers
like NZBgeek, NZBFinder, DrunkenSlug, etc.

Newznab API Reference:
- /api?t=movie&imdbid={id} - Movie search by IMDb
- /api?t=tvsearch&imdbid={id}&season={s}&ep={e} - TV search
- /api?t=search&q={query} - General search
- /api?t=get&id={guid} - Fetch NZB content
"""

import hashlib
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from xml.etree import ElementTree as ET

import httpx
import PTT

from db.schemas import MetadataData, StreamFileData, UserData
from db.schemas.config import NewznabIndexerConfig
from db.schemas.media import UsenetStreamData
from scrapers.base_scraper import BaseScraper, ScraperMetrics
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords

logger = logging.getLogger(__name__)


class NewznabScraper(BaseScraper):
    """Newznab-compatible API scraper for NZB indexers.

    Supports standard Newznab API endpoints for movie and TV searches.
    """

    # Standard Newznab categories
    MOVIE_CATEGORIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]
    TV_CATEGORIES = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080]

    # Newznab XML namespaces
    NEWZNAB_NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}

    def __init__(self, indexers: list[NewznabIndexerConfig]):
        """Initialize the Newznab scraper.

        Args:
            indexers: List of configured Newznab indexers
        """
        super().__init__(cache_key_prefix="newznab", logger_name=__name__)
        self.indexers = [i for i in indexers if i.enabled]
        self.metrics = ScraperMetrics("newznab")

    async def _scrape_and_parse(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[UsenetStreamData]:
        """Scrape NZB indexers and parse results.

        Args:
            user_data: User configuration
            metadata: Media metadata
            catalog_type: 'movie' or 'series'
            season: Season number for series
            episode: Episode number for series

        Returns:
            List of UsenetStreamData objects
        """
        if not self.indexers:
            self.logger.warning("No Newznab indexers configured")
            return []

        results: list[UsenetStreamData] = []
        processed_guids: set[str] = set()

        for indexer in self.indexers:
            try:
                if catalog_type == "movie":
                    async for stream in self._search_movie(indexer, metadata, processed_guids):
                        results.append(stream)
                elif catalog_type == "series":
                    async for stream in self._search_series(indexer, metadata, season, episode, processed_guids):
                        results.append(stream)
            except Exception as e:
                self.logger.error(f"Error scraping indexer {indexer.name}: {e}")
                self.metrics.record_indexer_error(indexer.name, str(e))

        return results

    async def _search_movie(
        self,
        indexer: NewznabIndexerConfig,
        metadata: MetadataData,
        processed_guids: set[str],
    ) -> AsyncGenerator[UsenetStreamData, None]:
        """Search for movie NZBs.

        Args:
            indexer: Indexer configuration
            metadata: Movie metadata
            processed_guids: Set of already processed GUIDs

        Yields:
            UsenetStreamData objects
        """
        imdb_id = metadata.get_imdb_id()
        categories = indexer.movie_categories or self.MOVIE_CATEGORIES

        # Try IMDb search first
        if imdb_id:
            params = {
                "t": "movie",
                "imdbid": imdb_id.lstrip("tt"),
                "cat": ",".join(str(c) for c in categories),
            }
            async for stream in self._execute_search(indexer, params, metadata, "movie", processed_guids):
                yield stream

        # Fallback to title search
        params = {
            "t": "search",
            "q": f"{metadata.title} {metadata.year}",
            "cat": ",".join(str(c) for c in categories),
        }
        async for stream in self._execute_search(indexer, params, metadata, "movie", processed_guids):
            yield stream

    async def _search_series(
        self,
        indexer: NewznabIndexerConfig,
        metadata: MetadataData,
        season: int,
        episode: int,
        processed_guids: set[str],
    ) -> AsyncGenerator[UsenetStreamData, None]:
        """Search for series NZBs.

        Args:
            indexer: Indexer configuration
            metadata: Series metadata
            season: Season number
            episode: Episode number
            processed_guids: Set of already processed GUIDs

        Yields:
            UsenetStreamData objects
        """
        imdb_id = metadata.get_imdb_id()
        tvdb_id = metadata.get_tvdb_id()
        categories = indexer.tv_categories or self.TV_CATEGORIES

        # Try IMDb search first
        if imdb_id:
            params = {
                "t": "tvsearch",
                "imdbid": imdb_id.lstrip("tt"),
                "season": str(season),
                "ep": str(episode),
                "cat": ",".join(str(c) for c in categories),
            }
            async for stream in self._execute_search(
                indexer, params, metadata, "series", processed_guids, season, episode
            ):
                yield stream

        # Try TVDB search
        if tvdb_id:
            params = {
                "t": "tvsearch",
                "tvdbid": tvdb_id,
                "season": str(season),
                "ep": str(episode),
                "cat": ",".join(str(c) for c in categories),
            }
            async for stream in self._execute_search(
                indexer, params, metadata, "series", processed_guids, season, episode
            ):
                yield stream

        # Fallback to title search
        search_queries = [
            f"{metadata.title} S{season:02d}E{episode:02d}",
            f"{metadata.title} Season {season}",
        ]

        for query in search_queries:
            params = {
                "t": "search",
                "q": query,
                "cat": ",".join(str(c) for c in categories),
            }
            async for stream in self._execute_search(
                indexer, params, metadata, "series", processed_guids, season, episode
            ):
                yield stream

    async def _execute_search(
        self,
        indexer: NewznabIndexerConfig,
        params: dict,
        metadata: MetadataData,
        catalog_type: str,
        processed_guids: set[str],
        season: int = None,
        episode: int = None,
    ) -> AsyncGenerator[UsenetStreamData, None]:
        """Execute a search against a Newznab indexer.

        Args:
            indexer: Indexer configuration
            params: Search parameters
            metadata: Media metadata
            catalog_type: 'movie' or 'series'
            processed_guids: Set of already processed GUIDs
            season: Season number for series
            episode: Episode number for series

        Yields:
            UsenetStreamData objects
        """
        # Build URL
        params["apikey"] = indexer.api_key
        params["o"] = "json"  # Request JSON output

        if indexer.use_zyclops:
            # Proxy through Zyclops health check proxy
            url = "https://zyclops.elfhosted.com/api"
            params["target"] = f"{str(indexer.url).rstrip('/')}/api"
            params["single_ip"] = "true"
            if indexer.zyclops_backbones:
                params["backbone"] = ",".join(indexer.zyclops_backbones)
        else:
            url = f"{str(indexer.url).rstrip('/')}/api"

        try:
            response = await self.http_client.get(url, params=params, timeout=30)
            response.raise_for_status()

            # Parse response (can be JSON or XML)
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                data = response.json()
                items = self._parse_json_response(data)
            else:
                items = self._parse_xml_response(response.text)

            self.metrics.record_found_items(len(items))
            self.metrics.record_indexer_success(indexer.name, len(items))

            for item in items:
                stream = await self._parse_item(item, indexer, metadata, catalog_type, processed_guids, season, episode)
                if stream:
                    yield stream

        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error from {indexer.name}: {e.response.status_code}")
            self.metrics.record_indexer_error(indexer.name, f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            self.logger.error(f"Request error for {indexer.name}: {e}")
            self.metrics.record_indexer_error(indexer.name, "connection_error")
        except Exception as e:
            self.logger.exception(f"Error searching {indexer.name}: {e}")
            self.metrics.record_indexer_error(indexer.name, str(e))

    def _parse_json_response(self, data: dict) -> list[dict]:
        """Parse JSON response from Newznab API.

        Args:
            data: JSON response data

        Returns:
            List of item dictionaries with normalized fields
        """
        # Handle different JSON structures
        raw_items = []
        if "channel" in data:
            raw_items = data.get("channel", {}).get("item", [])
        elif "item" in data:
            raw_items = data.get("item", [])
        elif "rss" in data:
            raw_items = data.get("rss", {}).get("channel", {}).get("item", [])

        # Ensure it's a list (single item might not be wrapped in list)
        if isinstance(raw_items, dict):
            raw_items = [raw_items]

        # Normalize items to have consistent field names
        items = []
        for raw in raw_items:
            item = {
                "title": raw.get("title", ""),
                "guid": raw.get("guid", ""),
                "link": raw.get("link", ""),
                "pubDate": raw.get("pubDate", raw.get("pub_date", "")),
                "description": raw.get("description", ""),
                "comments": raw.get("comments", ""),
            }

            # Handle guid that might be a dict with #text or @attributes
            if isinstance(item["guid"], dict):
                item["guid"] = item["guid"].get("#text", "") or item["guid"].get("text", "")

            # Get size from various possible locations
            size = 0
            if "size" in raw:
                try:
                    size = int(raw["size"])
                except (ValueError, TypeError):
                    pass

            # Try enclosure
            enclosure = raw.get("enclosure", {})
            if isinstance(enclosure, dict):
                if not size and enclosure.get("@attributes", {}).get("length"):
                    try:
                        size = int(enclosure["@attributes"]["length"])
                    except (ValueError, TypeError):
                        pass
                elif not size and enclosure.get("length"):
                    try:
                        size = int(enclosure["length"])
                    except (ValueError, TypeError):
                        pass
                # Get URL from enclosure
                enc_url = enclosure.get("@attributes", {}).get("url") or enclosure.get("url")
                if enc_url:
                    item["enclosure_url"] = enc_url

            item["size"] = size

            # Parse newznab:attr - might be list or dict
            attrs = {}
            attr_data = raw.get("newznab:attr", raw.get("attr", []))
            if isinstance(attr_data, dict):
                attr_data = [attr_data]
            for attr in attr_data:
                if isinstance(attr, dict):
                    # Handle @attributes style (from XML-to-JSON conversion)
                    if "@attributes" in attr:
                        name = attr["@attributes"].get("name")
                        value = attr["@attributes"].get("value")
                    else:
                        name = attr.get("name", attr.get("@name"))
                        value = attr.get("value", attr.get("@value"))
                    if name and value:
                        attrs[name] = value

            item["attributes"] = attrs

            # If size still not set, try from attributes
            if not item["size"] and attrs.get("size"):
                try:
                    item["size"] = int(attrs["size"])
                except (ValueError, TypeError):
                    pass

            items.append(item)

        return items

    def _parse_xml_response(self, xml_text: str) -> list[dict]:
        """Parse XML response from Newznab API.

        Args:
            xml_text: XML response text

        Returns:
            List of item dictionaries
        """
        items = []
        try:
            root = ET.fromstring(xml_text)

            # Handle namespace - try multiple common variations
            namespaces = [
                {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"},
                {"newznab": "http://www.newznab.com/DTD/2010/feeds/"},
                {},  # No namespace fallback
            ]

            for item_elem in root.findall(".//item"):
                item = {
                    "title": self._get_element_text(item_elem, "title"),
                    "guid": self._get_element_text(item_elem, "guid"),
                    "link": self._get_element_text(item_elem, "link"),
                    "pubDate": self._get_element_text(item_elem, "pubDate"),
                    "description": self._get_element_text(item_elem, "description"),
                    "comments": self._get_element_text(item_elem, "comments"),
                }

                # Parse enclosure for size and URL
                enclosure = item_elem.find("enclosure")
                if enclosure is not None:
                    length = enclosure.get("length")
                    if length:
                        try:
                            item["size"] = int(length)
                        except (ValueError, TypeError):
                            pass
                    # Also get enclosure URL as backup for nzb_url
                    enc_url = enclosure.get("url")
                    if enc_url:
                        item["enclosure_url"] = enc_url

                # Parse Newznab attributes - try with different namespaces
                attrs = {}
                for ns in namespaces:
                    if ns:
                        attr_elements = item_elem.findall("newznab:attr", ns)
                    else:
                        # Try without namespace (some indexers use plain attr tags)
                        attr_elements = item_elem.findall(".//{http://www.newznab.com/DTD/2010/feeds/attributes/}attr")
                        if not attr_elements:
                            # Try finding any element ending with 'attr'
                            attr_elements = [e for e in item_elem.iter() if e.tag.endswith("}attr") or e.tag == "attr"]

                    for attr in attr_elements:
                        name = attr.get("name")
                        value = attr.get("value")
                        if name and value:
                            attrs[name] = value

                    if attrs:
                        break  # Found attributes, stop trying other namespaces

                item["attributes"] = attrs

                # If size wasn't in enclosure, try to get from attributes
                if not item.get("size") and attrs.get("size"):
                    try:
                        item["size"] = int(attrs["size"])
                    except (ValueError, TypeError):
                        pass

                items.append(item)

        except ET.ParseError as e:
            self.logger.error(f"XML parse error: {e}")

        return items

    @staticmethod
    def _get_element_text(parent: ET.Element, tag: str) -> str | None:
        """Get text content of an XML element."""
        elem = parent.find(tag)
        return elem.text if elem is not None else None

    async def _parse_item(
        self,
        item: dict,
        indexer: NewznabIndexerConfig,
        metadata: MetadataData,
        catalog_type: str,
        processed_guids: set[str],
        season: int = None,
        episode: int = None,
    ) -> UsenetStreamData | None:
        """Parse a single search result item.

        Args:
            item: Item dictionary from search results
            indexer: Indexer configuration
            metadata: Media metadata
            catalog_type: 'movie' or 'series'
            processed_guids: Set of already processed GUIDs
            season: Season number for series
            episode: Episode number for series

        Returns:
            UsenetStreamData or None if item should be skipped
        """
        try:
            title = item.get("title", "")
            if not title:
                return None

            # Check for adult content
            if is_contain_18_plus_keywords(title):
                self.metrics.record_skip("Adult content")
                return None

            # Get GUID
            guid = item.get("guid", "")
            if isinstance(guid, dict):
                guid = guid.get("#text", "") or guid.get("text", "")

            if not guid or guid in processed_guids:
                self.metrics.record_skip("Duplicate GUID")
                return None

            # Parse title with PTT
            parsed = PTT.parse_title(title, True)

            # Validate title similarity
            max_ratio = calculate_max_similarity_ratio(parsed.get("title", ""), metadata.title, metadata.aka_titles)
            if max_ratio < 85:
                self.metrics.record_skip("Title mismatch")
                return None

            # Validate year for movies
            if catalog_type == "movie":
                parsed_year = parsed.get("year")
                if parsed_year and parsed_year != metadata.year:
                    self.metrics.record_skip("Year mismatch")
                    return None

            # Get attributes
            attrs = item.get("attributes", {})

            # Get size
            size = item.get("size", 0)
            if not size:
                size = int(attrs.get("size", 0))

            # Get grabs count
            grabs = int(attrs.get("grabs", 0)) if attrs.get("grabs") else None

            # Get group
            group = attrs.get("group")

            # Get poster/uploader
            poster = attrs.get("poster")

            # Get password status
            is_passworded = attrs.get("password") == "1"

            # Parse date
            pub_date_str = item.get("pubDate")
            posted_at = None
            if pub_date_str:
                try:
                    # Try common date formats
                    for fmt in [
                        "%a, %d %b %Y %H:%M:%S %z",
                        "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S%z",
                    ]:
                        try:
                            posted_at = datetime.strptime(pub_date_str, fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # Generate unique hash for this NZB
            nzb_hash = hashlib.sha256(f"{indexer.url}:{guid}".encode()).hexdigest()[:40]

            # Build NZB URL
            nzb_url = item.get("link")
            if not nzb_url:
                nzb_url = f"{str(indexer.url).rstrip('/')}/api?t=get&id={guid}&apikey={indexer.api_key}"

            # Build files list for series
            files: list[StreamFileData] = []
            if catalog_type == "series":
                # Get season/episode from parsed data or attributes
                parsed_seasons = parsed.get("seasons", [])
                parsed_episodes = parsed.get("episodes", [])

                if parsed_seasons and parsed_episodes:
                    for s in parsed_seasons:
                        for e in parsed_episodes:
                            files.append(
                                StreamFileData(
                                    file_index=0,
                                    filename=title,
                                    size=size,
                                    file_type="video",
                                    season_number=s,
                                    episode_number=e,
                                )
                            )
                elif season and episode:
                    # Use requested season/episode
                    files.append(
                        StreamFileData(
                            file_index=0,
                            filename=title,
                            size=size,
                            file_type="video",
                            season_number=season,
                            episode_number=episode,
                        )
                    )

                if not files:
                    self.metrics.record_skip("Missing episode info")
                    return None

            # Create stream data
            stream = UsenetStreamData(
                nzb_guid=nzb_hash,
                nzb_url=nzb_url,
                name=title,
                size=size,
                indexer=indexer.name,
                source=indexer.name,
                group_name=group,
                poster=poster,
                posted_at=posted_at,
                is_passworded=is_passworded,
                grabs=grabs,
                meta_id=metadata.external_id,
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
                # Release flags
                is_remastered=parsed.get("remastered", False),
                is_upscaled=parsed.get("upscaled", False),
                is_proper=parsed.get("proper", False),
                is_repack=parsed.get("repack", False),
                is_extended=parsed.get("extended", False),
                is_complete=parsed.get("complete", False),
                is_dubbed=parsed.get("dubbed", False),
                is_subbed=parsed.get("subbed", False),
                files=files,
            )

            processed_guids.add(guid)
            self.metrics.record_processed_item()
            self.metrics.record_quality(stream.quality)
            self.metrics.record_source(indexer.name)

            return stream

        except Exception as e:
            self.logger.exception(f"Error parsing item: {e}")
            self.metrics.record_error("parse_error")
            return None

    async def fetch_nzb_content(self, indexer: NewznabIndexerConfig, guid: str) -> bytes | None:
        """Fetch NZB file content from indexer.

        Args:
            indexer: Indexer configuration
            guid: NZB GUID

        Returns:
            NZB file content as bytes or None on error
        """
        url = f"{str(indexer.url).rstrip('/')}/api"
        params = {
            "t": "get",
            "id": guid,
            "apikey": indexer.api_key,
        }

        try:
            response = await self.http_client.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.content
        except Exception as e:
            self.logger.error(f"Error fetching NZB from {indexer.name}: {e}")
            return None


async def scrape_usenet_streams(
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
) -> list[UsenetStreamData]:
    """Scrape Usenet streams from configured indexers.

    Args:
        user_data: User configuration with Newznab indexers
        metadata: Media metadata
        catalog_type: 'movie' or 'series'
        season: Season number for series
        episode: Episode number for series

    Returns:
        List of UsenetStreamData objects
    """
    # Get indexers from user config (indexer_config.newznab_indexers)
    indexers = []
    if user_data.indexer_config and user_data.indexer_config.newznab_indexers:
        indexers = user_data.indexer_config.newznab_indexers

    if not indexers:
        return []

    async with NewznabScraper(indexers) as scraper:
        return await scraper.scrape_and_parse(user_data, metadata, catalog_type, season, episode)
