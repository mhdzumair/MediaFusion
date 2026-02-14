"""
Metadata migrator for MongoDB to PostgreSQL migration.

Updated for MediaFusion 5.0 architecture:
- Media table with integer PKs and external_id for old string IDs
- MovieMetadata, SeriesMetadata, TVMetadata linked via media_id
- MediaImage table for images (poster, background, logo)
- MediaRating table for ratings (IMDB, TMDB, etc.)
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import func, make_url, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from db.enums import MediaType, NudityStatus
from db.models import (
    AkaTitle,
    Episode,
    EpisodeImage,
    Media,
    MediaCast,
    MediaCatalogLink,
    MediaExternalID,
    MediaGenreLink,
    MediaImage,
    MediaParentalCertificateLink,
    MediaRating,
    MetadataProvider,
    MovieMetadata,
    RatingProvider,
    Season,
    SeriesMetadata,
    TVMetadata,
)
from migrations.mongo_models import (
    MediaFusionMetaData as OldMetaData,
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
from migrations.mongo_to_postgres.stats import MigrationStats, ResourceTracker

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class DatabaseMigration:
    """Enhanced database migration with connection pooling and batch processing"""

    def __init__(
        self,
        mongo_uri: str,
        postgres_uri: str,
        batch_size: int = 1000,
        sample_size: int | None = None,
    ):
        self.mongo_uri = mongo_uri
        self.postgres_uri = postgres_uri
        self.batch_size = batch_size
        self.sample_size = sample_size
        self.resource_tracker = ResourceTracker()
        self.stats = MigrationStats()
        self.pg_engine: AsyncEngine | None = None
        self.mongo_client: AsyncIOMotorClient | None = None

        # Cache for providers (created once during migration)
        self._metadata_providers: dict[str, int] = {}
        self._rating_providers: dict[str, int] = {}
        # Cache for external_id to media.id mapping
        self._external_id_to_media_id: dict[str, int] = {}

    @asynccontextmanager
    async def get_session(self):
        """Provide managed session context"""
        async with AsyncSession(self.pg_engine, expire_on_commit=False) as session:
            try:
                yield session
            except Exception as e:
                await session.rollback()
                raise e

    async def init_connections(self, connect_mongo: bool = True):
        """Initialize database connections with improved error handling"""
        try:
            if connect_mongo:
                self.mongo_client = AsyncIOMotorClient(self.mongo_uri, maxPoolSize=50, minPoolSize=10)
                db = self.mongo_client.get_default_database()
                await init_beanie(
                    database=db,
                    document_models=[
                        OldMovieMetaData,
                        OldSeriesMetaData,
                        OldTVMetaData,
                        OldTorrentStreams,
                        OldTVStreams,
                        OldRSSFeed,
                    ],
                )

            postgres_url = make_url(self.postgres_uri)
            database_name = postgres_url.database

            # Create database if not exists
            async with create_async_engine(postgres_url.set(database="postgres")).connect() as conn:
                await conn.execute(text("COMMIT"))
                result = await conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname='{database_name}'"))
                if not result.scalar():
                    await conn.execute(text(f"CREATE DATABASE {database_name}"))
                    logger.info(f"Database '{database_name}' created.")

            # Initialize PostgreSQL with optimized connection pool
            self.pg_engine = create_async_engine(
                self.postgres_uri,
                echo=False,
                pool_size=20,
                max_overflow=30,
                pool_pre_ping=True,
                pool_recycle=300,
            )

            # Create extensions and tables
            async with self.pg_engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin;"))
                await conn.run_sync(SQLModel.metadata.create_all)

        except Exception as e:
            logger.exception(f"Failed to initialize connections: {str(e)}")
            raise

    async def close_connections(self):
        """Close database connections"""
        try:
            if self.mongo_client:
                self.mongo_client.close()
        except Exception as e:
            logger.exception(f"Error closing MongoDB connection: {str(e)}")

        try:
            if self.pg_engine:
                await self.pg_engine.dispose()
        except Exception as e:
            logger.exception(f"Error closing PostgreSQL connection: {str(e)}")

    async def ensure_providers(self, session: AsyncSession):
        """Create metadata and rating providers if they don't exist."""
        # Metadata providers with display names and priority values
        # Priority: lower = higher priority (IMDb most authoritative for basic data)
        metadata_providers = {
            "imdb": {
                "display_name": "IMDb",
                "is_external": True,
                "priority": 10,
                "default_priority": 10,
            },
            "tvdb": {
                "display_name": "TheTVDB",
                "is_external": True,
                "priority": 15,
                "default_priority": 15,
            },
            "tmdb": {
                "display_name": "TMDB",
                "is_external": True,
                "priority": 20,
                "default_priority": 20,
            },
            "mal": {
                "display_name": "MyAnimeList",
                "is_external": True,
                "priority": 25,
                "default_priority": 25,
            },
            "kitsu": {
                "display_name": "Kitsu",
                "is_external": True,
                "priority": 30,
                "default_priority": 30,
            },
            "fanart": {
                "display_name": "Fanart.tv",
                "is_external": True,
                "priority": 50,
                "default_priority": 50,
            },
            "mediafusion": {
                "display_name": "MediaFusion",
                "is_external": False,
                "priority": 100,
                "default_priority": 100,
            },
        }

        for name, attrs in metadata_providers.items():
            result = await session.exec(
                select(MetadataProvider).where(func.lower(MetadataProvider.name) == name.lower())
            )
            provider = result.first()
            if not provider:
                provider = MetadataProvider(name=name, **attrs)
                session.add(provider)
                await session.flush()
            self._metadata_providers[name.lower()] = provider.id

        # Rating providers with display names
        rating_providers = {
            "imdb": {
                "display_name": "IMDb",
                "max_rating": 10.0,
                "is_percentage": False,
            },
            "tmdb": {
                "display_name": "TMDB",
                "max_rating": 10.0,
                "is_percentage": False,
            },
            "trakt": {
                "display_name": "Trakt",
                "max_rating": 10.0,
                "is_percentage": False,
            },
            "letterboxd": {
                "display_name": "Letterboxd",
                "max_rating": 5.0,
                "is_percentage": False,
            },
            "metacritic": {
                "display_name": "Metacritic",
                "max_rating": 100.0,
                "is_percentage": True,
            },
            "rottentomatoes": {
                "display_name": "Rotten Tomatoes",
                "max_rating": 100.0,
                "is_percentage": True,
            },
        }

        for name, attrs in rating_providers.items():
            result = await session.exec(select(RatingProvider).where(func.lower(RatingProvider.name) == name.lower()))
            provider = result.first()
            if not provider:
                provider = RatingProvider(name=name, **attrs)
                session.add(provider)
                await session.flush()
            self._rating_providers[name.lower()] = provider.id

        await session.commit()
        logger.info(
            f"✅ Ensured {len(self._metadata_providers)} metadata providers and {len(self._rating_providers)} rating providers"
        )

    def get_metadata_provider_id(self, name: str) -> int | None:
        """Get cached metadata provider ID."""
        return self._metadata_providers.get(name.lower())

    def get_rating_provider_id(self, name: str) -> int | None:
        """Get cached rating provider ID."""
        return self._rating_providers.get(name.lower())


class MetadataMigrator:
    """
    Metadata migrator for the new MediaFusion 5.0 architecture.

    Handles migration of:
    - Media (base table with integer PK)
    - MovieMetadata, SeriesMetadata, TVMetadata (type-specific tables)
    - MediaImage (poster, background, logo)
    - MediaRating (IMDB, TMDB ratings)
    - Relationships (genres, catalogs, stars, certificates, aka_titles)
    """

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.batch_size = migration.batch_size
        self.resource_tracker = migration.resource_tracker
        self.stats = migration.stats
        self.resume_mode = resume_mode
        self._existing_external_ids: set[str] = set()

    async def _load_existing_metadata_ids(self):
        """Load existing external IDs from PostgreSQL for resume mode.

        Uses MediaExternalID table to find existing records, then reconstructs
        the original MongoDB ID format for comparison.
        """
        if not self.resume_mode or self._existing_external_ids:
            return

        logger.info("📥 Loading existing metadata external IDs from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            total_count = await session.scalar(select(func.count()).select_from(MediaExternalID))
            logger.info(f"  Total external IDs in PostgreSQL: {total_count:,}")

            # Use keyset pagination on MediaExternalID
            chunk_size = 100000
            last_id = 0

            while True:
                stmt = (
                    select(
                        MediaExternalID.media_id,
                        MediaExternalID.provider,
                        MediaExternalID.external_id,
                    )
                    .where(MediaExternalID.id > last_id)
                    .order_by(MediaExternalID.id)
                    .limit(chunk_size)
                )
                result = await session.exec(stmt)
                rows = result.all()

                if not rows:
                    break

                for media_id, provider, ext_id in rows:
                    # Reconstruct the original MongoDB ID format
                    if provider == "imdb":
                        mongo_id = ext_id  # tt1234567
                    elif provider == "tmdb":
                        mongo_id = f"mftmdb{ext_id}"  # mftmdb123456 (legacy format)
                    else:
                        mongo_id = f"{provider}:{ext_id}"  # tvdb:123, mal:456

                    self._existing_external_ids.add(mongo_id)
                    self.migration._external_id_to_media_id[mongo_id] = media_id

                # Get last ID from the result for pagination
                if rows:
                    # Need to get actual MediaExternalID.id for pagination
                    last_id_result = await session.exec(
                        select(MediaExternalID.id).where(
                            MediaExternalID.media_id == rows[-1][0],
                            MediaExternalID.provider == rows[-1][1],
                            MediaExternalID.external_id == rows[-1][2],
                        )
                    )
                    last_id = last_id_result.first() or last_id

                if len(self._existing_external_ids) % 100000 < chunk_size:
                    logger.info(f"  Loaded {len(self._existing_external_ids):,} / {total_count:,} metadata IDs...")

        logger.info(f"✅ Loaded {len(self._existing_external_ids):,} existing external IDs")

    async def migrate_metadata(self, media_types: list = None):
        """Migrate metadata with enhanced parallel processing and error handling.

        Args:
            media_types: Optional list of media types to migrate.
                         Options: ["movies", "series", "tv"].
                         If None, all types are migrated.
        """
        all_collections = [
            (OldMovieMetaData, MovieMetadata, MediaType.MOVIE, "movies"),
            (OldSeriesMetaData, SeriesMetadata, MediaType.SERIES, "series"),
            (OldTVMetaData, TVMetadata, MediaType.TV, "tv"),
        ]

        # Filter collections based on media_types parameter
        if media_types:
            collections = [c for c in all_collections if c[3] in media_types]
        else:
            collections = all_collections

        # Ensure providers exist
        async with self.migration.get_session() as session:
            await self.migration.ensure_providers(session)

        # Load existing IDs for resume mode
        if self.resume_mode:
            await self._load_existing_metadata_ids()

        for old_model, new_model_class, media_type, collection_name in collections:
            try:
                total = await old_model.find().count()
                if total == 0:
                    logger.info(f"No {collection_name} to migrate")
                    continue

                logger.info(f"Starting migration of {total} {collection_name}")

                async for batch in self._get_document_batches(old_model, total):
                    # Filter out already-existing records in resume mode
                    if self.resume_mode:
                        batch = [doc for doc in batch if doc.id not in self._existing_external_ids]
                        if not batch:
                            continue

                    async with self.migration.get_session() as session:
                        await self._process_metadata_batch(session, batch, new_model_class, media_type)

            except Exception as e:
                logger.exception(f"Failed to migrate {collection_name}: {str(e)}")
                self.stats.add_error(collection_name, str(e))

    async def _get_document_batches(self, model, total):
        """
        Efficiently yield batches of documents, respecting sample_size limit.

        Uses skip/limit pagination to avoid cursor timeout issues on large collections.
        MongoDB cursors expire after 10 minutes by default, so for large migrations
        we fetch in chunks rather than using a long-lived cursor.
        """
        sample_limit = self.migration.sample_size
        effective_total = min(total, sample_limit) if sample_limit else total

        # Use chunk-based fetching to avoid cursor timeout
        FETCH_SIZE = self.batch_size * 5  # Fetch larger chunks, yield in batch_size

        with tqdm(total=effective_total, desc=f"Migrating {model.__name__}") as pbar:
            skip = 0
            total_fetched = 0

            while total_fetched < effective_total:
                # Calculate how many to fetch in this chunk
                remaining = effective_total - total_fetched
                fetch_limit = min(FETCH_SIZE, remaining)

                # Fetch a chunk of documents
                try:
                    chunk = await model.find().skip(skip).limit(fetch_limit).to_list()
                except Exception as e:
                    logger.error(f"Error fetching documents at skip={skip}: {e}")
                    break

                if not chunk:
                    break

                # Yield in batch_size increments
                current_batch = []
                for doc in chunk:
                    current_batch.append(doc)
                    total_fetched += 1

                    if len(current_batch) >= self.batch_size:
                        yield current_batch
                        pbar.update(len(current_batch))
                        current_batch = []

                    if sample_limit and total_fetched >= sample_limit:
                        break

                # Yield remaining docs in this chunk
                if current_batch:
                    yield current_batch
                    pbar.update(len(current_batch))

                skip += len(chunk)

                if sample_limit and total_fetched >= sample_limit:
                    break

    async def _process_metadata_batch(
        self,
        session: AsyncSession,
        batch: list[OldMetaData],
        new_model_class: type[SQLModel],
        media_type: MediaType,
    ):
        """
        Process a batch of metadata documents with OPTIMIZED bulk operations.

        Performance optimizations:
        - Bulk insert images/ratings across entire batch
        - Bulk insert relationships (genres, cast, catalogs)
        - Single commit per batch instead of per document
        - Pre-cached provider IDs
        """
        valid_docs = []
        media_records = []

        # Transform all documents first
        for old_doc in batch:
            try:
                media_data = self._transform_to_media(old_doc, media_type)
                media_records.append(media_data)
                valid_docs.append(old_doc)
            except Exception as e:
                logger.error(f"Error transforming document {old_doc.id}: {str(e)}")
                self.stats.add_error("metadata_transform", f"{old_doc.id}: {str(e)}")
                self.stats.failed += 1
                self.stats.processed += 1

        if not valid_docs:
            return

        try:
            # Create Media records
            created_media_ids = await self._create_media_batch(session, media_records)

            # Create type-specific metadata
            specific_records = []
            for old_doc in valid_docs:
                if old_doc.id in created_media_ids:
                    media_id = created_media_ids[old_doc.id]
                    specific_data = self._transform_specific_metadata(old_doc, media_type, media_id)
                    specific_records.append(specific_data)

            await self._create_specific_metadata_batch(session, specific_records, new_model_class)

            # BULK create images and ratings for entire batch
            all_images = []
            all_ratings = []
            mediafusion_provider_id = self.migration.get_metadata_provider_id("mediafusion")
            imdb_provider_id = self.migration.get_rating_provider_id("imdb")
            tmdb_provider_id = self.migration.get_rating_provider_id("tmdb")

            for old_doc in valid_docs:
                if old_doc.id not in created_media_ids:
                    continue
                media_id = created_media_ids[old_doc.id]

                # Collect images
                if mediafusion_provider_id:
                    poster = getattr(old_doc, "poster", None)
                    if poster:
                        all_images.append(
                            {
                                "media_id": media_id,
                                "provider_id": mediafusion_provider_id,
                                "image_type": "poster",
                                "url": poster,
                                "is_primary": True,
                            }
                        )
                    background = getattr(old_doc, "background", None)
                    if background:
                        all_images.append(
                            {
                                "media_id": media_id,
                                "provider_id": mediafusion_provider_id,
                                "image_type": "background",
                                "url": background,
                                "is_primary": True,
                            }
                        )
                    logo = getattr(old_doc, "logo", None)
                    if logo:
                        all_images.append(
                            {
                                "media_id": media_id,
                                "provider_id": mediafusion_provider_id,
                                "image_type": "logo",
                                "url": logo,
                                "is_primary": True,
                            }
                        )

                # Collect ratings
                imdb_rating = getattr(old_doc, "imdb_rating", None)
                if imdb_rating and imdb_provider_id:
                    all_ratings.append(
                        {
                            "media_id": media_id,
                            "rating_provider_id": imdb_provider_id,
                            "rating": float(imdb_rating),
                            "rating_raw": float(imdb_rating),
                            "rating_type": "audience",
                        }
                    )
                tmdb_rating = getattr(old_doc, "tmdb_rating", None)
                if tmdb_rating and tmdb_provider_id:
                    all_ratings.append(
                        {
                            "media_id": media_id,
                            "rating_provider_id": tmdb_provider_id,
                            "rating": float(tmdb_rating),
                            "rating_raw": float(tmdb_rating),
                            "rating_type": "audience",
                        }
                    )

            # Bulk insert images (filter out URLs that are too long for index)
            # PostgreSQL B-tree index has 8191 byte limit; URLs > 2000 chars are risky
            MAX_URL_LENGTH = 2000
            filtered_images = [img for img in all_images if img.get("url") and len(img["url"]) <= MAX_URL_LENGTH]
            skipped_images = len(all_images) - len(filtered_images)
            if skipped_images > 0:
                logger.warning(f"Skipped {skipped_images} images with URLs > {MAX_URL_LENGTH} chars")

            if filtered_images:
                stmt = pg_insert(MediaImage).values(filtered_images)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "provider_id", "image_type", "url"])
                await session.execute(stmt)

            # Bulk insert ratings
            if all_ratings:
                stmt = pg_insert(MediaRating).values(all_ratings)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "rating_provider_id", "rating_type"])
                await session.execute(stmt)

            # Commit core data
            await session.commit()
            self.stats.successful += len(valid_docs)
            self.stats.processed += len(valid_docs)

            # Update cache
            for ext_id, media_id in created_media_ids.items():
                self.migration._external_id_to_media_id[ext_id] = media_id

        except Exception as e:
            logger.exception(f"Batch metadata insert failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(valid_docs)
            self.stats.processed += len(valid_docs)
            return

        # BULK handle relationships for entire batch (single commit)
        try:
            await self._migrate_relationships_bulk(session, valid_docs, created_media_ids, media_type)
            await session.commit()
        except Exception as e:
            logger.error(f"Error migrating relationships batch: {e}")
            self.stats.add_error("relationships_batch", str(e))
            await session.rollback()

    def _transform_to_media(self, old_doc: OldMetaData, media_type: MediaType) -> dict:
        """Transform old metadata to new Media record."""
        updated_at = self._ensure_timezone(getattr(old_doc, "last_updated_at", None) or datetime.now(UTC))

        # Determine created_at
        if media_type in [MediaType.MOVIE, MediaType.SERIES] and getattr(old_doc, "year", None):
            created_at = self._ensure_timezone(
                getattr(old_doc, "created_at", None) or datetime(old_doc.year, 1, 1, tzinfo=UTC)
                if old_doc.year
                else datetime.now(UTC)
            )
        else:
            created_at = self._ensure_timezone(getattr(old_doc, "created_at", None) or datetime.now(UTC))

        # Parse runtime to minutes
        runtime_minutes = None
        runtime = getattr(old_doc, "runtime", None)
        if runtime:
            try:
                # Handle formats like "120 min", "2h 30m", or just "120"
                if isinstance(runtime, str):
                    if "h" in runtime.lower():
                        parts = runtime.lower().replace("h", " ").replace("m", " ").split()
                        hours = int(parts[0]) if parts else 0
                        mins = int(parts[1]) if len(parts) > 1 else 0
                        runtime_minutes = hours * 60 + mins
                    elif "min" in runtime.lower():
                        runtime_minutes = int(runtime.lower().replace("min", "").strip())
                    else:
                        runtime_minutes = int(runtime)
                elif isinstance(runtime, (int, float)):
                    runtime_minutes = int(runtime)
            except (ValueError, IndexError):
                pass

        # Parse release_date from year
        release_date = None
        if media_type in [MediaType.MOVIE, MediaType.SERIES] and getattr(old_doc, "year", None):
            try:
                release_date = date(old_doc.year, 1, 1)
            except (ValueError, TypeError):
                pass

        # Parse end_date from end_year for series
        end_date = None
        if media_type == MediaType.SERIES and getattr(old_doc, "end_year", None):
            try:
                end_date = date(old_doc.end_year, 12, 31)
            except (ValueError, TypeError):
                pass

        # Parse nudity_status (now on Media table for all types)
        nudity_status = getattr(old_doc, "parent_guide_nudity_status", None)
        if isinstance(nudity_status, str):
            try:
                nudity_status = NudityStatus(nudity_status)
            except ValueError:
                nudity_status = NudityStatus.UNKNOWN
        nudity_status = nudity_status or NudityStatus.UNKNOWN

        return {
            "_mongo_id": old_doc.id,  # Keep for MediaExternalID creation, not inserted into Media
            "type": media_type,
            "title": old_doc.title,
            "year": getattr(old_doc, "year", None),
            "release_date": release_date,
            "end_date": end_date,
            "runtime_minutes": runtime_minutes,
            "description": getattr(old_doc, "description", None),
            "status": "released",
            "nudity_status": nudity_status,
            "is_add_title_to_poster": getattr(old_doc, "is_add_title_to_poster", False) or False,
            "created_at": created_at,
            "updated_at": updated_at,
            "last_stream_added": created_at,
        }

    def _transform_specific_metadata(self, old_doc: OldMetaData, media_type: MediaType, media_id: int) -> dict:
        """Transform to type-specific metadata record."""
        data = {"media_id": media_id}

        if media_type == MediaType.MOVIE:
            # Movie-specific fields (nudity_status moved to Media table)
            pass

        elif media_type == MediaType.SERIES:
            # Series-specific fields (nudity_status and end_year moved to Media table)
            pass

        elif media_type == MediaType.TV:
            data["country"] = getattr(old_doc, "country", None)
            data["tv_language"] = getattr(old_doc, "tv_language", None)

        return data

    async def _create_media_batch(self, session: AsyncSession, media_records: list[dict]) -> dict[str, int]:
        """Create Media records and MediaExternalID records.

        The new architecture stores external IDs in MediaExternalID table, not Media.
        Uses title+year+type uniqueness for Media, then links via MediaExternalID.
        """
        if not media_records:
            return {}

        # Extract mongo_ids and prepare clean records for Media table
        mongo_id_to_record = {}
        seen_keys = set()
        clean_records = []

        for record in media_records:
            mongo_id = record.pop("_mongo_id")  # Remove _mongo_id, not a Media column
            # Create a unique key based on title, year, type
            key = (record.get("title", ""), record.get("year"), record.get("type"))
            if key not in seen_keys:
                seen_keys.add(key)
                clean_records.append(record)
                mongo_id_to_record[key] = mongo_id

        if not clean_records:
            return {}

        # Insert Media records
        stmt = pg_insert(Media).values(clean_records)
        # ON CONFLICT: use title+year+type as unique constraint
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)
        await session.flush()

        # Get the created Media IDs by matching title+year+type
        created_media_ids = {}
        for record in clean_records:
            key = (record.get("title", ""), record.get("year"), record.get("type"))
            mongo_id = mongo_id_to_record.get(key)

            # Find the media_id for this record
            result = await session.exec(
                select(Media.id).where(
                    Media.title == record.get("title"),
                    Media.year == record.get("year") if record.get("year") else Media.year.is_(None),
                    Media.type == record.get("type"),
                )
            )
            media_id = result.first()
            if media_id and mongo_id:
                created_media_ids[mongo_id] = media_id

        # Create MediaExternalID records
        await self._create_external_id_records(session, created_media_ids)

        return created_media_ids

    async def _create_external_id_records(self, session: AsyncSession, media_id_map: dict[str, int]):
        """Create MediaExternalID records from external_id strings.

        Parses external_id format and creates proper MediaExternalID records:
        - tt* -> IMDb (provider='imdb', external_id='tt*')
        - mftmdb* -> TMDB (provider='tmdb', external_id=numeric part)
        - tmdb:* -> TMDB (provider='tmdb', external_id=*)
        - tvdb:* -> TVDB (provider='tvdb', external_id=*)
        - mal:* -> MAL (provider='mal', external_id=*)
        - mf:* -> Skip (use Media.id directly for internal IDs)
        """
        if not media_id_map:
            return

        external_id_records = []

        for ext_id, media_id in media_id_map.items():
            provider = None
            provider_external_id = None

            if ext_id.startswith("tt"):
                # IMDb ID
                provider = "imdb"
                provider_external_id = ext_id
            elif ext_id.startswith("mftmdb"):
                # Legacy TMDB format: mftmdb123456 -> tmdb:123456
                provider = "tmdb"
                provider_external_id = ext_id.replace("mftmdb", "")
            elif ext_id.startswith("mf:"):
                # MediaFusion internal ID - skip (use Media.id directly)
                continue
            elif ":" in ext_id:
                # Provider prefix format: provider:id
                parts = ext_id.split(":", 1)
                if len(parts) == 2:
                    provider = parts[0].lower()
                    provider_external_id = parts[1]

            if provider and provider_external_id:
                external_id_records.append(
                    {
                        "media_id": media_id,
                        "provider": provider,
                        "external_id": provider_external_id,
                    }
                )

        if external_id_records:
            # Bulk insert with ON CONFLICT DO NOTHING
            stmt = pg_insert(MediaExternalID).values(external_id_records)
            stmt = stmt.on_conflict_do_nothing(constraint="uq_provider_external_id")
            await session.execute(stmt)

    async def _create_specific_metadata_batch(
        self, session: AsyncSession, records: list[dict], model_class: type[SQLModel]
    ):
        """Create type-specific metadata records using ON CONFLICT DO NOTHING."""
        if not records:
            return

        # Deduplicate by media_id
        seen_media_ids = set()
        unique_records = []
        for record in records:
            media_id = record["media_id"]
            if media_id not in seen_media_ids:
                seen_media_ids.add(media_id)
                unique_records.append(record)

        # Use INSERT ON CONFLICT DO NOTHING
        stmt = pg_insert(model_class).values(unique_records)
        stmt = stmt.on_conflict_do_nothing(index_elements=["media_id"])
        await session.execute(stmt)
        await session.flush()

    async def _create_images(self, session: AsyncSession, old_doc: OldMetaData, media_id: int):
        """Create MediaImage records for poster, background, logo using ON CONFLICT."""
        mediafusion_provider_id = self.migration.get_metadata_provider_id("mediafusion")
        if not mediafusion_provider_id:
            return

        images_to_create = []

        # Poster
        poster = getattr(old_doc, "poster", None)
        if poster:
            images_to_create.append(
                {
                    "media_id": media_id,
                    "provider_id": mediafusion_provider_id,
                    "image_type": "poster",
                    "url": poster,
                    "is_primary": True,
                }
            )

        # Background
        background = getattr(old_doc, "background", None)
        if background:
            images_to_create.append(
                {
                    "media_id": media_id,
                    "provider_id": mediafusion_provider_id,
                    "image_type": "background",
                    "url": background,
                    "is_primary": True,
                }
            )

        # Logo (TV only)
        logo = getattr(old_doc, "logo", None)
        if logo:
            images_to_create.append(
                {
                    "media_id": media_id,
                    "provider_id": mediafusion_provider_id,
                    "image_type": "logo",
                    "url": logo,
                    "is_primary": True,
                }
            )

        if images_to_create:
            stmt = pg_insert(MediaImage).values(images_to_create)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "provider_id", "image_type", "url"])
            await session.execute(stmt)

    async def _create_ratings(self, session: AsyncSession, old_doc: OldMetaData, media_id: int):
        """Create MediaRating records for IMDB, TMDB ratings using ON CONFLICT."""
        ratings_to_create = []

        # IMDB rating
        imdb_rating = getattr(old_doc, "imdb_rating", None)
        if imdb_rating:
            imdb_provider_id = self.migration.get_rating_provider_id("imdb")
            if imdb_provider_id:
                ratings_to_create.append(
                    {
                        "media_id": media_id,
                        "rating_provider_id": imdb_provider_id,
                        "rating": float(imdb_rating),
                        "rating_raw": float(imdb_rating),
                        "rating_type": "audience",
                    }
                )

        # TMDB rating (if available)
        tmdb_rating = getattr(old_doc, "tmdb_rating", None)
        if tmdb_rating:
            tmdb_provider_id = self.migration.get_rating_provider_id("tmdb")
            if tmdb_provider_id:
                ratings_to_create.append(
                    {
                        "media_id": media_id,
                        "rating_provider_id": tmdb_provider_id,
                        "rating": float(tmdb_rating),
                        "rating_raw": float(tmdb_rating),
                        "rating_type": "audience",
                    }
                )

        if ratings_to_create:
            stmt = pg_insert(MediaRating).values(ratings_to_create)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "rating_provider_id", "rating_type"])
            await session.execute(stmt)

    async def _migrate_relationships_bulk(
        self,
        session: AsyncSession,
        valid_docs: list[OldMetaData],
        created_media_ids: dict[str, int],
        media_type: MediaType,
    ):
        """
        BULK migrate all relationships for a batch of documents.

        This is a major performance optimization - instead of doing
        individual DB operations per document, we collect all data
        and do bulk inserts.
        """
        # Collect all relationship data
        all_genre_links = []
        all_aka_titles = []
        all_cast_entries = []
        all_cert_links = []
        all_catalog_links = []

        # Pre-collect all unique values to batch lookup
        all_genres = set()
        all_stars = set()
        all_certs = set()
        all_catalogs = set()

        for old_doc in valid_docs:
            if old_doc.id not in created_media_ids:
                continue
            if old_doc.genres:
                all_genres.update(old_doc.genres)
            if hasattr(old_doc, "stars") and old_doc.stars:
                all_stars.update(old_doc.stars)
            if hasattr(old_doc, "parent_guide_certificates") and old_doc.parent_guide_certificates:
                all_certs.update(old_doc.parent_guide_certificates)
            # Collect catalogs from catalog_stats
            catalog_stats = getattr(old_doc, "catalog_stats", []) or []
            for stat in catalog_stats:
                catalog = stat.catalog if hasattr(stat, "catalog") else stat.get("catalog")
                if catalog:
                    # Convert contribution_stream to type-specific catalog name
                    if catalog == "contribution_stream":
                        if media_type == MediaType.MOVIE:
                            catalog = "contribution_movies"
                        elif media_type == MediaType.SERIES:
                            catalog = "contribution_series"
                        else:
                            continue
                    all_catalogs.add(catalog)

        # Batch lookup all resource IDs
        genre_ids = {}
        for genre in all_genres:
            gid = await self.resource_tracker.get_resource_id(session, "genre", genre)
            if gid:
                genre_ids[genre] = gid

        person_ids = {}
        for star in all_stars:
            pid = await self.resource_tracker.get_resource_id(session, "person", star)
            if pid:
                person_ids[star] = pid

        cert_ids = {}
        for cert in all_certs:
            cid = await self.resource_tracker.get_resource_id(session, "parental_certificate", cert)
            if cid:
                cert_ids[cert] = cid

        catalog_ids = {}
        for catalog in all_catalogs:
            cat_id = await self.resource_tracker.get_resource_id(session, "catalog", catalog)
            if cat_id:
                catalog_ids[catalog] = cat_id

        # Now process each document with cached lookups
        for old_doc in valid_docs:
            if old_doc.id not in created_media_ids:
                continue
            media_id = created_media_ids[old_doc.id]

            # Genres
            if old_doc.genres:
                for genre_name in set(old_doc.genres):
                    if genre_name in genre_ids:
                        all_genre_links.append({"media_id": media_id, "genre_id": genre_ids[genre_name]})

            # AKA titles
            if hasattr(old_doc, "aka_titles") and old_doc.aka_titles:
                for title in set(old_doc.aka_titles):
                    if title:
                        all_aka_titles.append({"title": title, "media_id": media_id})

            # Cast
            if hasattr(old_doc, "stars") and old_doc.stars:
                for display_order, star_name in enumerate(old_doc.stars):
                    if star_name in person_ids:
                        all_cast_entries.append(
                            {
                                "media_id": media_id,
                                "person_id": person_ids[star_name],
                                "display_order": display_order,
                            }
                        )

            # Certificates
            if hasattr(old_doc, "parent_guide_certificates") and old_doc.parent_guide_certificates:
                for cert_name in set(old_doc.parent_guide_certificates):
                    if cert_name in cert_ids:
                        all_cert_links.append(
                            {
                                "media_id": media_id,
                                "certificate_id": cert_ids[cert_name],
                            }
                        )

            # Catalogs from catalog_stats
            catalog_stats = getattr(old_doc, "catalog_stats", []) or []
            processed_catalogs = set()
            for stat in catalog_stats:
                catalog = stat.catalog if hasattr(stat, "catalog") else stat.get("catalog")
                if not catalog or catalog in processed_catalogs:
                    continue
                # Convert contribution_stream to type-specific catalog name
                if catalog == "contribution_stream":
                    if media_type == MediaType.MOVIE:
                        catalog = "contribution_movies"
                    elif media_type == MediaType.SERIES:
                        catalog = "contribution_series"
                    else:
                        continue
                if catalog in catalog_ids:
                    all_catalog_links.append({"media_id": media_id, "catalog_id": catalog_ids[catalog]})
                    processed_catalogs.add(catalog)

        # Bulk insert all relationships
        # PostgreSQL has ~32,767 parameter limit per query
        # Chunk sizes based on column count to stay well under limit
        CHUNK_SMALL = 1000  # For tables with many columns (5+)
        CHUNK_MEDIUM = 3000  # For tables with 3-4 columns
        CHUNK_LARGE = 5000  # For simple 2-column link tables

        if all_genre_links:
            for i in range(0, len(all_genre_links), CHUNK_LARGE):
                chunk = all_genre_links[i : i + CHUNK_LARGE]
                stmt = pg_insert(MediaGenreLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "genre_id"])
                await session.exec(stmt)

        if all_aka_titles:
            for i in range(0, len(all_aka_titles), CHUNK_MEDIUM):
                chunk = all_aka_titles[i : i + CHUNK_MEDIUM]
                stmt = pg_insert(AkaTitle).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "title"])
                await session.exec(stmt)

        if all_cast_entries:
            # MediaCast has many columns - use smaller chunks
            for i in range(0, len(all_cast_entries), CHUNK_SMALL):
                chunk = all_cast_entries[i : i + CHUNK_SMALL]
                stmt = pg_insert(MediaCast).values(chunk)
                stmt = stmt.on_conflict_do_nothing()
                await session.execute(stmt)

        if all_cert_links:
            for i in range(0, len(all_cert_links), CHUNK_LARGE):
                chunk = all_cert_links[i : i + CHUNK_LARGE]
                stmt = pg_insert(MediaParentalCertificateLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "certificate_id"])
                await session.exec(stmt)

        if all_catalog_links:
            for i in range(0, len(all_catalog_links), CHUNK_LARGE):
                chunk = all_catalog_links[i : i + CHUNK_LARGE]
                stmt = pg_insert(MediaCatalogLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "catalog_id"])
                await session.exec(stmt)

        # Handle series episodes
        if media_type == MediaType.SERIES:
            series_tasks = []
            for old_doc in valid_docs:
                if old_doc.id in created_media_ids:
                    media_id = created_media_ids[old_doc.id]
                    if hasattr(old_doc, "episodes") and old_doc.episodes:
                        series_tasks.append(self._migrate_series_episodes(session, old_doc.episodes, media_id))
            if series_tasks:
                # Process in smaller batches to avoid overwhelming
                for i in range(0, len(series_tasks), 10):
                    batch = series_tasks[i : i + 10]
                    await asyncio.gather(*batch, return_exceptions=True)

    async def _migrate_metadata_relationships(
        self,
        session: AsyncSession,
        old_doc: OldMetaData,
        media_id: int,
        media_type: MediaType,
    ):
        """Migrate all relationships for a metadata record (legacy single-doc version)."""
        # Migrate genres
        if old_doc.genres:
            await self._migrate_genres(session, old_doc.genres, media_id)

        # Migrate AKA titles
        if hasattr(old_doc, "aka_titles") and old_doc.aka_titles:
            await self._migrate_aka_titles(session, old_doc.aka_titles, media_id)

        # Migrate cast (from old stars field)
        if hasattr(old_doc, "stars") and old_doc.stars:
            await self._migrate_cast(session, old_doc.stars, media_id)

        # Migrate certificates
        if hasattr(old_doc, "parent_guide_certificates") and old_doc.parent_guide_certificates:
            await self._migrate_certificates(session, old_doc.parent_guide_certificates, media_id)

        # Migrate catalogs from old metadata's catalog_stats
        await self._migrate_catalogs(session, old_doc, media_id, media_type)

        # Migrate series episodes from IMDB/TMDB data
        if media_type == MediaType.SERIES and hasattr(old_doc, "episodes") and old_doc.episodes:
            await self._migrate_series_episodes(session, old_doc.episodes, media_id)

    async def _migrate_genres(self, session: AsyncSession, genres: list[str], media_id: int):
        """Migrate genres using ON CONFLICT DO NOTHING."""
        genre_links = []
        for genre_name in set(genres):
            genre_id = await self.resource_tracker.get_resource_id(session, "genre", genre_name)
            if genre_id:
                genre_links.append({"media_id": media_id, "genre_id": genre_id})

        if genre_links:
            stmt = pg_insert(MediaGenreLink).values(genre_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "genre_id"])
            await session.exec(stmt)

    async def _migrate_aka_titles(self, session: AsyncSession, titles: list[str], media_id: int):
        """Migrate AKA titles using ON CONFLICT DO NOTHING."""
        title_data = [{"title": title, "media_id": media_id} for title in set(titles) if title]
        if title_data:
            stmt = pg_insert(AkaTitle).values(title_data)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "title"])
            await session.exec(stmt)

    async def _migrate_cast(self, session: AsyncSession, stars: list[str], media_id: int):
        """Migrate cast members using Person + MediaCast."""
        cast_entries = []
        for display_order, star_name in enumerate(stars):
            # Get or create person
            person_id = await self.resource_tracker.get_resource_id(session, "person", star_name)
            if person_id:
                cast_entries.append(
                    {
                        "media_id": media_id,
                        "person_id": person_id,
                        "display_order": display_order,
                    }
                )

        if cast_entries:
            # Use INSERT with ON CONFLICT DO NOTHING
            for entry in cast_entries:
                stmt = pg_insert(MediaCast).values([entry])
                stmt = stmt.on_conflict_do_nothing()  # No unique constraint on media_id + person_id, so just insert
                await session.execute(stmt)

    async def _migrate_certificates(self, session: AsyncSession, certificates: list[str], media_id: int):
        """Migrate certificates using ON CONFLICT DO NOTHING."""
        cert_links = []
        for cert_name in set(certificates):
            cert_id = await self.resource_tracker.get_resource_id(session, "parental_certificate", cert_name)
            if cert_id:
                cert_links.append({"media_id": media_id, "certificate_id": cert_id})

        if cert_links:
            stmt = pg_insert(MediaParentalCertificateLink).values(cert_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "certificate_id"])
            await session.exec(stmt)

    async def _migrate_catalogs(
        self,
        session: AsyncSession,
        old_doc: OldMetaData,
        media_id: int,
        media_type: MediaType,
    ):
        """Migrate catalogs from old metadata's catalog_stats field.

        The old MongoDB schema stores catalog information in catalog_stats on the metadata doc.
        Each CatalogStats entry has a 'catalog' field with the catalog name.
        """
        # Get catalogs from the metadata's catalog_stats field (pre-aggregated)
        catalog_stats = getattr(old_doc, "catalog_stats", []) or []

        if not catalog_stats:
            return

        catalog_links = []
        processed_catalogs = set()

        for stat in catalog_stats:
            catalog = stat.catalog if hasattr(stat, "catalog") else stat.get("catalog")

            if not catalog or catalog in processed_catalogs:
                continue

            # Convert contribution_stream to type-specific catalog name
            if catalog == "contribution_stream":
                if media_type == MediaType.MOVIE:
                    catalog = "contribution_movies"
                elif media_type == MediaType.SERIES:
                    catalog = "contribution_series"
                else:
                    continue

            catalog_id = await self.resource_tracker.get_resource_id(session, "catalog", catalog)

            if catalog_id:
                catalog_links.append({"media_id": media_id, "catalog_id": catalog_id})
                processed_catalogs.add(catalog)

        if catalog_links:
            stmt = pg_insert(MediaCatalogLink).values(catalog_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "catalog_id"])
            await session.exec(stmt)

    async def _migrate_series_episodes(self, session: AsyncSession, episodes: list, media_id: int):
        """
        Migrate series episodes from IMDB/TMDB scraped data.

        This creates Season and Episode records from the authoritative
        IMDB/TMDB metadata (better titles, air dates, etc.).
        The series_migrator will later add additional episodes from torrent data
        that weren't present in IMDB/TMDB using ON CONFLICT DO NOTHING.
        """
        if not episodes:
            return

        # Get series_metadata.id for this media_id
        stmt = select(SeriesMetadata.id).where(SeriesMetadata.media_id == media_id)
        result = await session.exec(stmt)
        series_id = result.first()

        if not series_id:
            logger.warning(f"No SeriesMetadata found for media_id={media_id}")
            return

        # Organize episodes by season
        seasons_data: dict[int, list[dict]] = {}
        for ep in episodes:
            season_num = getattr(ep, "season_number", None)
            episode_num = getattr(ep, "episode_number", None)

            if season_num is None or episode_num is None:
                continue

            if season_num not in seasons_data:
                seasons_data[season_num] = []

            # Extract episode data
            released = getattr(ep, "released", None)
            air_date = None
            if released:
                if isinstance(released, datetime):
                    air_date = released.date()
                elif isinstance(released, date):
                    air_date = released

            # Get thumbnail URL if available
            thumbnail = getattr(ep, "thumbnail", None)

            seasons_data[season_num].append(
                {
                    "episode_number": episode_num,
                    "title": getattr(ep, "title", None) or f"Episode {episode_num}",
                    "air_date": air_date,
                    "thumbnail": thumbnail,
                }
            )

        if not seasons_data:
            return

        # Create seasons
        season_records = [{"series_id": series_id, "season_number": sn} for sn in seasons_data.keys()]

        if season_records:
            stmt = pg_insert(Season).values(season_records)
            stmt = stmt.on_conflict_do_nothing(index_elements=["series_id", "season_number"])
            await session.exec(stmt)
            await session.flush()

        # Get season IDs
        seasons_map = {}
        fetch_stmt = select(Season).where(
            Season.series_id == series_id,
            Season.season_number.in_(list(seasons_data.keys())),
        )
        result = await session.exec(fetch_stmt)
        for season in result.all():
            seasons_map[season.season_number] = season.id

        # Create episodes and collect thumbnail data
        episode_records = []
        episode_thumbnails = []  # (season_id, episode_number, thumbnail_url)
        seen_episodes = set()

        for season_num, eps in seasons_data.items():
            season_id = seasons_map.get(season_num)
            if not season_id:
                continue

            for ep_data in eps:
                ep_num = ep_data["episode_number"]
                ep_key = (season_id, ep_num)

                if ep_key not in seen_episodes:
                    seen_episodes.add(ep_key)
                    episode_records.append(
                        {
                            "season_id": season_id,
                            "episode_number": ep_num,
                            "title": ep_data["title"],
                            "air_date": ep_data["air_date"],
                        }
                    )
                    # Store thumbnail info for later
                    if ep_data.get("thumbnail"):
                        episode_thumbnails.append(
                            {
                                "season_id": season_id,
                                "episode_number": ep_num,
                                "thumbnail": ep_data["thumbnail"],
                            }
                        )

        if episode_records:
            # Batch insert episodes in chunks
            CHUNK_SIZE = 1000
            for i in range(0, len(episode_records), CHUNK_SIZE):
                chunk = episode_records[i : i + CHUNK_SIZE]
                stmt = pg_insert(Episode).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["season_id", "episode_number"])
                await session.exec(stmt)

            await session.flush()

            # Create episode images for thumbnails
            if episode_thumbnails:
                # Get provider ID - try tmdb first, fallback to imdb or mediafusion
                provider_id = (
                    self.migration.get_metadata_provider_id("tmdb")
                    or self.migration.get_metadata_provider_id("imdb")
                    or self.migration.get_metadata_provider_id("mediafusion")
                )

                if provider_id:
                    # Get episode IDs for thumbnail insertion
                    for thumb_data in episode_thumbnails:
                        ep_query = select(Episode.id).where(
                            Episode.season_id == thumb_data["season_id"],
                            Episode.episode_number == thumb_data["episode_number"],
                        )
                        ep_result = await session.exec(ep_query)
                        episode_id = ep_result.first()

                        if episode_id:
                            image_stmt = pg_insert(EpisodeImage).values(
                                {
                                    "episode_id": episode_id,
                                    "provider_id": provider_id,
                                    "image_type": "still",
                                    "url": thumb_data["thumbnail"],
                                    "is_primary": True,
                                }
                            )
                            image_stmt = image_stmt.on_conflict_do_nothing(
                                index_elements=[
                                    "episode_id",
                                    "provider_id",
                                    "image_type",
                                    "url",
                                ]
                            )
                            await session.exec(image_stmt)

    @staticmethod
    def _ensure_timezone(dt: datetime | None) -> datetime | None:
        """Ensure datetime is timezone-aware."""
        if dt and dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
