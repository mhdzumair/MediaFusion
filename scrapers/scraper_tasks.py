import asyncio
import hashlib
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any

import dramatiq

from db.config import settings
from db.schemas import MetadataData, TorrentStreamData, UserData
from scrapers.base_scraper import BaseScraper
from scrapers.bt4g import BT4GScraper
from scrapers.imdb_data import get_imdb_title_data, search_imdb, search_multiple_imdb
from scrapers.jackett import JackettScraper
from scrapers.kitsu_data import (
    get_kitsu_anime_data,
    search_multiple_kitsu,
)
from scrapers.mal_data import (
    get_mal_anime_data,
    search_multiple_mal,
)
from scrapers.mediafusion import MediafusionScraper
from scrapers.prowlarr import ProwlarrScraper
from scrapers.tmdb_data import (
    get_imdb_id_from_tmdb,
    get_tmdb_data,
    get_tmdb_data_by_imdb,
    search_multiple_tmdb,
    search_tmdb,
)
from scrapers.torrentio import TorrentioScraper
from scrapers.tvdb_data import (
    get_tvdb_movie_data,
    get_tvdb_series_data,
    search_multiple_tvdb,
)
from scrapers.torznab import TorznabScraper
from scrapers.torbox_search import TorBoxSearchScraper
from scrapers.yts import YTSScraper
from scrapers.zilean import ZileanScraper
from scrapers.telegram import TelegramScraper
from utils import runtime_const

logger = logging.getLogger(__name__)

# Global scrapers - used when user doesn't have custom indexer config
SCRAPERS = [
    (settings.is_scrap_from_prowlarr, ProwlarrScraper, "prowlarr"),
    (settings.is_scrap_from_zilean, ZileanScraper, "zilean"),
    (settings.is_scrap_from_torrentio, TorrentioScraper, "torrentio"),
    (settings.is_scrap_from_mediafusion, MediafusionScraper, "mediafusion"),
    (settings.is_scrap_from_yts, YTSScraper, "yts"),
    (settings.is_scrap_from_bt4g, BT4GScraper, "bt4g"),
    (settings.is_scrap_from_jackett, JackettScraper, "jackett"),
]

# Scrapers that are not indexer-based (always use global config)
NON_INDEXER_SCRAPERS = [
    (settings.is_scrap_from_zilean, ZileanScraper, "zilean"),
    (settings.is_scrap_from_torrentio, TorrentioScraper, "torrentio"),
    (settings.is_scrap_from_mediafusion, MediafusionScraper, "mediafusion"),
    (settings.is_scrap_from_yts, YTSScraper, "yts"),
    (settings.is_scrap_from_bt4g, BT4GScraper, "bt4g"),
]

CACHED_DATA = [
    (ProwlarrScraper.cache_key_prefix, runtime_const.PROWLARR_SEARCH_TTL),
    (TorrentioScraper.cache_key_prefix, runtime_const.TORRENTIO_SEARCH_TTL),
    (ZileanScraper.cache_key_prefix, runtime_const.ZILEAN_SEARCH_TTL),
    (MediafusionScraper.cache_key_prefix, runtime_const.MEDIAFUSION_SEARCH_TTL),
    (YTSScraper.cache_key_prefix, runtime_const.YTS_SEARCH_TTL),
    (BT4GScraper.cache_key_prefix, runtime_const.BT4G_SEARCH_TTL),
    (JackettScraper.cache_key_prefix, runtime_const.JACKETT_SEARCH_TTL),
    (TelegramScraper.CACHE_KEY_PREFIX, runtime_const.TELEGRAM_SEARCH_TTL),
]


async def run_scrapers(
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
    selected_scrapers: list[str] | None = None,
) -> set[TorrentStreamData]:
    """Run all enabled scrapers and return unique streams.

    Supports user-configured indexers (Prowlarr, Jackett, Torznab) with fallback
    to global instances when user doesn't have custom configuration.

    Args:
        user_data: User configuration data (may include indexer_config)
        metadata: Media metadata for scraping
        catalog_type: Type of catalog (movie/series)
        season: Season number for series
        episode: Episode number for series
        selected_scrapers: Optional list of scraper IDs to use. If None, uses all enabled scrapers.
    """
    all_streams = []
    failed_scrapers = []
    tasks = []

    # Get user's indexer configuration
    ic = user_data.indexer_config

    async with asyncio.TaskGroup() as tg:
        # Handle Prowlarr: user instance or global
        if selected_scrapers is None or "prowlarr" in selected_scrapers:
            prowlarr_task = _create_prowlarr_task(tg, user_data, ic, metadata, catalog_type, season, episode)
            if prowlarr_task:
                tasks.append(prowlarr_task)

        # Handle Jackett: user instance or global
        if selected_scrapers is None or "jackett" in selected_scrapers:
            jackett_task = _create_jackett_task(tg, user_data, ic, metadata, catalog_type, season, episode)
            if jackett_task:
                tasks.append(jackett_task)

        # Handle custom Torznab endpoints (always user-specific)
        if selected_scrapers is None or "torznab" in selected_scrapers:
            torznab_task = _create_torznab_task(tg, user_data, ic, metadata, catalog_type, season, episode)
            if torznab_task:
                tasks.append(torznab_task)

        # Handle TorBox Search API (if user has TorBox configured)
        if selected_scrapers is None or "torbox_search" in selected_scrapers:
            torbox_task = _create_torbox_search_task(tg, user_data, metadata, catalog_type, season, episode)
            if torbox_task:
                tasks.append(torbox_task)

        # Handle Telegram scraper (global channels + user channels)
        if selected_scrapers is None or "telegram" in selected_scrapers:
            telegram_task = _create_telegram_task(tg, user_data, metadata, catalog_type, season, episode)
            if telegram_task:
                tasks.append(telegram_task)

        # Add non-indexer scrapers (always use global config)
        for is_enabled, scraper_cls, scraper_id in NON_INDEXER_SCRAPERS:
            if not is_enabled:
                continue
            if selected_scrapers is not None and scraper_id not in selected_scrapers:
                continue

            task = tg.create_task(
                scraper_cls().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                name=f"{scraper_cls.__name__}",
            )
            tasks.append(task)

    # Process results after all tasks complete
    for task in tasks:
        try:
            streams = task.result()
            all_streams.extend(streams)
            logging.info(f"Successfully scraped {len(streams)} streams from {task.get_name()}")
        except Exception as exc:
            # Log the error and keep track of failed scrapers
            failed_scrapers.append(task.get_name())
            logging.error(f"Error in scraper {task.get_name()}: {str(exc)}")

    # Log summary of failures if any occurred
    if failed_scrapers:
        logging.error(f"Failed scrapers: {', '.join(failed_scrapers)}")

    unique_streams = set(all_streams)
    logging.info(
        f"Successfully scraped {len(all_streams)} total streams ({len(unique_streams)} unique) for {metadata.title}"
    )
    return unique_streams


def _create_prowlarr_task(
    tg: asyncio.TaskGroup,
    user_data: UserData,
    ic,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None,
    episode: int | None,
):
    """Create Prowlarr scraper task based on user config or global settings."""
    from db.schemas.config import IndexerConfig

    # Check if user has custom Prowlarr config
    if ic and isinstance(ic, IndexerConfig) and ic.prowlarr:
        prowlarr_cfg = ic.prowlarr
        if prowlarr_cfg.enabled and not prowlarr_cfg.use_global:
            # Use user's custom Prowlarr instance
            if prowlarr_cfg.url and prowlarr_cfg.api_key:
                return tg.create_task(
                    ProwlarrScraper(
                        base_url=prowlarr_cfg.url,
                        api_key=prowlarr_cfg.api_key,
                    ).scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                    name="ProwlarrScraper (user)",
                )
        elif prowlarr_cfg.enabled and prowlarr_cfg.use_global:
            # Explicitly use global
            if settings.is_scrap_from_prowlarr:
                return tg.create_task(
                    ProwlarrScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                    name="ProwlarrScraper",
                )
        # If not enabled, skip Prowlarr entirely
        return None

    # No user config - fall back to global if enabled
    if settings.is_scrap_from_prowlarr:
        return tg.create_task(
            ProwlarrScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
            name="ProwlarrScraper",
        )
    return None


def _create_jackett_task(
    tg: asyncio.TaskGroup,
    user_data: UserData,
    ic,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None,
    episode: int | None,
):
    """Create Jackett scraper task based on user config or global settings."""
    from db.schemas.config import IndexerConfig

    # Check if user has custom Jackett config
    if ic and isinstance(ic, IndexerConfig) and ic.jackett:
        jackett_cfg = ic.jackett
        if jackett_cfg.enabled and not jackett_cfg.use_global:
            # Use user's custom Jackett instance
            if jackett_cfg.url and jackett_cfg.api_key:
                return tg.create_task(
                    JackettScraper(
                        base_url=jackett_cfg.url,
                        api_key=jackett_cfg.api_key,
                    ).scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                    name="JackettScraper (user)",
                )
        elif jackett_cfg.enabled and jackett_cfg.use_global:
            # Explicitly use global
            if settings.is_scrap_from_jackett:
                return tg.create_task(
                    JackettScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                    name="JackettScraper",
                )
        # If not enabled, skip Jackett entirely
        return None

    # No user config - fall back to global if enabled
    if settings.is_scrap_from_jackett:
        return tg.create_task(
            JackettScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
            name="JackettScraper",
        )
    return None


def _create_torznab_task(
    tg: asyncio.TaskGroup,
    user_data: UserData,
    ic,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None,
    episode: int | None,
):
    """Create Torznab scraper task if user has configured endpoints."""
    from db.schemas.config import IndexerConfig

    # Torznab is always user-specific (no global config)
    if ic and isinstance(ic, IndexerConfig) and ic.torznab_endpoints:
        enabled_endpoints = [e for e in ic.torznab_endpoints if e.enabled]
        if enabled_endpoints:
            return tg.create_task(
                TorznabScraper(enabled_endpoints).scrape_and_parse(user_data, metadata, catalog_type, season, episode),
                name="TorznabScraper",
            )
    return None


def _create_torbox_search_task(
    tg: asyncio.TaskGroup,
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None,
    episode: int | None,
):
    """Create TorBox Search scraper task if user has TorBox configured.

    TorBox Search API requires a TorBox API token and provides both
    torrent and Usenet search capabilities.
    """
    if not user_data:
        return None

    # Check multi-debrid streaming_providers list first
    has_torbox = False
    if user_data.streaming_providers:
        for sp in user_data.streaming_providers:
            if sp.service == "torbox" and sp.token:
                has_torbox = True
                break

    # Fallback to legacy single streaming_provider
    if not has_torbox and user_data.streaming_provider:
        sp = user_data.streaming_provider
        if sp.service == "torbox" and sp.token:
            has_torbox = True

    if not has_torbox:
        return None

    # User has TorBox configured - create the search task
    return tg.create_task(
        TorBoxSearchScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
        name="TorBoxSearchScraper",
    )


def _create_telegram_task(
    tg: asyncio.TaskGroup,
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int | None,
    episode: int | None,
):
    """Create Telegram scraper task if enabled.

    Telegram scraping can use:
    - Global admin-configured channels (from settings)
    - User-configured channels (from telegram_config)

    The scraper will use Pyrogram to fetch messages from Telegram channels.
    """
    # Check if Telegram scraping is enabled globally
    if not settings.is_scrap_from_telegram:
        return None

    # Check if we have the required Telegram API credentials
    if not all(
        [
            settings.telegram_api_id,
            settings.telegram_api_hash,
            settings.telegram_session_string,
        ]
    ):
        return None

    # Check if there are any channels to scrape
    has_channels = bool(settings.telegram_scraping_channels)

    # Check user's telegram config for additional channels
    if user_data and user_data.telegram_config:
        tg_config = user_data.telegram_config
        if tg_config.enabled:
            enabled_channels = [c for c in tg_config.channels if c.enabled]
            if enabled_channels:
                has_channels = True

    if not has_channels:
        return None

    # Create the Telegram scraper task
    return tg.create_task(
        TelegramScraper().scrape_and_parse(user_data, metadata, catalog_type, season, episode),
        name="TelegramScraper",
    )


async def run_usenet_scrapers(
    user_data: UserData,
    metadata: MetadataData,
    catalog_type: str,
    season: int = None,
    episode: int = None,
    selected_scrapers: list[str] | None = None,
) -> list:
    """Run selected Usenet scrapers and return unique streams.

    Combines results from:
    - Newznab indexers (if configured and selected)
    - TorBox Search Usenet (if TorBox is configured and selected)
    - Easynews (if configured and selected)

    Args:
        user_data: User configuration data
        metadata: Media metadata for scraping
        catalog_type: Type of catalog (movie/series)
        season: Season number for series
        episode: Episode number for series
        selected_scrapers: List of scraper IDs to run (None = all available)

    Returns:
        List of UsenetStreamData objects
    """
    from scrapers.newznab import scrape_usenet_streams
    from scrapers.easynews import scrape_easynews_streams

    all_streams = []
    tasks = []

    # Helper to check if scraper is selected
    def is_selected(scraper_id: str) -> bool:
        return selected_scrapers is None or scraper_id in selected_scrapers

    async with asyncio.TaskGroup() as tg:
        # Newznab indexer scraping (from indexer_config.newznab_indexers)
        if is_selected("newznab") and user_data.indexer_config and user_data.indexer_config.newznab_indexers:
            tasks.append(
                tg.create_task(
                    scrape_usenet_streams(user_data, metadata, catalog_type, season, episode),
                    name="NewznabScraper",
                )
            )

        # TorBox Search Usenet scraping - check both multi-debrid and legacy single provider
        has_torbox_token = False
        if user_data.streaming_providers:
            for sp in user_data.streaming_providers:
                if sp.service == "torbox" and sp.token:
                    has_torbox_token = True
                    break
        if not has_torbox_token and user_data.streaming_provider:
            if user_data.streaming_provider.service == "torbox" and user_data.streaming_provider.token:
                has_torbox_token = True

        # TorBox Usenet uses "torbox_search" scraper ID
        if is_selected("torbox_search") and has_torbox_token:
            tasks.append(
                tg.create_task(
                    TorBoxSearchScraper().scrape_usenet(user_data, metadata, catalog_type, season, episode),
                    name="TorBoxSearchUsenet",
                )
            )

        # Easynews scraping - check both multi-debrid and legacy single provider
        easynews_config = None
        if user_data.streaming_providers:
            for sp in user_data.streaming_providers:
                if sp.service == "easynews" and sp.easynews_config:
                    easynews_config = sp.easynews_config
                    break
        if not easynews_config and user_data.streaming_provider:
            if user_data.streaming_provider.service == "easynews" and user_data.streaming_provider.easynews_config:
                easynews_config = user_data.streaming_provider.easynews_config

        if is_selected("easynews") and easynews_config and easynews_config.username and easynews_config.password:
            tasks.append(
                tg.create_task(
                    scrape_easynews_streams(
                        username=easynews_config.username,
                        password=easynews_config.password,
                        user_data=user_data,
                        metadata=metadata,
                        catalog_type=catalog_type,
                        season=season,
                        episode=episode,
                    ),
                    name="EasynewsScraper",
                )
            )

    # Process results
    for task in tasks:
        try:
            streams = task.result()
            if streams:
                all_streams.extend(streams)
            logging.info(f"Successfully scraped {len(streams) if streams else 0} Usenet streams from {task.get_name()}")
        except Exception as exc:
            logging.error(f"Error in Usenet scraper {task.get_name()}: {str(exc)}")

    # Deduplicate by nzb_guid
    seen_guids = set()
    unique_streams = []
    for stream in all_streams:
        if stream.nzb_guid not in seen_guids:
            seen_guids.add(stream.nzb_guid)
            unique_streams.append(stream)

    logging.info(f"Found {len(unique_streams)} unique Usenet streams for {metadata.title}")
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
    TVDB = "tvdb"
    MAL = "mal"
    KITSU = "kitsu"


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

    @property
    def can_use_tvdb(self) -> bool:
        return bool(settings.tvdb_api_key)

    def get_source_order(self) -> list[MetadataSource]:
        """Determine the order of metadata sources based on configuration and availability"""
        if not self.fallback_enabled:
            return [self.primary_source]

        if self.primary_source == MetadataSource.TMDB and not self.can_use_tmdb:
            return [MetadataSource.IMDB]

        sources = [self.primary_source]
        fallback = MetadataSource.IMDB if self.primary_source == MetadataSource.TMDB else MetadataSource.TMDB

        if fallback == MetadataSource.TMDB and not self.can_use_tmdb:
            return sources

        return sources + [fallback]

    def get_all_available_sources(self) -> list[MetadataSource]:
        """Get all available metadata sources for multi-provider refresh."""
        sources = [MetadataSource.IMDB]  # Always available

        if self.can_use_tmdb:
            sources.append(MetadataSource.TMDB)

        if self.can_use_tvdb:
            sources.append(MetadataSource.TVDB)

        # MAL and Kitsu don't require API keys
        sources.append(MetadataSource.MAL)
        sources.append(MetadataSource.KITSU)

        return sources


class MetadataCache:
    """Redis-backed metadata cache shared across all workers."""

    def __init__(self, ttl_seconds: int = 30 * 60):
        self.ttl_seconds = ttl_seconds

    def _generate_key(self, **kwargs) -> str:
        sorted_items = sorted((k, str(v)) for k, v in kwargs.items() if v is not None and k != "self")
        key_string = ",".join(f"{k}:{v}" for k, v in sorted_items)
        digest = hashlib.md5(key_string.encode()).hexdigest()
        return f"meta_cache:{digest}"

    async def get(self, **kwargs) -> dict[str, Any] | None:
        from db.redis_database import REDIS_ASYNC_CLIENT

        key = self._generate_key(**kwargs)
        try:
            raw = await REDIS_ASYNC_CLIENT.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logging.debug(f"MetadataCache Redis get failed for {key}: {e}")
        return None

    async def set(self, data: dict[str, Any], **kwargs) -> None:
        from db.redis_database import REDIS_ASYNC_CLIENT

        if not data:
            return
        key = self._generate_key(**kwargs)
        try:
            await REDIS_ASYNC_CLIENT.setex(key, self.ttl_seconds, json.dumps(data, default=str))
        except Exception as e:
            logging.debug(f"MetadataCache Redis set failed for {key}: {e}")


class MetadataFetcher:
    def __init__(self, cache_ttl_minutes: int = 30):
        self.config = MetadataConfig(MetadataSource(settings.metadata_primary_source))
        self.cache = MetadataCache(ttl_seconds=cache_ttl_minutes * 60)

    async def get_metadata(
        self,
        title_id: str,
        media_type: str,
        source_type: str = "imdb",  # "imdb", "tmdb", "tvdb", "mal", "kitsu"
    ) -> dict[str, Any] | None:
        """
        Main method to fetch metadata using configured sources and fallback logic.
        """
        # Check cache first
        cached_data = await self.cache.get(
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
                    await self.cache.set(
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

    async def get_metadata_from_provider(
        self,
        provider: str,
        provider_id: str,
        media_type: str,
    ) -> dict[str, Any] | None:
        """
        Fetch metadata from a specific provider.

        Args:
            provider: Provider name (imdb, tmdb, tvdb, mal, kitsu)
            provider_id: The ID for that provider
            media_type: "movie" or "series"

        Returns:
            Metadata dict or None
        """
        try:
            if provider == "imdb":
                return await get_imdb_title_data(provider_id, media_type)

            elif provider == "tmdb" and self.config.can_use_tmdb:
                return await get_tmdb_data(provider_id, media_type)

            elif provider == "tvdb" and self.config.can_use_tvdb:
                if media_type == "movie":
                    return await get_tvdb_movie_data(provider_id)
                else:
                    return await get_tvdb_series_data(provider_id)

            elif provider == "mal":
                return await get_mal_anime_data(provider_id)

            elif provider == "kitsu":
                return await get_kitsu_anime_data(provider_id)

            else:
                logger.warning(f"Unknown provider: {provider}")
                return None

        except Exception as e:
            logger.error(f"Error fetching from {provider}: {e}")
            return None

    async def get_metadata_from_all_providers(
        self,
        external_ids: dict[str, str],
        media_type: str,
    ) -> dict[str, dict[str, Any]]:
        """
        Fetch metadata from all available providers in parallel.

        Args:
            external_ids: Dict mapping provider name to ID (e.g., {"imdb": "tt1234567", "tmdb": "123"})
            media_type: "movie" or "series"

        Returns:
            Dict mapping provider name to metadata dict
        """
        results = {}
        tasks = []
        providers = []

        # Valid metadata providers (not social media or other non-metadata IDs)
        valid_metadata_providers = {"imdb", "tmdb", "tvdb", "mal", "kitsu"}

        # Create tasks for each available provider
        for provider, provider_id in external_ids.items():
            if not provider_id:
                continue

            # Skip non-metadata providers (social media, etc.)
            if provider not in valid_metadata_providers:
                continue

            # Check if provider is available
            if provider == "tmdb" and not self.config.can_use_tmdb:
                continue
            if provider == "tvdb" and not self.config.can_use_tvdb:
                continue

            providers.append(provider)
            tasks.append(self.get_metadata_from_provider(provider, str(provider_id), media_type))

        if not tasks:
            return results

        # Fetch all in parallel
        fetched_data = await asyncio.gather(*tasks, return_exceptions=True)

        for provider, data in zip(providers, fetched_data):
            if isinstance(data, Exception):
                logger.error(f"Error fetching from {provider}: {data}")
                continue
            if data:
                results[provider] = data

        return results

    async def search_metadata(
        self,
        title: str,
        year: int | None = None,
        media_type: str | None = None,
        created_at: str | datetime | None = None,
    ) -> dict[str, Any]:
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
        cached_data = await self.cache.get(
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
                    metadata = await search_imdb(title, year, media_type, created_year=created_year)
                elif source == MetadataSource.TMDB and self.config.can_use_tmdb:
                    metadata = await search_tmdb(title, year, media_type, created_year=created_year)

                if metadata:
                    logger.info(f"Successfully searched metadata from {source.value}: {title}:{metadata['imdb_id']}")
                    # Cache the successful result
                    await self.cache.set(
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
        """No-op: Redis TTL handles expiry automatically."""
        pass

    async def search_multiple_results(
        self,
        title: str,
        limit: int = 10,
        year: int | None = None,
        media_type: str | None = None,
        created_year: int | None = None,
        min_similarity: int = 60,
        include_anime: bool = True,
    ) -> list[dict]:
        """
        Search for multiple matching titles across all available providers.

        Args:
            title: Title to search for
            limit: Maximum number of results to return per source
            year: Specific year to match (exact matching)
            media_type: Type of media ('movie' or 'series')
            created_year: Year used for sorting when exact year match isn't required
            min_similarity: Minimum title similarity score (0-100) for fuzzy matching
            include_anime: Whether to search MAL/Kitsu for anime
        """
        # Check cache first
        cached_data = await self.cache.get(
            method="search_multiple_results",
            title=title,
            limit=limit,
            year=year,
            media_type=media_type,
            created_year=created_year,
            min_similarity=min_similarity,
            include_anime=include_anime,
        )
        if cached_data:
            logging.debug(f"Cache hit for search_multiple_results: {title}")
            return cached_data.get("results", [])

        async def get_tmdb_candidates() -> list[dict[str, Any]]:
            if not self.config.can_use_tmdb:
                return []
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

        async def get_imdb_candidates() -> list[dict[str, Any]]:
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

        async def get_tvdb_candidates() -> list[dict[str, Any]]:
            if not self.config.can_use_tvdb:
                return []
            try:
                return await search_multiple_tvdb(
                    title=title,
                    limit=limit,
                    media_type=media_type,
                )
            except Exception as e:
                logging.error(f"Error searching TVDB: {e}")
                return []

        async def get_mal_candidates() -> list[dict[str, Any]]:
            if not include_anime:
                return []
            try:
                return await search_multiple_mal(
                    title=title,
                    limit=limit,
                    media_type=media_type,
                )
            except Exception as e:
                logging.error(f"Error searching MAL: {e}")
                return []

        async def get_kitsu_candidates() -> list[dict[str, Any]]:
            if not include_anime:
                return []
            try:
                return await search_multiple_kitsu(
                    title=title,
                    limit=limit,
                    media_type=media_type,
                )
            except Exception as e:
                logging.error(f"Error searching Kitsu: {e}")
                return []

        async def get_db_candidates() -> list[dict[str, Any]]:
            try:
                from db.crud.media import (
                    get_all_external_ids_batch,
                    get_media_images,
                    search_media,
                )
                from db.database import get_read_session_context
                from db.enums import MediaType as DBMediaType

                db_media_type = None
                if media_type == "movie":
                    db_media_type = DBMediaType.MOVIE
                elif media_type == "series":
                    db_media_type = DBMediaType.SERIES

                async with get_read_session_context() as session:
                    results = await search_media(session, title, db_media_type, limit=limit)
                    if not results:
                        return []

                    media_ids = [m.id for m in results]
                    ext_ids_batch = await get_all_external_ids_batch(session, media_ids)

                    candidates = []
                    for media in results:
                        ext_ids = ext_ids_batch.get(media.id, {})
                        imdb_id = ext_ids.get("imdb")
                        if not imdb_id and not ext_ids:
                            continue

                        poster_url = None
                        try:
                            images = await get_media_images(session, media.id, image_type="poster")
                            if images:
                                poster_url = images[0].url
                        except Exception:
                            pass

                        candidate: dict[str, Any] = {
                            "title": media.title,
                            "year": media.year,
                            "type": media.type.value,
                            "poster": poster_url,
                            "_source_provider": "db",
                        }
                        if imdb_id:
                            candidate["imdb_id"] = imdb_id
                        if ext_ids.get("tmdb"):
                            candidate["tmdb_id"] = ext_ids["tmdb"]
                        if ext_ids.get("tvdb"):
                            candidate["tvdb_id"] = ext_ids["tvdb"]
                        if ext_ids.get("mal"):
                            candidate["mal_id"] = ext_ids["mal"]
                        if ext_ids.get("kitsu"):
                            candidate["kitsu_id"] = ext_ids["kitsu"]

                        candidates.append(candidate)
                    return candidates
            except Exception as e:
                logging.debug(f"DB search failed for '{title}': {e}")
                return []

        # Fetch from all sources in parallel (DB first, then external providers)
        all_results = await asyncio.gather(
            get_db_candidates(),
            get_imdb_candidates(),
            get_tmdb_candidates(),
            get_tvdb_candidates(),
            get_mal_candidates(),
            get_kitsu_candidates(),
        )

        # Combine results with DB entries first, then deduplicate
        results = []
        seen_ids: set[str] = set()
        for source_results in all_results:
            for item in source_results:
                dedup_key = None
                imdb_id = item.get("imdb_id")
                if imdb_id:
                    dedup_key = f"imdb:{imdb_id}"
                elif item.get("tmdb_id"):
                    dedup_key = f"tmdb:{item['tmdb_id']}"
                elif item.get("tvdb_id"):
                    dedup_key = f"tvdb:{item['tvdb_id']}"

                if dedup_key and dedup_key in seen_ids:
                    continue
                if dedup_key:
                    seen_ids.add(dedup_key)
                results.append(item)

        # Cache the results
        if results:
            await self.cache.set(
                {"results": results},
                method="search_multiple_results",
                title=title,
                limit=limit,
                year=year,
                media_type=media_type,
                created_year=created_year,
                min_similarity=min_similarity,
                include_anime=include_anime,
            )

        return results


# Create a singleton instance with 30-minute cache TTL
meta_fetcher = MetadataFetcher(cache_ttl_minutes=30)
