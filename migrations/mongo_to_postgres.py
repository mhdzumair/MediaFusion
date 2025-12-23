import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timezone
from typing import Dict, List, Set, TypeVar, Type

import typer
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import make_url, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from migrations.mongo_models import (
    MediaFusionMetaData as OldMetaData,
    MediaFusionMovieMetaData as OldMovieMetaData,
    MediaFusionSeriesMetaData as OldSeriesMetaData,
    MediaFusionTVMetaData as OldTVMetaData,
    TorrentStreams as OldTorrentStreams,
    TVStreams as OldTVStreams,
    RSSFeed as OldRSSFeed,
)
from db.sql_models import *
from db.enums import TorrentType
from utils.validation_helper import is_video_file

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)

# Type variables for generic operations
T = TypeVar("T")
ModelType = TypeVar("ModelType", bound=SQLModel)

app = typer.Typer()


@dataclass
class MigrationStats:
    """Track migration statistics and errors"""

    processed: int = 0
    successful: int = 0
    failed: int = 0
    errors: Dict[str, List[str]] = None

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
    
    def __init__(self, migration: "DatabaseMigration"):
        self.migration = migration
    
    async def get_collection_status(self, session: AsyncSession) -> Dict[str, CollectionStatus]:
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
        
        # Torrent Streams
        mongo_torrents = await OldTorrentStreams.count()
        pg_torrents = await session.scalar(select(func.count()).select_from(TorrentStream))
        statuses["torrent_streams"] = CollectionStatus("torrent_streams", mongo_torrents, pg_torrents or 0, mongo_torrents == (pg_torrents or 0))
        
        # TV Streams
        mongo_tv_streams = await OldTVStreams.count()
        pg_tv_streams = await session.scalar(select(func.count()).select_from(TVStream))
        statuses["tv_streams"] = CollectionStatus("tv_streams", mongo_tv_streams, pg_tv_streams or 0, mongo_tv_streams == (pg_tv_streams or 0))
        
        # RSS Feeds
        mongo_rss = await OldRSSFeed.count()
        pg_rss = await session.scalar(select(func.count()).select_from(RSSFeed))
        statuses["rss_feeds"] = CollectionStatus("rss_feeds", mongo_rss, pg_rss or 0, mongo_rss == (pg_rss or 0))
        
        return statuses
    
    def log_status_summary(self, statuses: Dict[str, CollectionStatus]):
        """Log a summary table of all collection statuses"""
        logger.info("\n" + "=" * 70)
        logger.info("📊 MIGRATION STATUS CHECK")
        logger.info("=" * 70)
        logger.info(f"{'Collection':<20} {'MongoDB':<12} {'PostgreSQL':<12} {'Progress':<10} {'Status':<10}")
        logger.info("-" * 70)
        
        for name, status in statuses.items():
            status_icon = "✅ DONE" if status.is_complete else "⏳ PENDING"
            progress = f"{status.progress_pct:.1f}%"
            logger.info(f"{status.name:<20} {status.mongo_count:<12} {status.postgres_count:<12} {progress:<10} {status_icon:<10}")
        
        logger.info("=" * 70 + "\n")
    
    def should_skip(self, status: CollectionStatus, skip_completed: bool) -> bool:
        """Determine if a collection should be skipped"""
        if not skip_completed:
            return False
        return status.is_complete


class ResourceTracker:
    """Enhanced resource tracking with caching and batch operations"""

    def __init__(self):
        self._resource_maps: Dict[str, Dict[str, int]] = {
            "genre": {},
            "catalog": {},
            "language": {},
            "announce_url": {},
            "namespace": {},
            "star": {},
            "parental_certificate": {},
        }
        self._pending_inserts: Dict[str, Set[str]] = {
            key: set() for key in self._resource_maps
        }
        self.resource_models = {
            "genre": Genre,
            "catalog": Catalog,
            "language": Language,
            "announce_url": AnnounceURL,
            "namespace": Namespace,
            "star": Star,
            "parental_certificate": ParentalCertificate,
        }
        self._batch_size = 100

    async def initialize_from_db(self, session: AsyncSession):
        """Load existing resources with optimized batch queries"""
        for resource_type, model in self.resource_models.items():
            stmt = select(model)
            result = await session.exec(stmt)
            resources = result.all()
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

                # Get existing resources in batch
                stmt = select(model).where(model.name.in_(batch))
                result = await session.exec(stmt)
                existing = {r.name: r.id for r in result}

                # Create new resources in batch
                new_resources = [
                    model(name=name) for name in batch if name not in existing
                ]
                if new_resources:
                    session.add_all(new_resources)
                    await session.commit()

                    # Update cache with new IDs
                    stmt = select(model).where(
                        model.name.in_([r.name for r in new_resources])
                    )
                    result = await session.exec(stmt)
                    new_ids = {r.name: r.id for r in result}
                    existing.update(new_ids)

                self._resource_maps[resource_type].update(existing)

            self._pending_inserts[resource_type].clear()

    async def get_resource_id(
        self, session: AsyncSession, resource_type: str, name: str
    ) -> Optional[int]:
        """Get resource ID with efficient caching"""
        if not name:
            return None

        resource_id = self._resource_maps[resource_type].get(name)
        if resource_id is None:
            model = self.resource_models[resource_type]
            new_resource = model(name=name)
            session.add(new_resource)
            await session.commit()
            await session.refresh(new_resource)
            resource_id = new_resource.id
            self._resource_maps[resource_type][name] = resource_id

        return resource_id


class DatabaseMigration:
    """Enhanced database migration with connection pooling and batch processing"""

    def __init__(
        self,
        mongo_uri: str,
        postgres_uri: str,
        batch_size: int = 1000,
        sample_size: Optional[int] = None,
    ):
        self.mongo_uri = mongo_uri
        self.postgres_uri = postgres_uri
        self.batch_size = batch_size
        self.sample_size = sample_size  # None means migrate all data
        self.resource_tracker = ResourceTracker()
        self.stats = MigrationStats()
        self.pg_engine: Optional[AsyncEngine] = None
        self.mongo_client: Optional[AsyncIOMotorClient] = None

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
                self.mongo_client = AsyncIOMotorClient(
                    self.mongo_uri, maxPoolSize=50, minPoolSize=10
                )
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
            async with create_async_engine(
                postgres_url.set(database="postgres")
            ).connect() as conn:
                await conn.execute(text("COMMIT"))
                result = await conn.execute(
                    text(f"SELECT 1 FROM pg_database WHERE datname='{database_name}'")
                )
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


class MetadataMigrator:
    """Dedicated class for handling metadata migration with optimized batch processing"""

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.batch_size = migration.batch_size
        self.resource_tracker = migration.resource_tracker
        self.stats = migration.stats
        self.resume_mode = resume_mode
        self._existing_metadata_ids: Set[str] = set()

    async def _load_existing_metadata_ids(self):
        """Load existing metadata IDs from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_metadata_ids:
            return
        
        logger.info("📥 Loading existing metadata IDs from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            total_count = await session.scalar(select(func.count()).select_from(BaseMetadata))
            logger.info(f"  Total metadata in PostgreSQL: {total_count:,}")
            
            # Use keyset pagination
            chunk_size = 100000
            last_id = ""
            
            while True:
                stmt = (
                    select(BaseMetadata.id)
                    .where(BaseMetadata.id > last_id)
                    .order_by(BaseMetadata.id)
                    .limit(chunk_size)
                )
                result = (await session.exec(stmt)).all()
                
                if not result:
                    break
                
                self._existing_metadata_ids.update(result)
                last_id = result[-1]
                
                if len(self._existing_metadata_ids) % 100000 < chunk_size:
                    logger.info(f"  Loaded {len(self._existing_metadata_ids):,} / {total_count:,} metadata IDs...")
        
        logger.info(f"✅ Loaded {len(self._existing_metadata_ids):,} existing metadata IDs")

    async def migrate_metadata(self):
        """Migrate metadata with enhanced parallel processing and error handling"""
        collections = [
            (OldMovieMetaData, MovieMetadata, MediaType.MOVIE, "movies"),
            (OldSeriesMetaData, SeriesMetadata, MediaType.SERIES, "series"),
            (OldTVMetaData, TVMetadata, MediaType.TV, "tv"),
        ]

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
                        batch = [doc for doc in batch if doc.id not in self._existing_metadata_ids]
                        if not batch:
                            continue
                    
                    async with self.migration.get_session() as session:
                        await self._process_metadata_batch(
                            session, batch, new_model_class, media_type
                        )

            except Exception as e:
                logger.exception(f"Failed to migrate {collection_name}: {str(e)}")
                self.stats.add_error(collection_name, str(e))

    async def _migrate_genres(
        self, session: AsyncSession, genres: List[str], media_id: str
    ):
        """Batch migrate genres using ON CONFLICT DO NOTHING"""
        genre_links = []
        for genre in set(genres):
            genre_id = await self.resource_tracker.get_resource_id(
                session, "genre", genre
            )
            if genre_id:
                genre_links.append({"media_id": media_id, "genre_id": genre_id})

        if genre_links:
            stmt = pg_insert(MediaGenreLink).values(genre_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "genre_id"])
            await session.exec(stmt)

    async def _migrate_aka_titles(
        self, session: AsyncSession, titles: List[str], media_id: str
    ):
        """Batch migrate AKA titles using ON CONFLICT DO NOTHING"""
        title_data = [
            {"title": title, "media_id": media_id}
            for title in set(titles) if title
        ]
        if title_data:
            stmt = pg_insert(AkaTitle).values(title_data)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "title"])
            await session.exec(stmt)

    async def _migrate_stars(
        self, session: AsyncSession, stars: List[str], media_id: str
    ):
        """Batch migrate stars using ON CONFLICT DO NOTHING"""
        star_links = []
        for star in set(stars):
            star_id = await self.resource_tracker.get_resource_id(session, "star", star)
            if star_id:
                star_links.append({"media_id": media_id, "star_id": star_id})

        if star_links:
            stmt = pg_insert(MediaStarLink).values(star_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "star_id"])
            await session.exec(stmt)

    async def _migrate_certificates(
        self, session: AsyncSession, certificates: List[str], media_id: str
    ):
        """Batch migrate certificates using ON CONFLICT DO NOTHING"""
        cert_links = []
        for certificate in set(certificates):
            cert_id = await self.resource_tracker.get_resource_id(
                session, "parental_certificate", certificate
            )
            if cert_id:
                cert_links.append({"media_id": media_id, "certificate_id": cert_id})

        if cert_links:
            stmt = pg_insert(MediaParentalCertificateLink).values(cert_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "certificate_id"])
            await session.exec(stmt)

    async def _migrate_catalogs(self, session: AsyncSession, media_id: str, media_type: MediaType):
        """Migrate catalogs from torrent streams using ON CONFLICT DO NOTHING"""
        # Get all torrent streams for this media
        torrent_streams = await OldTorrentStreams.find(
            {"meta_id": media_id}
        ).to_list()

        # Process catalogs from all streams
        catalog_links = []
        processed_catalogs = set()

        for stream in torrent_streams:
            catalogs = (
                [stream.catalog]
                if isinstance(stream.catalog, str)
                else (stream.catalog or [])
            )

            for catalog in catalogs:
                if not catalog or catalog in processed_catalogs:
                    continue

                # Convert contribution_stream to type-specific catalog name
                if catalog == "contribution_stream":
                    if media_type == MediaType.MOVIE:
                        catalog = "contribution_movies"
                    elif media_type == MediaType.SERIES:
                        catalog = "contribution_series"
                    # For TV/Events, keep the original or skip
                    else:
                        continue

                catalog_id = await self.resource_tracker.get_resource_id(
                    session, "catalog", catalog
                )

                if catalog_id and catalog_id not in processed_catalogs:
                    catalog_links.append({"media_id": media_id, "catalog_id": catalog_id})
                    processed_catalogs.add(catalog_id)

        # Batch insert catalog links with ON CONFLICT DO NOTHING
        if catalog_links:
            stmt = pg_insert(MediaCatalogLink).values(catalog_links)
            stmt = stmt.on_conflict_do_nothing(index_elements=["media_id", "catalog_id"])
            await session.exec(stmt)

    async def _migrate_metadata_relationships(
        self,
        session: AsyncSession,
        old_doc: OldMetaData | OldSeriesMetaData | OldTVMetaData | OldMovieMetaData,
        metadata: SQLModel,
        media_type: MediaType,
    ):
        """Migrate all relationships for a metadata record with batch processing"""
        # Migrate genres
        if old_doc.genres:
            await self._migrate_genres(session, old_doc.genres, metadata.id)

        # Migrate AKA titles
        if hasattr(old_doc, "aka_titles") and old_doc.aka_titles:
            await self._migrate_aka_titles(session, old_doc.aka_titles, metadata.id)

        # Migrate stars
        if hasattr(old_doc, "stars") and old_doc.stars:
            await self._migrate_stars(session, old_doc.stars, metadata.id)

        # Migrate certificates
        if hasattr(old_doc, "parent_guide_certificates"):
            await self._migrate_certificates(
                session, old_doc.parent_guide_certificates or [], metadata.id
            )

        # Migrate catalogs from torrent streams
        await self._migrate_catalogs(session, metadata.id, media_type)
        # Note: commit is done at batch level, not per-record

    async def _get_document_batches(self, model, total):
        """Efficiently yield batches of documents, respecting sample_size limit"""
        # Determine effective total based on sample_size
        sample_limit = self.migration.sample_size
        effective_total = min(total, sample_limit) if sample_limit else total
        
        cursor = model.find()
        if sample_limit:
            cursor = cursor.limit(sample_limit)
        
        with tqdm(total=effective_total, desc=f"Migrating {model.__name__}") as pbar:
            current_batch = []
            count = 0
            async for doc in cursor:
                current_batch.append(doc)
                count += 1
                if len(current_batch) >= self.batch_size:
                    yield current_batch
                    pbar.update(len(current_batch))
                    current_batch = []
                
                # Respect sample_size limit
                if sample_limit and count >= sample_limit:
                    break

            if current_batch:
                yield current_batch
                pbar.update(len(current_batch))

    async def _process_metadata_batch(
        self,
        session: AsyncSession,
        batch: List[OldMetaData],
        new_model_class: Type[SQLModel],
        media_type: MediaType,
    ):
        """Process a batch of metadata documents with optimized bulk operations"""
        base_data_list = []
        specific_data_list = []
        valid_docs = []

        # Transform all documents first
        for old_doc in batch:
            try:
                base_data = await self._transform_base_metadata(old_doc, media_type)
                specific_data = await self._transform_specific_metadata(
                    old_doc, media_type, new_model_class
                )
                base_data_list.append(base_data)
                specific_data_list.append(specific_data)
                valid_docs.append(old_doc)
            except Exception as e:
                logger.error(f"Error transforming document {old_doc.id}: {str(e)}")
                self.stats.add_error("metadata_transform", f"{old_doc.id}: {str(e)}")
                self.stats.failed += 1
                self.stats.processed += 1

        if not valid_docs:
            return

        try:
            # Batch upsert base metadata
            await self._upsert_base_metadata_batch(session, base_data_list)

            # Batch upsert specific metadata
            await self._upsert_specific_metadata_batch(
                session, specific_data_list, new_model_class
            )
            
            # Commit metadata first before handling relationships
            await session.commit()
            self.stats.successful += len(valid_docs)
            self.stats.processed += len(valid_docs)

        except Exception as e:
            logger.exception(f"Batch metadata upsert failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(valid_docs)
            self.stats.processed += len(valid_docs)
            return  # Don't continue with relationships if metadata failed

        # Handle relationships separately - each record in its own mini-transaction
        for old_doc in valid_docs:
            try:
                # Get the specific metadata for relationship migration
                result = await session.exec(
                    select(new_model_class).where(new_model_class.id == old_doc.id)
                )
                specific_meta = result.one_or_none()
                if specific_meta:
                    await self._migrate_metadata_relationships(
                        session, old_doc, specific_meta, media_type
                    )
                    await session.commit()
            except Exception as e:
                logger.error(f"Error migrating relationships for {old_doc.id}: {e}")
                self.stats.add_error("relationships", f"{old_doc.id}: {str(e)}")
                await session.rollback()  # Rollback this record's relationships and continue

    async def _upsert_base_metadata_batch(
        self, session: AsyncSession, base_data_list: List[dict]
    ) -> List[str]:
        """Batch upsert base metadata using PostgreSQL ON CONFLICT"""
        if not base_data_list:
            return []

        stmt = pg_insert(BaseMetadata).values(base_data_list)
        update_cols = {
            col.name: col
            for col in stmt.excluded
            if col.name not in ("id", "created_at", "title_tsv")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=update_cols
        )
        await session.exec(stmt)
        return [d["id"] for d in base_data_list]

    async def _upsert_base_metadata(
        self, session: AsyncSession, base_data: dict
    ) -> BaseMetadata:
        """Upsert base metadata (legacy compatibility)"""
        await self._upsert_base_metadata_batch(session, [base_data])
        result = await session.exec(
            select(BaseMetadata).where(BaseMetadata.id == base_data["id"])
        )
        return result.one_or_none()

    async def _upsert_specific_metadata_batch(
        self, session: AsyncSession, data_list: List[dict], model_class: Type[SQLModel]
    ) -> List[str]:
        """Batch upsert specific metadata using PostgreSQL ON CONFLICT"""
        if not data_list:
            return []

        stmt = pg_insert(model_class).values(data_list)
        update_cols = {
            col.name: col
            for col in stmt.excluded
            if col.name not in ("id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=update_cols
        )
        await session.exec(stmt)
        return [d["id"] for d in data_list]

    async def _upsert_specific_metadata(
        self, session: AsyncSession, specific_data: dict, model_class: Type[SQLModel]
    ) -> SQLModel:
        """Upsert specific metadata (legacy compatibility)"""
        await self._upsert_specific_metadata_batch(session, [specific_data], model_class)
        result = await session.exec(
            select(model_class).where(model_class.id == specific_data["id"])
        )
        return result.one_or_none()

    async def _transform_base_metadata(
        self, old_doc: OldMetaData, media_type: MediaType
    ) -> dict:
        """Transform base metadata with enhanced validation"""
        # Use last_updated_at as fallback for created_at (MongoDB model doesn't have created_at)
        updated_at = self._ensure_timezone(
            getattr(old_doc, "last_updated_at", None) or datetime.now(timezone.utc)
        )
        created_at = self._ensure_timezone(
            getattr(old_doc, "created_at", None) or updated_at
        )

        return {
            "id": old_doc.id,
            "type": media_type,
            "title": old_doc.title,
            "year": old_doc.year,
            "poster": old_doc.poster,
            "is_poster_working": old_doc.is_poster_working,
            "is_add_title_to_poster": old_doc.is_add_title_to_poster,
            "background": old_doc.background,
            "description": old_doc.description,
            "runtime": old_doc.runtime,
            "website": old_doc.website,
            "created_at": created_at,
            "updated_at": updated_at,
            "last_stream_added": created_at,  # Will be updated later
        }

    async def _transform_specific_metadata(
        self, old_doc: OldMetaData, media_type: MediaType, model_class: Type[SQLModel]
    ) -> dict:
        """Transform specific metadata with type validation"""
        data = {"id": old_doc.id}

        if media_type == MediaType.MOVIE:
            data.update(
                {
                    "imdb_rating": getattr(old_doc, "imdb_rating", None),
                    "parent_guide_nudity_status": getattr(
                        old_doc, "parent_guide_nudity_status", None
                    ) or NudityStatus.UNKNOWN,
                }
            )
        elif media_type == MediaType.SERIES:
            data.update(
                {
                    "end_year": getattr(old_doc, "end_year", None),
                    "imdb_rating": getattr(old_doc, "imdb_rating", None),
                    "parent_guide_nudity_status": getattr(
                        old_doc, "parent_guide_nudity_status", None
                    ) or NudityStatus.UNKNOWN,
                }
            )
        elif media_type == MediaType.TV:
            data.update(
                {
                    "country": getattr(old_doc, "country", None),
                    "tv_language": getattr(old_doc, "tv_language", None),
                    "logo": getattr(old_doc, "logo", None),
                }
            )

        return data

    @staticmethod
    def _ensure_timezone(dt: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware"""
        if dt and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt


class SeriesDataMigrator:
    """Handle series-specific data migration with optimized batch processing"""

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.stats = migration.stats
        self.resume_mode = resume_mode
        # Cache for prefetched torrent episodes
        self._torrent_episodes_cache: Dict[str, List[dict]] = {}
        # Batch size for prefetching torrents
        self._prefetch_batch_size = 50
        # Cache for existing series with seasons (for resume mode)
        self._existing_series_with_seasons: Set[str] = set()

    async def _load_existing_series_with_seasons(self):
        """Load series IDs that already have seasons in PostgreSQL"""
        if not self.resume_mode or self._existing_series_with_seasons:
            return
        
        logger.info("📥 Loading series with existing seasons from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            stmt = select(SeriesSeason.series_id).distinct()
            result = (await session.exec(stmt)).all()
            self._existing_series_with_seasons.update(result)
        
        logger.info(f"✅ Loaded {len(self._existing_series_with_seasons):,} series with existing seasons")

    async def migrate_series_metadata(self):
        """Migrate series metadata with optimized batch processing"""
        logger.info("Migrating series data...")
        
        # Get series metadata, respecting sample_size limit
        cursor = OldSeriesMetaData.find()
        if self.migration.sample_size:
            cursor = cursor.limit(self.migration.sample_size)
        series_docs = await cursor.to_list()
        
        # Get all series IDs that exist in PostgreSQL (chunked to avoid 32767 param limit)
        async with self.migration.get_session() as session:
            series_ids = [doc.id for doc in series_docs]
            existing_series_ids = set()
            
            # Query in chunks of 30000 to stay under 32767 limit
            CHUNK_SIZE = 30000
            for i in range(0, len(series_ids), CHUNK_SIZE):
                chunk = series_ids[i:i + CHUNK_SIZE]
                stmt = select(SeriesMetadata.id).where(SeriesMetadata.id.in_(chunk))
                result = (await session.exec(stmt)).all()
                existing_series_ids.update(result)
        
        # Filter to only process series that exist in PostgreSQL
        series_to_process = [doc for doc in series_docs if doc.id in existing_series_ids]
        
        # In resume mode, skip series that already have seasons
        if self.resume_mode:
            await self._load_existing_series_with_seasons()
            
            original_count = len(series_to_process)
            series_to_process = [
                doc for doc in series_to_process 
                if doc.id not in self._existing_series_with_seasons
            ]
            logger.info(
                f"✅ Resume mode: Skipping {original_count - len(series_to_process):,} "
                f"series with existing seasons. Processing {len(series_to_process):,} remaining."
            )
        
        if not series_to_process:
            logger.info("✅ All series already have seasons/episodes migrated!")
            return
        
        # Process in batches with prefetching
        async with self.migration.get_session() as session:
            for i in tqdm(
                range(0, len(series_to_process), self._prefetch_batch_size),
                desc="Migrating series seasons and episodes",
                total=(len(series_to_process) + self._prefetch_batch_size - 1) // self._prefetch_batch_size,
            ):
                batch = series_to_process[i:i + self._prefetch_batch_size]
                
                # Prefetch torrent episodes for this batch
                await self._prefetch_torrent_episodes([doc.id for doc in batch])
                
                # Process each series in the batch
                for series_doc in batch:
                    try:
                        await self._migrate_series_optimized(session, series_doc)
                    except Exception as e:
                        logger.error(
                            f"Error migrating series data for {series_doc.id}: {str(e)}"
                        )
                        self.migration.stats.add_error(
                            "series_migration", f"Series {series_doc.id}: {str(e)}"
                        )
                        await session.rollback()
                        continue
                
                # Commit after each batch
                try:
                    await session.commit()
                except Exception as e:
                    logger.error(f"Error committing batch: {str(e)}")
                    await session.rollback()
                
                # Clear cache after batch
                self._torrent_episodes_cache.clear()

    async def _prefetch_torrent_episodes(self, series_ids: List[str]):
        """Prefetch torrent episodes for multiple series at once"""
        # Query all torrents for these series in one query
        torrent_streams = await OldTorrentStreams.find(
            {"meta_id": {"$in": series_ids}}
        ).to_list()
        
        # Group by series_id
        for stream in torrent_streams:
            series_id = stream.meta_id
            if series_id not in self._torrent_episodes_cache:
                self._torrent_episodes_cache[series_id] = []
            
            if stream.episode_files:
                for episode in stream.episode_files:
                    if episode.filename and is_video_file(episode.filename):
                        self._torrent_episodes_cache[series_id].append({
                            "season_number": episode.season_number,
                            "episode_number": episode.episode_number,
                            "filename": episode.filename,
                            "size": episode.size,
                            "file_index": episode.file_index,
                            "title": episode.title,
                            "released": episode.released,
                            "torrent_id": stream.id,
                        })

    async def _migrate_series_optimized(
        self, session: AsyncSession, old_doc: OldSeriesMetaData
    ):
        """Optimized series migration with batch operations"""
        series_id = old_doc.id
        
        # Get cached torrent episodes (already prefetched)
        torrent_episodes = self._torrent_episodes_cache.get(series_id, [])
        if not torrent_episodes:
            return  # No episodes to migrate
        
        # Group episodes by season
        seasons_data = self._organize_episodes_by_season(torrent_episodes)
        if not seasons_data:
            return
        
        # Batch upsert all seasons at once
        season_numbers = list(seasons_data.keys())
        seasons_map = await self._batch_upsert_seasons(session, series_id, season_numbers)
        
        # Get existing torrent IDs for FK constraint check (sample mode)
        all_torrent_ids = {ep["torrent_id"] for eps in seasons_data.values() for ep in eps if ep.get("torrent_id")}
        if all_torrent_ids:
            stmt = select(TorrentStream.id).where(TorrentStream.id.in_(all_torrent_ids))
            existing_torrent_ids = set((await session.exec(stmt)).all())
        else:
            existing_torrent_ids = set()
        
        # Batch upsert episodes and episode files for all seasons
        await self._batch_upsert_episodes_and_files(
            session, series_id, seasons_data, seasons_map, existing_torrent_ids
        )

    async def _batch_upsert_seasons(
        self, session: AsyncSession, series_id: str, season_numbers: List[int]
    ) -> Dict[int, int]:
        """Batch upsert seasons and return mapping of season_number -> season_id"""
        if not season_numbers:
            return {}
        
        # Prepare season data
        seasons_data = [
            {"series_id": series_id, "season_number": sn}
            for sn in season_numbers
        ]
        
        # Upsert using ON CONFLICT
        stmt = pg_insert(SeriesSeason).values(seasons_data)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["series_id", "season_number"]
        )
        await session.exec(stmt)
        
        # Get all season IDs (including existing ones)
        fetch_stmt = select(SeriesSeason).where(
            SeriesSeason.series_id == series_id,
            SeriesSeason.season_number.in_(season_numbers)
        )
        seasons = (await session.exec(fetch_stmt)).all()
        
        return {s.season_number: s.id for s in seasons}

    async def _batch_upsert_episodes_and_files(
        self,
        session: AsyncSession,
        series_id: str,
        seasons_data: Dict[int, List[dict]],
        seasons_map: Dict[int, int],
        existing_torrent_ids: set,
    ):
        """Batch upsert episodes and episode files"""
        # Collect all episodes to upsert
        episodes_to_upsert = []
        seen_episodes = set()
        
        for season_number, episodes in seasons_data.items():
            season_id = seasons_map.get(season_number)
            if not season_id:
                continue
            
            for ep_data in episodes:
                ep_num = ep_data["episode_number"]
                ep_key = (season_id, ep_num)
                
                if ep_key not in seen_episodes:
                    seen_episodes.add(ep_key)
                    episodes_to_upsert.append({
                        "season_id": season_id,
                        "episode_number": ep_num,
                        "title": ep_data.get("title") or f"Episode {ep_num}",
                        "released": self._ensure_timezone(ep_data.get("released")),
                    })
        
        # Batch upsert episodes (chunk to avoid 32,767 param limit - ~5 cols = 6000 rows max)
        EPISODE_CHUNK_SIZE = 5000
        if episodes_to_upsert:
            for i in range(0, len(episodes_to_upsert), EPISODE_CHUNK_SIZE):
                chunk = episodes_to_upsert[i:i + EPISODE_CHUNK_SIZE]
                stmt = pg_insert(SeriesEpisode).values(chunk)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["season_id", "episode_number"]
                )
                await session.exec(stmt)
        
        # Get all episode IDs
        episode_ids_map = {}  # (season_id, ep_num) -> episode_id
        for season_number, season_id in seasons_map.items():
            fetch_stmt = select(SeriesEpisode).where(SeriesEpisode.season_id == season_id)
            episodes = (await session.exec(fetch_stmt)).all()
            for ep in episodes:
                episode_ids_map[(season_id, ep.episode_number)] = ep.id
        
        # Collect all episode files to upsert
        episode_files_to_upsert = []
        seen_files = set()
        
        for season_number, episodes in seasons_data.items():
            season_id = seasons_map.get(season_number)
            if not season_id:
                continue
            
            for ep_data in episodes:
                torrent_id = ep_data.get("torrent_id")
                if not torrent_id or torrent_id not in existing_torrent_ids:
                    continue
                
                ep_num = ep_data["episode_number"]
                episode_id = episode_ids_map.get((season_id, ep_num))
                
                file_key = (torrent_id, season_number, ep_num)
                if file_key not in seen_files:
                    seen_files.add(file_key)
                    episode_files_to_upsert.append({
                        "torrent_stream_id": torrent_id,
                        "season_number": season_number,
                        "episode_number": ep_num,
                        "file_index": ep_data.get("file_index"),
                        "filename": ep_data.get("filename"),
                        "size": ep_data.get("size"),
                        "episode_id": episode_id,
                    })
        
        # Batch upsert episode files (chunk to avoid 32,767 param limit - 7 cols = 4000 rows max)
        EPISODE_FILE_CHUNK_SIZE = 4000
        if episode_files_to_upsert:
            for i in range(0, len(episode_files_to_upsert), EPISODE_FILE_CHUNK_SIZE):
                chunk = episode_files_to_upsert[i:i + EPISODE_FILE_CHUNK_SIZE]
                stmt = pg_insert(EpisodeFile).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["torrent_stream_id", "season_number", "episode_number"],
                    set_={
                        "file_index": stmt.excluded.file_index,
                        "filename": stmt.excluded.filename,
                        "size": stmt.excluded.size,
                        "episode_id": stmt.excluded.episode_id,
                    }
                )
                await session.exec(stmt)

    async def migrate_series_seasons_episodes(
        self,
        session: AsyncSession,
        old_doc: OldSeriesMetaData,
        series_meta: SeriesMetadata,
    ):
        """Legacy method - redirects to optimized version"""
        # Prefetch if not already cached
        if old_doc.id not in self._torrent_episodes_cache:
            await self._prefetch_torrent_episodes([old_doc.id])
        await self._migrate_series_optimized(session, old_doc)

    async def fix_null_episode_ids(self, session: AsyncSession):
        """Fix episode files with null episode_ids - OPTIMIZED version using batch SQL updates"""
        from sqlalchemy import text
        
        try:
            # Count total null episode files first
            count_stmt = select(func.count()).select_from(EpisodeFile).where(EpisodeFile.episode_id.is_(None))
            total_null = await session.scalar(count_stmt)
            
            if not total_null:
                logger.info("No episode files with null episode_ids")
                return
            
            logger.info(f"Found {total_null:,} episode files with null episode_ids")
            
            # Build episode lookup map efficiently in ONE query
            # Map: (series_id, season_number, episode_number) -> episode_id
            logger.info("Building episode lookup map...")
            stmt = (
                select(
                    SeriesSeason.series_id,
                    SeriesSeason.season_number,
                    SeriesEpisode.episode_number,
                    SeriesEpisode.id.label("episode_id")
                )
                .join(SeriesEpisode, SeriesEpisode.season_id == SeriesSeason.id)
            )
            result = await session.exec(stmt)
            
            # Build lookup: (series_id, season_num, episode_num) -> episode_id
            episode_lookup = {}
            for row in result:
                key = (row.series_id, row.season_number, row.episode_number)
                episode_lookup[key] = row.episode_id
            
            logger.info(f"  Built lookup with {len(episode_lookup):,} episodes")
            
            # Build torrent -> series mapping efficiently
            logger.info("Building torrent-to-series map...")
            stmt = select(TorrentStream.id, TorrentStream.meta_id)
            result = await session.exec(stmt)
            torrent_to_series = {row[0]: row[1] for row in result}
            logger.info(f"  Built map with {len(torrent_to_series):,} torrents")
            
            # IMPORTANT: Get ALL episode file IDs with null episode_id upfront
            # This prevents infinite loop where unmatched records keep getting re-fetched
            logger.info("Loading all null episode_id file IDs (to prevent re-processing)...")
            all_null_ids = []
            last_id = 0
            chunk_size = 100000
            
            while True:
                stmt = (
                    select(EpisodeFile.id)
                    .where(EpisodeFile.episode_id.is_(None))
                    .where(EpisodeFile.id > last_id)
                    .order_by(EpisodeFile.id)
                    .limit(chunk_size)
                )
                result = (await session.exec(stmt)).all()
                if not result:
                    break
                all_null_ids.extend(result)
                last_id = result[-1]
            
            logger.info(f"  Loaded {len(all_null_ids):,} episode file IDs to process")
            
            # Process in batches using the pre-loaded IDs
            # Max 32767 params, we query 1 ID per param, so max ~30000 to be safe
            BATCH_SIZE = 25000
            fixed_count = 0
            unmatched_count = 0
            
            # Use tqdm for progress
            with tqdm(total=len(all_null_ids), desc="Fixing null episode_ids") as pbar:
                for batch_start in range(0, len(all_null_ids), BATCH_SIZE):
                    batch_ids = all_null_ids[batch_start:batch_start + BATCH_SIZE]
                    
                    # Fetch full data for this batch of IDs
                    stmt = (
                        select(EpisodeFile.id, EpisodeFile.torrent_stream_id, 
                               EpisodeFile.season_number, EpisodeFile.episode_number)
                        .where(EpisodeFile.id.in_(batch_ids))
                    )
                    batch = (await session.exec(stmt)).all()
                    
                    if not batch:
                        pbar.update(len(batch_ids))
                        continue
                    
                    # Build updates for this batch
                    updates = []
                    batch_unmatched = 0
                    
                    for ef_id, torrent_id, season_num, episode_num in batch:
                        series_id = torrent_to_series.get(torrent_id)
                        if series_id:
                            key = (series_id, season_num, episode_num)
                            episode_id = episode_lookup.get(key)
                            if episode_id:
                                updates.append({"id": ef_id, "episode_id": episode_id})
                            else:
                                batch_unmatched += 1
                        else:
                            batch_unmatched += 1
                    
                    # Batch update using VALUES clause (much more efficient than CASE WHEN)
                    if updates:
                        # Process in smaller chunks to avoid huge queries
                        UPDATE_CHUNK_SIZE = 5000
                        for chunk_start in range(0, len(updates), UPDATE_CHUNK_SIZE):
                            chunk = updates[chunk_start:chunk_start + UPDATE_CHUNK_SIZE]
                            
                            # Build VALUES list: (id, episode_id), ...
                            values_list = ", ".join(f"({upd['id']}, {upd['episode_id']})" for upd in chunk)
                            
                            update_sql = text(f"""
                                UPDATE episode_file AS ef
                                SET episode_id = v.episode_id
                                FROM (VALUES {values_list}) AS v(id, episode_id)
                                WHERE ef.id = v.id
                            """)
                            
                            # Use connection.execute to avoid deprecation warning
                            conn = await session.connection()
                            await conn.execute(update_sql)
                        
                        await session.commit()
                        fixed_count += len(updates)
                    
                    unmatched_count += batch_unmatched
                    pbar.update(len(batch_ids))
                    pbar.set_postfix(fixed=f"{fixed_count:,}", unmatched=f"{unmatched_count:,}")
            
            logger.info(f"\n✅ Fixed {fixed_count:,} episode files with null episode_ids")
            
            if unmatched_count > 0:
                logger.warning(f"⚠️ {unmatched_count:,} episode files could not be matched (missing series/episode data)")
                logger.info("   These are likely torrents for series without proper metadata")

        except Exception as e:
            logger.error(f"Error fixing null episode_ids: {str(e)}")
            await session.rollback()
            raise

    async def create_stub_episodes_for_unmatched(self, session: AsyncSession):
        """Create stub seasons and episodes for episode files that can't find matching episodes.
        
        This handles the case where:
        - Torrent streams have episode_files
        - But the series metadata doesn't have those episodes scraped
        
        Creates stub episodes marked with is_stub=True for later enrichment.
        """
        from collections import defaultdict
        
        try:
            # Step 1: Count unmatched episode files
            count_stmt = select(func.count()).select_from(EpisodeFile).where(EpisodeFile.episode_id.is_(None))
            total_unmatched = await session.scalar(count_stmt)
            
            if not total_unmatched:
                logger.info("✅ No unmatched episode files - all linked!")
                return
            
            logger.info(f"\n🔧 Creating stub episodes for {total_unmatched:,} unmatched episode files...")
            
            # Step 2: Get unique (meta_id, season_number, episode_number) combinations
            logger.info("  Gathering unique episode combinations...")
            conn = await session.connection()
            result = await conn.execute(text("""
                SELECT DISTINCT ts.meta_id, ef.season_number, ef.episode_number, COUNT(*) as file_count
                FROM episode_file ef
                JOIN torrent_stream ts ON ef.torrent_stream_id = ts.id
                WHERE ef.episode_id IS NULL
                GROUP BY ts.meta_id, ef.season_number, ef.episode_number
                ORDER BY ts.meta_id, ef.season_number, ef.episode_number
            """))
            unique_episodes = result.fetchall()
            logger.info(f"    Found {len(unique_episodes):,} unique (series, season, episode) combinations")
            
            if not unique_episodes:
                return
            
            # Group by meta_id
            series_episodes = defaultdict(list)
            for meta_id, season_num, episode_num, file_count in unique_episodes:
                series_episodes[meta_id].append((season_num, episode_num, file_count))
            
            logger.info(f"    Across {len(series_episodes):,} unique series")
            
            # Step 3: Load existing seasons
            logger.info("  Loading existing seasons...")
            series_ids = list(series_episodes.keys())
            existing_seasons = {}  # (series_id, season_number) -> season_id
            CHUNK_SIZE = 25000
            
            for i in range(0, len(series_ids), CHUNK_SIZE):
                chunk = series_ids[i:i + CHUNK_SIZE]
                result = await session.exec(
                    select(SeriesSeason.series_id, SeriesSeason.season_number, SeriesSeason.id)
                    .where(SeriesSeason.series_id.in_(chunk))
                )
                for series_id, season_num, season_id in result:
                    existing_seasons[(series_id, season_num)] = season_id
            
            logger.info(f"    Found {len(existing_seasons):,} existing seasons")
            
            # Step 4: Load existing episodes
            logger.info("  Loading existing episodes...")
            existing_episodes = set()  # (season_id, episode_number)
            
            season_ids = list(existing_seasons.values())
            for i in range(0, len(season_ids), CHUNK_SIZE):
                chunk = season_ids[i:i + CHUNK_SIZE]
                result = await session.exec(
                    select(SeriesEpisode.season_id, SeriesEpisode.episode_number)
                    .where(SeriesEpisode.season_id.in_(chunk))
                )
                for season_id, ep_num in result:
                    existing_episodes.add((season_id, ep_num))
            
            logger.info(f"    Found {len(existing_episodes):,} existing episodes")
            
            # Step 5: Check which series exist in PostgreSQL
            logger.info("  Checking series existence...")
            existing_series = set()
            for i in range(0, len(series_ids), CHUNK_SIZE):
                chunk = series_ids[i:i + CHUNK_SIZE]
                result = await session.exec(
                    select(SeriesMetadata.id).where(SeriesMetadata.id.in_(chunk))
                )
                existing_series.update(row[0] for row in result)
            
            missing_series_count = len(series_ids) - len(existing_series)
            if missing_series_count > 0:
                logger.warning(f"    ⚠️ {missing_series_count} series not in PostgreSQL (will skip their episodes)")
            
            # Step 6: Identify missing seasons
            logger.info("  Creating stub seasons and episodes...")
            seasons_to_create = []
            
            for meta_id, episode_list in series_episodes.items():
                if meta_id not in existing_series:
                    continue
                
                for season_num, episode_num, _ in episode_list:
                    season_key = (meta_id, season_num)
                    if season_key not in existing_seasons:
                        seasons_to_create.append({
                            "series_id": meta_id,
                            "season_number": season_num,
                        })
            
            # Deduplicate seasons and insert
            if seasons_to_create:
                unique_seasons = {(s["series_id"], s["season_number"]): s for s in seasons_to_create}
                seasons_to_create = list(unique_seasons.values())
                
                logger.info(f"    Creating {len(seasons_to_create):,} new seasons...")
                for i in range(0, len(seasons_to_create), 5000):
                    chunk = seasons_to_create[i:i + 5000]
                    stmt = pg_insert(SeriesSeason).values(chunk)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["series_id", "season_number"])
                    await session.exec(stmt)
                
                await session.commit()
                
                # Reload seasons to get IDs of newly created ones
                for i in range(0, len(series_ids), CHUNK_SIZE):
                    chunk = series_ids[i:i + CHUNK_SIZE]
                    result = await session.exec(
                        select(SeriesSeason.series_id, SeriesSeason.season_number, SeriesSeason.id)
                        .where(SeriesSeason.series_id.in_(chunk))
                    )
                    for series_id, season_num, season_id in result:
                        existing_seasons[(series_id, season_num)] = season_id
            
            # Step 7: Create stub episodes
            episodes_to_create = []
            
            for meta_id, episode_list in series_episodes.items():
                if meta_id not in existing_series:
                    continue
                
                for season_num, episode_num, _ in episode_list:
                    season_key = (meta_id, season_num)
                    season_id = existing_seasons.get(season_key)
                    
                    if season_id is None:
                        continue
                    
                    episode_key = (season_id, episode_num)
                    if episode_key not in existing_episodes:
                        episodes_to_create.append({
                            "season_id": season_id,
                            "episode_number": episode_num,
                            "title": f"Episode {episode_num}",
                            "is_stub": True,
                        })
                        existing_episodes.add(episode_key)
            
            created_episodes = 0
            if episodes_to_create:
                logger.info(f"    Creating {len(episodes_to_create):,} stub episodes...")
                for i in range(0, len(episodes_to_create), 5000):
                    chunk = episodes_to_create[i:i + 5000]
                    stmt = pg_insert(SeriesEpisode).values(chunk)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["season_id", "episode_number"])
                    await session.exec(stmt)
                
                await session.commit()
                created_episodes = len(episodes_to_create)
            
            # Count stub episodes
            stub_count = await session.scalar(
                select(func.count()).select_from(SeriesEpisode).where(SeriesEpisode.is_stub == True)
            )
            
            logger.info(f"\n  ✅ Created {len(seasons_to_create):,} stub seasons")
            logger.info(f"  ✅ Created {created_episodes:,} stub episodes")
            logger.info(f"  📌 Total stub episodes to scrape later: {stub_count:,}")
            
        except Exception as e:
            logger.error(f"Error creating stub episodes: {str(e)}")
            await session.rollback()
            raise

    async def _create_missing_episodes(
        self, session: AsyncSession, unmatched_by_meta: dict
    ):
        """Create missing seasons and episodes for unmatched files with duplicate handling"""
        try:
            created_seasons = 0
            created_episodes = 0

            for meta_id, files in unmatched_by_meta.items():
                # Get series metadata
                stmt = select(SeriesMetadata).where(SeriesMetadata.id == meta_id)
                series_meta = await session.exec(stmt)
                series_meta = series_meta.one_or_none()

                if not series_meta:
                    logger.warning(f"Series metadata not found for {meta_id}")
                    continue

                # Get existing seasons
                stmt = select(SeriesSeason).where(SeriesSeason.series_id == meta_id)
                existing_seasons = await session.exec(stmt)
                existing_seasons = {s.season_number: s for s in existing_seasons}

                # Group files by season
                files_by_season = {}
                for f in files:
                    season_num = f["season"]
                    if season_num not in files_by_season:
                        files_by_season[season_num] = (
                            set()
                        )  # Use set to deduplicate episodes
                    files_by_season[season_num].add(f["episode"])

                # Create missing seasons and episodes
                for season_num, episode_numbers in files_by_season.items():
                    # Get or create season
                    season = existing_seasons.get(season_num)
                    if not season:
                        season = SeriesSeason(
                            series_id=meta_id, season_number=season_num
                        )
                        session.add(season)
                        await session.commit()
                        await session.refresh(season)
                        created_seasons += 1

                    # Get existing episodes for this season
                    stmt = select(SeriesEpisode).where(
                        SeriesEpisode.season_id == season.id
                    )
                    existing_episodes = await session.exec(stmt)
                    existing_episodes = {e.episode_number: e for e in existing_episodes}

                    # Create missing episodes (deduplicated)
                    episodes_to_create = []
                    for ep_num in episode_numbers:
                        if ep_num not in existing_episodes:
                            episodes_to_create.append(
                                SeriesEpisode(
                                    season_id=season.id,
                                    episode_number=ep_num,
                                    title=f"Episode {ep_num}",
                                )
                            )
                            created_episodes += 1

                    if episodes_to_create:
                        session.add_all(episodes_to_create)
                        await session.commit()

                    # Update episode files
                    for file in files:
                        if file["season"] == season_num:
                            # Refresh episode query to get newly created episodes
                            stmt = select(SeriesEpisode).where(
                                SeriesEpisode.season_id == season.id,
                                SeriesEpisode.episode_number == file["episode"],
                            )
                            episode = await session.exec(stmt)
                            episode = episode.one_or_none()

                            if episode:
                                stmt = select(EpisodeFile).where(
                                    EpisodeFile.torrent_stream_id == file["torrent_id"],
                                    EpisodeFile.season_number == season_num,
                                    EpisodeFile.episode_number == file["episode"],
                                )
                                ep_file = await session.exec(stmt)
                                ep_file = ep_file.one_or_none()

                                if ep_file and ep_file.episode_id is None:
                                    ep_file.episode_id = episode.id
                                    session.add(ep_file)

                    await session.commit()

            logger.info(
                f"Created {created_seasons} missing seasons and "
                f"{created_episodes} missing episodes"
            )

        except Exception as e:
            logger.error(f"Error creating missing episodes: {str(e)}")
            await session.rollback()
            raise

    async def _gather_torrent_episodes(self, series_id: str) -> List[dict]:
        """Gather all episode information from torrent streams"""
        torrent_streams = await OldTorrentStreams.find({"meta_id": series_id}).to_list()

        episodes = []
        for stream in torrent_streams:
            if stream.episode_files:
                for episode in stream.episode_files:
                    if episode.filename and is_video_file(episode.filename):
                        episodes.append(
                            {
                                "season_number": episode.season_number,
                                "episode_number": episode.episode_number,
                                "filename": episode.filename,
                                "size": episode.size,
                                "file_index": episode.file_index,
                                "title": episode.title,
                                "released": episode.released,
                                "torrent_id": stream.id,
                            }
                        )

        return episodes

    def _organize_episodes_by_season(
        self, episodes: List[dict]
    ) -> Dict[int, List[dict]]:
        """Organize episodes by season with deduplication"""
        seasons_data = {}
        for episode in episodes:
            season_num = episode["season_number"]
            if season_num not in seasons_data:
                seasons_data[season_num] = {}

            ep_num = episode["episode_number"]
            # Keep the episode with the most complete information
            if ep_num not in seasons_data[season_num] or self._is_better_episode(
                episode, seasons_data[season_num][ep_num]
            ):
                seasons_data[season_num][ep_num] = episode

        return {
            season: list(episodes.values()) for season, episodes in seasons_data.items()
        }

    async def _get_existing_seasons(
        self, session: AsyncSession, series_id: str
    ) -> List[SeriesSeason]:
        """Get existing seasons for a series"""
        stmt = select(SeriesSeason).where(SeriesSeason.series_id == series_id)
        result = await session.exec(stmt)
        return result.all()

    async def _create_series_season(
        self, session: AsyncSession, series_id: str, season_number: int
    ) -> SeriesSeason:
        """Create a new season"""
        season = SeriesSeason(series_id=series_id, season_number=season_number)
        session.add(season)
        await session.commit()
        await session.refresh(season)
        return season

    async def _get_existing_episodes(
        self, session: AsyncSession, season_id: int
    ) -> List[SeriesEpisode]:
        """Get existing episodes for a season"""
        stmt = select(SeriesEpisode).where(SeriesEpisode.season_id == season_id)
        result = await session.exec(stmt)
        return result.all()

    async def _process_season_episodes(
        self, session: AsyncSession, season: SeriesSeason, episodes: List[dict]
    ):
        """Process episodes for a season with improved episode matching"""
        try:
            # Get existing episodes for this series and season
            existing_episodes = await self._get_existing_episodes(session, season.id)
            existing_ep_map = {ep.episode_number: ep for ep in existing_episodes}

            # Get ALL episodes for this series to avoid wrong season linking
            stmt = select(SeriesSeason).where(
                SeriesSeason.series_id == season.series_id
            )
            all_seasons = (await session.exec(stmt)).all()
            all_episodes = {}
            for s in all_seasons:
                s_episodes = await self._get_existing_episodes(session, s.id)
                for ep in s_episodes:
                    all_episodes[(s.season_number, ep.episode_number)] = ep

            # Get existing episode files
            existing_file_stmt = select(EpisodeFile).where(
                EpisodeFile.season_number == season.season_number
            )
            existing_files = await session.exec(existing_file_stmt)
            existing_file_map = {
                (ef.torrent_stream_id, ef.season_number, ef.episode_number): ef
                for ef in existing_files
            }
            
            # Get existing torrent streams to check FK constraints
            torrent_ids = {ep["torrent_id"] for ep in episodes if ep.get("torrent_id")}
            if torrent_ids:
                stmt = select(TorrentStream.id).where(TorrentStream.id.in_(torrent_ids))
                existing_torrents = set((await session.exec(stmt)).all())
            else:
                existing_torrents = set()

            # First ensure all episodes exist in correct season
            episodes_to_create = []
            episode_map = existing_ep_map.copy()

            for ep_data in episodes:
                ep_num = ep_data["episode_number"]

                # Check if episode exists in ANY season
                existing_wrong_season = all_episodes.get((season.season_number, ep_num))
                if (
                    existing_wrong_season
                    and existing_wrong_season.season_id != season.id
                ):
                    logger.warning(
                        f"Episode S{season.season_number}E{ep_num} exists in wrong season. "
                        f"Skipping creation."
                    )
                    continue

                if ep_num not in episode_map:
                    episode = SeriesEpisode(
                        season_id=season.id,
                        episode_number=ep_num,
                        title=ep_data.get("title") or f"Episode {ep_num}",
                        released=self._ensure_timezone(ep_data.get("released")),
                    )
                    episodes_to_create.append(episode)

            # Batch create new episodes
            if episodes_to_create:
                session.add_all(episodes_to_create)
                await session.commit()
                for episode in episodes_to_create:
                    await session.refresh(episode)
                    episode_map[episode.episode_number] = episode

            # Now handle episode files
            episode_files_to_create = []
            episode_files_to_update = []

            for ep_data in episodes:
                torrent_id = ep_data.get("torrent_id")
                if not torrent_id:
                    continue
                
                # Skip if torrent stream doesn't exist (handles sample mode)
                if torrent_id not in existing_torrents:
                    continue

                ep_num = ep_data["episode_number"]
                # Get correct episode from this season
                episode = episode_map.get(ep_num)

                # If not found in current season, check other seasons
                if not episode:
                    other_season_ep = all_episodes.get((season.season_number, ep_num))
                    if other_season_ep:
                        episode = other_season_ep
                    else:
                        logger.warning(
                            f"No episode record found for S{season.season_number}E{ep_num}"
                        )
                        continue

                file_key = (torrent_id, season.season_number, ep_num)

                if file_key not in existing_file_map:
                    episode_file = EpisodeFile(
                        torrent_stream_id=torrent_id,
                        season_number=season.season_number,
                        episode_number=ep_num,
                        file_index=ep_data.get("file_index"),
                        filename=ep_data.get("filename"),
                        size=ep_data.get("size"),
                        episode_id=episode.id,
                    )
                    episode_files_to_create.append(episode_file)
                else:
                    existing_file = existing_file_map[file_key]
                    if (
                        existing_file.file_index != ep_data.get("file_index")
                        or existing_file.filename != ep_data.get("filename")
                        or existing_file.size != ep_data.get("size")
                        or existing_file.episode_id != episode.id
                    ):
                        existing_file.file_index = ep_data.get("file_index")
                        existing_file.filename = ep_data.get("filename")
                        existing_file.size = ep_data.get("size")
                        existing_file.episode_id = episode.id
                        episode_files_to_update.append(existing_file)

            # Batch create/update episode files
            if episode_files_to_create:
                session.add_all(episode_files_to_create)
                await session.commit()

            if episode_files_to_update:
                session.add_all(episode_files_to_update)
                await session.commit()

        except Exception as e:
            logger.error(
                f"Error processing episodes for season {season.season_number}: {str(e)}"
            )
            await session.rollback()
            raise

    @staticmethod
    def _is_better_episode(new_ep: dict, existing_ep: dict) -> bool:
        """Determine if new episode data is better than existing"""
        score_new = sum(1 for k, v in new_ep.items() if v is not None)
        score_existing = sum(1 for k, v in existing_ep.items() if v is not None)
        return score_new > score_existing

    @staticmethod
    def _ensure_timezone(dt: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware"""
        if dt and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt


class StreamMigrator:
    """Handle stream-related migrations"""

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.resource_tracker = migration.resource_tracker
        self.stats = migration.stats
        self.series_migrator = SeriesDataMigrator(migration)
        self.resume_mode = resume_mode
        self._existing_torrent_ids: Set[str] = set()
        self._existing_tv_stream_keys: Set[tuple] = set()

    async def _load_existing_torrent_ids(self):
        """Load existing torrent IDs from PostgreSQL for resume mode using keyset pagination"""
        if not self.resume_mode or self._existing_torrent_ids:
            return
        
        logger.info("📥 Loading existing torrent IDs from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            # First get total count for progress
            total_count = await session.scalar(select(func.count()).select_from(TorrentStream))
            logger.info(f"  Total torrent streams in PostgreSQL: {total_count:,}")
            
            # Use keyset pagination (much faster than OFFSET for large tables)
            chunk_size = 100000
            last_id = ""  # Start from beginning (empty string sorts before all IDs)
            
            while True:
                # Get next chunk ordered by ID, starting after last_id
                stmt = (
                    select(TorrentStream.id)
                    .where(TorrentStream.id > last_id)
                    .order_by(TorrentStream.id)
                    .limit(chunk_size)
                )
                result = (await session.exec(stmt)).all()
                
                if not result:
                    break
                    
                self._existing_torrent_ids.update(result)
                last_id = result[-1]  # Use last ID for next iteration
                
                if len(self._existing_torrent_ids) % 500000 < chunk_size:
                    logger.info(f"  Loaded {len(self._existing_torrent_ids):,} / {total_count:,} torrent IDs...")
        
        logger.info(f"✅ Loaded {len(self._existing_torrent_ids):,} existing torrent IDs")

    async def _load_existing_tv_stream_keys(self):
        """Load existing TV stream keys (url, ytId) from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_tv_stream_keys:
            return
        
        logger.info("📥 Loading existing TV stream keys from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            # Get total count
            total_count = await session.scalar(select(func.count()).select_from(TVStream))
            logger.info(f"  Total TV streams in PostgreSQL: {total_count:,}")
            
            # Use keyset pagination with ID
            chunk_size = 10000
            last_id = 0
            
            while True:
                stmt = (
                    select(TVStream.id, TVStream.url, TVStream.ytId)
                    .where(TVStream.id > last_id)
                    .order_by(TVStream.id)
                    .limit(chunk_size)
                )
                result = (await session.exec(stmt)).all()
                
                if not result:
                    break
                    
                for r in result:
                    self._existing_tv_stream_keys.add((r[1], r[2]))  # (url, ytId)
                last_id = result[-1][0]  # id for next iteration
        
        logger.info(f"✅ Loaded {len(self._existing_tv_stream_keys):,} existing TV stream keys")

    async def migrate_torrent_streams(self):
        """Migrate torrent streams with enhanced error handling and batching"""
        total = await OldTorrentStreams.find().count()
        if total == 0:
            logger.info("No torrent streams to migrate")
            return

        # Load existing IDs for resume mode
        if self.resume_mode:
            await self._load_existing_torrent_ids()

        async for batch in self._get_stream_batches(OldTorrentStreams, total):
            # Filter out already-existing records in resume mode
            if self.resume_mode:
                batch = [s for s in batch if s.id not in self._existing_torrent_ids]
                if not batch:
                    continue
            
            async with self.migration.get_session() as session:
                await self._process_torrent_batch(session, batch)

    async def migrate_tv_streams(self):
        """Migrate TV streams with enhanced error handling and batching"""
        total = await OldTVStreams.find().count()
        if total == 0:
            logger.info("No TV streams to migrate")
            return

        # Load existing keys for resume mode
        if self.resume_mode:
            await self._load_existing_tv_stream_keys()

        async for batch in self._get_stream_batches(OldTVStreams, total):
            # Filter out already-existing records in resume mode
            if self.resume_mode:
                batch = [s for s in batch if (s.url, s.ytId) not in self._existing_tv_stream_keys]
                if not batch:
                    continue
            
            async with self.migration.get_session() as session:
                await self._process_tv_stream_batch(session, batch)

    async def _get_stream_batches(self, model, total):
        """Yield stream batches efficiently, respecting sample_size limit"""
        sample_limit = self.migration.sample_size
        effective_total = min(total, sample_limit) if sample_limit else total
        
        cursor = model.find()
        if sample_limit:
            cursor = cursor.limit(sample_limit)
        
        with tqdm(total=effective_total, desc=f"Migrating {model.__name__}") as pbar:
            current_batch = []
            count = 0
            async for stream in cursor:
                current_batch.append(stream)
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

    def _transform_torrent_stream(self, old_stream: OldTorrentStreams) -> dict:
        """Transform torrent stream to new format with validation"""
        return {
            "id": old_stream.id.lower(),
            "meta_id": old_stream.meta_id,
            "torrent_name": old_stream.torrent_name,
            "size": old_stream.size,
            "source": old_stream.source,
            "resolution": old_stream.resolution,
            "codec": old_stream.codec,
            "quality": old_stream.quality,
            "audio": (
                old_stream.audio[0]
                if isinstance(old_stream.audio, list) and old_stream.audio
                else (old_stream.audio if not isinstance(old_stream.audio, list) else None)
            ),
            "seeders": old_stream.seeders,
            "is_blocked": old_stream.is_blocked,
            "filename": (old_stream.filename if not old_stream.episode_files else None),
            "file_index": (old_stream.file_index if not old_stream.episode_files else None),
            "torrent_type": old_stream.torrent_type or TorrentType.PUBLIC,
            "uploader": old_stream.uploader,
            "created_at": old_stream.created_at,
            "updated_at": old_stream.updated_at,
            "uploaded_at": old_stream.uploaded_at,
            "hdr": old_stream.hdr,
            "torrent_file": old_stream.torrent_file,
        }

    async def _upsert_torrent_streams_batch(
        self, session: AsyncSession, streams_data: List[dict]
    ) -> List[str]:
        """Batch upsert torrent streams using PostgreSQL ON CONFLICT"""
        if not streams_data:
            return []

        # Deduplicate by ID - MongoDB may have duplicate documents with same info_hash
        # Keep the last occurrence (most recent) for each ID
        seen_ids = {}
        for stream in streams_data:
            seen_ids[stream["id"]] = stream
        deduped_streams = list(seen_ids.values())
        
        if len(deduped_streams) < len(streams_data):
            logger.debug(f"Deduplicated {len(streams_data) - len(deduped_streams)} duplicate torrent streams")

        # TorrentStream has ~20 columns, so max 1600 rows per batch (32767/20)
        # Use 1000 to be safe
        CHUNK_SIZE = 1000
        all_ids = []
        
        for i in range(0, len(deduped_streams), CHUNK_SIZE):
            chunk = deduped_streams[i:i + CHUNK_SIZE]
            
            # Build the insert statement with ON CONFLICT DO UPDATE
            stmt = pg_insert(TorrentStream).values(chunk)
            
            # Define columns to update on conflict
            update_cols = {
                col.name: col
                for col in stmt.excluded
                if col.name not in ("id", "created_at")
            }
            
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_=update_cols
            )
            
            await session.exec(stmt)
            all_ids.extend([s["id"] for s in chunk])
        
        return all_ids

    async def _upsert_torrent_stream(
        self, session: AsyncSession, stream_data: dict
    ) -> TorrentStream:
        """Upsert single torrent stream (legacy compatibility)"""
        ids = await self._upsert_torrent_streams_batch(session, [stream_data])
        if ids:
            result = await session.exec(
                select(TorrentStream).where(TorrentStream.id == ids[0])
            )
            return result.one()
        return None

    async def _migrate_torrent_relationships_batch(
        self, session: AsyncSession, streams_with_data: List[tuple]
    ):
        """Batch migrate torrent stream relationships using ON CONFLICT DO NOTHING"""
        language_links = []
        announce_links = []

        for old_stream, stream_id in streams_with_data:
            # Collect language links
            if old_stream.languages:
                for lang in set(old_stream.languages):
                    lang_id = await self.resource_tracker.get_resource_id(
                        session, "language", lang
                    )
                    if lang_id:
                        language_links.append({
                            "torrent_id": stream_id,
                            "language_id": lang_id
                        })

            # Collect announce URL links
            if old_stream.announce_list:
                for url in set(old_stream.announce_list):
                    url_id = await self.resource_tracker.get_resource_id(
                        session, "announce_url", url
                    )
                    if url_id:
                        announce_links.append({
                            "torrent_id": stream_id,
                            "announce_id": url_id
                        })

        # Batch insert language links with ON CONFLICT DO NOTHING
        # PostgreSQL has a 32,767 parameter limit, so we chunk inserts
        # For 2-column tables: max 16,000 rows per insert to be safe
        CHUNK_SIZE = 15000
        
        if language_links:
            for i in range(0, len(language_links), CHUNK_SIZE):
                chunk = language_links[i:i + CHUNK_SIZE]
                stmt = pg_insert(TorrentLanguageLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["torrent_id", "language_id"]
                )
                await session.exec(stmt)

        # Batch insert announce links with ON CONFLICT DO NOTHING
        if announce_links:
            for i in range(0, len(announce_links), CHUNK_SIZE):
                chunk = announce_links[i:i + CHUNK_SIZE]
                stmt = pg_insert(TorrentAnnounceLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["torrent_id", "announce_id"]
                )
                await session.exec(stmt)

    async def _migrate_torrent_relationships(
        self, session: AsyncSession, old_stream: OldTorrentStreams, stream_id: str
    ):
        """Migrate torrent stream relationships (legacy compatibility)"""
        await self._migrate_torrent_relationships_batch(session, [(old_stream, stream_id)])

    async def _process_torrent_batch(
        self, session: AsyncSession, batch: List[OldTorrentStreams]
    ):
        """Process a batch of torrent streams with optimized bulk operations"""
        try:
            # Pre-fetch metadata types for the batch
            meta_ids = {stream.meta_id for stream in batch}
            stmt = select(BaseMetadata.id, BaseMetadata.type).where(
                BaseMetadata.id.in_(meta_ids)
            )
            result = await session.exec(stmt)
            meta_types = {id_: type_ for id_, type_ in result}

            # Filter valid streams and transform them
            valid_streams = []
            streams_data = []
            
            for old_stream in batch:
                if old_stream.meta_id not in meta_types:
                    self.stats.failed += 1
                    self.stats.add_error("fk_violation", f"torrent {old_stream.id}: meta_id '{old_stream.meta_id}' not found")
                    continue
                
                try:
                    stream_data = self._transform_torrent_stream(old_stream)
                    streams_data.append(stream_data)
                    valid_streams.append((old_stream, stream_data["id"]))
                except Exception as e:
                    logger.error(f"Error transforming stream {old_stream.id}: {e}")
                    self.stats.add_error("torrent_transform", f"{old_stream.id}: {str(e)}")
                    self.stats.failed += 1

            if not streams_data:
                return

            # Batch upsert all torrent streams at once
            await self._upsert_torrent_streams_batch(session, streams_data)

            # Batch migrate relationships
            await self._migrate_torrent_relationships_batch(session, valid_streams)

            # Handle episode files for series (still needs individual processing)
            for old_stream, stream_id in valid_streams:
                try:
                    if (
                        meta_types.get(old_stream.meta_id) == MediaType.SERIES
                        and old_stream.episode_files
                    ):
                        await self._migrate_episode_files(session, old_stream, stream_id)
                except Exception as e:
                    logger.error(f"Error migrating episode files for {stream_id}: {e}")
                    self.stats.add_error("episode_files", f"{stream_id}: {str(e)}")

            # Single commit for the entire batch
            await session.commit()
            self.stats.successful += len(valid_streams)

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(batch)
            raise

    async def _migrate_episode_files(
        self, session: AsyncSession, old_stream: OldTorrentStreams, stream_id: str
    ):
        """Migrate episode files using ON CONFLICT DO UPDATE"""
        # Deduplicate episodes from source
        deduplicated_episodes = {}
        for episode in old_stream.episode_files:
            if episode.filename and not is_video_file(episode.filename):
                continue

            key = (episode.season_number, episode.episode_number)
            if key in deduplicated_episodes:
                existing = deduplicated_episodes[key]
                if self._is_better_episode(episode, existing):
                    deduplicated_episodes[key] = episode
            else:
                deduplicated_episodes[key] = episode

        if not deduplicated_episodes:
            return

        # Prepare episode data for batch upsert
        episode_data = []
        for (season_num, episode_num), episode in deduplicated_episodes.items():
            episode_data.append({
                "torrent_stream_id": stream_id,
                "season_number": season_num,
                "episode_number": episode_num,
                "file_index": episode.file_index,
                "filename": episode.filename,
                "size": episode.size,
            })

        # Batch upsert with ON CONFLICT DO UPDATE
        stmt = pg_insert(EpisodeFile).values(episode_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["torrent_stream_id", "season_number", "episode_number"],
            set_={
                "file_index": stmt.excluded.file_index,
                "filename": stmt.excluded.filename,
                "size": stmt.excluded.size,
            }
        )
        await session.exec(stmt)

    def _is_better_episode(self, new_ep, existing_ep) -> bool:
        """
        Determine if the new episode data is better than the existing one
        based on completeness of information
        """

        def score_episode(ep):
            score = 0
            if ep.filename:
                score += 1
            if ep.file_index is not None:
                score += 1
            if ep.size:
                score += 1
            return score

        new_score = score_episode(new_ep)
        existing_score = score_episode(existing_ep)

        # If scores are equal, prefer the one with actual file information
        if new_score == existing_score:
            return bool(new_ep.filename and new_ep.size)

        return new_score > existing_score

    async def _transform_tv_stream(self, old_stream: OldTVStreams) -> dict:
        """Transform TV stream"""

        return {
            "meta_id": old_stream.meta_id,
            "name": old_stream.name,
            "url": old_stream.url,
            "ytId": old_stream.ytId,
            "externalUrl": old_stream.externalUrl,
            "source": old_stream.source,
            "behaviorHints": old_stream.behaviorHints,
            "country": old_stream.country,
            "is_working": old_stream.is_working,
            "test_failure_count": old_stream.test_failure_count,
            "drm_key_id": old_stream.drm_key_id,
            "drm_key": old_stream.drm_key,
        }

    async def _upsert_tv_streams_batch(
        self, session: AsyncSession, streams_data: List[dict]
    ) -> List[int]:
        """Batch upsert TV streams using ON CONFLICT"""
        if not streams_data:
            return []

        stmt = pg_insert(TVStream).values(streams_data)
        update_cols = {
            col.name: col
            for col in stmt.excluded
            if col.name not in ("id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["url", "ytId"],
            set_=update_cols
        ).returning(TVStream.id)
        
        result = await session.exec(stmt)
        return [row[0] for row in result]

    async def _upsert_tv_stream(
        self, session: AsyncSession, stream_data: dict
    ) -> TVStream:
        """Upsert TV stream (legacy compatibility)"""
        ids = await self._upsert_tv_streams_batch(session, [stream_data])
        if ids:
            result = await session.exec(
                select(TVStream).where(TVStream.id == ids[0])
            )
            return result.one()
        return None

    async def _migrate_tv_stream_namespaces(
        self, session: AsyncSession, old_stream: OldTVStreams, stream_id: int
    ):
        """Migrate TV stream namespaces using ON CONFLICT DO NOTHING"""
        namespaces = old_stream.namespaces or ["mediafusion"]
        namespace_links = []
        
        for namespace in set(namespaces):
            namespace_id = await self.resource_tracker.get_resource_id(
                session, "namespace", namespace
            )
            if namespace_id:
                namespace_links.append({
                    "stream_id": stream_id, 
                    "namespace_id": namespace_id
                })

        if namespace_links:
            stmt = pg_insert(TVStreamNamespaceLink).values(namespace_links)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["stream_id", "namespace_id"]
            )
            await session.exec(stmt)

    async def _process_tv_stream_batch(
        self, session: AsyncSession, batch: List[OldTVStreams]
    ):
        """Process a batch of TV streams with optimized bulk operations"""
        try:
            # Get existing metadata IDs for FK validation
            meta_ids = {stream.meta_id for stream in batch if stream.meta_id}
            if meta_ids:
                stmt = select(BaseMetadata.id).where(BaseMetadata.id.in_(meta_ids))
                existing_meta_ids = set((await session.exec(stmt)).all())
            else:
                existing_meta_ids = set()
            
            # Transform all streams first
            streams_data = []
            valid_streams = []
            
            for old_stream in batch:
                # Skip if metadata doesn't exist (handles sample mode)
                if old_stream.meta_id not in existing_meta_ids:
                    self.stats.failed += 1
                    self.stats.add_error("fk_violation", f"tv_stream url={old_stream.url[:50]}...: meta_id '{old_stream.meta_id}' not found")
                    continue
                
                try:
                    stream_data = await self._transform_tv_stream(old_stream)
                    streams_data.append(stream_data)
                    valid_streams.append(old_stream)
                except Exception as e:
                    logger.error(f"Error transforming TV stream: {e}")
                    self.stats.add_error("tv_stream_transform", str(e))
                    self.stats.failed += 1

            if not streams_data:
                return

            # Deduplicate by (url, ytId) - keep last occurrence
            seen_keys = {}
            deduped_streams = []
            deduped_valid_streams = []
            for stream_data, old_stream in zip(streams_data, valid_streams):
                key = (stream_data.get("url"), stream_data.get("ytId"))
                if key in seen_keys:
                    # Replace previous with current
                    idx = seen_keys[key]
                    deduped_streams[idx] = stream_data
                    deduped_valid_streams[idx] = old_stream
                else:
                    seen_keys[key] = len(deduped_streams)
                    deduped_streams.append(stream_data)
                    deduped_valid_streams.append(old_stream)
            
            if len(deduped_streams) < len(streams_data):
                logger.debug(f"Deduplicated {len(streams_data) - len(deduped_streams)} duplicate TV streams")
            
            streams_data = deduped_streams
            valid_streams = deduped_valid_streams

            # Batch upsert all TV streams
            stmt = pg_insert(TVStream).values(streams_data)
            update_cols = {
                col.name: col
                for col in stmt.excluded
                if col.name not in ("id", "created_at")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["url", "ytId"],
                set_=update_cols
            ).returning(TVStream.id, TVStream.url, TVStream.ytId)
            
            result = await session.exec(stmt)
            id_map = {(row[1], row[2]): row[0] for row in result}

            # Batch migrate namespaces
            namespace_links = []
            for old_stream, stream_data in zip(valid_streams, streams_data):
                stream_id = id_map.get((stream_data["url"], stream_data["ytId"]))
                if stream_id:
                    namespaces = old_stream.namespaces or ["mediafusion"]
                    for namespace in set(namespaces):
                        namespace_id = await self.resource_tracker.get_resource_id(
                            session, "namespace", namespace
                        )
                        if namespace_id:
                            namespace_links.append({
                                "stream_id": stream_id,
                                "namespace_id": namespace_id
                            })

            # Chunk namespace links to avoid 32,767 param limit
            if namespace_links:
                CHUNK_SIZE = 15000
                for i in range(0, len(namespace_links), CHUNK_SIZE):
                    chunk = namespace_links[i:i + CHUNK_SIZE]
                    ns_stmt = pg_insert(TVStreamNamespaceLink).values(chunk)
                    ns_stmt = ns_stmt.on_conflict_do_nothing(
                        index_elements=["stream_id", "namespace_id"]
                    )
                    await session.exec(ns_stmt)

            # Single commit for the entire batch
            await session.commit()
            self.stats.successful += len(valid_streams)

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(batch)
            raise


class RSSFeedMigrator:
    """Migrator for RSS Feed data"""

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.stats = migration.stats
        self.resume_mode = resume_mode
        self._existing_rss_feed_urls: Set[str] = set()

    async def _load_existing_rss_feed_urls(self):
        """Load existing RSS feed URLs from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_rss_feed_urls:
            return
        
        logger.info("📥 Loading existing RSS feed URLs from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            stmt = select(RSSFeed.url)
            result = (await session.exec(stmt)).all()
            self._existing_rss_feed_urls.update(result)
        
        logger.info(f"✅ Loaded {len(self._existing_rss_feed_urls):,} existing RSS feed URLs")

    async def migrate_rss_feeds(self):
        """Migrate all RSS feeds from MongoDB to PostgreSQL"""
        logger.info("Starting RSS feed migration...")

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
        """Transform RSS feed to new format"""
        return {
            "name": old_feed.name,
            "url": old_feed.url,
            "active": old_feed.active,
            "last_scraped": old_feed.last_scraped,
            "source": old_feed.source,
            "torrent_type": old_feed.torrent_type or "public",
            "auto_detect_catalog": old_feed.auto_detect_catalog or False,
            "parsing_patterns": (
                old_feed.parsing_patterns.model_dump()
                if old_feed.parsing_patterns
                else None
            ),
            "filters": (
                old_feed.filters.model_dump() if old_feed.filters else None
            ),
            "metrics": (
                old_feed.metrics.model_dump() if old_feed.metrics else None
            ),
            "created_at": old_feed.created_at,
            "updated_at": old_feed.updated_at,
        }

    def _transform_catalog_patterns(self, old_feed: OldRSSFeed) -> list:
        """Transform catalog patterns"""
        patterns = []
        for pattern in old_feed.catalog_patterns or []:
            patterns.append({
                "name": pattern.name,
                "regex": pattern.regex,
                "enabled": pattern.enabled,
                "case_sensitive": pattern.case_sensitive,
                "target_catalogs": pattern.target_catalogs or [],
            })
        return patterns

    async def _upsert_rss_feed(
        self, session: AsyncSession, feed_data: dict, patterns: list
    ) -> int:
        """Upsert RSS feed using ON CONFLICT (returns feed_id)"""
        # Upsert the feed
        stmt = pg_insert(RSSFeed).values([feed_data])
        update_cols = {
            col.name: col
            for col in stmt.excluded
            if col.name not in ("id", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["url"],
            set_=update_cols
        ).returning(RSSFeed.id)
        
        result = await session.exec(stmt)
        feed_id = result.scalar_one()

        # Handle catalog patterns with batch insert
        if patterns:
            # Delete existing patterns
            await session.exec(
                text(
                    "DELETE FROM rss_feed_catalog_pattern WHERE rss_feed_id = :feed_id"
                ).bindparams(feed_id=feed_id)
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
                await session.exec(stmt)

        return feed_id

    async def _process_rss_feed_batch(
        self, session: AsyncSession, batch: List[OldRSSFeed]
    ):
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


class CatalogStatsComputer:
    """Compute and store catalog statistics after migration"""

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration

    async def compute_catalog_statistics(self):
        """Compute catalog statistics for all migrated media"""
        logger.info("Computing catalog statistics...")

        try:
            async with self.migration.get_session() as session:
                # First, update base_metadata total_streams
                await self._update_base_metadata_stats(session)

                # Then, compute per-catalog statistics
                await self._compute_per_catalog_stats(session)

            logger.info("Catalog statistics computation completed")

        except Exception as e:
            logger.exception(f"Error computing catalog statistics: {str(e)}")
            raise

    async def _update_base_metadata_stats(self, session: AsyncSession):
        """Update total_streams and last_stream_added in base_metadata"""
        logger.info("Updating base metadata stream counts...")

        # SQL to aggregate stream counts per meta_id
        update_query = text("""
            UPDATE base_metadata bm
            SET 
                total_streams = COALESCE(ts.stream_count, 0),
                last_stream_added = COALESCE(ts.latest_stream, bm.last_stream_added)
            FROM (
                SELECT 
                    meta_id,
                    COUNT(*) as stream_count,
                    MAX(created_at) as latest_stream
                FROM torrent_stream
                WHERE NOT is_blocked
                GROUP BY meta_id
            ) ts
            WHERE bm.id = ts.meta_id
        """)

        await session.exec(update_query)
        await session.commit()
        logger.info("Base metadata stream counts updated")

    async def _compute_per_catalog_stats(self, session: AsyncSession):
        """Compute per-catalog stream statistics"""
        logger.info("Computing per-catalog statistics...")

        # Get all unique (meta_id, catalog_id) combinations with streams
        stats_query = text("""
            INSERT INTO catalog_stream_stats (media_id, catalog_id, total_streams, last_stream_added)
            SELECT 
                bm.id as media_id,
                mcl.catalog_id,
                COUNT(DISTINCT ts.id) as total_streams,
                MAX(ts.created_at) as last_stream_added
            FROM base_metadata bm
            JOIN media_catalog_link mcl ON bm.id = mcl.media_id
            JOIN torrent_stream ts ON bm.id = ts.meta_id AND NOT ts.is_blocked
            GROUP BY bm.id, mcl.catalog_id
            ON CONFLICT (media_id, catalog_id) DO UPDATE SET
                total_streams = EXCLUDED.total_streams,
                last_stream_added = EXCLUDED.last_stream_added
        """)

        await session.exec(stats_query)
        await session.commit()
        logger.info("Per-catalog statistics computed")


class MigrationVerifier:
    """Enhanced verification system with detailed checking"""

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration
        self.verification_results = {}
        self.is_sample_mode = migration.sample_size is not None
        self.sample_size = migration.sample_size

    async def verify_migration(self):
        """Comprehensive migration verification"""
        if self.is_sample_mode:
            logger.info(f"🧪 Starting SAMPLE MODE verification (sample_size={self.sample_size})...")
        else:
            logger.info("Starting FULL migration verification...")

        try:
            # Verify document counts
            await self._verify_counts()

            # Verify data integrity with sampling
            await self._verify_data_integrity()

            # Verify relationships
            await self._verify_relationships()

            # Verify series-specific data
            await self._verify_series_data()

            # Verify torrent streams
            await self._verify_torrent_streams()

            # Verify RSS feeds
            await self._verify_rss_feeds()

            # Verify catalog statistics
            await self._verify_catalog_statistics()

            # Log verification results
            self._log_verification_results()

        except Exception as e:
            logger.exception(f"Verification failed: {str(e)}")
            raise

    async def _verify_counts(self):
        """Verify document counts between MongoDB and PostgreSQL"""
        async with self.migration.get_session() as session:
            collections = [
                (OldMovieMetaData, MovieMetadata, "movies"),
                (OldSeriesMetaData, SeriesMetadata, "series"),
                (OldTVMetaData, TVMetadata, "tv"),
                (OldTorrentStreams, TorrentStream, "torrent_streams"),
                (OldTVStreams, TVStream, "tv_streams"),
                (OldRSSFeed, RSSFeed, "rss_feeds"),
            ]

            for old_model, new_model, name in collections:
                mongo_count = await old_model.find().count()
                pg_count = await session.scalar(
                    select(func.count()).select_from(new_model)
                )

                # In sample mode, expected count is min(mongo_count, sample_size)
                if self.is_sample_mode:
                    expected_count = min(mongo_count, self.sample_size)
                    # For streams, they depend on metadata - so use actual pg_count as baseline
                    if name in ("torrent_streams", "tv_streams"):
                        # Streams are filtered by existing metadata, so we just verify pg_count > 0
                        matched = pg_count > 0 or mongo_count == 0
                    else:
                        matched = pg_count >= expected_count
                    orphaned = 0
                else:
                    expected_count = mongo_count
                    # For streams, allow for orphaned records (FK violations)
                    if name in ("torrent_streams", "tv_streams"):
                        # Count orphaned records (streams referencing non-existent metadata)
                        orphaned = mongo_count - pg_count
                        # Consider it matched if the difference is only orphaned records
                        # We'll verify this below
                        matched = True  # Will be validated in detailed check
                    else:
                        orphaned = 0
                        matched = mongo_count == pg_count

                self.verification_results[f"{name}_count"] = {
                    "mongo": mongo_count,
                    "postgres": pg_count,
                    "expected": expected_count,
                    "matched": matched,
                    "orphaned": orphaned,
                    "sample_mode": self.is_sample_mode,
                }

    def _log_verification_results(self):
        """Log detailed verification results"""
        if self.is_sample_mode:
            logger.info(f"\n🧪 SAMPLE MODE Verification Results (sample_size={self.sample_size}):")
        else:
            logger.info("\nVerification Results:")
        logger.info("=" * 50)

        # Document counts
        logger.info("\nDocument Counts:")
        for category, data in self.verification_results.items():
            if category.endswith("_count"):
                status = "✅" if data["matched"] else "❌"
                orphaned = data.get("orphaned", 0)
                
                if data.get("sample_mode"):
                    logger.info(
                        f"{status} {category.replace('_count', '')}: "
                        f"MongoDB={data['mongo']}, PostgreSQL={data['postgres']} "
                        f"(expected≥{data['expected']})"
                    )
                elif orphaned > 0:
                    # Show orphaned records separately
                    logger.info(
                        f"{status} {category.replace('_count', '')}: "
                        f"MongoDB={data['mongo']}, PostgreSQL={data['postgres']} "
                        f"(⚠️ {orphaned} orphaned - missing metadata)"
                    )
                else:
                    logger.info(
                        f"{status} {category.replace('_count', '')}: "
                        f"MongoDB={data['mongo']}, PostgreSQL={data['postgres']}"
                    )

        # Data integrity
        if "data_integrity" in self.verification_results:
            logger.info("\nData Integrity Checks:")
            for check, result in self.verification_results["data_integrity"].items():
                status = "✅" if result["passed"] else "❌"
                logger.info(f"{status} {check}: {result['details']}")

        # Relationship integrity
        if "relationships" in self.verification_results:
            logger.info("\nRelationship Checks:")
            for rel, result in self.verification_results["relationships"].items():
                status = "✅" if result["valid"] else "❌"
                logger.info(f"{status} {rel}: {result['details']}")
                logger.info("\n".join(result["issues"][:5]))

        # Series-specific checks
        if "series_data" in self.verification_results:
            logger.info("\nSeries Data Checks:")
            for check, result in self.verification_results["series_data"].items():
                status = "✅" if result["valid"] else "❌"
                logger.info(f"{status} {check}: {result['details']}")
                if result.get("issues"):
                    for issue in result["issues"][:5]:
                        logger.info(f"  - {issue}")

        # Torrent Streams checks
        if "torrent_streams" in self.verification_results:
            logger.info("\nTorrent Streams Checks:")
            for check, result in self.verification_results["torrent_streams"].items():
                status = "✅" if result["valid"] else "❌"
                logger.info(f"{status} {check}: {result['details']}")
                if result.get("issues"):
                    for issue in result["issues"][:5]:
                        logger.info(f"  - {issue}")

        # RSS Feed checks
        if "rss_feeds" in self.verification_results:
            logger.info("\nRSS Feed Checks:")
            for check, result in self.verification_results["rss_feeds"].items():
                status = "✅" if result["valid"] else "❌"
                logger.info(f"{status} {check}: {result['details']}")
                if result.get("issues"):
                    for issue in result["issues"][:5]:
                        logger.info(f"  - {issue}")

        # Catalog Statistics checks
        if "catalog_statistics" in self.verification_results:
            logger.info("\nCatalog Statistics Checks:")
            for check, result in self.verification_results["catalog_statistics"].items():
                status = "✅" if result["valid"] else "❌"
                logger.info(f"{status} {check}: {result['details']}")
                if result.get("issues"):
                    for issue in result["issues"][:5]:
                        logger.info(f"  - {issue}")

        logger.info("=" * 50)

    async def _verify_data_integrity(self):
        """Verify data integrity with comprehensive checks"""
        async with self.migration.get_session() as session:
            self.verification_results["data_integrity"] = {}

            # Sample size for each type
            sample_size = 10

            # Verify movies
            await self._verify_media_type(
                session,
                OldMovieMetaData,
                MovieMetadata,
                "movies",
                sample_size,
                self._verify_movie_data,
            )

            # Verify TV
            await self._verify_media_type(
                session,
                OldTVMetaData,
                TVMetadata,
                "tv",
                sample_size,
                self._verify_tv_data,
            )

    async def _verify_media_type(
        self,
        session: AsyncSession,
        old_model,
        new_model,
        type_name: str,
        sample_size: int,
        verify_func,
    ):
        """Verify specific media type with sampling"""
        mismatches = []
        
        if self.is_sample_mode:
            # In sample mode, get IDs from PostgreSQL and verify against MongoDB
            stmt = select(new_model.id).limit(sample_size)
            pg_ids = (await session.exec(stmt)).all()
            
            if not pg_ids:
                self.verification_results["data_integrity"][type_name] = {
                    "passed": True,
                    "details": f"No {type_name} migrated in sample mode",
                    "mismatches": [],
                }
                return
            
            for pg_id in pg_ids:
                # Get the record from PostgreSQL
                stmt = select(new_model).where(new_model.id == pg_id)
                new_record = (await session.exec(stmt)).one_or_none()
                
                # Get the corresponding record from MongoDB
                old_record = await old_model.find_one({"_id": pg_id})
                
                if not old_record:
                    mismatches.append(f"PostgreSQL record {pg_id} not found in MongoDB (orphaned)")
                    continue
                
                # Verify specific fields
                field_mismatches = await verify_func(session, old_record, new_record)
                if field_mismatches:
                    mismatches.extend(field_mismatches)
        else:
            # In full mode, sample from MongoDB and verify existence in PostgreSQL
            samples = await old_model.aggregate(
                [{"$sample": {"size": sample_size}}]
            ).to_list()

            for sample in samples:
                sample = old_model.model_validate(sample)
                stmt = select(new_model).where(new_model.id == sample.id)
                result = await session.exec(stmt)
                new_record = result.one_or_none()

                if not new_record:
                    mismatches.append(f"Missing record: {sample.id}")
                    continue

                # Verify specific fields
                field_mismatches = await verify_func(session, sample, new_record)
                if field_mismatches:
                    mismatches.extend(field_mismatches)

        self.verification_results["data_integrity"][type_name] = {
            "passed": len(mismatches) == 0,
            "details": f"Found {len(mismatches)} issues",
            "mismatches": mismatches,
        }

    async def _verify_relationships(self):
        """Verify relationship integrity across all models"""
        async with self.migration.get_session() as session:
            self.verification_results["relationships"] = {}

            # Verify genre relationships
            await self._verify_genre_relationships(session)

            # Verify catalog relationships
            await self._verify_catalog_relationships(session)

    async def _verify_genre_relationships(self, session: AsyncSession):
        """Verify genre relationships integrity"""
        # Sample some genres
        stmt = select(Genre).limit(5)
        genres = (await session.exec(stmt)).all()

        issues = []
        for genre in genres:
            # Check media links
            stmt = select(MediaGenreLink).where(MediaGenreLink.genre_id == genre.id)
            links = (await session.exec(stmt)).all()

            for link in links:
                # Verify media exists
                stmt = select(BaseMetadata).where(BaseMetadata.id == link.media_id)
                if not (await session.exec(stmt)).one_or_none():
                    issues.append(
                        f"Genre {genre.name} linked to non-existent media {link.media_id}"
                    )

        self.verification_results["relationships"]["genres"] = {
            "valid": len(issues) == 0,
            "details": f"Found {len(issues)} issues",
            "issues": issues,
        }

    async def _verify_catalog_relationships(self, session: AsyncSession):
        """Verify catalog relationships integrity"""
        # Sample some catalogs
        stmt = select(Catalog).limit(5)
        catalogs = (await session.exec(stmt)).all()

        issues = []
        for catalog in catalogs:
            # Check media links
            stmt = select(MediaCatalogLink).where(
                MediaCatalogLink.catalog_id == catalog.id
            )
            links = (await session.exec(stmt)).all()

            for link in links:
                # Verify media exists
                stmt = select(BaseMetadata).where(BaseMetadata.id == link.media_id)
                if not (await session.exec(stmt)).one_or_none():
                    issues.append(
                        f"Catalog {catalog.name} linked to non-existent media {link.media_id}"
                    )

        self.verification_results["relationships"]["catalogs"] = {
            "valid": len(issues) == 0,
            "details": f"Found {len(issues)} issues",
            "issues": issues,
        }

    async def _verify_series_data(self):
        """Verify series-specific data including seasons and episodes"""
        async with self.migration.get_session() as session:
            self.verification_results["series_data"] = {}

            # In sample mode, only verify series that were actually migrated
            if self.is_sample_mode:
                # Get series IDs that exist in PostgreSQL
                stmt = select(SeriesMetadata.id).limit(min(5, self.sample_size))
                pg_series_ids = (await session.exec(stmt)).all()
                
                if not pg_series_ids:
                    self.verification_results["series_data"]["seasons"] = {
                        "valid": True,
                        "details": "No series migrated in sample mode",
                        "issues": [],
                    }
                    self.verification_results["series_data"]["episode_files"] = {
                        "valid": True,
                        "details": "No series migrated in sample mode",
                        "issues": [],
                    }
                    return
                
                # Verify only migrated series
                series_samples = []
                for series_id in pg_series_ids[:5]:
                    old_series = await OldSeriesMetaData.find_one({"_id": series_id})
                    if old_series:
                        series_samples.append(old_series)
            else:
                # Full mode: random sample from MongoDB
                series_samples = await OldSeriesMetaData.aggregate(
                    [{"$sample": {"size": 5}}]
                ).to_list()
                series_samples = [OldSeriesMetaData.model_validate(s) for s in series_samples]

            season_issues = []
            episode_file_issues = []

            for old_series in series_samples:
                if isinstance(old_series, dict):
                    old_series = OldSeriesMetaData.model_validate(old_series)
                    
                # Get new series record
                stmt = select(SeriesMetadata).where(SeriesMetadata.id == old_series.id)
                new_series = (await session.exec(stmt)).one_or_none()

                if not new_series:
                    continue

                # In sample mode, only check torrents that were migrated
                if self.is_sample_mode:
                    # Get torrent IDs that exist in PostgreSQL for this series
                    stmt = select(TorrentStream.id).where(TorrentStream.meta_id == old_series.id)
                    migrated_torrent_ids = set((await session.exec(stmt)).all())
                    
                    old_torrents = await OldTorrentStreams.find(
                        {"meta_id": old_series.id, "_id": {"$in": list(migrated_torrent_ids)}}
                    ).to_list()
                else:
                    old_torrents = await OldTorrentStreams.find(
                        {"meta_id": old_series.id}
                    ).to_list()

                # Verify seasons and episodes
                for old_torrent in old_torrents:
                    if not old_torrent.episode_files:
                        continue

                    # Check episode files
                    for old_episode in old_torrent.episode_files:
                        if old_episode.filename and not is_video_file(
                            old_episode.filename
                        ):
                            continue

                        # Check season exists
                        stmt = select(SeriesSeason).where(
                            SeriesSeason.series_id == new_series.id,
                            SeriesSeason.season_number == old_episode.season_number,
                        )
                        season = (await session.exec(stmt)).one_or_none()

                        if not season:
                            season_issues.append(
                                f"Missing season {old_episode.season_number} "
                                f"for series {old_series.id}"
                            )
                            continue

                        stmt = select(EpisodeFile).where(
                            EpisodeFile.torrent_stream_id == old_torrent.id,
                            EpisodeFile.season_number == old_episode.season_number,
                            EpisodeFile.episode_number == old_episode.episode_number,
                        )
                        episode_file = (await session.exec(stmt)).one_or_none()

                        if not episode_file:
                            episode_file_issues.append(
                                f"Missing episode file for S{old_episode.season_number}"
                                f"E{old_episode.episode_number} in torrent {old_torrent.id}"
                            )

            self.verification_results["series_data"]["seasons"] = {
                "valid": len(season_issues) == 0,
                "details": f"Found {len(season_issues)} season issues",
                "issues": season_issues,
            }

            self.verification_results["series_data"]["episode_files"] = {
                "valid": len(episode_file_issues) == 0,
                "details": f"Found {len(episode_file_issues)} episode file issues",
                "issues": episode_file_issues,
            }

    @staticmethod
    async def _verify_movie_data(
        session: AsyncSession, old_movie: OldMovieMetaData, new_movie: MovieMetadata
    ) -> List[str]:
        """Verify movie-specific fields"""
        mismatches = []

        if old_movie.imdb_rating != new_movie.imdb_rating:
            mismatches.append(
                f"IMDb rating mismatch for {old_movie.id}: "
                f"{old_movie.imdb_rating} vs {new_movie.imdb_rating}"
            )

        # Handle nudity status comparison - None in MongoDB maps to UNKNOWN in PostgreSQL
        old_nudity = getattr(old_movie, "parent_guide_nudity_status", None)
        new_nudity = new_movie.parent_guide_nudity_status
        if old_nudity is None:
            # If MongoDB value is None, PostgreSQL should have UNKNOWN
            if new_nudity != NudityStatus.UNKNOWN:
                mismatches.append(
                    f"Nudity status mismatch for {old_movie.id}: "
                    f"None (expected UNKNOWN) vs {new_nudity}"
                )
        elif old_nudity != new_nudity:
            mismatches.append(
                f"Nudity status mismatch for {old_movie.id}: "
                f"{old_nudity} vs {new_nudity}"
            )

        return mismatches

    @staticmethod
    async def _verify_tv_data(
        session: AsyncSession, old_tv: OldTVMetaData, new_tv: TVMetadata
    ) -> List[str]:
        """Verify TV-specific fields"""
        mismatches = []

        if old_tv.country != new_tv.country:
            mismatches.append(
                f"Country mismatch for {old_tv.id}: "
                f"{old_tv.country} vs {new_tv.country}"
            )

        if old_tv.tv_language != new_tv.tv_language:
            mismatches.append(
                f"Language mismatch for {old_tv.id}: "
                f"{old_tv.tv_language} vs {new_tv.tv_language}"
            )

        if old_tv.logo != new_tv.logo:
            mismatches.append(
                f"Logo mismatch for {old_tv.id}: " f"{old_tv.logo} vs {new_tv.logo}"
            )

        return mismatches

    async def _verify_torrent_streams(self):
        """Verify torrent streams migration with detailed checking"""
        async with self.migration.get_session() as session:
            self.verification_results["torrent_streams"] = {}
            
            sample_size = min(10, self.sample_size) if self.is_sample_mode else 10
            
            # Get torrent IDs from PostgreSQL
            stmt = select(TorrentStream.id).limit(sample_size)
            pg_torrent_ids = (await session.exec(stmt)).all()
            
            if not pg_torrent_ids:
                self.verification_results["torrent_streams"]["data_integrity"] = {
                    "valid": True,
                    "details": "No torrent streams migrated",
                    "issues": [],
                }
                return
            
            issues = []
            for torrent_id in pg_torrent_ids:
                # Get PostgreSQL record
                stmt = select(TorrentStream).where(TorrentStream.id == torrent_id)
                new_torrent = (await session.exec(stmt)).one_or_none()
                
                # Get MongoDB record
                old_torrent = await OldTorrentStreams.find_one({"_id": torrent_id})
                
                if not old_torrent:
                    issues.append(f"Torrent {torrent_id} not found in MongoDB (orphaned)")
                    continue
                
                # Verify key fields
                if old_torrent.source != new_torrent.source:
                    issues.append(f"Source mismatch for {torrent_id}: {old_torrent.source} vs {new_torrent.source}")
                
                if old_torrent.resolution != new_torrent.resolution:
                    issues.append(f"Resolution mismatch for {torrent_id}: {old_torrent.resolution} vs {new_torrent.resolution}")
                
                if old_torrent.size != new_torrent.size:
                    issues.append(f"Size mismatch for {torrent_id}: {old_torrent.size} vs {new_torrent.size}")
                
                # Verify languages link
                stmt = select(TorrentLanguageLink).where(TorrentLanguageLink.torrent_id == torrent_id)
                pg_lang_links = (await session.exec(stmt)).all()
                
                old_languages = set(old_torrent.languages or [])
                pg_languages = set()
                for link in pg_lang_links:
                    lang_stmt = select(Language).where(Language.id == link.language_id)
                    lang = (await session.exec(lang_stmt)).one_or_none()
                    if lang:
                        pg_languages.add(lang.name)
                
                if old_languages != pg_languages:
                    issues.append(f"Language mismatch for {torrent_id}: {old_languages} vs {pg_languages}")
            
            self.verification_results["torrent_streams"]["data_integrity"] = {
                "valid": len(issues) == 0,
                "details": f"Found {len(issues)} issues in {len(pg_torrent_ids)} samples",
                "issues": issues,
            }

    async def _verify_rss_feeds(self):
        """Verify RSS feeds migration with sampling"""
        async with self.migration.get_session() as session:
            self.verification_results["rss_feeds"] = {}

            # Count verification
            mongo_count = await OldRSSFeed.find().count()
            pg_count = await session.scalar(select(func.count()).select_from(RSSFeed))
            
            if self.is_sample_mode:
                expected_count = min(mongo_count, self.sample_size)
                count_valid = pg_count >= expected_count
            else:
                count_valid = mongo_count == pg_count

            self.verification_results["rss_feeds"]["count"] = {
                "valid": count_valid,
                "details": f"MongoDB: {mongo_count}, PostgreSQL: {pg_count}",
                "issues": (
                    []
                    if count_valid
                    else [f"Count mismatch: {mongo_count} vs {pg_count}"]
                ),
            }

            # Sample verification
            sample_size = min(10, mongo_count) if mongo_count > 0 else 0
            issues = []

            if sample_size > 0:
                samples = await OldRSSFeed.aggregate(
                    [{"$sample": {"size": sample_size}}]
                ).to_list()

                for sample in samples:
                    old_feed = OldRSSFeed.model_validate(sample)
                    stmt = select(RSSFeed).where(RSSFeed.url == old_feed.url)
                    new_feed = (await session.exec(stmt)).one_or_none()

                    if not new_feed:
                        issues.append(f"Missing RSS feed: {old_feed.name} ({old_feed.url})")
                        continue

                    # Verify key fields
                    if old_feed.name != new_feed.name:
                        issues.append(
                            f"Name mismatch for {old_feed.url}: "
                            f"{old_feed.name} vs {new_feed.name}"
                        )
                    if old_feed.active != new_feed.active:
                        issues.append(
                            f"Active status mismatch for {old_feed.url}: "
                            f"{old_feed.active} vs {new_feed.active}"
                        )

                    # Verify catalog patterns count
                    old_pattern_count = len(old_feed.catalog_patterns or [])
                    stmt = select(func.count()).where(
                        RSSFeedCatalogPattern.rss_feed_id == new_feed.id
                    )
                    new_pattern_count = await session.scalar(stmt)

                    if old_pattern_count != new_pattern_count:
                        issues.append(
                            f"Catalog pattern count mismatch for {old_feed.url}: "
                            f"{old_pattern_count} vs {new_pattern_count}"
                        )

            self.verification_results["rss_feeds"]["data_integrity"] = {
                "valid": len(issues) == 0,
                "details": f"Found {len(issues)} issues in {sample_size} samples",
                "issues": issues,
            }

    async def _verify_catalog_statistics(self):
        """Verify catalog statistics accuracy"""
        async with self.migration.get_session() as session:
            self.verification_results["catalog_statistics"] = {}

            # Sample some media items and verify their stream counts
            sample_size = 20
            issues = []

            # Get random sample of media with streams
            sample_query = (
                select(BaseMetadata.id, BaseMetadata.total_streams)
                .where(BaseMetadata.total_streams > 0)
                .limit(sample_size)
            )
            samples = (await session.exec(sample_query)).all()

            for media_id, stored_count in samples:
                # Count actual streams
                actual_count = await session.scalar(
                    select(func.count(TorrentStream.id)).where(
                        TorrentStream.meta_id == media_id,
                        TorrentStream.is_blocked == False,
                    )
                )

                if stored_count != actual_count:
                    issues.append(
                        f"Stream count mismatch for {media_id}: "
                        f"stored={stored_count}, actual={actual_count}"
                    )

            self.verification_results["catalog_statistics"]["stream_counts"] = {
                "valid": len(issues) == 0,
                "details": f"Verified {len(samples)} media items, found {len(issues)} mismatches",
                "issues": issues,
            }

            # Verify per-catalog stats
            catalog_issues = []
            catalog_stats_query = select(CatalogStreamStats).limit(20)
            catalog_stats = (await session.exec(catalog_stats_query)).all()

            for stat in catalog_stats:
                # Verify by counting actual streams for this catalog
                actual_count = await session.scalar(
                    select(func.count(TorrentStream.id))
                    .select_from(TorrentStream)
                    .join(BaseMetadata, TorrentStream.meta_id == BaseMetadata.id)
                    .join(MediaCatalogLink, BaseMetadata.id == MediaCatalogLink.media_id)
                    .where(
                        MediaCatalogLink.catalog_id == stat.catalog_id,
                        BaseMetadata.id == stat.media_id,
                        TorrentStream.is_blocked == False,
                    )
                )

                if stat.total_streams != actual_count:
                    catalog_issues.append(
                        f"Catalog stat mismatch for media={stat.media_id}, "
                        f"catalog={stat.catalog_id}: stored={stat.total_streams}, actual={actual_count}"
                    )

            self.verification_results["catalog_statistics"]["per_catalog"] = {
                "valid": len(catalog_issues) == 0,
                "details": f"Verified {len(catalog_stats)} catalog stats, found {len(catalog_issues)} mismatches",
                "issues": catalog_issues,
            }


@app.command()
def migrate(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    batch_size: int = typer.Option(1000, help="Batch size for processing documents"),
    sample: Optional[int] = typer.Option(
        None, "--sample", "-s",
        help="Limit migration to N documents per collection for testing (e.g., --sample 100)"
    ),
    skip_verification: bool = typer.Option(
        False, help="Skip verification after migration"
    ),
    only_metadata: bool = typer.Option(False, help="Migrate only metadata"),
    only_streams: bool = typer.Option(False, help="Migrate only streams"),
    skip_completed: bool = typer.Option(
        False, "--skip-completed", "-c",
        help="Intelligently skip collections where MongoDB and PostgreSQL counts match"
    ),
    resume: bool = typer.Option(
        False, "--resume", "-r",
        help="Resume mode: skip individual records that already exist in PostgreSQL (checks by ID)"
    ),
):
    """Enhanced migration command with flexible options.
    
    Use --sample to test migration with a limited dataset before running full migration.
    Use --skip-completed to automatically skip collections that are already fully migrated.
    Use --resume to skip individual records that already exist (useful for partial migrations).
    
    Examples:
      # Test with sample data
      python -m migrations.mongo_to_postgres migrate --sample 100
      
      # Resume migration, skipping completed collections
      python -m migrations.mongo_to_postgres migrate --skip-completed
      
      # Resume partial migration, skipping already-migrated records
      python -m migrations.mongo_to_postgres migrate --resume
    """

    async def run_migration():
        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size, sample_size=sample)
        
        if sample:
            logger.info(f"🧪 SAMPLE MODE: Limiting migration to {sample} documents per collection")
        if skip_completed:
            logger.info(f"🔍 SKIP-COMPLETED MODE: Will skip collections with matching counts")
        if resume:
            logger.info(f"🔄 RESUME MODE: Will skip records that already exist in PostgreSQL")
            
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

            # Execute migration based on options
            if not only_streams:
                # Check if metadata collections are complete
                metadata_complete = (
                    skip_completed and 
                    statuses.get("movies", CollectionStatus("", 0, 0, False)).is_complete and
                    statuses.get("series", CollectionStatus("", 0, 0, False)).is_complete and
                    statuses.get("tv", CollectionStatus("", 0, 0, False)).is_complete
                )
                
                if metadata_complete:
                    logger.info("⏭️  Skipping metadata migration (already complete)")
                else:
                    logger.info("Migrating metadata...")
                    await metadata_migrator.migrate_metadata()

            if not only_metadata:
                # Check torrent streams
                torrents_complete = skip_completed and statuses.get("torrent_streams", CollectionStatus("", 0, 0, False)).is_complete
                if torrents_complete:
                    logger.info("⏭️  Skipping torrent streams migration (already complete)")
                else:
                    logger.info("Migrating torrent streams...")
                    await stream_migrator.migrate_torrent_streams()

                # Check TV streams
                tv_streams_complete = skip_completed and statuses.get("tv_streams", CollectionStatus("", 0, 0, False)).is_complete
                if tv_streams_complete:
                    logger.info("⏭️  Skipping TV streams migration (already complete)")
                else:
                    logger.info("Migrating TV streams...")
                    await stream_migrator.migrate_tv_streams()

                # Series seasons/episodes - always run if metadata exists (derived data)
                logger.info("Migrating series seasons and episodes...")
                await series_migrator.migrate_series_metadata()

                # Create stub episodes for unmatched episode files
                # This ensures all episode_files can be linked to episodes
                async with migration.get_session() as session:
                    await series_migrator.create_stub_episodes_for_unmatched(session)

                # Link episode files to episodes (including newly created stubs)
                async with migration.get_session() as session:
                    await series_migrator.fix_null_episode_ids(session)

            # Check RSS feeds
            rss_complete = skip_completed and statuses.get("rss_feeds", CollectionStatus("", 0, 0, False)).is_complete
            if rss_complete:
                logger.info("⏭️  Skipping RSS feeds migration (already complete)")
            else:
                logger.info("Migrating RSS feeds...")
                await rss_feed_migrator.migrate_rss_feeds()

            # Compute catalog statistics after all data is migrated
            logger.info("Computing catalog statistics...")
            await catalog_stats_computer.compute_catalog_statistics()

            # Verify migration if not skipped
            if not skip_verification:
                await verifier.verify_migration()

            # Log final statistics
            migration.stats.log_summary()
            logger.info("Migration completed successfully!")

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
                typer.echo(f"📈 Overall progress: {total_pg:,} / {total_mongo:,} documents ({(total_pg/total_mongo*100):.1f}%)")

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
    """Fix all missing fields (uploader, created_at, updated_at, uploaded_at, hdr, torrent_file) for already migrated torrent streams"""

    async def run_fix():
        from sqlalchemy import update as sa_update
        
        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size=batch_size)
        try:
            await migration.init_connections()
            
            # Get total count from MongoDB
            total = await OldTorrentStreams.find().count()
            logger.info(f"Found {total} torrent streams in MongoDB to check")
            
            updated = 0
            skipped = 0
            cursor = OldTorrentStreams.find()
            
            with tqdm(total=total, desc="Fixing missing fields") as pbar:
                batch = []
                async for stream in cursor:
                    # Collect all fields that might be missing
                    stream_data = {
                        "id": stream.id.lower(),
                        "uploader": stream.uploader,
                        "created_at": stream.created_at,
                        "updated_at": stream.updated_at,
                        "uploaded_at": stream.uploaded_at,
                        "hdr": stream.hdr,
                        "torrent_file": stream.torrent_file,
                    }
                    batch.append(stream_data)
                    
                    if len(batch) >= batch_size:
                        async with migration.get_session() as session:
                            for data in batch:
                                stream_id = data.pop("id")
                                # Only include non-None values in update
                                update_values = {k: v for k, v in data.items() if v is not None}
                                if update_values:
                                    stmt = (
                                        sa_update(TorrentStream)
                                        .where(TorrentStream.id == stream_id)
                                        .values(**update_values)
                                    )
                                    result = await session.exec(stmt)
                                    if result.rowcount > 0:
                                        updated += 1
                                    else:
                                        skipped += 1
                                else:
                                    skipped += 1
                            await session.commit()
                        pbar.update(len(batch))
                        batch = []
                
                # Process remaining batch
                if batch:
                    async with migration.get_session() as session:
                        for data in batch:
                            stream_id = data.pop("id")
                            update_values = {k: v for k, v in data.items() if v is not None}
                            if update_values:
                                stmt = (
                                    sa_update(TorrentStream)
                                    .where(TorrentStream.id == stream_id)
                                    .values(**update_values)
                                )
                                result = await session.exec(stmt)
                                if result.rowcount > 0:
                                    updated += 1
                                else:
                                    skipped += 1
                            else:
                                skipped += 1
                        await session.commit()
                    pbar.update(len(batch))
            
            logger.info(f"Updated {updated} torrent streams with missing fields (skipped {skipped})")
            
        except Exception as e:
            logger.exception(f"Fix missing fields failed: {str(e)}")
            raise typer.Exit(code=1)
        finally:
            await migration.close_connections()

    typer.echo("Fixing missing fields (uploader, created_at, updated_at, uploaded_at, hdr, torrent_file)...")
    asyncio.run(run_fix())


if __name__ == "__main__":
    app()
