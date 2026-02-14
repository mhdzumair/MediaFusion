import asyncio
import logging
from typing import TypeVar

import typer
from tqdm.asyncio import tqdm

from db.models import (
    Stream,
    TorrentStream,
)
from migrations.mongo_models import (
    TorrentStreams as OldTorrentStreams,
)
from migrations.mongo_to_postgres.metadata_migrator import DatabaseMigration, MetadataMigrator
from migrations.mongo_to_postgres.migration_verifier import MigrationVerifier
from migrations.mongo_to_postgres.rss_migrator import RSSFeedMigrator
from migrations.mongo_to_postgres.series_migrator import SeriesDataMigrator
from migrations.mongo_to_postgres.stats import (
    CatalogStatsComputer,
    CollectionCountChecker,
    CollectionStatus,
)
from migrations.mongo_to_postgres.stream_migrator import StreamMigrator

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)

# Type variables for generic operations
T = TypeVar("T")

app = typer.Typer()


# Migration targets for --only and --skip flags
MIGRATION_TARGETS = ["metadata", "streams", "files", "series", "rss", "stats"]


# Media type filter options
MEDIA_TYPES = ["movies", "series", "tv"]


@app.command()
def migrate(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    batch_size: int = typer.Option(
        1000,
        help="Batch size for processing documents (1000-2000 recommended with bulk inserts)",
    ),
    sample: int | None = typer.Option(
        None,
        "--sample",
        "-s",
        help="Limit migration to N documents per collection for testing (e.g., --sample 100)",
    ),
    skip_verification: bool = typer.Option(False, help="Skip verification after migration"),
    # Legacy compatibility
    only_metadata: bool = typer.Option(False, help="[DEPRECATED] Use --only metadata instead"),
    only_streams: bool = typer.Option(False, help="[DEPRECATED] Use --only streams instead"),
    # New granular flags
    only: str | None = typer.Option(
        None,
        "--only",
        help=f"Migrate only specific targets. Options: {', '.join(MIGRATION_TARGETS)}. Comma-separated for multiple (e.g., --only metadata,streams)",
    ),
    skip: str | None = typer.Option(
        None,
        "--skip",
        help=f"Skip specific migration targets. Options: {', '.join(MIGRATION_TARGETS)}. Comma-separated for multiple (e.g., --skip rss,stats)",
    ),
    skip_completed: bool = typer.Option(
        False,
        "--skip-completed",
        "-c",
        help="Intelligently skip collections where MongoDB and PostgreSQL counts match",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Resume mode: skip individual records that already exist in PostgreSQL (checks by ID)",
    ),
    media_type: str | None = typer.Option(
        None,
        "--media-type",
        "-t",
        help=f"Filter metadata migration by media type. Options: {', '.join(MEDIA_TYPES)}. Comma-separated for multiple (e.g., --media-type series,movies)",
    ),
):
    """Enhanced migration command with flexible options.

    Migration Targets:
      - metadata: Movies, Series, TV shows (Media table) with catalog associations
      - streams: Torrent and HTTP/YouTube streams
      - files: StreamFile and FileMediaLink (file-to-media linking)
      - series: Season and Episode data
      - rss: RSS feeds
      - stats: Catalog statistics

    Note: Catalog associations (MediaCatalogLink) are automatically migrated
    as part of metadata migration from MongoDB's catalog_stats field.

    Media Type Filter (for metadata migration):
      - movies: Only migrate movie metadata
      - series: Only migrate series metadata
      - tv: Only migrate TV channel metadata

    Examples:
      # Test metadata only
      python -m migrations.mongo_to_postgres migrate --only metadata --sample 100

      # Migrate only series metadata with 50 sample
      python -m migrations.mongo_to_postgres migrate --only metadata --media-type series --sample 50

      # Migrate series and movies, skip TV
      python -m migrations.mongo_to_postgres migrate --only metadata --media-type series,movies

      # Migrate streams with files
      python -m migrations.mongo_to_postgres migrate --only streams,files

      # Skip RSS and stats
      python -m migrations.mongo_to_postgres migrate --skip rss,stats

      # Resume migration, skipping completed collections
      python -m migrations.mongo_to_postgres migrate --skip-completed --resume
    """

    async def run_migration():
        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size, sample_size=sample)

        # Parse --only and --skip into sets
        only_targets = set(only.split(",")) if only else set()
        skip_targets = set(skip.split(",")) if skip else set()

        # Parse --media-type filter
        media_type_filter = set(media_type.split(",")) if media_type else set()

        # Validate targets
        for target in only_targets | skip_targets:
            if target and target not in MIGRATION_TARGETS:
                logger.error(f"Invalid migration target: '{target}'. Valid targets: {', '.join(MIGRATION_TARGETS)}")
                raise typer.Exit(code=1)

        # Validate media types
        for mt in media_type_filter:
            if mt and mt not in MEDIA_TYPES:
                logger.error(f"Invalid media type: '{mt}'. Valid types: {', '.join(MEDIA_TYPES)}")
                raise typer.Exit(code=1)

        # Legacy flag support
        if only_metadata:
            only_targets = {"metadata"}
            logger.warning("--only-metadata is deprecated. Use --only metadata instead.")
        if only_streams:
            only_targets = {"streams", "files"}
            logger.warning("--only-streams is deprecated. Use --only streams,files instead.")

        # Helper to check if a target should be migrated
        def should_migrate(target: str) -> bool:
            if target in skip_targets:
                return False
            if only_targets:
                return target in only_targets
            return True

        # Helper to check if a media type should be migrated
        def should_migrate_media_type(mt: str) -> bool:
            if not media_type_filter:
                return True  # No filter = migrate all
            return mt in media_type_filter

        if sample:
            logger.info(f"🧪 SAMPLE MODE: Limiting migration to {sample} documents per collection")
        if only_targets:
            logger.info(f"📋 ONLY MODE: Migrating {', '.join(sorted(only_targets))}")
        if skip_targets:
            logger.info(f"⏭️  SKIP MODE: Skipping {', '.join(sorted(skip_targets))}")
        if media_type_filter:
            logger.info(f"🎬 MEDIA TYPE FILTER: Only migrating {', '.join(sorted(media_type_filter))}")
        if skip_completed:
            logger.info("🔍 SKIP-COMPLETED MODE: Will skip collections with matching counts")
        if resume:
            logger.info("🔄 RESUME MODE: Will skip records that already exist in PostgreSQL")

        try:
            # Initialize connections and resources
            await migration.init_connections()
            async with migration.get_session() as session:
                await migration.resource_tracker.initialize_from_db(session)

            # Check collection statuses if skip_completed is enabled
            statuses = {}
            if skip_completed:
                count_checker = CollectionCountChecker(migration)
                async with migration.get_session() as session:
                    statuses = await count_checker.get_collection_status(session)
                count_checker.log_status_summary(statuses)

            # Initialize migrators (all with resume_mode support)
            metadata_migrator = MetadataMigrator(migration, resume_mode=resume)
            stream_migrator = StreamMigrator(migration, resume_mode=resume)
            series_migrator = SeriesDataMigrator(migration, resume_mode=resume)
            rss_feed_migrator = RSSFeedMigrator(migration, resume_mode=resume)
            catalog_stats_computer = CatalogStatsComputer(migration)
            verifier = MigrationVerifier(migration)

            # =========================================================================
            # METADATA (Movies, Series, TV)
            # =========================================================================
            if should_migrate("metadata"):
                # Build list of media types to migrate based on filters
                types_to_migrate = []
                if should_migrate_media_type("movies"):
                    if skip_completed and statuses.get("movies", CollectionStatus("", 0, 0, False)).is_complete:
                        logger.info("⏭️  Skipping movies migration (already complete)")
                    else:
                        types_to_migrate.append("movies")

                if should_migrate_media_type("series"):
                    if skip_completed and statuses.get("series", CollectionStatus("", 0, 0, False)).is_complete:
                        logger.info("⏭️  Skipping series migration (already complete)")
                    else:
                        types_to_migrate.append("series")

                if should_migrate_media_type("tv"):
                    if skip_completed and statuses.get("tv", CollectionStatus("", 0, 0, False)).is_complete:
                        logger.info("⏭️  Skipping TV migration (already complete)")
                    else:
                        types_to_migrate.append("tv")

                if types_to_migrate:
                    logger.info(f"📦 Migrating metadata ({', '.join(types_to_migrate)})...")
                    await metadata_migrator.migrate_metadata(media_types=types_to_migrate)
                else:
                    logger.info("⏭️  Skipping metadata migration (all types filtered or complete)")
            else:
                logger.info("⏭️  Skipping metadata migration (--skip/--only)")

            # =========================================================================
            # STREAMS (Torrent + HTTP/YouTube)
            # =========================================================================
            if should_migrate("streams"):
                # Check torrent streams
                torrents_complete = (
                    skip_completed and statuses.get("torrent_streams", CollectionStatus("", 0, 0, False)).is_complete
                )
                if torrents_complete:
                    logger.info("⏭️  Skipping torrent streams migration (already complete)")
                else:
                    logger.info("🌊 Migrating torrent streams...")
                    await stream_migrator.migrate_torrent_streams()

                # Check TV/HTTP streams
                tv_streams_complete = (
                    skip_completed and statuses.get("tv_streams", CollectionStatus("", 0, 0, False)).is_complete
                )
                if tv_streams_complete:
                    logger.info("⏭️  Skipping TV/HTTP streams migration (already complete)")
                else:
                    logger.info("📺 Migrating TV/HTTP streams...")
                    await stream_migrator.migrate_tv_streams()
            else:
                logger.info("⏭️  Skipping streams migration (--skip/--only)")

            # =========================================================================
            # FILES (StreamFile + FileMediaLink) - Requires streams to exist
            # =========================================================================
            if should_migrate("files"):
                logger.info("📁 Migrating file structures and media links...")
                # File migration happens as part of stream migration
                # This is a separate target so we can run it independently if needed
                # For now it's integrated - we'll refactor if needed
                pass
            else:
                logger.info("⏭️  Skipping file migration (--skip/--only)")

            # =========================================================================
            # SERIES DATA (Seasons, Episodes) - Requires metadata to exist
            # =========================================================================
            if should_migrate("series"):
                logger.info("📺 Migrating series seasons and episodes...")
                await series_migrator.migrate_series_metadata()

                # Verify episode linkage
                async with migration.get_session() as session:
                    await series_migrator.verify_episode_linkage(session)
            else:
                logger.info("⏭️  Skipping series data migration (--skip/--only)")

            # =========================================================================
            # RSS FEEDS
            # =========================================================================
            if should_migrate("rss"):
                rss_complete = (
                    skip_completed and statuses.get("rss_feeds", CollectionStatus("", 0, 0, False)).is_complete
                )
                if rss_complete:
                    logger.info("⏭️  Skipping RSS feeds migration (already complete)")
                else:
                    logger.info("📡 Migrating RSS feeds...")
                    await rss_feed_migrator.migrate_rss_feeds()
            else:
                logger.info("⏭️  Skipping RSS feeds migration (--skip/--only)")

            # =========================================================================
            # STATISTICS
            # =========================================================================
            if should_migrate("stats"):
                logger.info("📊 Computing catalog statistics...")
                await catalog_stats_computer.compute_catalog_statistics()
            else:
                logger.info("⏭️  Skipping statistics computation (--skip/--only)")

            # =========================================================================
            # VERIFICATION
            # =========================================================================
            if not skip_verification:
                await verifier.verify_migration()

            # Log final statistics
            migration.stats.log_summary()
            logger.info("✅ Migration completed successfully!")

        except Exception as e:
            logger.exception(f"Migration failed: {str(e)}")
            raise typer.Exit(code=1)

        finally:
            await migration.close_connections()

    typer.echo("Starting migration...")
    asyncio.run(run_migration())


@app.command()
def status(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
):
    """Check migration status - shows document counts comparison between MongoDB and PostgreSQL"""

    async def run_status():
        migration = DatabaseMigration(mongo_uri, postgres_uri)
        try:
            await migration.init_connections()

            count_checker = CollectionCountChecker(migration)
            async with migration.get_session() as session:
                statuses = await count_checker.get_collection_status(session)

            count_checker.log_status_summary(statuses)

            # Summary
            all_complete = all(s.is_complete for s in statuses.values())
            total_mongo = sum(s.mongo_count for s in statuses.values())
            total_pg = sum(s.postgres_count for s in statuses.values())

            if all_complete:
                typer.echo("✅ All collections are fully migrated!")
            else:
                pending = [s.name for s in statuses.values() if not s.is_complete]
                typer.echo(f"⏳ Pending collections: {', '.join(pending)}")
                typer.echo(
                    f"📈 Overall progress: {total_pg:,} / {total_mongo:,} documents ({(total_pg / total_mongo * 100):.1f}%)"
                )

        except Exception as e:
            logger.exception(f"Status check failed: {str(e)}")
            raise typer.Exit(code=1)

        finally:
            await migration.close_connections()

    asyncio.run(run_status())


@app.command()
def verify(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    sample: int = typer.Option(
        None,
        help="Verification sample size. Use this if migration was run with --sample flag",
    ),
):
    """Verify migration data integrity and relationships"""

    async def run_verification():
        migration = DatabaseMigration(mongo_uri, postgres_uri, sample_size=sample)
        try:
            # Initialize connections and resources
            await migration.init_connections()
            async with migration.get_session() as session:
                await migration.resource_tracker.initialize_from_db(session)

            # Initialize verifier and run verification
            verifier = MigrationVerifier(migration)
            await verifier.verify_migration()

        except Exception as e:
            logger.exception(f"Verification failed: {str(e)}")
            raise typer.Exit(code=1)

        finally:
            await migration.close_connections()

    if sample:
        typer.echo(f"Starting verification in SAMPLE MODE (sample_size={sample})...")
    else:
        typer.echo("Starting verification...")
    asyncio.run(run_verification())


@app.command()
def fix_missing_fields(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    batch_size: int = typer.Option(1000, help="Batch size for updates"),
):
    """Fix all missing fields (uploader, created_at, updated_at, uploaded_at, torrent_file) for already migrated torrent streams.

    Updated for MediaFusion 5.0: Uses Stream + TorrentStream tables.
    Note: HDR formats are now normalized into StreamHDRLink table.
    """

    async def run_fix():
        from sqlalchemy import update as sa_update
        from sqlmodel import select

        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size=batch_size)
        try:
            await migration.init_connections()

            # Get total count from MongoDB
            total = await OldTorrentStreams.find().count()
            logger.info(f"Found {total} torrent streams in MongoDB to check")

            updated_streams = 0
            updated_torrents = 0
            skipped = 0
            cursor = OldTorrentStreams.find()

            with tqdm(total=total, desc="Fixing missing fields") as pbar:
                batch = []
                async for stream in cursor:
                    # Collect all fields that might be missing
                    stream_data = {
                        "info_hash": stream.id.lower(),
                        # Stream table fields
                        "uploader": stream.uploader,
                        "created_at": stream.created_at,
                        "updated_at": stream.updated_at,
                        # TorrentStream table fields
                        "uploaded_at": stream.uploaded_at,
                        "torrent_file": stream.torrent_file,
                    }
                    batch.append(stream_data)

                    if len(batch) >= batch_size:
                        async with migration.get_session() as session:
                            for data in batch:
                                info_hash = data["info_hash"]

                                # Find TorrentStream by info_hash
                                stmt = select(TorrentStream).where(TorrentStream.info_hash == info_hash)
                                torrent = (await session.exec(stmt)).first()

                                if not torrent:
                                    skipped += 1
                                    continue

                                # Update Stream table fields
                                stream_updates = {}
                                if data["uploader"]:
                                    stream_updates["uploader"] = data["uploader"]
                                if data["created_at"]:
                                    stream_updates["created_at"] = data["created_at"]
                                if data["updated_at"]:
                                    stream_updates["updated_at"] = data["updated_at"]

                                if stream_updates:
                                    stmt = (
                                        sa_update(Stream).where(Stream.id == torrent.stream_id).values(**stream_updates)
                                    )
                                    result = await session.exec(stmt)
                                    if result.rowcount > 0:
                                        updated_streams += 1

                                # Update TorrentStream table fields
                                torrent_updates = {}
                                if data["uploaded_at"]:
                                    torrent_updates["uploaded_at"] = data["uploaded_at"]
                                if data["torrent_file"]:
                                    torrent_updates["torrent_file"] = data["torrent_file"]

                                if torrent_updates:
                                    stmt = (
                                        sa_update(TorrentStream)
                                        .where(TorrentStream.id == torrent.id)
                                        .values(**torrent_updates)
                                    )
                                    result = await session.exec(stmt)
                                    if result.rowcount > 0:
                                        updated_torrents += 1

                            await session.commit()
                        pbar.update(len(batch))
                        batch = []

                # Process remaining batch
                if batch:
                    async with migration.get_session() as session:
                        for data in batch:
                            info_hash = data["info_hash"]

                            # Find TorrentStream by info_hash
                            stmt = select(TorrentStream).where(TorrentStream.info_hash == info_hash)
                            torrent = (await session.exec(stmt)).first()

                            if not torrent:
                                skipped += 1
                                continue

                            # Update Stream table fields
                            stream_updates = {}
                            if data["uploader"]:
                                stream_updates["uploader"] = data["uploader"]
                            if data["created_at"]:
                                stream_updates["created_at"] = data["created_at"]
                            if data["updated_at"]:
                                stream_updates["updated_at"] = data["updated_at"]

                            if stream_updates:
                                stmt = sa_update(Stream).where(Stream.id == torrent.stream_id).values(**stream_updates)
                                result = await session.exec(stmt)
                                if result.rowcount > 0:
                                    updated_streams += 1

                            # Update TorrentStream table fields
                            torrent_updates = {}
                            if data["uploaded_at"]:
                                torrent_updates["uploaded_at"] = data["uploaded_at"]
                            if data["torrent_file"]:
                                torrent_updates["torrent_file"] = data["torrent_file"]

                            if torrent_updates:
                                stmt = (
                                    sa_update(TorrentStream)
                                    .where(TorrentStream.id == torrent.id)
                                    .values(**torrent_updates)
                                )
                                result = await session.exec(stmt)
                                if result.rowcount > 0:
                                    updated_torrents += 1

                        await session.commit()
                    pbar.update(len(batch))

            logger.info(
                f"Updated {updated_streams} streams and {updated_torrents} torrents with missing fields (skipped {skipped})"
            )

        except Exception as e:
            logger.exception(f"Fix missing fields failed: {str(e)}")
            raise typer.Exit(code=1)
        finally:
            await migration.close_connections()

    typer.echo("Fixing missing fields (uploader, created_at, updated_at, uploaded_at, torrent_file)...")
    asyncio.run(run_fix())


if __name__ == "__main__":
    app()
