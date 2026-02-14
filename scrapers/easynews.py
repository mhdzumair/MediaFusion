"""Easynews scraper for Usenet content.

This scraper uses the Easynews search API to find Usenet content.
Unlike other Usenet providers, Easynews provides direct HTTP streaming
without requiring a separate download client.
"""

import hashlib
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import PTT

from db.schemas import MetadataData, StreamFileData, UserData
from db.schemas.media import UsenetStreamData
from scrapers.base_scraper import BaseScraper, ScraperMetrics
from streaming_providers.easynews.client import Easynews
from utils.parser import calculate_max_similarity_ratio, is_contain_18_plus_keywords

logger = logging.getLogger(__name__)


class EasynewsScraper(BaseScraper):
    """Easynews scraper for Usenet content.

    Uses Easynews search API to find video content.
    Results can be streamed directly via HTTP.
    """

    def __init__(self, username: str, password: str):
        """Initialize the Easynews scraper.

        Args:
            username: Easynews account username
            password: Easynews account password
        """
        super().__init__(cache_key_prefix="easynews", logger_name=__name__)
        self.username = username
        self.password = password
        self.metrics = ScraperMetrics("easynews")
        self._client: Easynews | None = None

    async def __aenter__(self):
        """Initialize the HTTP client and Easynews client."""
        await super().__aenter__()
        self._client = Easynews(self.username, self.password)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Clean up the Easynews client."""
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _scrape_and_parse(
        self,
        user_data: UserData,
        metadata: MetadataData,
        catalog_type: str,
        season: int = None,
        episode: int = None,
    ) -> list[UsenetStreamData]:
        """Scrape Easynews and parse results.

        Args:
            user_data: User configuration
            metadata: Media metadata
            catalog_type: 'movie' or 'series'
            season: Season number for series
            episode: Episode number for series

        Returns:
            List of UsenetStreamData objects
        """
        if not self._client:
            self.logger.error("Easynews client not initialized")
            return []

        results: list[UsenetStreamData] = []
        processed_hashes: set[str] = set()

        try:
            if catalog_type == "movie":
                async for stream in self._search_movie(metadata, processed_hashes):
                    results.append(stream)
            elif catalog_type == "series":
                async for stream in self._search_series(metadata, season, episode, processed_hashes):
                    results.append(stream)
        except Exception as e:
            self.logger.error(f"Error scraping Easynews: {e}")
            self.metrics.record_indexer_error("easynews", str(e))

        return results

    async def _search_movie(
        self,
        metadata: MetadataData,
        processed_hashes: set[str],
    ) -> AsyncGenerator[UsenetStreamData, None]:
        """Search for movie content on Easynews.

        Args:
            metadata: Movie metadata
            processed_hashes: Set of already processed hashes

        Yields:
            UsenetStreamData objects
        """
        # Try title + year search
        search_queries = [
            f"{metadata.title} {metadata.year}",
            metadata.title,
        ]

        # Add IMDb ID if available
        imdb_id = metadata.get_imdb_id()
        if imdb_id:
            search_queries.insert(0, f"{metadata.title} {imdb_id}")

        for query in search_queries:
            try:
                results = await self._client.search(query, max_results=50, video_only=True)
                self.metrics.record_found_items(len(results))

                for item in results:
                    stream = await self._parse_item(item, metadata, "movie", processed_hashes)
                    if stream:
                        yield stream

                # If we found results, don't try fallback queries
                if results:
                    break
            except Exception as e:
                self.logger.warning(f"Easynews search error for '{query}': {e}")

    async def _search_series(
        self,
        metadata: MetadataData,
        season: int,
        episode: int,
        processed_hashes: set[str],
    ) -> AsyncGenerator[UsenetStreamData, None]:
        """Search for series content on Easynews.

        Args:
            metadata: Series metadata
            season: Season number
            episode: Episode number
            processed_hashes: Set of already processed hashes

        Yields:
            UsenetStreamData objects
        """
        # Try different episode format patterns
        search_queries = [
            f"{metadata.title} S{season:02d}E{episode:02d}",
            f"{metadata.title} s{season:02d}e{episode:02d}",
            f"{metadata.title} {season}x{episode:02d}",
        ]

        for query in search_queries:
            try:
                results = await self._client.search(query, max_results=30, video_only=True)
                self.metrics.record_found_items(len(results))

                for item in results:
                    stream = await self._parse_item(item, metadata, "series", processed_hashes, season, episode)
                    if stream:
                        yield stream

                # If we found results, don't try fallback queries
                if results:
                    break
            except Exception as e:
                self.logger.warning(f"Easynews search error for '{query}': {e}")

    async def _parse_item(
        self,
        item: dict,
        metadata: MetadataData,
        catalog_type: str,
        processed_hashes: set[str],
        season: int = None,
        episode: int = None,
    ) -> UsenetStreamData | None:
        """Parse a single Easynews search result.

        Args:
            item: Search result item
            metadata: Media metadata
            catalog_type: 'movie' or 'series'
            processed_hashes: Set of already processed hashes
            season: Season number for series
            episode: Episode number for series

        Returns:
            UsenetStreamData or None if item should be skipped
        """
        try:
            # Get file info
            file_id = item.get("id") or item.get("hash", "")
            filename = item.get("filename", "") or item.get("subject", "")

            if not file_id or not filename:
                return None

            # Generate unique hash
            item_hash = hashlib.sha256(f"easynews:{file_id}".encode()).hexdigest()[:40]

            if item_hash in processed_hashes:
                self.metrics.record_skip("Duplicate hash")
                return None

            # Check for adult content
            if is_contain_18_plus_keywords(filename):
                self.metrics.record_skip("Adult content")
                return None

            # Parse title with PTT
            parsed = PTT.parse_title(filename, True)

            # Validate title similarity
            max_ratio = calculate_max_similarity_ratio(parsed.get("title", ""), metadata.title, metadata.aka_titles)
            if max_ratio < 80:
                self.metrics.record_skip("Title mismatch")
                return None

            # Validate year for movies
            if catalog_type == "movie":
                parsed_year = parsed.get("year")
                if parsed_year and parsed_year != metadata.year:
                    self.metrics.record_skip("Year mismatch")
                    return None

            # Get size
            size = item.get("size", 0)

            # Get resolution
            resolution = item.get("resolution", parsed.get("resolution"))

            # Get codec
            codec = item.get("codec", parsed.get("codec"))

            # Parse posted date
            posted_at = None
            posted_str = item.get("posted_at")
            if posted_str:
                try:
                    # Easynews returns various date formats
                    for fmt in [
                        "%Y-%m-%d %H:%M:%S",
                        "%a, %d %b %Y %H:%M:%S %z",
                        "%Y-%m-%dT%H:%M:%S",
                    ]:
                        try:
                            posted_at = datetime.strptime(posted_str, fmt)
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # Get group
            group = item.get("group")

            # Get signature for download URL
            sig = item.get("sig")

            # Generate download URL (with credentials embedded)
            download_url = self._client.generate_download_url(file_id, filename, sig)

            # Build files list for series
            files: list[StreamFileData] = []
            if catalog_type == "series":
                parsed_seasons = parsed.get("seasons", [])
                parsed_episodes = parsed.get("episodes", [])

                if parsed_seasons and parsed_episodes:
                    for s in parsed_seasons:
                        for e in parsed_episodes:
                            files.append(
                                StreamFileData(
                                    file_index=0,
                                    filename=filename,
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
                            filename=filename,
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
                nzb_guid=item_hash,
                nzb_url=download_url,  # For Easynews, this is the direct stream URL
                name=filename,
                size=size,
                indexer="Easynews",
                source="Easynews",
                group_name=group,
                posted_at=posted_at,
                meta_id=metadata.external_id,
                # Quality attributes
                resolution=resolution,
                codec=codec,
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
                cached=True,  # Easynews content is always "cached" (instant streaming)
            )

            processed_hashes.add(item_hash)
            self.metrics.record_processed_item()
            self.metrics.record_quality(stream.quality)
            self.metrics.record_source("Easynews")

            return stream

        except Exception as e:
            self.logger.exception(f"Error parsing Easynews item: {e}")
            self.metrics.record_error("parse_error")
            return None


async def scrape_easynews_streams(
    username: str,
    password: str,
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
) -> list[UsenetStreamData]:
    """Scrape Usenet streams from Easynews.

    Args:
        username: Easynews username
        password: Easynews password
        user_data: User configuration
        metadata: Media metadata
        catalog_type: 'movie' or 'series'
        season: Season number for series
        episode: Episode number for series

    Returns:
        List of UsenetStreamData objects
    """
    async with EasynewsScraper(username, password) as scraper:
        return await scraper.scrape_and_parse(user_data, metadata, catalog_type, season, episode)
