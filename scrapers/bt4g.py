import asyncio
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

import httpx
import PTT

from db.config import settings
from db.schemas import MetadataData, StreamFileData, TorrentStreamData
from scrapers.base_scraper import BaseScraper
from utils.parser import convert_size_to_bytes, is_contain_18_plus_keywords
from utils.runtime_const import BT4G_SEARCH_TTL


class BT4GScraper(BaseScraper):
    MOVIE_SEARCH_QUERY_TEMPLATES = [
        "{title} {year}",  # Title with year
        "{title}",  # Title only
    ]
    SERIES_SEARCH_QUERY_TEMPLATES = [
        "{title} S{season:02d}E{episode:02d}",  # Standard SXXEYY format
        "{title} S{season:02d}",  # Season-only format
        "{title}",  # Title only
    ]
    cache_key_prefix = "bt4g"

    def __init__(self):
        super().__init__(cache_key_prefix=self.cache_key_prefix, logger_name=__name__)
        # Limit concurrent requests to avoid rate limiting (429 errors)
        self.semaphore = asyncio.Semaphore(3)
        self.http_client = httpx.AsyncClient(
            proxy=settings.requests_proxy_url,
            timeout=settings.bt4g_search_timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            },
        )

    @BaseScraper.cache(ttl=BT4G_SEARCH_TTL)
    @BaseScraper.rate_limit(calls=2, period=timedelta(seconds=1))
    async def _scrape_and_parse(
        self,
        user_data,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentStreamData]:
        results = []
        processed_info_hashes = set()

        search_generators = []
        if catalog_type == "movie":
            for query_template in self.MOVIE_SEARCH_QUERY_TEMPLATES:
                search_query = query_template.format(title=metadata.title, year=metadata.year)
                search_generators.append(
                    self.scrape_by_query(
                        processed_info_hashes,
                        metadata,
                        search_query,
                        catalog_type,
                    )
                )
            if settings.scrape_with_aka_titles:
                for aka_title in metadata.aka_titles:
                    search_generators.append(
                        self.scrape_by_query(
                            processed_info_hashes,
                            metadata,
                            aka_title,
                            catalog_type,
                        )
                    )
        else:  # series
            for query_template in self.SERIES_SEARCH_QUERY_TEMPLATES:
                search_query = query_template.format(
                    title=metadata.title,
                    season=season,
                    episode=episode,
                )
                search_generators.append(
                    self.scrape_by_query(
                        processed_info_hashes,
                        metadata,
                        search_query,
                        catalog_type,
                        season=season,
                        episode=episode,
                    )
                )
            if settings.scrape_with_aka_titles:
                for aka_title in metadata.aka_titles:
                    search_generators.append(
                        self.scrape_by_query(
                            processed_info_hashes,
                            metadata,
                            aka_title,
                            catalog_type,
                            season=season,
                            episode=episode,
                        )
                    )

        try:
            async for stream in self.process_streams(
                *search_generators,
                max_process=settings.bt4g_immediate_max_process,
                max_process_time=settings.bt4g_immediate_max_process_time,
                catalog_type=catalog_type,
                season=season,
                episode=episode,
            ):
                results.append(stream)
        except Exception as e:
            self.metrics.record_error("stream_processing_error")
            self.logger.error(f"Error processing streams: {e}")

        return results

    @staticmethod
    def _get_rss_url(search_query: str) -> str:
        """Generate RSS feed URL for BT4G search (bypasses Cloudflare)"""
        encoded_query = quote(search_query)
        return f"{settings.bt4g_url}/search?q={encoded_query}&page=rss"

    def _parse_rss_description(self, description: str) -> tuple[int | None, str | None]:
        """Parse RSS description to extract size and info_hash.

        Description format: "Title<br>Size<br>Category<br>InfoHash"
        Example: "Stranger.Things.S01E01...<br>3.31GB<br>Movie<br>ae9a85ce..."
        """
        try:
            # Split by <br> tag
            parts = description.split("<br>")
            if len(parts) >= 4:
                size_str = parts[1].strip()
                info_hash = parts[3].strip().lower()
                size = convert_size_to_bytes(size_str)
                return size, info_hash
            elif len(parts) >= 2:
                # Try to extract just size
                size_str = parts[1].strip()
                size = convert_size_to_bytes(size_str)
                return size, None
        except Exception as e:
            self.logger.debug(f"Error parsing RSS description: {e}")
        return None, None

    def _extract_info_hash_from_magnet(self, magnet_link: str) -> str | None:
        """Extract info_hash from magnet link."""
        try:
            match = re.search(r"btih:([a-fA-F0-9]{40})", magnet_link)
            if match:
                return match.group(1).lower()
        except Exception:
            pass
        return None

    def _extract_trackers_from_magnet(self, magnet_link: str) -> list[str]:
        """Extract tracker URLs from magnet link."""
        trackers = []
        try:
            # Find all tr= parameters
            matches = re.findall(r"tr=([^&]+)", magnet_link)
            for match in matches:
                tracker = unquote(match)
                if tracker not in trackers:
                    trackers.append(tracker)
        except Exception:
            pass
        return trackers

    async def scrape_by_query(
        self,
        processed_info_hashes: set[str],
        metadata: MetadataData,
        search_query: str,
        catalog_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> AsyncGenerator[TorrentStreamData, None]:
        """Scrape BT4G using RSS feed (bypasses Cloudflare protection)."""
        try:
            # Use semaphore to limit concurrent requests and avoid rate limiting
            async with self.semaphore:
                rss_url = self._get_rss_url(search_query)
                # Add small delay between requests to avoid hammering the server
                await asyncio.sleep(0.5)
                response = await self.make_request(rss_url, is_expected_to_fail=True)

            if not response.text or "cloudflare" in response.text.lower():
                self.logger.warning(f"Cloudflare blocked RSS request for: {search_query}")
                return

            # Parse RSS XML
            try:
                root = ET.fromstring(response.text)
            except ET.ParseError as e:
                self.logger.error(f"Failed to parse RSS XML: {e}")
                return

            # Find all items in the RSS feed
            items = root.findall(".//item")
            if not items:
                self.logger.debug(f"No results found in RSS for: {search_query}")
                return

            self.logger.info(f"Found {len(items)} results for '{search_query}' in BT4G RSS feed")
            self.metrics.record_found_items(len(items))

            for item in items:
                stream = await self._process_rss_item(
                    item,
                    metadata,
                    catalog_type,
                    season,
                    episode,
                    processed_info_hashes,
                )
                if stream:
                    yield stream

        except Exception as e:
            self.metrics.record_error("search_error")
            self.logger.exception(f"Error searching BT4G RSS: {e}")

    async def _process_rss_item(
        self,
        item: ET.Element,
        metadata: MetadataData,
        catalog_type: str,
        season: int | None,
        episode: int | None,
        processed_info_hashes: set[str],
    ) -> TorrentStreamData | None:
        """Process a single RSS item and return a TorrentStreamData if valid."""
        try:
            # Extract title
            title_elem = item.find("title")
            if title_elem is None or not title_elem.text:
                return None
            torrent_title = title_elem.text.strip()

            # Check for adult content
            if is_contain_18_plus_keywords(torrent_title):
                self.metrics.record_skip("Adult content")
                return None

            # Parse title
            parsed_data = PTT.parse_title(torrent_title, True)

            # Validate title and year
            if not self.validate_title_and_year(
                parsed_data,
                metadata,
                catalog_type,
                torrent_title,
            ):
                return None

            # Extract magnet link
            link_elem = item.find("link")
            if link_elem is None or not link_elem.text:
                self.metrics.record_skip("No magnet link")
                return None
            magnet_link = link_elem.text.strip()

            # Extract info_hash from magnet
            info_hash = self._extract_info_hash_from_magnet(magnet_link)
            if not info_hash:
                self.metrics.record_skip("Invalid magnet link")
                return None

            # Check for duplicates
            if info_hash in processed_info_hashes:
                self.metrics.record_skip("Duplicate info_hash")
                return None
            processed_info_hashes.add(info_hash)

            # Extract trackers
            trackers = self._extract_trackers_from_magnet(magnet_link)

            # Extract size and info_hash from description
            description_elem = item.find("description")
            description = description_elem.text if description_elem is not None else ""
            size, _ = self._parse_rss_description(description)

            # Extract publish date
            pub_date_elem = item.find("pubDate")
            created_date = None
            if pub_date_elem is not None and pub_date_elem.text:
                try:
                    # Parse RSS date format: "Tue,25 Nov 2025 20:25:35 -0000"
                    date_str = pub_date_elem.text.strip()
                    # Handle various RSS date formats
                    for fmt in [
                        "%a,%d %b %Y %H:%M:%S %z",
                        "%a, %d %b %Y %H:%M:%S %z",
                        "%a,%d %b %Y %H:%M:%S -0000",
                        "%a, %d %b %Y %H:%M:%S -0000",
                    ]:
                        try:
                            created_date = datetime.strptime(date_str, fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # For RSS, we don't have seeders info, so we use a default
            # We can skip old torrents based on date if needed
            if created_date and created_date.replace(tzinfo=None) < datetime.now() - timedelta(days=365 * 2):
                # Skip very old torrents (2+ years) as they likely have no seeders
                self.metrics.record_skip("Very old torrent")
                return None

            # Build file info from parsed title (RSS doesn't provide file list)
            files = []
            if catalog_type == "series":
                # For series, create a file entry based on parsed data
                season_num = parsed_data.get("seasons", [None])[0] if parsed_data.get("seasons") else season
                episode_num = parsed_data.get("episodes", [None])[0] if parsed_data.get("episodes") else episode
                files.append(
                    StreamFileData(
                        file_index=0,
                        filename=torrent_title,
                        size=size or 0,
                        file_type="video",
                        season_number=season_num,
                        episode_number=episode_num,
                    )
                )
            else:
                files.append(
                    StreamFileData(
                        file_index=0,
                        filename=torrent_title,
                        size=size or 0,
                        file_type="video",
                    )
                )

            stream = TorrentStreamData(
                info_hash=info_hash,
                meta_id=metadata.get_canonical_id(),
                name=torrent_title,
                size=size or 0,
                source="BT4G",
                seeders=0,  # RSS doesn't provide seeders
                announce_list=trackers,
                files=files,
                # Single-value quality attributes
                resolution=parsed_data.get("resolution"),
                codec=parsed_data.get("codec"),
                quality=parsed_data.get("quality"),
                bit_depth=parsed_data.get("bit_depth"),
                release_group=parsed_data.get("group"),
                # Multi-value quality attributes (from PTT)
                audio_formats=parsed_data.get("audio", []) if isinstance(parsed_data.get("audio"), list) else [],
                channels=parsed_data.get("channels", []) if isinstance(parsed_data.get("channels"), list) else [],
                hdr_formats=parsed_data.get("hdr", []) if isinstance(parsed_data.get("hdr"), list) else [],
                languages=parsed_data.get("languages", []),
                # Release flags
                is_remastered=parsed_data.get("remastered", False),
                is_upscaled=parsed_data.get("upscaled", False),
                is_proper=parsed_data.get("proper", False),
                is_repack=parsed_data.get("repack", False),
                is_extended=parsed_data.get("extended", False),
                is_complete=parsed_data.get("complete", False),
                is_dubbed=parsed_data.get("dubbed", False),
                is_subbed=parsed_data.get("subbed", False),
            )

            # Validate series data
            if catalog_type == "series":
                if not self._validate_series_data(stream, parsed_data, season, episode):
                    return None

            # Validate movie data
            if catalog_type == "movie":
                if parsed_data.get("seasons") or parsed_data.get("episodes"):
                    self.metrics.record_skip("Unexpected season/episode info")
                    return None

            self.metrics.record_processed_item()
            self.metrics.record_quality(stream.quality)
            self.metrics.record_source(stream.source)

            return stream

        except Exception as e:
            self.metrics.record_error("result_processing_error")
            self.logger.exception(f"Error processing RSS item: {e}")
            return None

    def _validate_series_data(
        self,
        stream: TorrentStreamData,
        parsed_data: dict,
        target_season: int | None,
        target_episode: int | None,
    ) -> bool:
        """Validate series data matches the requested season/episode."""
        seasons = parsed_data.get("seasons", [])
        episodes = parsed_data.get("episodes", [])

        # Must have season info
        if not seasons:
            self.metrics.record_skip("Missing season info")
            return False

        # If we're looking for a specific season, check it matches
        if target_season is not None:
            if target_season not in seasons:
                self.metrics.record_skip("Season mismatch")
                return False

        # If we're looking for a specific episode, check it matches
        # (or it's a season pack without specific episodes)
        if target_episode is not None and episodes:
            if target_episode not in episodes:
                self.metrics.record_skip("Episode mismatch")
                return False

        return True
