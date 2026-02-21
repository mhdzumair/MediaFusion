"""
Scraping API endpoints for content stream discovery.
Allows users to trigger scraping and view scrape status.
"""

import hashlib
import logging
import time
from datetime import datetime
from typing import Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import update as sa_update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db import crud
from db.database import get_async_session
from db.enums import UserRole
from db.models import Media, MediaExternalID, User, UserProfile
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import ExternalIDs, MetadataData, UserData
from scrapers import scraper_tasks
from utils import runtime_const
from db.config import settings
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/scraping", tags=["Scraping"])


# ============================================
# Scraper Configuration
# ============================================

# Scraper definitions with metadata
# requires_debrid: True if scraper needs a streaming provider to work
# is_user_configurable: True if this scraper can be configured per-user
SCRAPER_CONFIG = {
    "prowlarr": {
        "name": "Prowlarr",
        "enabled": settings.is_scrap_from_prowlarr,
        "requires_debrid": False,
        "ttl": runtime_const.PROWLARR_SEARCH_TTL,
        "description": "Search via Prowlarr indexer manager",
        "is_user_configurable": True,
    },
    "zilean": {
        "name": "Zilean",
        "enabled": settings.is_scrap_from_zilean,
        "requires_debrid": False,
        "ttl": runtime_const.ZILEAN_SEARCH_TTL,
        "description": "Search Zilean DMM database",
        "is_user_configurable": False,
    },
    "torrentio": {
        "name": "Torrentio",
        "enabled": settings.is_scrap_from_torrentio,
        "requires_debrid": True,  # Requires debrid service
        "ttl": runtime_const.TORRENTIO_SEARCH_TTL,
        "description": "Search Torrentio addon (requires debrid)",
        "is_user_configurable": False,
    },
    "mediafusion": {
        "name": "MediaFusion",
        "enabled": settings.is_scrap_from_mediafusion,
        "requires_debrid": True,  # Requires debrid service
        "ttl": runtime_const.MEDIAFUSION_SEARCH_TTL,
        "description": "Search MediaFusion addon (requires debrid)",
        "is_user_configurable": False,
    },
    "yts": {
        "name": "YTS",
        "enabled": settings.is_scrap_from_yts,
        "requires_debrid": False,
        "ttl": runtime_const.YTS_SEARCH_TTL,
        "description": "Search YTS for movies only",
        "is_user_configurable": False,
    },
    "bt4g": {
        "name": "BT4G",
        "enabled": settings.is_scrap_from_bt4g,
        "requires_debrid": False,
        "ttl": runtime_const.BT4G_SEARCH_TTL,
        "description": "Search BT4G torrent search",
        "is_user_configurable": False,
    },
    "jackett": {
        "name": "Jackett",
        "enabled": settings.is_scrap_from_jackett,
        "requires_debrid": False,
        "ttl": runtime_const.JACKETT_SEARCH_TTL,
        "description": "Search via Jackett indexer",
        "is_user_configurable": True,
    },
    "torznab": {
        "name": "Custom Torznab",
        "enabled": settings.is_scrap_from_torznab,
        "requires_debrid": False,
        "ttl": runtime_const.TORZNAB_SEARCH_TTL,
        "description": "Search via custom Torznab endpoints",
        "is_user_configurable": True,
    },
    "torbox_search": {
        "name": "TorBox Search",
        "enabled": False,  # Only enabled when user has TorBox configured
        "requires_debrid": True,  # Requires TorBox debrid service
        "ttl": runtime_const.TORBOX_SEARCH_TTL,
        "description": "Search TorBox API for torrents and Usenet (requires TorBox)",
        "is_user_configurable": True,
    },
    "newznab": {
        "name": "Newznab Indexers",
        "enabled": False,  # Only enabled when user has Newznab indexers configured
        "requires_debrid": False,  # Can work with any Usenet-capable provider
        "ttl": runtime_const.PROWLARR_SEARCH_TTL,  # Use same TTL as Prowlarr
        "description": "Search Newznab-compatible indexers for Usenet content",
        "is_user_configurable": True,
        "is_usenet": True,  # Mark as Usenet scraper
    },
    "easynews": {
        "name": "Easynews",
        "enabled": False,  # Only enabled when user has Easynews configured
        "requires_debrid": False,  # Easynews is its own streaming service
        "ttl": runtime_const.PROWLARR_SEARCH_TTL,
        "description": "Search Easynews for direct Usenet streaming",
        "is_user_configurable": True,
        "is_usenet": True,  # Mark as Usenet scraper
    },
}


# ============================================
# Pydantic Schemas
# ============================================


class ScraperInfo(BaseModel):
    """Information about a scraper."""

    id: str
    name: str
    enabled: bool
    requires_debrid: bool
    ttl: int
    description: str


class ScraperStatusInfo(BaseModel):
    """Status info for a specific scraper."""

    last_scraped: str | None = None
    cooldown_remaining: int = 0
    can_scrape: bool = True
    ttl: int
    enabled: bool = True
    requires_debrid: bool = False


class ScrapeStatusResponse(BaseModel):
    """Response with scrape status information."""

    media_id: int
    title: str | None = None
    last_scraped_at: datetime | None = None
    cooldown_remaining: int | None = Field(
        default=None, description="Seconds remaining before scraping is allowed again"
    )
    can_scrape: bool = Field(default=True, description="Whether scraping can be triggered now")
    scraper_statuses: dict[str, ScraperStatusInfo] | None = Field(
        default=None, description="Per-scraper cooldown status"
    )
    available_scrapers: list[ScraperInfo] = Field(default_factory=list, description="List of available scrapers")
    is_moderator: bool = Field(default=False, description="Whether user can force scrape")
    has_debrid: bool = Field(default=False, description="Whether user has a debrid service configured")


class ScrapeRequest(BaseModel):
    """Request to trigger scraping for content."""

    media_type: Literal["movie", "series"]
    season: int | None = Field(default=None, description="Season number (required for series)")
    episode: int | None = Field(default=None, description="Episode number (required for series)")
    force: bool = Field(default=False, description="Force scraping even if within cooldown (moderators/admins only)")
    scrapers: list[str] | None = Field(default=None, description="List of scraper IDs to use (None = all enabled)")


class ScrapeResponse(BaseModel):
    """Response from scrape operation."""

    status: str
    message: str
    media_id: int
    title: str | None = None
    streams_found: int = 0
    scraped_at: datetime | None = None
    scrapers_used: list[str] = Field(default_factory=list)
    scrapers_skipped: list[str] = Field(default_factory=list)


# ============================================
# Helper Functions
# ============================================


def get_full_profile_config(profile: UserProfile) -> dict:
    """Get the full config with secrets decrypted and merged."""
    config = profile.config or {}

    if profile.encrypted_secrets:
        try:
            secrets = profile_crypto.decrypt_secrets(profile.encrypted_secrets)
            config = profile_crypto.merge_secrets(config, secrets)
        except Exception as e:
            logger.warning(f"Failed to decrypt profile secrets: {e}")

    return config


def _sanitize_user_data_config(config: dict) -> dict:
    """Sanitize known legacy/invalid profile values before UserData validation."""
    sanitized = dict(config)

    for key in ("sr", "selected_resolutions"):
        resolutions = sanitized.get(key)
        if not isinstance(resolutions, list):
            continue

        cleaned_resolutions = [res for res in resolutions if isinstance(res, str) and res.strip()]
        if cleaned_resolutions:
            sanitized[key] = cleaned_resolutions
        else:
            # Remove invalid/empty list and let UserData defaults apply.
            sanitized.pop(key, None)

    return sanitized


async def get_user_data_from_profile(
    session: AsyncSession, user: User, profile_id: int | None = None
) -> tuple[UserData | None, bool, set[str]]:
    """
    Get UserData from user's profile.

    Args:
        session: Database session
        user: Current user
        profile_id: Optional profile ID (uses default if not provided)

    Returns:
        Tuple of (UserData or None, has_debrid, set of streaming_provider_names)
    """
    # Get the profile
    if profile_id:
        result = await session.exec(
            select(UserProfile).where(UserProfile.id == profile_id, UserProfile.user_id == user.id)
        )
        profile = result.first()
        if not profile:
            return None, False, None
    else:
        # Get default profile
        result = await session.exec(
            select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
        )
        profile = result.first()
        if not profile:
            # Get any profile
            result = await session.exec(select(UserProfile).where(UserProfile.user_id == user.id))
            profile = result.first()

    if not profile:
        logger.debug(f"No profile found for user {user.id}")
        return None, False, None

    # Get full config with decrypted secrets
    config = get_full_profile_config(profile)

    # Check if user has debrid configured and collect all provider names
    has_debrid = False
    provider_names: set[str] = set()
    streaming_providers = config.get("sps") or config.get("streaming_providers", [])
    streaming_provider = config.get("sp") or config.get("streaming_provider")

    logger.debug(f"streaming_providers: {streaming_providers}, streaming_provider: {streaming_provider}")

    # Services that are NOT debrid (don't set has_debrid for these)
    non_debrid_services = {"easynews", "sabnzbd", "nzbget"}

    if streaming_providers and isinstance(streaming_providers, list):
        for sp in streaming_providers:
            if isinstance(sp, dict):
                service = sp.get("sv") or sp.get("service")
                is_enabled = sp.get("en", True)  # Default to enabled if not specified
                if service and is_enabled:
                    # Only set has_debrid for actual debrid services
                    if service not in non_debrid_services:
                        has_debrid = True
                    provider_names.add(service)
                    logger.debug(
                        f"Found streaming provider: {service} (is_debrid={service not in non_debrid_services})"
                    )
    elif streaming_provider and isinstance(streaming_provider, dict):
        service = streaming_provider.get("sv") or streaming_provider.get("service")
        if service:
            has_debrid = True
            provider_names.add(service)
            logger.debug(f"Found debrid provider in streaming_provider: {service}")

    # If no debrid, still return False but don't try to build UserData for debrid scrapers
    if not has_debrid:
        logger.debug(f"User {user.id} has no debrid service configured")
        return None, False, set()

    # Build UserData from config
    try:
        user_data = UserData.model_validate(config)
        logger.info(f"User profile: has_debrid={has_debrid}, provider_names={provider_names}")
        return user_data, True, provider_names
    except Exception as e:
        sanitized_config = _sanitize_user_data_config(config)
        if sanitized_config != config:
            try:
                user_data = UserData.model_validate(sanitized_config)
                logger.warning(
                    "Recovered UserData validation after sanitizing profile config for user %s: %s",
                    user.id,
                    e,
                )
                logger.info(f"User profile: has_debrid={has_debrid}, provider_names={provider_names}")
                return user_data, True, provider_names
            except Exception:
                pass

        logger.warning(f"Failed to build UserData from profile config: {e}")
        return None, has_debrid, provider_names  # Return has_debrid status even if UserData build fails


def get_available_scrapers(
    has_debrid: bool = False,
    indexer_config: dict | None = None,
    streaming_providers: set[str] | None = None,
) -> list[ScraperInfo]:
    """
    Get list of available scrapers.

    Args:
        has_debrid: Whether user has a debrid service configured
        indexer_config: User's indexer configuration from profile (ic field)
        streaming_providers: Set of streaming provider names (e.g., {"torbox", "realdebrid"})
    """
    logger.info(f"get_available_scrapers: has_debrid={has_debrid}, streaming_providers={streaming_providers}")
    if streaming_providers is None:
        streaming_providers = set()
    scrapers = []

    for scraper_id, config in SCRAPER_CONFIG.items():
        is_enabled = config["enabled"]

        # Check user's indexer config for user-configurable scrapers
        if config.get("is_user_configurable"):
            if scraper_id == "prowlarr":
                if indexer_config:
                    pr_config = indexer_config.get("pr")
                    if pr_config and pr_config.get("en"):
                        # User has prowlarr enabled (either global or custom)
                        is_enabled = True
                    elif not config["enabled"]:
                        # Global not enabled and user hasn't enabled it
                        is_enabled = False
            elif scraper_id == "jackett":
                if indexer_config:
                    jk_config = indexer_config.get("jk")
                    if jk_config and jk_config.get("en"):
                        is_enabled = True
                    elif not config["enabled"]:
                        is_enabled = False
            elif scraper_id == "torznab":
                # Torznab is available when merged user/global endpoint set is non-empty
                is_enabled = len(scraper_tasks.get_effective_torznab_endpoints(indexer_config)) > 0
            elif scraper_id == "torbox_search":
                # TorBox Search is only available if user has TorBox configured
                is_enabled = "torbox" in streaming_providers and has_debrid
            elif scraper_id == "newznab":
                # Newznab is available if user has configured indexers in indexer_config.nz
                if indexer_config:
                    nz_indexers = indexer_config.get("nz", [])
                    if nz_indexers and len(nz_indexers) > 0:
                        is_enabled = any(idx.get("en", True) for idx in nz_indexers)
                    else:
                        is_enabled = False
                else:
                    is_enabled = False
            elif scraper_id == "easynews":
                # Easynews is only available if user has Easynews configured as a streaming provider
                is_enabled = "easynews" in streaming_providers

        if is_enabled:
            scrapers.append(
                ScraperInfo(
                    id=scraper_id,
                    name=config["name"],
                    enabled=True,
                    requires_debrid=config["requires_debrid"],
                    ttl=config["ttl"],
                    description=config["description"],
                )
            )

    return scrapers


def _get_torznab_cache_prefix(indexer_config: dict | None) -> str | None:
    """Generate the torznab cache prefix matching TorznabScraper._generate_cache_prefix."""
    effective_endpoints = scraper_tasks.get_effective_torznab_endpoints(indexer_config)
    if not effective_endpoints:
        return None

    # Get enabled endpoint URLs (matching TorznabScraper logic)
    urls = sorted(endpoint.url for endpoint in effective_endpoints)
    if not urls:
        return None

    url_hash = hashlib.md5(":".join(urls).encode()).hexdigest()[:8]
    return f"torznab:{url_hash}"


async def get_scraper_cooldown_status(
    meta_id: str,
    catalog_type: str,
    season: int = None,
    episode: int = None,
    indexer_config: dict | None = None,
    streaming_providers: set[str] | None = None,
    has_debrid: bool = False,
) -> dict[str, ScraperStatusInfo]:
    """Get cooldown status for each scraper."""
    current_time = int(time.time())
    statuses = {}
    if streaming_providers is None:
        streaming_providers = set()

    # Pre-compute torznab cache prefix if user has torznab configured
    torznab_cache_prefix = _get_torznab_cache_prefix(indexer_config)

    for scraper_id, config in SCRAPER_CONFIG.items():
        is_enabled = config["enabled"]

        # Check user's indexer config for user-configurable scrapers
        if config.get("is_user_configurable"):
            if scraper_id == "prowlarr":
                if indexer_config:
                    pr_config = indexer_config.get("pr")
                    if pr_config and pr_config.get("en"):
                        is_enabled = True
                    elif not config["enabled"]:
                        is_enabled = False
            elif scraper_id == "jackett":
                if indexer_config:
                    jk_config = indexer_config.get("jk")
                    if jk_config and jk_config.get("en"):
                        is_enabled = True
                    elif not config["enabled"]:
                        is_enabled = False
            elif scraper_id == "torznab":
                is_enabled = len(scraper_tasks.get_effective_torznab_endpoints(indexer_config)) > 0
            elif scraper_id == "torbox_search":
                # TorBox Search is only enabled if user has TorBox configured
                is_enabled = "torbox" in streaming_providers and has_debrid
            elif scraper_id == "newznab":
                # Newznab is only enabled if user has configured indexers in indexer_config.nz
                if indexer_config:
                    nz_indexers = indexer_config.get("nz", [])
                    if nz_indexers and len(nz_indexers) > 0:
                        is_enabled = any(idx.get("en", True) for idx in nz_indexers)
                    else:
                        is_enabled = False
                else:
                    is_enabled = False
            elif scraper_id == "easynews":
                # Easynews is only enabled if user has Easynews configured
                is_enabled = "easynews" in streaming_providers

        if not is_enabled:
            continue

        ttl = config["ttl"]

        # Build cache key similar to BaseScraper.get_cache_key
        if catalog_type == "movie":
            cache_key = f"{catalog_type}:{meta_id}"
        else:
            cache_key = f"{catalog_type}:{meta_id}:{season}:{episode}"

        # For torznab, use the user-specific cache prefix
        redis_key = scraper_id
        if scraper_id == "torznab" and torznab_cache_prefix:
            redis_key = torznab_cache_prefix

        # Check Redis sorted set for last scrape time
        score = await REDIS_ASYNC_CLIENT.zscore(redis_key, cache_key)

        if score:
            time_since_scrape = current_time - int(score)
            cooldown_remaining = max(0, ttl - time_since_scrape)
            statuses[scraper_id] = ScraperStatusInfo(
                last_scraped=datetime.fromtimestamp(score, tz=pytz.UTC).isoformat(),
                cooldown_remaining=cooldown_remaining,
                can_scrape=cooldown_remaining == 0,
                ttl=ttl,
                enabled=True,
                requires_debrid=config["requires_debrid"],
            )
        else:
            statuses[scraper_id] = ScraperStatusInfo(
                last_scraped=None,
                cooldown_remaining=0,
                can_scrape=True,
                ttl=ttl,
                enabled=True,
                requires_debrid=config["requires_debrid"],
            )

    return statuses


async def clear_scraper_cooldowns(
    meta_id: str,
    catalog_type: str,
    season: int = None,
    episode: int = None,
    scrapers: list[str] | None = None,
    indexer_config: dict | None = None,
) -> None:
    """Clear cooldown entries for specified scrapers (or all if None)."""
    if catalog_type == "movie":
        cache_key = f"{catalog_type}:{meta_id}"
    else:
        cache_key = f"{catalog_type}:{meta_id}:{season}:{episode}"

    # Pre-compute torznab cache prefix if clearing torznab
    torznab_cache_prefix = _get_torznab_cache_prefix(indexer_config)

    scrapers_to_clear = scrapers if scrapers else list(SCRAPER_CONFIG.keys())
    for scraper_id in scrapers_to_clear:
        if scraper_id in SCRAPER_CONFIG:
            redis_key = scraper_id
            # For torznab, use the user-specific cache prefix
            if scraper_id == "torznab" and torznab_cache_prefix:
                redis_key = torznab_cache_prefix
            await REDIS_ASYNC_CLIENT.zrem(redis_key, cache_key)


# ============================================
# API Endpoints
# ============================================


@router.get("/scrapers", response_model=list[ScraperInfo])
async def list_scrapers(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List all available scrapers and their configuration.
    """
    # Get user's profile for debrid service and indexer config
    _, has_debrid, streaming_provider = await get_user_data_from_profile(session, user)

    # Get user's indexer config from profile
    indexer_config = None
    profile_result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    default_profile = profile_result.first()
    if default_profile and default_profile.config:
        indexer_config = default_profile.config.get("ic")

    return get_available_scrapers(has_debrid, indexer_config, streaming_provider)


@router.get("/{media_id}/status", response_model=ScrapeStatusResponse)
async def get_scrape_status(
    media_id: int,
    media_type: Literal["movie", "series"] = "movie",
    season: int | None = None,
    episode: int | None = None,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get scraping status for a media item.

    Returns the last scrape time and cooldown information.
    """
    is_moderator = user.role in (UserRole.MODERATOR, UserRole.ADMIN)

    # Get the media record
    result = await session.exec(select(Media).where(Media.id == media_id))
    media = result.first()
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with ID {media_id} not found.",
        )

    # Get external IDs for cache key lookup
    external_ids_result = await session.exec(select(MediaExternalID).where(MediaExternalID.media_id == media_id))
    external_ids_records = external_ids_result.all()

    # Get canonical external ID for cache key
    meta_id = None
    for ext_id in external_ids_records:
        if ext_id.provider == "imdb":
            meta_id = ext_id.external_id
            break
        elif ext_id.provider == "tmdb" and not meta_id:
            meta_id = f"tmdb:{ext_id.external_id}"
        elif ext_id.provider == "tvdb" and not meta_id:
            meta_id = f"tvdb:{ext_id.external_id}"

    if not meta_id:
        meta_id = f"mf:{media_id}"

    # Check if user has debrid configured - needed for scraper availability
    _, has_debrid, streaming_provider = await get_user_data_from_profile(session, user)

    # Get user's indexer config from profile (before cooldown status)
    indexer_config = None
    profile_result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    default_profile = profile_result.first()
    if default_profile and default_profile.config:
        indexer_config = default_profile.config.get("ic")

    # Get per-scraper cooldown status
    scraper_statuses = await get_scraper_cooldown_status(
        meta_id, media_type, season, episode, indexer_config, streaming_provider, has_debrid
    )

    # Calculate overall cooldown (only for non-debrid scrapers since we don't have debrid)
    min_cooldown = None
    can_scrape_any = False
    for scraper_id, scraper_status in scraper_statuses.items():
        # Skip debrid-requiring scrapers for cooldown calculation
        if scraper_status.requires_debrid:
            continue
        if scraper_status.can_scrape:
            can_scrape_any = True
        if scraper_status.cooldown_remaining > 0:
            if min_cooldown is None or scraper_status.cooldown_remaining < min_cooldown:
                min_cooldown = scraper_status.cooldown_remaining

    # If user has debrid, include debrid scrapers in cooldown calculation
    if has_debrid:
        for scraper_id, scraper_status in scraper_statuses.items():
            if scraper_status.can_scrape:
                can_scrape_any = True
            if scraper_status.cooldown_remaining > 0:
                if min_cooldown is None or scraper_status.cooldown_remaining < min_cooldown:
                    min_cooldown = scraper_status.cooldown_remaining

    return ScrapeStatusResponse(
        media_id=media_id,
        title=media.title,
        last_scraped_at=media.last_scraped_at,
        cooldown_remaining=min_cooldown,
        can_scrape=can_scrape_any or media.last_scraped_at is None,
        scraper_statuses=scraper_statuses,
        available_scrapers=get_available_scrapers(has_debrid, indexer_config, streaming_provider),
        is_moderator=is_moderator,
        has_debrid=has_debrid,
    )


@router.post("/{media_id}/scrape", response_model=ScrapeResponse)
async def trigger_scrape(
    media_id: int,
    request: ScrapeRequest,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Trigger scraping for a media item.

    For series, season and episode must be provided.
    Force option is only available for moderators and admins.
    """
    from sqlalchemy.orm import selectinload

    is_moderator = user.role in (UserRole.MODERATOR, UserRole.ADMIN)

    # Validate force permission
    if request.force and not is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Force scraping is only available for moderators and admins.",
        )

    # Validate series parameters
    if request.media_type == "series":
        if request.season is None or request.episode is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Season and episode are required for series scraping.",
            )

    # Get the media record with external IDs and aka_titles
    query = (
        select(Media)
        .where(Media.id == media_id)
        .options(
            selectinload(Media.external_ids),
            selectinload(Media.aka_titles),
        )
    )
    result = await session.exec(query)
    media = result.first()
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with ID {media_id} not found.",
        )

    # Build external_ids dict
    external_ids: dict[str, str] = {}
    for ext_id in media.external_ids:
        external_ids[ext_id.provider] = ext_id.external_id

    if not external_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No external IDs found for this media. Link an external ID first.",
        )

    # Get canonical external ID for cache key - prefer IMDb
    meta_id = None
    if external_ids.get("imdb"):
        meta_id = external_ids["imdb"]
    elif external_ids.get("tmdb"):
        meta_id = f"tmdb:{external_ids['tmdb']}"
    elif external_ids.get("tvdb"):
        meta_id = f"tvdb:{external_ids['tvdb']}"
    else:
        meta_id = f"mf:{media_id}"

    # Get user's profile for debrid service and indexer config
    user_data, has_debrid, streaming_provider = await get_user_data_from_profile(session, user)

    # Get user's indexer config from profile
    indexer_config = None
    profile_result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    default_profile = profile_result.first()
    if default_profile and default_profile.config:
        indexer_config = default_profile.config.get("ic")

    # Determine which scrapers to use based on request and user config
    requested_scrapers = request.scrapers

    # Build list of available scrapers considering user's indexer config
    available_scraper_ids = [s.id for s in get_available_scrapers(has_debrid, indexer_config, streaming_provider)]

    if requested_scrapers:
        # Validate requested scrapers
        invalid_scrapers = [s for s in requested_scrapers if s not in SCRAPER_CONFIG]
        if invalid_scrapers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid scraper IDs: {', '.join(invalid_scrapers)}",
            )
        # Filter to only scrapers that are available (enabled globally or via user config)
        scrapers_to_use = [s for s in requested_scrapers if s in available_scraper_ids]
    else:
        # Use all available scrapers
        scrapers_to_use = available_scraper_ids

    # Filter out scrapers that require debrid if user doesn't have debrid configured
    scrapers_skipped = []
    scrapers_available = []
    for scraper_id in scrapers_to_use:
        if SCRAPER_CONFIG[scraper_id]["requires_debrid"] and not has_debrid:
            scrapers_skipped.append(scraper_id)
            logger.info(f"Skipping {scraper_id} - requires debrid service (user has none configured)")
        else:
            scrapers_available.append(scraper_id)

    if not scrapers_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No scrapers available. All selected scrapers require a debrid service.",
        )

    # Check cooldown if not forcing
    if not request.force:
        scraper_statuses = await get_scraper_cooldown_status(
            meta_id, request.media_type, request.season, request.episode, indexer_config, streaming_provider, has_debrid
        )
        # Check only the scrapers we're going to use
        can_scrape_any = any(scraper_statuses[s].can_scrape for s in scrapers_available if s in scraper_statuses)

        if not can_scrape_any:
            cooldowns = [
                scraper_statuses[s].cooldown_remaining
                for s in scrapers_available
                if s in scraper_statuses and scraper_statuses[s].cooldown_remaining > 0
            ]
            min_cooldown = min(cooldowns) if cooldowns else 0
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"All selected scrapers are on cooldown. Try again in {min_cooldown} seconds.",
            )

    # Clear cooldowns if forcing (only for selected scrapers)
    if request.force:
        await clear_scraper_cooldowns(
            meta_id,
            request.media_type,
            request.season,
            request.episode,
            scrapers=scrapers_available,
            indexer_config=indexer_config,
        )

    # Extract aka_titles safely (already eagerly loaded)
    aka_titles_list = [aka.title for aka in media.aka_titles] if media.aka_titles else []

    # Build ExternalIDs object from the eagerly loaded external_ids
    external_ids_obj = ExternalIDs.from_db(media.external_ids) if media.external_ids else None

    # Build metadata for scrapers
    metadata = MetadataData(
        id=media.id,
        external_id=meta_id,
        type=request.media_type,
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        release_date=media.release_date,
        aka_titles=aka_titles_list,
        external_ids=external_ids_obj,
    )

    # Use user's profile data if available, otherwise create minimal user data
    if not user_data:
        user_data = UserData(
            streaming_provider=None,
            selected_catalogs=[],
            selected_resolutions=[],
            enable_catalogs=False,
        )

    # Run torrent scrapers with selected list
    torrent_streams = []
    usenet_streams = []

    try:
        torrent_streams = await scraper_tasks.run_scrapers(
            user_data=user_data,
            metadata=metadata,
            catalog_type=request.media_type,
            season=request.season,
            episode=request.episode,
            selected_scrapers=scrapers_available,  # Pass the filtered list
        )
        logger.info(f"Found {len(torrent_streams)} torrent streams for media_id={media_id}")
    except Exception as e:
        logger.exception(f"Error running torrent scrapers for media {media_id}: {e}")
        # Continue to Usenet scrapers even if torrent fails

    # Run Usenet scrapers if user has Usenet-capable providers (TorBox, Debrider, Easynews)
    # or if they have Newznab indexers configured
    has_usenet_provider = (
        "torbox" in streaming_provider or "debrider" in streaming_provider or "easynews" in streaming_provider
    )
    # Check indexer_config.newznab_indexers
    has_newznab_indexers = user_data and user_data.indexer_config and user_data.indexer_config.newznab_indexers

    if has_usenet_provider or has_newznab_indexers:
        try:
            usenet_streams = await scraper_tasks.run_usenet_scrapers(
                user_data=user_data,
                metadata=metadata,
                catalog_type=request.media_type,
                season=request.season,
                episode=request.episode,
                selected_scrapers=scrapers_available,  # Pass the filtered list
            )
            logger.info(f"Found {len(usenet_streams)} Usenet streams for media_id={media_id}")
        except Exception as e:
            logger.exception(f"Error running Usenet scrapers for media {media_id}: {e}")
            # Continue even if Usenet scrapers fail

    streams_count = len(torrent_streams) + len(usenet_streams)

    # Store scraped torrent streams in database
    if torrent_streams:
        try:
            stored_count = await crud.store_new_torrent_streams(
                session,
                [s.model_dump(by_alias=True) for s in torrent_streams],
            )
            logger.info(f"Stored {stored_count} new torrent streams for media_id={media_id}")
        except Exception as e:
            logger.exception(f"Error storing torrent streams for media {media_id}: {e}")
            # Don't fail the request, just log the error

    # Store scraped Usenet streams in database
    if usenet_streams:
        try:
            stored_count = await crud.store_new_usenet_streams(
                session,
                [s.model_dump(by_alias=True) for s in usenet_streams],
            )
            logger.info(f"Stored {stored_count} new Usenet streams for media_id={media_id}")
        except Exception as e:
            logger.exception(f"Error storing Usenet streams for media {media_id}: {e}")
            # Don't fail the request, just log the error

    # Update last_scraped_at
    scraped_at = datetime.now(pytz.UTC)
    await session.exec(
        sa_update(Media)
        .where(Media.id == media_id)
        .values(
            last_scraped_at=scraped_at,
            last_scraped_by_user_id=user.id,
        )
    )
    await session.commit()

    logger.info(
        f"User {user.id} triggered scrape for media_id={media_id}, "
        f"scrapers={scrapers_available}, found {streams_count} streams"
    )

    return ScrapeResponse(
        status="success",
        message=f"Scraping completed. Found {streams_count} streams.",
        media_id=media_id,
        title=media.title,
        streams_found=streams_count,
        scraped_at=scraped_at,
        scrapers_used=scrapers_available,
        scrapers_skipped=scrapers_skipped,
    )
