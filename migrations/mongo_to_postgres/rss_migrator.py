"""
RSS Feed migrator for MongoDB to PostgreSQL migration.

Updated for MediaFusion 5.0 architecture:
- All RSS feeds are now user-based
- System feeds are assigned to the first active admin user
"""

import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from db.enums import UserRole
from db.models import RSSFeed, RSSFeedCatalogPattern, User

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class RSSFeedMigrator:
    """Migrator for RSS Feed data - now user-based"""

    def __init__(self, migration, resume_mode: bool = False):
        self.migration = migration
        self.stats = migration.stats
        self.resume_mode = resume_mode
        self._existing_rss_feed_urls: set[str] = set()
        self._admin_user_id: int | None = None

    async def _get_admin_user_id(self, session: AsyncSession) -> int:
        """Get the admin user ID to assign migrated feeds to.

        Finds the first active admin user. Raises an error if none exists,
        directing the user to complete the initial setup first.
        """
        stmt = (
            select(User)
            .where(
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .limit(1)
        )
        result = await session.exec(stmt)
        admin = result.first()

        if admin:
            logger.info("Assigning migrated RSS feeds to admin user: %s (id=%d)", admin.email, admin.id)
            return admin.id

        raise RuntimeError(
            "No admin user found for RSS feed migration. "
            "Please complete the initial setup first by creating an admin user via the web UI."
        )

    async def _load_existing_rss_feed_urls(self):
        """Load existing RSS feed URLs from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_rss_feed_urls:
            return

        logger.info("📥 Loading existing RSS feed URLs from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            stmt = select(RSSFeed.url)
            result = await session.exec(stmt)
            self._existing_rss_feed_urls.update(result.all())

        logger.info(f"✅ Loaded {len(self._existing_rss_feed_urls):,} existing RSS feed URLs")

    async def migrate_rss_feeds(self):
        """Migrate all RSS feeds from MongoDB to PostgreSQL"""
        logger.info("Starting RSS feed migration...")

        # Get admin user for feed ownership
        async with self.migration.get_session() as session:
            self._admin_user_id = await self._get_admin_user_id(session)

        # Load existing URLs for resume mode
        if self.resume_mode:
            await self._load_existing_rss_feed_urls()

        try:
            total_feeds = await OldRSSFeed.count()
            logger.info(f"Found {total_feeds} RSS feeds to migrate")

            if total_feeds == 0:
                logger.info("No RSS feeds to migrate")
                return

            async for batch in self._get_rss_feed_batches(total_feeds):
                # Filter out already-existing feeds in resume mode
                if self.resume_mode:
                    batch = [feed for feed in batch if feed.url not in self._existing_rss_feed_urls]
                    if not batch:
                        continue

                async with self.migration.get_session() as session:
                    await self._process_rss_feed_batch(session, batch)

            logger.info("RSS feed migration completed")

        except Exception as e:
            logger.exception(f"Error migrating RSS feeds: {str(e)}")
            raise

    async def _get_rss_feed_batches(self, total: int):
        """Yield batches of RSS feeds, respecting sample_size limit"""
        sample_limit = self.migration.sample_size
        effective_total = min(total, sample_limit) if sample_limit else total

        cursor = OldRSSFeed.find_all()
        if sample_limit:
            cursor = cursor.limit(sample_limit)

        with tqdm(total=effective_total, desc="Migrating RSS Feeds") as pbar:
            current_batch = []
            count = 0
            async for feed in cursor:
                current_batch.append(feed)
                count += 1
                if len(current_batch) >= self.migration.batch_size:
                    yield current_batch
                    pbar.update(len(current_batch))
                    current_batch = []

                # Respect sample_size limit
                if sample_limit and count >= sample_limit:
                    break

            if current_batch:
                yield current_batch
                pbar.update(len(current_batch))

    def _transform_rss_feed(self, old_feed: OldRSSFeed) -> dict:
        """Transform RSS feed to new format (user-based)"""
        return {
            "user_id": self._admin_user_id,  # Assign to admin user
            "name": old_feed.name,
            "url": old_feed.url,
            "is_active": old_feed.active,
            "is_public": True,  # System feeds are public
            "last_scraped_at": old_feed.last_scraped,
            "source": old_feed.source,
            "torrent_type": old_feed.torrent_type or "public",
            "auto_detect_catalog": old_feed.auto_detect_catalog or False,
            "parsing_patterns": (old_feed.parsing_patterns.model_dump() if old_feed.parsing_patterns else None),
            "filters": (old_feed.filters.model_dump() if old_feed.filters else None),
            "metrics": (old_feed.metrics.model_dump() if old_feed.metrics else None),
            "created_at": old_feed.created_at,
            "updated_at": old_feed.updated_at,
        }

    def _transform_catalog_patterns(self, old_feed: OldRSSFeed) -> list:
        """Transform catalog patterns"""
        patterns = []
        for pattern in old_feed.catalog_patterns or []:
            patterns.append(
                {
                    "name": pattern.name,
                    "regex": pattern.regex,
                    "enabled": pattern.enabled,
                    "case_sensitive": pattern.case_sensitive,
                    "target_catalogs": pattern.target_catalogs or [],
                }
            )
        return patterns

    async def _upsert_rss_feed(self, session: AsyncSession, feed_data: dict, patterns: list) -> int:
        """Upsert RSS feed using ON CONFLICT (returns feed_id)"""
        # Insert the feed
        stmt = pg_insert(RSSFeed).values([feed_data])
        # ON CONFLICT on user_id + url combination
        update_cols = {col.name: col for col in stmt.excluded if col.name not in ("id", "created_at", "uuid")}
        stmt = stmt.on_conflict_do_update(constraint="uq_rss_feed_user_url", set_=update_cols).returning(RSSFeed.id)

        result = await session.execute(stmt)
        feed_id = result.scalar_one()

        # Handle catalog patterns with batch insert
        if patterns:
            # Delete existing patterns
            await session.execute(
                text("DELETE FROM rss_feed_catalog_pattern WHERE rss_feed_id = :feed_id").bindparams(feed_id=feed_id)
            )

            # Batch insert new patterns
            pattern_data_list = [
                {
                    "rss_feed_id": feed_id,
                    "name": pattern_data.get("name"),
                    "regex": pattern_data["regex"],
                    "enabled": pattern_data.get("enabled", True),
                    "case_sensitive": pattern_data.get("case_sensitive", False),
                    "target_catalogs": pattern_data.get("target_catalogs", []),
                }
                for pattern_data in patterns
            ]
            if pattern_data_list:
                stmt = pg_insert(RSSFeedCatalogPattern).values(pattern_data_list)
                await session.execute(stmt)

        return feed_id

    async def _process_rss_feed_batch(self, session: AsyncSession, batch: list[OldRSSFeed]):
        """Process a batch of RSS feeds with batch operations"""
        try:
            successful = 0
            for old_feed in batch:
                try:
                    feed_data = self._transform_rss_feed(old_feed)
                    patterns = self._transform_catalog_patterns(old_feed)
                    await self._upsert_rss_feed(session, feed_data, patterns)
                    successful += 1
                except Exception as e:
                    logger.error(f"Error processing RSS feed {old_feed.name}: {str(e)}")
                    self.stats.add_error("rss_feed_processing", str(e))
                    self.stats.failed += 1

            # Single commit for the batch
            await session.commit()
            self.stats.successful += successful

        except Exception as e:
            logger.exception(f"RSS feed batch processing failed: {str(e)}")
            await session.rollback()
            raise
