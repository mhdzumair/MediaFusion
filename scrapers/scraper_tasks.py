import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, Any, List

import dramatiq

from db.config import settings
from db.schemas import TorrentStreamData, MetadataData
from db.schemas import UserData
from scrapers.base_scraper import BaseScraper
from scrapers.bt4g import BT4GScraper
from scrapers.imdb_data import get_imdb_title_data, search_imdb, search_multiple_imdb
from scrapers.jackett import JackettScraper
from scrapers.mediafusion import MediafusionScraper
from scrapers.prowlarr import ProwlarrScraper
from scrapers.tmdb_data import (
    get_tmdb_data_by_imdb,
    search_tmdb,
    get_tmdb_data,
    get_imdb_id_from_tmdb,
    search_multiple_tmdb,
)
from scrapers.torrentio import TorrentioScraper
from scrapers.yts import YTSScraper
from scrapers.zilean import ZileanScraper
from utils import runtime_const

logger = logging.getLogger(__name__)

SCRAPERS = [
    (settings.is_scrap_from_prowlarr, ProwlarrScraper),
    (settings.is_scrap_from_zilean, ZileanScraper),
    (settings.is_scrap_from_torrentio, TorrentioScraper),
    (settings.is_scrap_from_mediafusion, MediafusionScraper),
    (settings.is_scrap_from_yts, YTSScraper),
    (settings.is_scrap_from_bt4g, BT4GScraper),
    (settings.is_scrap_from_jackett, JackettScraper),
]

CACHED_DATA = [
    (ProwlarrScraper.cache_key_prefix, runtime_const.PROWLARR_SEARCH_TTL),
    (TorrentioScraper.cache_key_prefix, runtime_const.TORRENTIO_SEARCH_TTL),
    (ZileanScraper.cache_key_prefix, runtime_const.ZILEAN_SEARCH_TTL),
    (MediafusionScraper.cache_key_prefix, runtime_const.MEDIAFUSION_SEARCH_TTL),
    (YTSScraper.cache_key_prefix, runtime_const.YTS_SEARCH_TTL),
    (BT4GScraper.cache_key_prefix, runtime_const.BT4G_SEARCH_TTL),
    (JackettScraper.cache_key_prefix, runtime_const.JACKETT_SEARCH_TTL),
]


async def run_scrapers(
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
) -> set[TorrentStreamData]:
    """Run all enabled scrapers and return unique streams"""
    all_streams = []
    failed_scrapers = []

    async with asyncio.TaskGroup() as tg:
        # Create tasks for enabled scrapers
        tasks = [
            tg.create_task(
                scraper_cls().scrape_and_parse(
                    user_data, metadata, catalog_type, season, episode
                ),
                name=f"{scraper_cls.__name__}",
            )
            for is_enabled, scraper_cls in SCRAPERS
            if is_enabled
        ]

    # Process results after all tasks complete
    for task in tasks:
        try:
            streams = task.result()
            all_streams.extend(streams)
            logging.info(
                f"Successfully scraped {len(streams)} streams from {task.get_name()}"
            )
        except Exception as exc:
            # Log the error and keep track of failed scrapers
            failed_scrapers.append(task.get_name())
            logging.error(f"Error in scraper {task.get_name()}: {str(exc)}")

    # Log summary of failures if any occurred
    if failed_scrapers:
        logging.error(f"Failed scrapers: {', '.join(failed_scrapers)}")

    unique_streams = set(all_streams)
    logging.info(
        f"Successfully scraped {len(all_streams)} total streams "
        f"({len(unique_streams)} unique) for {metadata.title}"
    )
    return unique_streams


@dramatiq.actor(
    time_limit=5 * 60 * 1000,  # 5 minutes
    priority=20,
)
async def cleanup_expired_scraper_task(**kwargs):
    """Cleanup expired items from all scrapers"""
    logging.info("Cleaning up expired scraper items")
    for cache_key_prefix, ttl in CACHED_DATA:
        await BaseScraper.remove_expired_items(cache_key_prefix, ttl)


class MetadataSource(Enum):
    IMDB = "imdb"
    TMDB = "tmdb"


class MetadataConfig:
    def __init__(
        self,
        primary_source: MetadataSource = MetadataSource.IMDB,
        fallback_enabled: bool = True,
    ):
        self.primary_source = primary_source
        self.fallback_enabled = fallback_enabled

    @property
    def can_use_tmdb(self) -> bool:
        return bool(settings.tmdb_api_key)

    def get_source_order(self) -> List[MetadataSource]:
        """Determine the order of metadata sources based on configuration and availability"""
        if not self.fallback_enabled:
            return [self.primary_source]

        if self.primary_source == MetadataSource.TMDB and not self.can_use_tmdb:
            return [MetadataSource.IMDB]

        sources = [self.primary_source]
        fallback = (
            MetadataSource.IMDB
            if self.primary_source == MetadataSource.TMDB
            else MetadataSource.TMDB
        )

        if fallback == MetadataSource.TMDB and not self.can_use_tmdb:
            return sources

        return sources + [fallback]


@dataclass
class CacheEntry:
    data: Dict[str, Any]
    expires_at: datetime


class MetadataCache:
    def __init__(self, ttl_minutes: int = 30):
        self.cache: Dict[str, CacheEntry] = {}
        self.ttl = timedelta(minutes=ttl_minutes)

    def _generate_key(self, **kwargs) -> str:
        """Generate a cache key from the search parameters"""
        # Sort kwargs to ensure consistent key generation
        sorted_items = sorted(
            (k, str(v)) for k, v in kwargs.items() if v is not None and k != "self"
        )
        key_string = ",".join(f"{k}:{v}" for k, v in sorted_items)
        return hashlib.md5(key_string.encode()).hexdigest()

    def get(self, **kwargs) -> Optional[Dict[str, Any]]:
        """Get cached data if it exists and is not expired"""
        key = self._generate_key(**kwargs)
        if key in self.cache:
            entry = self.cache[key]
            if datetime.now() < entry.expires_at:
                return entry.data
            else:
                # Clean up expired entry
                del self.cache[key]
        return None

    def set(self, data: Dict[str, Any], **kwargs) -> None:
        """Cache the data with expiration time"""
        if not data:  # Don't cache empty results
            return
        key = self._generate_key(**kwargs)
        self.cache[key] = CacheEntry(data=data, expires_at=datetime.now() + self.ttl)

    def clear_expired(self) -> None:
        """Remove all expired entries from the cache"""
        now = datetime.now()
        expired_keys = [
            key for key, entry in self.cache.items() if now >= entry.expires_at
        ]
        for key in expired_keys:
            del self.cache[key]


class MetadataFetcher:
    def __init__(self, cache_ttl_minutes: int = 30):
        self.config = MetadataConfig(MetadataSource(settings.metadata_primary_source))
        self.cache = MetadataCache(ttl_minutes=cache_ttl_minutes)

    async def get_metadata(
        self,
        title_id: str,
        media_type: str,
        source_type: str = "imdb",  # "imdb" or "tmdb" depending on ID type
    ) -> Optional[Dict[str, Any]]:
        """
        Main method to fetch metadata using configured sources and fallback logic.
        """
        # Check cache first
        cached_data = self.cache.get(
            method="get_metadata",
            title_id=title_id,
            media_type=media_type,
            source_type=source_type,
        )
        if cached_data:
            logger.info(f"Cache hit for metadata: {title_id}")
            return cached_data

        metadata = None
        sources = self.config.get_source_order()

        for source in sources:
            try:
                if source == MetadataSource.IMDB:
                    if source_type == "tmdb":
                        # Need to get IMDB ID first
                        tmdb_data = await get_imdb_id_from_tmdb(title_id, media_type)
                        if not tmdb_data:
                            continue
                        title_id = tmdb_data

                    metadata = await get_imdb_title_data(title_id, media_type)

                elif source == MetadataSource.TMDB and self.config.can_use_tmdb:
                    if source_type == "imdb":
                        metadata = await get_tmdb_data_by_imdb(title_id, media_type)
                    else:
                        metadata = await get_tmdb_data(title_id, media_type)

                if metadata:
                    logger.info(
                        f"Successfully fetched metadata from {source.value} for {title_id}: {metadata['title']}"
                    )
                    # Cache the successful result
                    self.cache.set(
                        metadata,
                        method="get_metadata",
                        title_id=title_id,
                        media_type=media_type,
                        source_type=source_type,
                    )
                    break

            except Exception as e:
                logger.exception(f"Error fetching from {source.value}: {e}")
                continue

        return metadata

    async def search_metadata(
        self,
        title: str,
        year: Optional[int] = None,
        media_type: Optional[str] = None,
        created_at: Optional[str | datetime] = None,
    ) -> Dict[str, Any]:
        """
        Search for metadata across configured sources with caching support.
        """
        # Process created_year before cache check
        created_year = None
        if isinstance(created_at, datetime):
            created_year = created_at.year
        elif isinstance(created_at, str):
            try:
                created_year = int(created_at.split("-")[0])
            except (ValueError, IndexError):
                pass

        # Check cache first
        cached_data = self.cache.get(
            method="search_metadata",
            title=title,
            year=year,
            media_type=media_type,
            created_year=created_year,
        )
        if cached_data:
            logger.info(f"Cache hit for search: {title}")
            return cached_data

        metadata = {}
        sources = self.config.get_source_order()

        for source in sources:
            try:
                if source == MetadataSource.IMDB:
                    metadata = await search_imdb(
                        title, year, media_type, created_year=created_year
                    )
                elif source == MetadataSource.TMDB and self.config.can_use_tmdb:
                    metadata = await search_tmdb(
                        title, year, media_type, created_year=created_year
                    )

                if metadata:
                    logger.info(
                        f"Successfully searched metadata from {source.value}: {title}:{metadata['imdb_id']}"
                    )
                    # Cache the successful result
                    self.cache.set(
                        metadata,
                        method="search_metadata",
                        title=title,
                        year=year,
                        media_type=media_type,
                        created_year=created_year,
                    )
                    break

            except Exception as e:
                logger.error(f"Error searching in {source.value}: {e}")
                continue

        return metadata

    def clear_expired_cache(self) -> None:
        """Clear expired cache entries"""
        self.cache.clear_expired()

    async def search_multiple_results(
        self,
        title: str,
        limit: int = 10,
        year: Optional[int] = None,
        media_type: Optional[str] = None,
        created_year: Optional[int] = None,
        min_similarity: int = 60,
    ) -> list[dict]:
        """
        Search for multiple matching titles across IMDB and TMDB.

        Args:
            title: Title to search for
            limit: Maximum number of results to return per source
            year: Specific year to match (exact matching)
            media_type: Type of media ('movie' or 'series')
            created_year: Year used for sorting when exact year match isn't required
            min_similarity: Minimum title similarity score (0-100) for fuzzy matching
        """

        async def get_tmdb_candidates() -> List[Dict[str, Any]]:
            try:
                return await search_multiple_tmdb(
                    title=title,
                    limit=limit,
                    year=year,
                    media_type=media_type,
                    created_year=created_year,
                    min_similarity=min_similarity,
                )
            except Exception as e:
                logging.error(f"Error searching TMDB: {e}")
                return []

        async def get_imdb_candidates() -> List[Dict[str, Any]]:
            try:
                return await search_multiple_imdb(
                    title=title,
                    limit=limit,
                    year=year,
                    media_type=media_type,
                    created_year=created_year,
                    min_similarity=min_similarity,
                )
            except Exception as e:
                logging.error(f"Error searching IMDB: {e}")
                return []

        imdb_candidates, tmdb_candidates = await asyncio.gather(
            get_imdb_candidates(), get_tmdb_candidates()
        )
        return imdb_candidates + tmdb_candidates


# Create a singleton instance with 30-minute cache TTL
meta_fetcher = MetadataFetcher(cache_ttl_minutes=30)
