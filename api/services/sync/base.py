"""
Base sync service for external platform integrations.

Provides abstract interface for implementing platform-specific sync services.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

import pytz
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.crud.media import get_all_external_ids_dict, get_media_by_external_id
from db.database import get_async_session_context
from db.enums import HistorySource, IntegrationType, MediaType, SyncDirection, WatchAction
from db.models import Media, MediaExternalID, ProfileIntegration, UserProfile, WatchHistory
from scrapers.scraper_tasks import meta_fetcher

logger = logging.getLogger(__name__)

# Type variable for platform-specific config
ConfigT = TypeVar("ConfigT")


def _coerce_external_numeric_id(value: object) -> int | None:
    """Parse TMDB/TVDB/MAL-style ids that may be int or messy strings (e.g. '/83867', 'tv/123')."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    s = str(value).strip()
    if not s:
        return None
    s = s.strip("/")
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    try:
        n = int(s, 10)
    except ValueError:
        return None
    return n if n > 0 else None


@dataclass
class WatchedItem:
    """Represents a watched item from external platform."""

    # External IDs
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    mal_id: int | None = None

    # Content info
    title: str = ""
    media_type: str = "movie"  # movie or series
    year: int | None = None

    # Episode info (for series)
    season: int | None = None
    episode: int | None = None

    # Watch metadata
    watched_at: datetime | None = None
    progress: int = 0  # Progress in seconds or percentage
    duration: int | None = None
    action: WatchAction = WatchAction.WATCHED

    # Platform-specific data
    platform_id: str | None = None  # ID on the external platform
    platform_data: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool = True
    error: str | None = None

    # Import stats
    imported: int = 0
    import_skipped: int = 0
    import_errors: int = 0

    # Export stats
    exported: int = 0
    export_skipped: int = 0
    export_errors: int = 0

    # Conflict stats
    conflicts: int = 0
    conflicts_resolved: int = 0

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(pytz.UTC))
    completed_at: datetime | None = None
    duration_seconds: float = 0

    # Details for debugging
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "success": self.success,
            "error": self.error,
            "imported": self.imported,
            "import_skipped": self.import_skipped,
            "import_errors": self.import_errors,
            "exported": self.exported,
            "export_skipped": self.export_skipped,
            "export_errors": self.export_errors,
            "conflicts": self.conflicts,
            "conflicts_resolved": self.conflicts_resolved,
            "duration_seconds": self.duration_seconds,
        }


class BaseSyncService(ABC, Generic[ConfigT]):
    """
    Abstract base class for platform sync services.

    Each platform (Trakt, Simkl, etc.) should implement this interface.
    """

    # Platform identifier
    platform: IntegrationType

    def __init__(self, config: ConfigT, profile_id: int):
        """
        Initialize sync service.

        Args:
            config: Platform-specific configuration (e.g., TraktConfig)
            profile_id: User profile ID for this sync
        """
        self.config = config
        self.profile_id = profile_id

    # =========================================================================
    # Abstract methods - must be implemented by each platform
    # =========================================================================

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """
        Validate that the stored credentials are valid.

        Returns:
            True if credentials are valid, False otherwise
        """
        pass

    @abstractmethod
    async def refresh_token(self) -> ConfigT | None:
        """
        Refresh OAuth token if needed.

        Returns:
            Updated config with new tokens, or None if refresh failed
        """
        pass

    @abstractmethod
    async def fetch_watch_history(
        self,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[WatchedItem]:
        """
        Fetch watch history from external platform.

        Args:
            since: Only fetch items watched after this time
            limit: Maximum number of items to fetch

        Returns:
            List of watched items from the platform
        """
        pass

    @abstractmethod
    async def push_watch_history(
        self,
        items: list[WatchedItem],
    ) -> tuple[int, int]:
        """
        Push watch history to external platform.

        Args:
            items: List of items to push

        Returns:
            Tuple of (success_count, error_count)
        """
        pass

    @abstractmethod
    async def get_platform_id_for_media(
        self,
        imdb_id: str | None = None,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        title: str | None = None,
        year: int | None = None,
    ) -> str | None:
        """
        Get the platform-specific ID for a media item.

        Args:
            imdb_id: IMDb ID
            tmdb_id: TMDB ID
            tvdb_id: TVDB ID
            title: Title for fallback search
            year: Year for fallback search

        Returns:
            Platform-specific ID or None if not found
        """
        pass

    # =========================================================================
    # Optional methods - can be overridden for platform-specific behavior
    # =========================================================================

    async def scrobble_start(self, item: WatchedItem) -> bool:
        """
        Notify platform that user started watching.

        Args:
            item: Item being watched

        Returns:
            True if scrobble was successful
        """
        # Default: no-op, not all platforms support scrobbling
        return True

    async def scrobble_pause(self, item: WatchedItem) -> bool:
        """
        Notify platform that user paused watching.

        Args:
            item: Item being watched

        Returns:
            True if scrobble was successful
        """
        return True

    async def scrobble_stop(self, item: WatchedItem, progress_percent: float) -> bool:
        """
        Notify platform that user stopped watching.

        Args:
            item: Item being watched
            progress_percent: How much was watched (0-100)

        Returns:
            True if scrobble was successful
        """
        return True

    # =========================================================================
    # Sync orchestration methods
    # =========================================================================

    async def sync(
        self,
        direction: SyncDirection | None = None,
        full_sync: bool = False,
    ) -> SyncResult:
        """
        Perform bidirectional sync with the external platform.

        Args:
            direction: Override sync direction (uses config default if None)
            full_sync: If True, fetch all history ignoring last_sync_at

        Returns:
            SyncResult with statistics
        """
        result = SyncResult()

        try:
            # Validate credentials first
            if not await self.validate_credentials():
                # Try to refresh token
                new_config = await self.refresh_token()
                if not new_config:
                    result.success = False
                    result.error = "Invalid or expired credentials"
                    return result
                self.config = new_config

            # Get integration record for sync state (read-only, just to get last_sync_at)
            async with get_async_session_context() as session:
                integration = await self._get_integration(session)
            # Use None for full sync to fetch all history
            last_sync_at = None if full_sync else (integration.last_sync_at if integration else None)
            effective_direction = direction or self._get_sync_direction()

            # Import from platform
            if effective_direction in (SyncDirection.IMPORT, SyncDirection.BIDIRECTIONAL):
                import_result = await self._import_from_platform(
                    since=last_sync_at,
                )
                result.imported = import_result.imported
                result.import_skipped = import_result.import_skipped
                result.import_errors = import_result.import_errors

            # Export to platform
            if effective_direction in (SyncDirection.EXPORT, SyncDirection.BIDIRECTIONAL):
                export_result = await self._export_to_platform(
                    since=last_sync_at,
                )
                result.exported = export_result.exported
                result.export_skipped = export_result.export_skipped
                result.export_errors = export_result.export_errors

            # Set completion time
            result.completed_at = datetime.now(pytz.UTC)
            result.duration_seconds = (result.completed_at - result.started_at).total_seconds()

            # NOTE: Sync state update is handled by the caller (integrations router)
            # to avoid transaction issues when sync partially fails

        except Exception as e:
            logger.exception(f"Sync failed for {self.platform}: {e}")
            result.success = False
            result.error = str(e)
            result.completed_at = datetime.now(pytz.UTC)
            result.duration_seconds = (result.completed_at - result.started_at).total_seconds()

        return result

    async def _import_from_platform(
        self,
        since: datetime | None = None,
    ) -> SyncResult:
        """Import watch history from external platform."""
        result = SyncResult()

        try:
            # Fetch from platform
            items = await self.fetch_watch_history(since=since)
            result.details.append(f"Fetched {len(items)} items from {self.platform}")

            media_not_found = 0
            media_created = 0
            already_exists = 0

            for item in items:
                try:
                    async with get_async_session_context() as session:
                        media_id = await self._resolve_media_id(session, item)

                    if not media_id:
                        media_id = await self._create_media_from_item(item)
                        if media_id:
                            media_created += 1
                        else:
                            media_not_found += 1
                            result.import_skipped += 1
                            continue

                    async with get_async_session_context() as session:
                        exists = await self._watch_entry_exists(session, media_id, item)
                        if exists:
                            already_exists += 1
                            result.import_skipped += 1
                            continue

                        await self._create_watch_entry(session, media_id, item)
                        await session.commit()
                        result.imported += 1

                except Exception as e:
                    logger.warning(f"Failed to import item {item.title}: {e}")
                    result.import_errors += 1

        except Exception as e:
            logger.exception(f"Import failed: {e}")
            result.success = False
            result.error = str(e)

        logger.info(
            f"Import summary: {result.imported} imported, "
            f"{media_created} media created, "
            f"{media_not_found} media not found, "
            f"{already_exists} already in history, "
            f"{result.import_errors} errors"
        )
        return result

    async def _export_to_platform(
        self,
        since: datetime | None = None,
    ) -> SyncResult:
        """Export watch history to external platform."""
        result = SyncResult()

        try:
            # Get local watch history
            items_to_push: list[WatchedItem] = []
            async with get_async_session_context() as session:
                query = select(WatchHistory).where(
                    WatchHistory.profile_id == self.profile_id,
                    WatchHistory.action == WatchAction.WATCHED,
                )
                if since:
                    query = query.where(WatchHistory.watched_at > since)

                db_result = await session.exec(query)
                entries = db_result.all()

                result.details.append(f"Found {len(entries)} local entries to export")

                for entry in entries:
                    item = await self._convert_to_watched_item(session, entry)
                    if item:
                        items_to_push.append(item)
                    else:
                        result.export_skipped += 1

            if items_to_push:
                success, errors = await self.push_watch_history(items_to_push)
                result.exported = success
                result.export_errors = errors

        except Exception as e:
            logger.exception(f"Export failed: {e}")
            result.success = False
            result.error = str(e)

        return result

    # =========================================================================
    # Helper methods
    # =========================================================================

    def _get_sync_direction(self) -> SyncDirection:
        """Get sync direction from config."""
        if hasattr(self.config, "sync_direction"):
            return self.config.sync_direction
        return SyncDirection.BIDIRECTIONAL

    async def _get_integration(
        self,
        session: AsyncSession,
    ) -> ProfileIntegration | None:
        """Get the integration record for this profile/platform (read-only)."""
        query = select(ProfileIntegration).where(
            ProfileIntegration.profile_id == self.profile_id,
            ProfileIntegration.platform == self.platform,
        )
        result = await session.exec(query)
        return result.first()

    async def _resolve_media_id(
        self,
        session: AsyncSession,
        item: WatchedItem,
    ) -> int | None:
        """Resolve external IDs to internal media ID."""
        # Try each external ID
        # Try IMDb ID (format: tt1234567)
        if item.imdb_id:
            media = await get_media_by_external_id(session, item.imdb_id)
            if media:
                return media.id

        # Try TMDb ID (format: tmdb:123456)
        if item.tmdb_id:
            media = await get_media_by_external_id(session, f"tmdb:{item.tmdb_id}")
            if media:
                return media.id

        # Try TVDB ID (format: tvdb:123456)
        if item.tvdb_id:
            media = await get_media_by_external_id(session, f"tvdb:{item.tvdb_id}")
            if media:
                return media.id

        return None

    async def _create_media_from_item(
        self,
        item: WatchedItem,
    ) -> int | None:
        """Create media entry from external metadata when it doesn't exist.

        Fetches metadata from TMDb/IMDb and creates the media record.
        """
        try:
            provider = None
            provider_id = None

            if item.tmdb_id:
                provider = "tmdb"
                provider_id = str(item.tmdb_id)
            elif item.imdb_id:
                provider = "imdb"
                provider_id = item.imdb_id
            elif item.tvdb_id:
                provider = "tvdb"
                provider_id = str(item.tvdb_id)

            if not provider or not provider_id:
                logger.debug(f"No external ID available to fetch metadata for {item.title}")
                return None

            ext_id_str = provider_id if provider == "imdb" else f"{provider}:{provider_id}"
            media_type_str = "movie" if item.media_type == "movie" else "series"
            metadata = await meta_fetcher.get_metadata_from_provider(provider, provider_id, media_type_str)

            if not metadata:
                logger.debug(f"No metadata found for {item.title} from {provider}:{provider_id}")
                return None

            async with get_async_session_context() as session:
                existing = await get_media_by_external_id(session, ext_id_str)
                if existing:
                    return existing.id

                db_media_type = MediaType.MOVIE if item.media_type == "movie" else MediaType.SERIES

                runtime_minutes = None
                if metadata.get("runtime"):
                    runtime_str = str(metadata["runtime"])
                    try:
                        if "min" in runtime_str.lower():
                            runtime_minutes = int(runtime_str.lower().replace("min", "").strip())
                        elif runtime_str.isdigit():
                            runtime_minutes = int(runtime_str)
                    except (ValueError, TypeError):
                        pass

                media = Media(
                    type=db_media_type,
                    title=metadata.get("title", item.title),
                    year=metadata.get("year") or item.year,
                    description=metadata.get("description"),
                    runtime_minutes=runtime_minutes,
                    is_user_created=False,
                )
                session.add(media)
                await session.flush()

                if item.imdb_id:
                    session.add(
                        MediaExternalID(
                            media_id=media.id,
                            provider="imdb",
                            external_id=item.imdb_id,
                        )
                    )
                if item.tmdb_id:
                    session.add(
                        MediaExternalID(
                            media_id=media.id,
                            provider="tmdb",
                            external_id=str(item.tmdb_id),
                        )
                    )
                if item.tvdb_id:
                    session.add(
                        MediaExternalID(
                            media_id=media.id,
                            provider="tvdb",
                            external_id=str(item.tvdb_id),
                        )
                    )

                await session.commit()
                logger.info(f"Created media '{media.title}' (ID: {media.id}) from {provider}")
                return media.id

        except Exception as e:
            logger.warning(f"Failed to create media for {item.title}: {e}")
            return None

    async def _watch_entry_exists(
        self,
        session: AsyncSession,
        media_id: int,
        item: WatchedItem,
    ) -> bool:
        """Check if watch entry already exists."""
        query = select(WatchHistory).where(
            WatchHistory.profile_id == self.profile_id,
            WatchHistory.media_id == media_id,
        )

        if item.media_type == "series" and item.season and item.episode:
            query = query.where(
                WatchHistory.season == item.season,
                WatchHistory.episode == item.episode,
            )

        result = await session.exec(query)
        return result.first() is not None

    async def _create_watch_entry(
        self,
        session: AsyncSession,
        media_id: int,
        item: WatchedItem,
        source: HistorySource | None = None,
    ) -> WatchHistory:
        """Create watch history entry from imported item."""
        # Get user_id from profile
        profile = await session.get(UserProfile, self.profile_id)
        if not profile:
            raise ValueError(f"Profile {self.profile_id} not found")

        # Determine source from platform if not specified
        if source is None:
            source = self._get_history_source()

        entry = WatchHistory(
            user_id=profile.user_id,
            profile_id=self.profile_id,
            media_id=media_id,
            title=item.title,
            media_type=item.media_type,
            season=item.season,
            episode=item.episode,
            progress=item.progress,
            duration=item.duration,
            action=item.action,
            source=source,
            watched_at=item.watched_at or datetime.now(pytz.UTC),
        )
        session.add(entry)
        return entry

    def _get_history_source(self) -> HistorySource:
        """Get the history source based on platform type."""
        platform_to_source = {
            "trakt": HistorySource.TRAKT,
            "simkl": HistorySource.SIMKL,
        }
        return platform_to_source.get(self.platform.lower(), HistorySource.MEDIAFUSION)

    async def _convert_to_watched_item(
        self,
        session: AsyncSession,
        entry: WatchHistory,
    ) -> WatchedItem | None:
        """Convert WatchHistory to WatchedItem for export."""
        # Get external IDs
        ext_ids = await get_all_external_ids_dict(session, entry.media_id)

        return WatchedItem(
            imdb_id=ext_ids.get("imdb"),
            tmdb_id=_coerce_external_numeric_id(ext_ids.get("tmdb")),
            tvdb_id=_coerce_external_numeric_id(ext_ids.get("tvdb")),
            title=entry.title,
            media_type=entry.media_type,
            season=entry.season,
            episode=entry.episode,
            watched_at=entry.watched_at,
            progress=entry.progress,
            duration=entry.duration,
            action=entry.action,
        )
