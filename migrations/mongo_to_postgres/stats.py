"""
Migration statistics and helper classes.

Updated for MediaFusion 5.0 architecture:
- Uses Stream, TorrentStream, HTTPStream instead of old models
- Uses Tracker instead of AnnounceURL
- Removed references to base_metadata
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import func, text
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import (
    AudioChannel,
    AudioFormat,
    Catalog,
    Genre,
    HDRFormat,
    HTTPStream,
    Language,
    MovieMetadata,
    ParentalCertificate,
    Person,
    RSSFeed,
    SeriesMetadata,
    TorrentStream,
    Tracker,
    TVMetadata,
)
from migrations.mongo_models import (
    MediaFusionMovieMetaData as OldMovieMetaData,
)
from migrations.mongo_models import (
    MediaFusionSeriesMetaData as OldSeriesMetaData,
)
from migrations.mongo_models import (
    MediaFusionTVMetaData as OldTVMetaData,
)
from migrations.mongo_models import (
    RSSFeed as OldRSSFeed,
)
from migrations.mongo_models import (
    TorrentStreams as OldTorrentStreams,
)
from migrations.mongo_models import (
    TVStreams as OldTVStreams,
)

if TYPE_CHECKING:
    from migrations.mongo_to_postgres.metadata_migrator import DatabaseMigration

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)

# Type variables for generic operations
T = TypeVar("T")
ModelType = TypeVar("ModelType", bound=SQLModel)


@dataclass
class MigrationStats:
    """Track migration statistics and errors"""

    processed: int = 0
    successful: int = 0
    failed: int = 0
    errors: dict[str, list[str]] = None

    def __post_init__(self):
        self.errors = {}

    def add_error(self, category: str, error: str):
        if category not in self.errors:
            self.errors[category] = []
        self.errors[category].append(error)

    def log_summary(self):
        logger.info("\n" + "=" * 60)
        logger.info("📊 MIGRATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Total Processed: {self.processed:,}")
        logger.info(f"  Successful: {self.successful:,}")
        logger.info(f"  Failed/Skipped: {self.failed:,}")

        if self.errors:
            logger.info("\n📋 Errors by Category:")
            for category, errors in self.errors.items():
                # Show count and sample of errors
                logger.info(f"  {category}: {len(errors):,} records")
                # Show first 5 as samples
                for error in errors[:5]:
                    logger.info(f"    - {error}")
                if len(errors) > 5:
                    logger.info(f"    ... and {len(errors) - 5} more")

        logger.info("=" * 60 + "\n")


@dataclass
class Stats:
    """Simple stats tracker for individual migrators"""

    successful: int = 0
    failed: int = 0
    errors: dict[str, list[str]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = {}

    def add_error(self, category: str, error: str):
        if category not in self.errors:
            self.errors[category] = []
        self.errors[category].append(error)


@dataclass
class CollectionStatus:
    """Status of a collection's migration progress"""

    name: str
    mongo_count: int
    postgres_count: int
    is_complete: bool

    @property
    def remaining(self) -> int:
        return max(0, self.mongo_count - self.postgres_count)

    @property
    def progress_pct(self) -> float:
        if self.mongo_count == 0:
            return 100.0
        return (self.postgres_count / self.mongo_count) * 100


class CollectionCountChecker:
    """Check and compare document counts between MongoDB and PostgreSQL"""

    def __init__(self, migration: "DatabaseMigration"):  # type: ignore  # Forward reference
        self.migration = migration

    async def get_collection_status(self, session: AsyncSession) -> dict[str, CollectionStatus]:
        """Get status for all migrateable collections"""
        statuses = {}

        # Movies
        mongo_movies = await OldMovieMetaData.count()
        pg_movies = await session.scalar(select(func.count()).select_from(MovieMetadata))
        statuses["movies"] = CollectionStatus("movies", mongo_movies, pg_movies or 0, mongo_movies == (pg_movies or 0))

        # Series
        mongo_series = await OldSeriesMetaData.count()
        pg_series = await session.scalar(select(func.count()).select_from(SeriesMetadata))
        statuses["series"] = CollectionStatus("series", mongo_series, pg_series or 0, mongo_series == (pg_series or 0))

        # TV
        mongo_tv = await OldTVMetaData.count()
        pg_tv = await session.scalar(select(func.count()).select_from(TVMetadata))
        statuses["tv"] = CollectionStatus("tv", mongo_tv, pg_tv or 0, mongo_tv == (pg_tv or 0))

        # Torrent Streams (via TorrentStream table)
        mongo_torrents = await OldTorrentStreams.count()
        pg_torrents = await session.scalar(select(func.count()).select_from(TorrentStream))
        statuses["torrent_streams"] = CollectionStatus(
            "torrent_streams",
            mongo_torrents,
            pg_torrents or 0,
            mongo_torrents == (pg_torrents or 0),
        )

        # HTTP Streams (formerly TV Streams)
        mongo_tv_streams = await OldTVStreams.count()
        pg_http_streams = await session.scalar(select(func.count()).select_from(HTTPStream))
        statuses["http_streams"] = CollectionStatus(
            "http_streams",
            mongo_tv_streams,
            pg_http_streams or 0,
            mongo_tv_streams == (pg_http_streams or 0),
        )

        # RSS Feeds
        mongo_rss = await OldRSSFeed.count()
        pg_rss = await session.scalar(select(func.count()).select_from(RSSFeed))
        statuses["rss_feeds"] = CollectionStatus("rss_feeds", mongo_rss, pg_rss or 0, mongo_rss == (pg_rss or 0))

        return statuses

    def log_status_summary(self, statuses: dict[str, CollectionStatus]):
        """Log a summary table of all collection statuses"""
        logger.info("\n" + "=" * 70)
        logger.info("📊 MIGRATION STATUS CHECK")
        logger.info("=" * 70)
        logger.info(f"{'Collection':<20} {'MongoDB':<12} {'PostgreSQL':<12} {'Progress':<10} {'Status':<10}")
        logger.info("-" * 70)

        for name, status in statuses.items():
            status_icon = "✅ DONE" if status.is_complete else "⏳ PENDING"
            progress = f"{status.progress_pct:.1f}%"
            logger.info(
                f"{status.name:<20} {status.mongo_count:<12} {status.postgres_count:<12} {progress:<10} {status_icon:<10}"
            )

        logger.info("=" * 70 + "\n")

    def should_skip(self, status: CollectionStatus, skip_completed: bool) -> bool:
        """Determine if a collection should be skipped"""
        if not skip_completed:
            return False
        return status.is_complete


class ResourceTracker:
    """Enhanced resource tracking with caching and batch operations"""

    def __init__(self):
        self._resource_maps: dict[str, dict[str, int]] = {
            "genre": {},
            "catalog": {},
            "language": {},
            "audio_format": {},
            "audio_channel": {},
            "hdr_format": {},
            "tracker": {},
            "parental_certificate": {},
            "person": {},  # For cast/crew migration
        }
        self._pending_inserts: dict[str, set[str]] = {key: set() for key in self._resource_maps}
        self.resource_models = {
            "genre": Genre,
            "catalog": Catalog,
            "language": Language,
            "audio_format": AudioFormat,
            "audio_channel": AudioChannel,
            "hdr_format": HDRFormat,
            "tracker": Tracker,
            "parental_certificate": ParentalCertificate,
            "person": Person,
        }
        self._batch_size = 100

    async def initialize_from_db(self, session: AsyncSession):
        """Load existing resources with optimized batch queries"""
        for resource_type, model in self.resource_models.items():
            stmt = select(model)
            result = await session.exec(stmt)
            resources = result.all()

            # Handle different attribute names
            if resource_type == "tracker":
                self._resource_maps[resource_type] = {r.url: r.id for r in resources}
            else:
                self._resource_maps[resource_type] = {r.name: r.id for r in resources}

    async def ensure_resources(self, session: AsyncSession):
        """Batch create pending resources efficiently"""
        for resource_type, pending in self._pending_inserts.items():
            if not pending:
                continue

            model = self.resource_models[resource_type]
            # Process in batches
            pending_list = list(pending)
            for i in range(0, len(pending_list), self._batch_size):
                batch = pending_list[i : i + self._batch_size]

                # Get attribute name based on type
                if resource_type == "tracker":
                    attr_name = "url"
                else:
                    attr_name = "name"

                # Get existing resources in batch
                attr = getattr(model, attr_name)
                stmt = select(model).where(attr.in_(batch))
                result = await session.exec(stmt)
                existing = {getattr(r, attr_name): r.id for r in result}

                # Create new resources in batch
                new_resources = [model(**{attr_name: name}) for name in batch if name not in existing]
                if new_resources:
                    session.add_all(new_resources)
                    await session.commit()

                    # Update cache with new IDs
                    stmt = select(model).where(attr.in_([getattr(r, attr_name) for r in new_resources]))
                    result = await session.exec(stmt)
                    new_ids = {getattr(r, attr_name): r.id for r in result}
                    existing.update(new_ids)

                self._resource_maps[resource_type].update(existing)

            self._pending_inserts[resource_type].clear()

    async def get_resource_ids_bulk(self, session: AsyncSession, resource_type: str, names: set) -> dict[str, int]:
        """
        BULK get/create resource IDs for a set of names.
        Returns dict mapping name -> id.

        HIGHLY OPTIMIZED: Single query for existing, bulk insert for missing.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        if not names:
            return {}

        names = {n for n in names if n}  # Filter out empty names
        if not names:
            return {}

        result_map = {}
        model = self.resource_models[resource_type]

        # Check cache first
        uncached = set()
        for name in names:
            cached_id = self._resource_maps[resource_type].get(name)
            if cached_id is not None:
                result_map[name] = cached_id
            else:
                uncached.add(name)

        if not uncached:
            return result_map

        # Bulk query database for uncached names
        if resource_type == "tracker":
            attr = model.url
        else:
            attr = model.name

        uncached_list = list(uncached)
        CHUNK_SIZE = 5000

        for i in range(0, len(uncached_list), CHUNK_SIZE):
            chunk = uncached_list[i : i + CHUNK_SIZE]
            stmt = select(model.id, attr).where(attr.in_(chunk))
            db_result = await session.exec(stmt)
            for row in db_result.all():
                resource_id, name = row
                result_map[name] = resource_id
                self._resource_maps[resource_type][name] = resource_id
                uncached.discard(name)

        # Bulk create missing resources
        if uncached:
            to_create = []
            for name in uncached:
                if resource_type == "tracker":
                    to_create.append({"url": name, "status": "unknown"})
                else:
                    to_create.append({"name": name})

            # Bulk insert with ON CONFLICT DO NOTHING
            try:
                if resource_type == "language":
                    # For languages, use name as unique key
                    stmt = pg_insert(model).values(to_create)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
                elif resource_type == "tracker":
                    stmt = pg_insert(model).values(to_create)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
                elif resource_type == "person":
                    # Person doesn't have unique name constraint - insert without conflict handling
                    # Just insert all new persons (duplicates by name are allowed)
                    stmt = pg_insert(model).values(to_create)
                else:
                    stmt = pg_insert(model).values(to_create)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])

                await session.execute(stmt)
                await session.commit()

                # Re-fetch the IDs for created items
                for i in range(0, len(uncached_list), CHUNK_SIZE):
                    chunk = [n for n in uncached_list[i : i + CHUNK_SIZE] if n in uncached]
                    if chunk:
                        stmt = select(model.id, attr).where(attr.in_(chunk))
                        db_result = await session.exec(stmt)
                        for row in db_result.all():
                            resource_id, name = row
                            result_map[name] = resource_id
                            self._resource_maps[resource_type][name] = resource_id
            except Exception as e:
                logger.warning(f"Error bulk creating {resource_type}: {e}")
                await session.rollback()

        return result_map

    async def get_resource_id(self, session: AsyncSession, resource_type: str, name: str) -> int | None:
        """Get single resource ID - delegates to bulk method"""
        if not name:
            return None
        result = await self.get_resource_ids_bulk(session, resource_type, {name})
        return result.get(name)


class CatalogStatsComputer:
    """Compute and store catalog statistics after migration.

    Updates media.total_streams and media.last_stream_added fields.
    Catalog associations are handled via MediaCatalogLink table.
    """

    def __init__(self, migration):
        self.migration = migration

    async def compute_catalog_statistics(self):
        """Compute catalog statistics for all migrated media."""
        logger.info("Computing catalog statistics...")

        try:
            async with self.migration.get_session() as session:
                # Update media total_streams and last_stream_added
                await self._update_media_stats(session)

            logger.info("Catalog statistics computation completed")

        except Exception as e:
            logger.exception(f"Error computing catalog statistics: {str(e)}")
            raise

    async def _update_media_stats(self, session: AsyncSession):
        """Update total_streams and last_stream_added in media table"""
        logger.info("Updating media stream counts...")

        # SQL to aggregate stream counts per media via StreamMediaLink
        update_query = text("""
            UPDATE media m
            SET
                total_streams = COALESCE(ts.stream_count, 0),
                last_stream_added = COALESCE(ts.latest_stream, m.last_stream_added)
            FROM (
                SELECT
                    sml.media_id,
                    COUNT(*) as stream_count,
                    MAX(s.created_at) as latest_stream
                FROM stream_media_link sml
                JOIN stream s ON sml.stream_id = s.id
                WHERE NOT s.is_blocked AND s.is_active
                GROUP BY sml.media_id
            ) ts
            WHERE m.id = ts.media_id
        """)

        await session.execute(update_query)
        await session.commit()
        logger.info("Media stream counts updated")
