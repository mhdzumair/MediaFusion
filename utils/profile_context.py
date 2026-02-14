"""
Profile Context Utilities

Provides a unified system for managing authenticated user profile data,
including Redis caching (encrypted) and per-request decryption.

This module separates the authenticated UI flow from the Stremio addon flow:
- Stremio: Uses secret_str in URL -> UserData (existing middleware)
- UI: Uses JWT -> ProfileContext -> UserData (this module)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.schemas.config import StreamingProvider

from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import UserProfile
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)


class CachedProfileData(BaseModel):
    """
    Encrypted profile data stored in Redis.

    This stores the raw profile data as it exists in the database,
    with secrets still encrypted. Decryption happens per-request.
    """

    profile_id: int  # Auto-increment integer ID
    config: dict  # Non-sensitive config (stored plain in DB)
    encrypted_secrets: str | None = None  # Still AES-encrypted
    updated_at: float  # Timestamp for cache validation


class ProfileContext(BaseModel):
    """
    Resolved profile data for authenticated users.

    This is built per-request by decrypting secrets and constructing
    a UserData instance. It provides convenient access to commonly
    needed profile attributes.
    """

    user_id: int
    profile_id: int | None = None
    user_data: UserData
    rpdb_api_key: str | None = None
    has_mediaflow_configured: bool = False  # Whether MediaFlow proxy URL and password are configured
    web_playback_enabled: bool = False  # Whether web browser playback is enabled (requires MediaFlow)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def empty(cls, user_id: int) -> "ProfileContext":
        """Create empty context for users without configured profiles."""
        return cls(
            user_id=user_id,
            profile_id=None,
            user_data=UserData(),
            has_mediaflow_configured=False,
            web_playback_enabled=False,
        )


class ProfileDataProvider:
    """
    Manages profile loading with Redis caching.

    Caching Strategy:
    - Stores encrypted profile data in Redis (config + encrypted_secrets)
    - Decrypts secrets per-request (never stores decrypted data)
    - Invalidates cache on profile update/delete

    This provides:
    - Performance: Skip DB queries on cache hit
    - Security: Secrets remain encrypted in Redis
    """

    CACHE_TTL = 300  # 5 minutes
    CACHE_PREFIX = "profile_enc:"

    @classmethod
    async def get_context(cls, user_id: int, session: AsyncSession, profile_id: int | None = None) -> ProfileContext:
        """
        Get profile context for authenticated user.

        Flow:
        1. Check Redis cache for encrypted profile data
        2. On miss, load from database and cache
        3. Decrypt secrets per-request
        4. Build and return ProfileContext with UserData

        Args:
            user_id: The authenticated user's ID
            session: Database session for profile lookup
            profile_id: Optional specific profile ID to load (must belong to user)

        Returns:
            ProfileContext with resolved UserData
        """
        # If specific profile requested, load it directly (no caching for specific profiles)
        if profile_id is not None:
            profile = await cls._get_profile_by_id(user_id, profile_id, session)
            if not profile:
                logger.debug(f"Profile {profile_id} not found for user {user_id}, falling back to default")
                # Fall back to default profile
                profile_id = None
            else:
                # Build context directly from this profile (skip cache)
                cached = CachedProfileData(
                    profile_id=profile.id,
                    config=profile.config or {},
                    encrypted_secrets=profile.encrypted_secrets,
                    updated_at=profile.updated_at.timestamp() if profile.updated_at else 0,
                )
                return await cls._build_context_from_cached(user_id, cached)

        # 1. Try Redis cache first (encrypted data only) - only for default profile
        cached = await cls._get_cached_profile(user_id)

        if not cached:
            # 2. Load from database
            profile = await cls._get_active_profile(user_id, session)
            if not profile:
                logger.debug(f"No active profile found for user {user_id}")
                return ProfileContext.empty(user_id)

            # 3. Cache encrypted data in Redis
            cached = CachedProfileData(
                profile_id=profile.id,
                config=profile.config or {},
                encrypted_secrets=profile.encrypted_secrets,
                updated_at=profile.updated_at.timestamp() if profile.updated_at else 0,
            )
            await cls._cache_profile(user_id, cached)
            logger.debug(f"Cached profile for user {user_id}")

        return await cls._build_context_from_cached(user_id, cached)

    @classmethod
    async def _build_context_from_cached(cls, user_id: int, cached: CachedProfileData) -> ProfileContext:
        """Build ProfileContext from cached profile data."""
        # Decrypt secrets PER REQUEST (never cached decrypted)
        try:
            full_config = profile_crypto.get_full_config(cached.config, cached.encrypted_secrets)
        except Exception as e:
            logger.error(f"Failed to decrypt profile secrets for user {user_id}: {e}")
            return ProfileContext.empty(user_id)

        # Build UserData with user identification
        try:
            # Include user_id and profile_id for playback tracking
            full_config["user_id"] = user_id
            full_config["profile_id"] = cached.profile_id

            # Filter out invalid streaming providers (empty service names or missing required credentials)
            # Note: P2P doesn't require a token, so we check service-specific requirements
            for key in ["streaming_providers", "sps"]:
                if key in full_config and isinstance(full_config[key], list):
                    valid_providers = []
                    for sp in full_config[key]:
                        if not isinstance(sp, dict):
                            continue
                        service = sp.get("sv") or sp.get("service")
                        if not service:
                            continue
                        # P2P doesn't require credentials
                        if service == "p2p":
                            valid_providers.append(sp)
                        # Other services require a token (or other credentials handled by validator)
                        elif sp.get("tk") or sp.get("token"):
                            valid_providers.append(sp)
                    full_config[key] = valid_providers

            # Filter out invalid RPDB config (missing API key)
            for key in ["rpdb_config", "rpc"]:
                if key in full_config and isinstance(full_config[key], dict):
                    if not full_config[key].get("ak") and not full_config[key].get("api_key"):
                        del full_config[key]

            # Filter out invalid resolutions (empty strings, None values)
            for key in ["selected_resolutions", "sr"]:
                if key in full_config and isinstance(full_config[key], list):
                    full_config[key] = [r for r in full_config[key] if r]

            user_data = UserData(**full_config)
        except Exception as e:
            logger.error(f"Failed to build UserData for user {user_id}: {e}")
            return ProfileContext.empty(user_id)

        # Extract common fields and return context
        return ProfileContext(
            user_id=user_id,
            profile_id=cached.profile_id,
            user_data=user_data,
            rpdb_api_key=cls._extract_rpdb_key(full_config),
            has_mediaflow_configured=cls._check_mediaflow_configured(user_data),
            web_playback_enabled=cls._check_web_playback_enabled(user_data),
        )

    @classmethod
    async def invalidate_cache(cls, user_id: int) -> None:
        """
        Invalidate cached profile data for a user.

        Call this whenever a profile is updated or deleted.
        """
        cache_key = f"{cls.CACHE_PREFIX}{user_id}"
        try:
            await REDIS_ASYNC_CLIENT.delete(cache_key)
            logger.debug(f"Invalidated profile cache for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate profile cache for user {user_id}: {e}")

    @classmethod
    async def _get_cached_profile(cls, user_id: int) -> CachedProfileData | None:
        """Get cached profile data from Redis."""
        cache_key = f"{cls.CACHE_PREFIX}{user_id}"
        try:
            cached_json = await REDIS_ASYNC_CLIENT.get(cache_key)
            if cached_json:
                return CachedProfileData.model_validate_json(cached_json)
        except Exception as e:
            logger.warning(f"Failed to get cached profile for user {user_id}: {e}")
        return None

    @classmethod
    async def _cache_profile(cls, user_id: int, data: CachedProfileData) -> None:
        """Cache profile data in Redis."""
        cache_key = f"{cls.CACHE_PREFIX}{user_id}"
        try:
            await REDIS_ASYNC_CLIENT.setex(cache_key, cls.CACHE_TTL, data.model_dump_json())
        except Exception as e:
            logger.warning(f"Failed to cache profile for user {user_id}: {e}")

    @classmethod
    async def _get_active_profile(cls, user_id: int, session: AsyncSession) -> UserProfile | None:
        """Get user's active (default) profile from database."""
        # First try to get the default profile
        result = await session.exec(
            select(UserProfile).where(UserProfile.user_id == user_id, UserProfile.is_default == True)
        )
        profile = result.first()

        if profile:
            return profile

        # If no default, get the first profile
        result = await session.exec(select(UserProfile).where(UserProfile.user_id == user_id).limit(1))
        return result.first()

    @classmethod
    async def _get_profile_by_id(cls, user_id: int, profile_id: int, session: AsyncSession) -> UserProfile | None:
        """Get a specific profile by ID, ensuring it belongs to the user."""
        result = await session.exec(
            select(UserProfile).where(UserProfile.id == profile_id, UserProfile.user_id == user_id)
        )
        return result.first()

    @classmethod
    def _extract_rpdb_key(cls, config: dict) -> str | None:
        """Extract RPDB API key from config, handling both aliases."""
        # Check alias format first (rpc.ak)
        rpc = config.get("rpc", {})
        if rpc and isinstance(rpc, dict):
            api_key = rpc.get("ak")
            if api_key:
                return api_key

        # Check full name format (rpdb_config.api_key)
        rpdb_config = config.get("rpdb_config", {})
        if rpdb_config and isinstance(rpdb_config, dict):
            api_key = rpdb_config.get("api_key")
            if api_key:
                return api_key

        return None

    @classmethod
    def _check_mediaflow_configured(cls, user_data: UserData) -> bool:
        """Check if user has MediaFlow proxy URL and password configured."""
        if not user_data.mediaflow_config:
            return False

        # Check if proxy URL and password are configured
        if not user_data.mediaflow_config.proxy_url:
            return False
        if not user_data.mediaflow_config.api_password:
            return False

        return True

    @classmethod
    def _check_web_playback_enabled(cls, user_data: UserData) -> bool:
        """
        Check if web browser playback is enabled.

        Web playback requires:
        1. MediaFlow proxy configured (URL + password)
        2. enable_web_playback toggle enabled

        This is required for playing debrid streams in the browser due to CORS restrictions.
        """
        if not cls._check_mediaflow_configured(user_data):
            return False

        # Check if web playback is enabled
        return user_data.mediaflow_config.enable_web_playback

    @classmethod
    def check_mediaflow_for_provider(cls, user_data: UserData, provider: "StreamingProvider | None" = None) -> bool:
        """
        Check if MediaFlow should be applied for a specific provider (Stremio/Kodi).

        This checks the per-provider use_mediaflow setting. Each provider can
        independently enable/disable MediaFlow proxy for Stremio/Kodi playback.

        Args:
            user_data: The user's configuration data
            provider: The streaming provider to check

        Returns:
            True if MediaFlow should be applied for this provider
        """
        # First check global MediaFlow configuration
        if not cls._check_mediaflow_configured(user_data):
            return False

        # If no specific provider provided, return False (need provider for Stremio)
        if provider is None:
            return False

        # Check per-provider setting (defaults to True if not set)
        return provider.use_mediaflow
