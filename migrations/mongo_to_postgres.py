import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timezone
from typing import Dict, Set, TypeVar, Type

import typer
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import make_url, func, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from db.models import (
    MediaFusionMetaData as OldMetaData,
    MediaFusionMovieMetaData as OldMovieMetaData,
    MediaFusionSeriesMetaData as OldSeriesMetaData,
    MediaFusionTVMetaData as OldTVMetaData,
    TorrentStreams as OldTorrentStreams,
    TVStreams as OldTVStreams,
)
from db.sql_models import *
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
        logger.info(
            f"""
Migration Summary:
----------------
Total Processed: {self.processed}
Successful: {self.successful}
Failed: {self.failed}
        """
        )
        if self.errors:
            logger.exception("Errors by category:")
            for category, errors in self.errors.items():
                logger.exception(f"\n{category}:")
                for error in errors:
                    logger.exception(f"  - {error}")


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
    ):
        self.mongo_uri = mongo_uri
        self.postgres_uri = postgres_uri
        self.batch_size = batch_size
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

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration
        self.batch_size = migration.batch_size
        self.resource_tracker = migration.resource_tracker
        self.stats = migration.stats

    async def migrate_metadata(self):
        """Migrate metadata with enhanced parallel processing and error handling"""
        collections = [
            (OldMovieMetaData, MovieMetadata, MediaType.MOVIE, "movies"),
            (OldSeriesMetaData, SeriesMetadata, MediaType.SERIES, "series"),
            (OldTVMetaData, TVMetadata, MediaType.TV, "tv"),
        ]

        for old_model, new_model_class, media_type, collection_name in collections:
            try:
                total = await old_model.find().count()
                if total == 0:
                    logger.info(f"No {collection_name} to migrate")
                    continue

                logger.info(f"Starting migration of {total} {collection_name}")

                async for batch in self._get_document_batches(old_model, total):
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
        """Batch migrate genres"""
        existing = await session.exec(
            select(MediaGenreLink.genre_id).where(MediaGenreLink.media_id == media_id)
        )
        existing_ids = set(row for row in existing)

        for genre in genres:
            genre_id = await self.resource_tracker.get_resource_id(
                session, "genre", genre
            )
            if genre_id and genre_id not in existing_ids:
                session.add(MediaGenreLink(media_id=media_id, genre_id=genre_id))

    async def _migrate_aka_titles(
        self, session: AsyncSession, titles: List[str], media_id: str
    ):
        """Batch migrate AKA titles"""
        existing = await session.exec(
            select(AkaTitle.title).where(AkaTitle.media_id == media_id)
        )
        existing_titles = set(row for row in existing)

        new_titles = [
            AkaTitle(title=title, media_id=media_id)
            for title in titles
            if title not in existing_titles
        ]
        if new_titles:
            session.add_all(new_titles)

    async def _migrate_stars(
        self, session: AsyncSession, stars: List[str], media_id: str
    ):
        """Batch migrate stars"""
        existing = await session.exec(
            select(MediaStarLink.star_id).where(MediaStarLink.media_id == media_id)
        )
        existing_ids = set(row for row in existing)

        for star in stars:
            star_id = await self.resource_tracker.get_resource_id(session, "star", star)
            if star_id and star_id not in existing_ids:
                session.add(MediaStarLink(media_id=media_id, star_id=star_id))

    async def _migrate_certificates(
        self, session: AsyncSession, certificates: List[str], media_id: str
    ):
        """Batch migrate certificates"""
        existing = await session.exec(
            select(MediaParentalCertificateLink.certificate_id).where(
                MediaParentalCertificateLink.media_id == media_id
            )
        )
        existing_ids = set(row for row in existing)

        for certificate in certificates:
            cert_id = await self.resource_tracker.get_resource_id(
                session, "parental_certificate", certificate
            )
            if cert_id and cert_id not in existing_ids:
                session.add(
                    MediaParentalCertificateLink(
                        media_id=media_id, certificate_id=cert_id
                    )
                )

    async def _migrate_catalogs(self, session: AsyncSession, media_id: str):
        """Migrate catalogs from torrent streams with efficient batch processing"""
        try:
            # Get existing catalogs for the media
            existing_result = await session.exec(
                select(MediaCatalogLink.catalog_id).where(
                    MediaCatalogLink.media_id == media_id
                )
            )
            existing_catalog_ids = set(row for row in existing_result)

            # Get all torrent streams for this media
            torrent_streams = await OldTorrentStreams.find(
                {"meta_id": media_id}
            ).to_list()

            # Process catalogs from all streams
            new_catalog_links = []
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

                    catalog_id = await self.resource_tracker.get_resource_id(
                        session, "catalog", catalog
                    )

                    if (
                        catalog_id
                        and catalog_id not in existing_catalog_ids
                        and catalog_id not in processed_catalogs
                    ):
                        new_catalog_links.append(
                            MediaCatalogLink(media_id=media_id, catalog_id=catalog_id)
                        )
                        processed_catalogs.add(catalog_id)

            # Batch insert new catalog links
            if new_catalog_links:
                session.add_all(new_catalog_links)
                await session.commit()

        except Exception as e:
            logger.exception(f"Error migrating catalogs for media {media_id}: {str(e)}")
            await session.rollback()
            raise

    async def _migrate_metadata_relationships(
        self,
        session: AsyncSession,
        old_doc: OldMetaData | OldSeriesMetaData | OldTVMetaData | OldMovieMetaData,
        metadata: SQLModel,
        media_type: MediaType,
    ):
        """Migrate all relationships for a metadata record with batch processing"""
        try:
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
            await self._migrate_catalogs(session, metadata.id)

            await session.commit()

        except Exception as e:
            logger.exception(
                f"Error migrating relationships for {metadata.id}: {str(e)}"
            )
            await session.rollback()
            raise

    async def _get_document_batches(self, model, total):
        """Efficiently yield batches of documents"""
        cursor = model.find()
        with tqdm(total=total) as pbar:
            current_batch = []
            async for doc in cursor:
                current_batch.append(doc)
                if len(current_batch) >= self.batch_size:
                    yield current_batch
                    pbar.update(len(current_batch))
                    current_batch = []

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
        """Process a batch of metadata documents efficiently"""
        for old_doc in batch:
            try:
                # Handle base metadata
                base_data = await self._transform_base_metadata(old_doc, media_type)
                base_meta = await self._upsert_base_metadata(session, base_data)

                # Handle type-specific metadata
                specific_data = await self._transform_specific_metadata(
                    old_doc, media_type, new_model_class
                )
                specific_meta = await self._upsert_specific_metadata(
                    session, specific_data, new_model_class
                )

                # Handle relationships
                await self._migrate_metadata_relationships(
                    session, old_doc, specific_meta, media_type
                )

                self.stats.successful += 1

            except Exception as e:
                logger.exception(f"Error processing document {old_doc.id}: {str(e)}")
                self.stats.add_error("metadata_processing", f"{old_doc.id}: {str(e)}")
                self.stats.failed += 1
                continue

            finally:
                self.stats.processed += 1

    async def _upsert_base_metadata(
        self, session: AsyncSession, base_data: dict
    ) -> BaseMetadata:
        """Upsert base metadata with optimized query"""
        stmt = select(BaseMetadata).where(BaseMetadata.id == base_data["id"])
        result = await session.exec(stmt)
        base_meta = result.one_or_none()

        if base_meta:
            for key, value in base_data.items():
                setattr(base_meta, key, value)
        else:
            base_meta = BaseMetadata(**base_data)
            session.add(base_meta)

        await session.commit()
        return base_meta

    async def _upsert_specific_metadata(
        self, session: AsyncSession, specific_data: dict, model_class: Type[SQLModel]
    ) -> SQLModel:
        """Upsert specific metadata with optimized query"""
        stmt = select(model_class).where(model_class.id == specific_data["id"])
        result = await session.exec(stmt)
        specific_meta = result.one_or_none()

        if specific_meta:
            for key, value in specific_data.items():
                if key != "id" and key in specific_meta.model_fields:
                    setattr(specific_meta, key, value)
        else:
            specific_meta = model_class(**specific_data)
            session.add(specific_meta)

        await session.commit()
        return specific_meta

    async def _transform_base_metadata(
        self, old_doc: OldMetaData, media_type: MediaType
    ) -> dict:
        """Transform base metadata with enhanced validation"""
        created_at = self._ensure_timezone(old_doc.created_at)
        updated_at = self._ensure_timezone(old_doc.last_updated_at)

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
                        old_doc, "parent_guide_nudity_status", NudityStatus.UNKNOWN
                    ),
                }
            )
        elif media_type == MediaType.SERIES:
            data.update(
                {
                    "end_year": getattr(old_doc, "end_year", None),
                    "imdb_rating": getattr(old_doc, "imdb_rating", None),
                    "parent_guide_nudity_status": getattr(
                        old_doc, "parent_guide_nudity_status", NudityStatus.UNKNOWN
                    ),
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
    """Handle series-specific data migration"""

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration
        self.stats = migration.stats

    async def migrate_series_metadata(self):
        """Migrate series metadata with enhanced parallel processing and error handling"""
        logger.info("Migrating series data...")
        # Get all series metadata
        series_docs = await OldSeriesMetaData.find().to_list()
        async with self.migration.get_session() as session:
            for series_doc in tqdm(
                series_docs, desc="Migrating series seasons and episodes"
            ):
                try:
                    # Get the corresponding series metadata from postgres
                    stmt = select(SeriesMetadata).where(
                        SeriesMetadata.id == series_doc.id
                    )
                    series_meta = (await session.exec(stmt)).one_or_none()

                    if series_meta:
                        await self.migrate_series_seasons_episodes(
                            session, series_doc, series_meta
                        )
                except Exception as e:
                    logger.error(
                        f"Error migrating series data for {series_doc.id}: {str(e)}"
                    )
                    self.migration.stats.add_error(
                        "series_migration", f"Series {series_doc.id}: {str(e)}"
                    )
                    await session.rollback()
                    continue

    async def migrate_series_seasons_episodes(
        self,
        session: AsyncSession,
        old_doc: OldSeriesMetaData,
        series_meta: SeriesMetadata,
    ):
        """Migrate series seasons and episodes with efficient batch processing"""
        try:
            # Get all existing seasons for the series
            existing_seasons = await self._get_existing_seasons(session, series_meta.id)
            existing_season_numbers = {s.season_number for s in existing_seasons}

            # Get episodes from torrent streams
            torrent_episodes = await self._gather_torrent_episodes(old_doc.id)

            # Group episodes by season
            seasons_data = self._organize_episodes_by_season(torrent_episodes)

            # Process each season
            for season_number, episodes in seasons_data.items():
                if season_number not in existing_season_numbers:
                    season = await self._create_series_season(
                        session, series_meta.id, season_number
                    )
                else:
                    season = next(
                        s for s in existing_seasons if s.season_number == season_number
                    )

                await self._process_season_episodes(session, season, episodes)

        except Exception as e:
            logger.exception(
                f"Failed to migrate seasons/episodes for series {old_doc.id}: {str(e)}"
            )
            self.stats.add_error("series_migration", f"{old_doc.id}: {str(e)}")
            raise

    async def fix_null_episode_ids(self, session: AsyncSession):
        """Fix episode files with null episode_ids with enhanced matching and logging"""
        try:
            # Get all episode files with null episode_ids
            stmt = select(EpisodeFile).where(EpisodeFile.episode_id.is_(None))
            null_episode_files = (await session.exec(stmt)).all()

            if not null_episode_files:
                return

            logger.info(
                f"Found {len(null_episode_files)} episode files with null episode_ids"
            )

            # Get all seasons and episodes with series information
            stmt = select(SeriesSeason, SeriesMetadata).join(
                SeriesMetadata, SeriesSeason.series_id == SeriesMetadata.id
            )
            seasons_result = await session.exec(stmt)
            seasons_with_meta = seasons_result.all()

            # Create comprehensive mapping structures
            episodes_by_season = {}
            meta_by_season = {}
            for season, meta in seasons_with_meta:
                # Get episodes for this season
                stmt = select(SeriesEpisode).where(SeriesEpisode.season_id == season.id)
                episodes = (await session.exec(stmt)).all()
                episodes_by_season[season.season_number] = {
                    ep.episode_number: ep for ep in episodes
                }
                meta_by_season[season.season_number] = meta

            # Track unmatched files for analysis
            unmatched_files = []
            fixed_count = 0

            # Get torrent stream information for unmatched files
            torrent_ids = {ef.torrent_stream_id for ef in null_episode_files}
            stmt = select(TorrentStream).where(TorrentStream.id.in_(torrent_ids))
            torrent_streams = (await session.exec(stmt)).all()
            torrent_meta_map = {ts.id: ts.meta_id for ts in torrent_streams}

            # Fix null episode_ids with enhanced matching
            for ef in null_episode_files:
                season_episodes = episodes_by_season.get(ef.season_number, {})
                matching_episode = season_episodes.get(ef.episode_number)

                if matching_episode:
                    ef.episode_id = matching_episode.id
                    fixed_count += 1
                else:
                    # Collect detailed information about unmatched files
                    meta_id = torrent_meta_map.get(ef.torrent_stream_id)
                    unmatched_files.append(
                        {
                            "torrent_id": ef.torrent_stream_id,
                            "meta_id": meta_id,
                            "season": ef.season_number,
                            "episode": ef.episode_number,
                            "filename": ef.filename,
                        }
                    )

            if fixed_count:
                await session.commit()
                logger.info(f"Fixed {fixed_count} episode files with null episode_ids")

            if unmatched_files:
                logger.warning(
                    f"\nRemaining {len(unmatched_files)} unmatched episode files:"
                )

                # Group unmatched files by meta_id for better analysis
                unmatched_by_meta = {}
                for uf in unmatched_files:
                    meta_id = uf["meta_id"]
                    if meta_id not in unmatched_by_meta:
                        unmatched_by_meta[meta_id] = []
                    unmatched_by_meta[meta_id].append(uf)

                # Create missing seasons and episodes
                await self._create_missing_episodes(session, unmatched_by_meta)

        except Exception as e:
            logger.error(f"Error fixing null episode_ids: {str(e)}")
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
            if stream.season and stream.season.episodes:
                for episode in stream.season.episodes:
                    if episode.filename and is_video_file(episode.filename):
                        episodes.append(
                            {
                                "season_number": stream.season.season_number,
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
                        air_date=self._ensure_timezone(ep_data.get("released")),
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
                if not ep_data.get("torrent_id"):
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

                file_key = (ep_data["torrent_id"], season.season_number, ep_num)

                if file_key not in existing_file_map:
                    episode_file = EpisodeFile(
                        torrent_stream_id=ep_data["torrent_id"],
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

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration
        self.resource_tracker = migration.resource_tracker
        self.stats = migration.stats
        self.series_migrator = SeriesDataMigrator(migration)

    async def migrate_torrent_streams(self):
        """Migrate torrent streams with enhanced error handling and batching"""
        total = await OldTorrentStreams.find().count()
        if total == 0:
            logger.info("No torrent streams to migrate")
            return

        async for batch in self._get_stream_batches(OldTorrentStreams, total):
            async with self.migration.get_session() as session:
                await self._process_torrent_batch(session, batch)

    async def migrate_tv_streams(self):
        """Migrate TV streams with enhanced error handling and batching"""
        total = await OldTVStreams.find().count()
        if total == 0:
            logger.info("No TV streams to migrate")
            return

        async for batch in self._get_stream_batches(OldTVStreams, total):
            async with self.migration.get_session() as session:
                await self._process_tv_stream_batch(session, batch)

    async def _get_stream_batches(self, model, total):
        """Yield stream batches efficiently"""
        cursor = model.find()
        with tqdm(total=total) as pbar:
            current_batch = []
            async for stream in cursor:
                current_batch.append(stream)
                if len(current_batch) >= self.migration.batch_size:
                    yield current_batch
                    pbar.update(len(current_batch))
                    current_batch = []

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
                if isinstance(old_stream.audio, list)
                else old_stream.audio
            ),
            "seeders": old_stream.seeders,
            "is_blocked": old_stream.is_blocked,
            "filename": (old_stream.filename if not old_stream.season else None),
            "file_index": (old_stream.file_index if not old_stream.season else None),
            "indexer_flag": (
                old_stream.indexer_flags[0]
                if old_stream.indexer_flags
                else IndexerType.FREELEACH
            ),
        }

    async def _upsert_torrent_stream(
        self, session: AsyncSession, stream_data: dict
    ) -> TorrentStream:
        """Upsert torrent stream with optimized query"""
        stmt = select(TorrentStream).where(TorrentStream.id == stream_data["id"])
        result = await session.exec(stmt)
        stream = result.one_or_none()

        if stream:
            for key, value in stream_data.items():
                setattr(stream, key, value)
        else:
            stream = TorrentStream(**stream_data)
            session.add(stream)

        await session.commit()
        await session.refresh(stream)
        return stream

    async def _migrate_torrent_relationships(
        self, session: AsyncSession, old_stream: OldTorrentStreams, stream_id: str
    ):
        """Migrate torrent stream relationships with batch processing"""
        try:
            # Migrate languages
            if old_stream.languages:
                existing = await session.exec(
                    select(TorrentLanguageLink.language_id).where(
                        TorrentLanguageLink.torrent_id == stream_id
                    )
                )
                existing_ids = set(row for row in existing)

                for lang in old_stream.languages:
                    lang_id = await self.resource_tracker.get_resource_id(
                        session, "language", lang
                    )
                    if lang_id and lang_id not in existing_ids:
                        session.add(
                            TorrentLanguageLink(
                                torrent_id=stream_id, language_id=lang_id
                            )
                        )
                        existing_ids.add(lang_id)

            # Migrate announce URLs
            if old_stream.announce_list:
                existing = await session.exec(
                    select(TorrentAnnounceLink.announce_id).where(
                        TorrentAnnounceLink.torrent_id == stream_id
                    )
                )
                existing_ids = set(row for row in existing)

                for url in set(old_stream.announce_list):
                    url_id = await self.resource_tracker.get_resource_id(
                        session, "announce_url", url
                    )
                    if url_id and url_id not in existing_ids:
                        session.add(
                            TorrentAnnounceLink(
                                torrent_id=stream_id, announce_id=url_id
                            )
                        )
                        existing_ids.add(url_id)

            await session.commit()

        except Exception as e:
            logger.exception(
                f"Error migrating relationships for stream {stream_id}: {str(e)}"
            )
            await session.rollback()
            raise

    async def _process_torrent_batch(
        self, session: AsyncSession, batch: List[OldTorrentStreams]
    ):
        """Process a batch of torrent streams with optimized operations"""
        try:
            # Pre-fetch metadata types for the batch
            meta_ids = {stream.meta_id for stream in batch}
            stmt = select(BaseMetadata.id, BaseMetadata.type).where(
                BaseMetadata.id.in_(meta_ids)
            )
            result = await session.exec(stmt)
            meta_types = {id_: type_ for id_, type_ in result}

            for old_stream in batch:
                try:
                    if old_stream.meta_id not in meta_types:
                        continue

                    # Transform and upsert torrent stream
                    stream_data = self._transform_torrent_stream(old_stream)
                    torrent_stream = await self._upsert_torrent_stream(
                        session, stream_data
                    )

                    # Handle episode files for series
                    if (
                        meta_types[old_stream.meta_id] == MediaType.SERIES
                        and old_stream.season
                    ):
                        await self._migrate_episode_files(
                            session, old_stream, torrent_stream.id
                        )

                    # Migrate stream relationships
                    await self._migrate_torrent_relationships(
                        session, old_stream, torrent_stream.id
                    )

                    self.stats.successful += 1

                except Exception as e:
                    logger.exception(
                        f"Error processing torrent stream {old_stream.id}: {str(e)}"
                    )
                    self.stats.add_error(
                        "torrent_processing", f"{old_stream.id}: {str(e)}"
                    )
                    self.stats.failed += 1
                    await session.rollback()

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            raise

    async def _migrate_episode_files(
        self, session: AsyncSession, old_stream: OldTorrentStreams, stream_id: str
    ):
        """Migrate episode files with efficient batch processing and duplicate handling"""
        try:
            # First get all existing episode files for this torrent stream
            existing_episodes = await session.exec(
                select(EpisodeFile).where(EpisodeFile.torrent_stream_id == stream_id)
            )
            existing_map = {
                (ep.season_number, ep.episode_number): ep for ep in existing_episodes
            }

            # Deduplicate episodes from source
            deduplicated_episodes = {}
            for episode in old_stream.season.episodes:
                if episode.filename and not is_video_file(episode.filename):
                    continue

                key = (old_stream.season.season_number, episode.episode_number)
                # If we already have this episode, keep the one with more complete information
                if key in deduplicated_episodes:
                    existing = deduplicated_episodes[key]
                    if self._is_better_episode(episode, existing):
                        deduplicated_episodes[key] = episode
                else:
                    deduplicated_episodes[key] = episode

            episode_files = []
            for key, episode in deduplicated_episodes.items():
                season_num, episode_num = key

                # Check if this episode already exists in the database
                if key in existing_map:
                    # Update existing episode if needed
                    existing_ep = existing_map[key]
                    if (
                        existing_ep.file_index != episode.file_index
                        or existing_ep.filename != episode.filename
                        or existing_ep.size != episode.size
                    ):
                        existing_ep.file_index = episode.file_index
                        existing_ep.filename = episode.filename
                        existing_ep.size = episode.size
                    continue

                # Create new episode file if it doesn't exist
                episode_file = EpisodeFile(
                    torrent_stream_id=stream_id,
                    season_number=season_num,
                    episode_number=episode_num,
                    file_index=episode.file_index,
                    filename=episode.filename,
                    size=episode.size,
                )
                episode_files.append(episode_file)

            # Batch insert new episode files
            if episode_files:
                session.add_all(episode_files)
                await session.commit()

        except Exception as e:
            logger.error(
                f"Error migrating episode files for stream {stream_id}: {str(e)}"
            )
            await session.rollback()
            raise

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

    async def _upsert_tv_stream(
        self, session: AsyncSession, stream_data: dict
    ) -> TVStream:
        """Upsert TV stream with constraint handling"""
        stmt = select(TVStream).where(
            TVStream.url == stream_data["url"], TVStream.ytId == stream_data["ytId"]
        )
        result = await session.exec(stmt)
        stream = result.one_or_none()

        if stream:
            for key, value in stream_data.items():
                if key != "id":  # Don't update ID for existing streams
                    setattr(stream, key, value)
        else:
            stream = TVStream(**stream_data)
            session.add(stream)

        await session.commit()
        await session.refresh(stream)
        return stream

    async def _migrate_tv_stream_namespaces(
        self, session: AsyncSession, old_stream: OldTVStreams, stream_id: int
    ):
        """Migrate TV stream namespaces with efficient querying"""
        try:
            existing = await session.exec(
                select(TVStreamNamespaceLink.namespace_id).where(
                    TVStreamNamespaceLink.stream_id == stream_id
                )
            )
            existing_ids = set(row for row in existing)

            namespaces = old_stream.namespaces or ["mediafusion"]
            for namespace in namespaces:
                namespace_id = await self.resource_tracker.get_resource_id(
                    session, "namespace", namespace
                )
                if namespace_id and namespace_id not in existing_ids:
                    session.add(
                        TVStreamNamespaceLink(
                            stream_id=stream_id, namespace_id=namespace_id
                        )
                    )

            await session.commit()

        except Exception as e:
            logger.exception(
                f"Error migrating namespaces for stream {stream_id}: {str(e)}"
            )
            await session.rollback()
            raise

    async def _process_tv_stream_batch(
        self, session: AsyncSession, batch: List[OldTVStreams]
    ):
        """Process a batch of TV streams with optimized operations"""
        try:
            for old_stream in batch:
                try:
                    # Transform and upsert TV stream
                    stream_data = await self._transform_tv_stream(old_stream)
                    tv_stream = await self._upsert_tv_stream(session, stream_data)

                    # Migrate namespace relationships
                    await self._migrate_tv_stream_namespaces(
                        session, old_stream, tv_stream.id
                    )

                    self.stats.successful += 1

                except Exception as e:
                    logger.exception(f"Error processing TV stream: {str(e)}")
                    self.stats.add_error("tv_stream_processing", str(e))
                    self.stats.failed += 1

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            raise


class MigrationVerifier:
    """Enhanced verification system with detailed checking"""

    def __init__(self, migration: DatabaseMigration):
        self.migration = migration
        self.verification_results = {}

    async def verify_migration(self):
        """Comprehensive migration verification"""
        logger.info("Starting migration verification...")

        try:
            # Verify document counts
            await self._verify_counts()

            # Verify data integrity
            await self._verify_data_integrity()

            # Verify relationships
            await self._verify_relationships()

            # Verify series-specific data
            await self._verify_series_data()

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
            ]

            for old_model, new_model, name in collections:
                mongo_count = await old_model.find().count()
                pg_count = await session.scalar(
                    select(func.count()).select_from(new_model)
                )

                self.verification_results[f"{name}_count"] = {
                    "mongo": mongo_count,
                    "postgres": pg_count,
                    "matched": mongo_count == pg_count,
                }

    def _log_verification_results(self):
        """Log detailed verification results"""
        logger.info("\nVerification Results:")
        logger.info("=" * 50)

        # Document counts
        logger.info("\nDocument Counts:")
        for category, data in self.verification_results.items():
            if category.endswith("_count"):
                status = "✅" if data["matched"] else "❌"
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
                logger.info("\n".join(result["issues"][:5]))
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
        samples = await old_model.aggregate(
            [{"$sample": {"size": sample_size}}]
        ).to_list()

        mismatches = []
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

            # Sample some series
            series_samples = await OldSeriesMetaData.aggregate(
                [{"$sample": {"size": 5}}]
            ).to_list()

            season_issues = []
            episode_issues = []
            episode_file_issues = []

            for old_series in series_samples:
                old_series = OldSeriesMetaData.model_validate(old_series)
                # Get new series record
                stmt = select(SeriesMetadata).where(SeriesMetadata.id == old_series.id)
                new_series = (await session.exec(stmt)).one_or_none()

                if not new_series:
                    continue

                # Get old torrent streams for series
                old_torrents = await OldTorrentStreams.find(
                    {"meta_id": old_series.id}
                ).to_list()

                # Verify seasons and episodes
                for old_torrent in old_torrents:
                    if not old_torrent.season:
                        continue

                    # Check season exists
                    stmt = select(SeriesSeason).where(
                        SeriesSeason.series_id == new_series.id,
                        SeriesSeason.season_number == old_torrent.season.season_number,
                    )
                    season = (await session.exec(stmt)).one_or_none()

                    if not season:
                        season_issues.append(
                            f"Missing season {old_torrent.season.season_number} "
                            f"for series {old_series.id}"
                        )
                        continue

                    # Check episode files
                    for old_episode in old_torrent.season.episodes:
                        if old_episode.filename and not is_video_file(
                            old_episode.filename
                        ):
                            continue

                        stmt = select(EpisodeFile).where(
                            EpisodeFile.torrent_stream_id == old_torrent.id,
                            EpisodeFile.season_number
                            == old_torrent.season.season_number,
                            EpisodeFile.episode_number == old_episode.episode_number,
                        )
                        episode_file = (await session.exec(stmt)).one_or_none()

                        if not episode_file:
                            episode_file_issues.append(
                                f"Missing episode file for S{season.season_number}"
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

        if (
            getattr(old_movie, "parent_guide_nudity_status", None)
            != new_movie.parent_guide_nudity_status
        ):
            mismatches.append(f"Nudity status mismatch for {old_movie.id}")

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


@app.command()
def migrate(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    batch_size: int = typer.Option(1000, help="Batch size for processing documents"),
    skip_verification: bool = typer.Option(
        False, help="Skip verification after migration"
    ),
    only_metadata: bool = typer.Option(False, help="Migrate only metadata"),
    only_streams: bool = typer.Option(False, help="Migrate only streams"),
):
    """Enhanced migration command with flexible options"""

    async def run_migration():
        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size)
        try:
            # Initialize connections and resources
            await migration.init_connections()
            async with migration.get_session() as session:
                await migration.resource_tracker.initialize_from_db(session)

            # Initialize migrators
            metadata_migrator = MetadataMigrator(migration)
            stream_migrator = StreamMigrator(migration)
            series_migrator = SeriesDataMigrator(migration)
            verifier = MigrationVerifier(migration)

            # Execute migration based on options
            if not only_streams:
                logger.info("Migrating metadata...")
                await metadata_migrator.migrate_metadata()

            if not only_metadata:
                logger.info("Migrating torrent streams...")
                await stream_migrator.migrate_torrent_streams()

                logger.info("Migrating TV streams...")
                await stream_migrator.migrate_tv_streams()

                logger.info("Migrating series seasons and episodes...")
                await series_migrator.migrate_series_metadata()

                # Fix null episode_ids
                async with migration.get_session() as session:
                    await series_migrator.fix_null_episode_ids(session)

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
def verify(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
):
    """Verify migration data integrity and relationships"""

    async def run_verification():
        migration = DatabaseMigration(mongo_uri, postgres_uri)
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

    typer.echo("Starting verification...")
    asyncio.run(run_verification())


if __name__ == "__main__":
    app()
