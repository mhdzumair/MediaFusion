"""
Integration Manager - Centralized service for external platform integrations.

This module provides a single entry point for all integration-related operations:
- Scrobbling (real-time playback tracking)
- History sync (batch sync of watch history)

Integrations are stored in the ProfileIntegration table (not in UserData).
Only authenticated users with a profile can have integrations.

Usage:
    from api.services.sync.manager import IntegrationManager

    # Scrobble when playback starts (lookup from DB)
    await IntegrationManager.scrobble_playback_start(
        profile_id=profile_id,
        imdb_id="tt1234567",
        title="Movie Title",
        media_type="movie",
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from db.enums import IntegrationType

logger = logging.getLogger(__name__)


@dataclass
class ScrobbleData:
    """Data required for scrobbling to external platforms."""

    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    mal_id: int | None = None
    title: str = ""
    media_type: str = "movie"  # "movie" or "series"
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    progress: float = 0  # 0-100 percentage
    watched_at: datetime | None = None


class IntegrationManager:
    """
    Centralized manager for all external platform integrations.

    Provides static methods for common operations that can be called
    from any endpoint (Stremio playback, frontend API, etc.)

    Integrations are looked up from the ProfileIntegration table.
    """

    @staticmethod
    async def scrobble_playback_start(
        profile_id: int,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        title: str = "",
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> None:
        """
        Notify external platforms that playback has started.

        Looks up integrations from the database for the given profile.

        Args:
            profile_id: Profile ID to lookup integrations for
            imdb_id: IMDb ID of the content
            tmdb_id: TMDb ID of the content
            title: Title of the content
            media_type: "movie" or "series"
            season: Season number (for series)
            episode: Episode number (for series)
        """
        if not profile_id:
            return

        data = ScrobbleData(
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            title=title,
            media_type=media_type,
            season=season,
            episode=episode,
            progress=0,
        )

        await IntegrationManager._scrobble_to_platforms(profile_id, data, action="start")

    @staticmethod
    async def scrobble_playback_pause(
        profile_id: int,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        title: str = "",
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
        progress: float = 50,
    ) -> None:
        """Notify external platforms that playback has paused."""
        if not profile_id:
            return

        data = ScrobbleData(
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            title=title,
            media_type=media_type,
            season=season,
            episode=episode,
            progress=progress,
        )

        await IntegrationManager._scrobble_to_platforms(profile_id, data, action="pause")

    @staticmethod
    async def scrobble_playback_stop(
        profile_id: int,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        title: str = "",
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
        progress: float = 100,
    ) -> None:
        """
        Notify external platforms that playback has stopped.

        If progress >= min_watch_percent, the content will be marked as watched.
        """
        if not profile_id:
            return

        data = ScrobbleData(
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            title=title,
            media_type=media_type,
            season=season,
            episode=episode,
            progress=progress,
        )

        await IntegrationManager._scrobble_to_platforms(profile_id, data, action="stop")

    @staticmethod
    async def _scrobble_to_platforms(
        profile_id: int,
        data: ScrobbleData,
        action: str,
    ) -> None:
        """
        Internal method to scrobble to all enabled platforms.

        Looks up integrations from the database.
        """
        from db.database import get_async_session_context
        from db.models import ProfileIntegration
        from sqlmodel import select
        from utils.profile_crypto import profile_crypto

        try:
            async with get_async_session_context() as session:
                # Get all enabled integrations with scrobbling for this profile
                query = select(ProfileIntegration).where(
                    ProfileIntegration.profile_id == profile_id,
                    ProfileIntegration.is_enabled == True,
                    ProfileIntegration.scrobble_enabled == True,
                )
                result = await session.exec(query)
                integrations = result.all()

                for integration in integrations:
                    try:
                        # Decrypt credentials
                        credentials = profile_crypto.decrypt_secrets(integration.encrypted_credentials)

                        if integration.platform == IntegrationType.TRAKT:
                            await IntegrationManager._scrobble_trakt(
                                credentials, integration.settings, profile_id, data, action
                            )
                        # Simkl doesn't support real-time scrobbling
                        # Future platforms can be added here

                    except Exception as e:
                        logger.warning(f"Failed to scrobble to {integration.platform}: {e}")

        except Exception as e:
            logger.warning(f"Failed to lookup integrations for scrobbling: {e}")

    @staticmethod
    async def _scrobble_trakt(
        credentials: dict,
        settings: dict,
        profile_id: int,
        data: ScrobbleData,
        action: str,
    ) -> bool:
        """Scrobble to Trakt."""
        try:
            from api.services.sync.base import WatchedItem
            from api.services.sync.trakt import TraktSyncService
            from db.schemas.config import TraktConfig

            config = TraktConfig(
                access_token=credentials.get("access_token", ""),
                refresh_token=credentials.get("refresh_token"),
                expires_at=credentials.get("expires_at"),
                client_id=credentials.get("client_id"),
                client_secret=credentials.get("client_secret"),
                sync_enabled=True,
                scrobble_enabled=True,
                min_watch_percent=settings.get("min_watch_percent", 80),
            )

            item = WatchedItem(
                imdb_id=data.imdb_id,
                tmdb_id=data.tmdb_id,
                title=data.title,
                media_type=data.media_type,
                season=data.season,
                episode=data.episode,
            )

            service = TraktSyncService(config, profile_id)

            if action == "start":
                result = await service.scrobble_start(item)
            elif action == "pause":
                result = await service.scrobble_pause(item)
            elif action == "stop":
                result = await service.scrobble_stop(item, data.progress)
            else:
                logger.warning(f"Unknown scrobble action: {action}")
                return False

            if result:
                logger.debug(f"Trakt scrobble {action} successful for {data.imdb_id or data.title}")
            return result

        except Exception as e:
            logger.warning(f"Failed to scrobble to Trakt: {e}")
            return False

    @staticmethod
    async def get_enabled_platforms(profile_id: int) -> list[IntegrationType]:
        """Get list of enabled integration platforms for a profile."""
        from db.database import get_async_session_context
        from db.models import ProfileIntegration
        from sqlmodel import select

        try:
            async with get_async_session_context() as session:
                query = select(ProfileIntegration.platform).where(
                    ProfileIntegration.profile_id == profile_id,
                    ProfileIntegration.is_enabled == True,
                )
                result = await session.exec(query)
                return list(result.all())
        except Exception as e:
            logger.warning(f"Failed to get enabled platforms: {e}")
            return []

    @staticmethod
    async def is_platform_connected(
        profile_id: int,
        platform: IntegrationType,
    ) -> bool:
        """Check if a specific platform is connected for a profile."""
        from db.database import get_async_session_context
        from db.models import ProfileIntegration
        from sqlmodel import select

        try:
            async with get_async_session_context() as session:
                query = select(ProfileIntegration).where(
                    ProfileIntegration.profile_id == profile_id,
                    ProfileIntegration.platform == platform,
                )
                result = await session.exec(query)
                return result.first() is not None
        except Exception as e:
            logger.warning(f"Failed to check platform connection: {e}")
            return False
