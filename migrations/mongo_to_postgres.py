import asyncio
import logging
from datetime import timezone
from typing import Dict, List

import sqlalchemy
import typer
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import make_url, func, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm import tqdm

from db.models import (
    MediaFusionMetaData as OldMetaData,
    MediaFusionMovieMetaData as OldMovieMetaData,
    MediaFusionSeriesMetaData as OldSeriesMetaData,
    MediaFusionTVMetaData as OldTVMetaData,
    TorrentStreams as OldTorrentStreams,
    TVStreams as OldTVStreams,
)
from db.new_models import *  # Import all new models
from utils.validation_helper import is_video_file

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = typer.Typer()


class ResourceTracker:
    """Track and manage resources across the migration"""

    def __init__(self):
        self._resource_maps: Dict[str, Dict[str, int]] = {
            "genre": {},
            "catalog": {},
            "language": {},
            "announce_url": {},
            "namespace": {},
            "star": {},
        }
        self._pending_inserts: Dict[str, set] = {
            key: set() for key in self._resource_maps
        }

    async def initialize_from_db(self, session: AsyncSession):
        """Load existing resource IDs from PostgreSQL"""
        resource_models = {
            "genre": Genre,
            "catalog": Catalog,
            "language": Language,
            "announce_url": AnnounceURL,
            "namespace": Namespace,
            "star": Star,
        }

        for resource_type, model in resource_models.items():
            result = await session.exec(select(model))
            existing_resources = result.all()
            for resource in existing_resources:
                self._resource_maps[resource_type][resource.name] = resource.id

    async def ensure_resources(self, session: AsyncSession):
        """Ensure all pending resources are created in the database"""
        resource_models = {
            "genre": Genre,
            "catalog": Catalog,
            "language": Language,
            "announce_url": AnnounceURL,
            "namespace": Namespace,
            "star": Star,
        }

        for resource_type, model in resource_models.items():
            pending = self._pending_inserts[resource_type]
            if not pending:
                continue

            # Get existing resources
            stmt = select(model).where(model.name.in_(pending))
            result = await session.exec(stmt)
            existing = {r.name: r.id for r in result}

            # Create new resources
            new_resources = pending - existing.keys()
            if new_resources:
                for name in new_resources:
                    new_resource = model(name=name)
                    session.add(new_resource)

                await session.commit()

                # Get IDs of newly created resources
                stmt = select(model).where(model.name.in_(new_resources))
                result = await session.exec(stmt)
                new_ids = {r.name: r.id for r in result}

                existing.update(new_ids)

            # Update resource map
            self._resource_maps[resource_type].update(existing)
            self._pending_inserts[resource_type].clear()

    def track_resource(self, resource_type: str, name: str):
        """Track a resource for creation"""
        if name and name not in self._resource_maps[resource_type]:
            self._pending_inserts[resource_type].add(name)

    def get_resource_id(self, resource_type: str, name: str) -> int | None:
        """Get ID of a tracked resource"""
        return self._resource_maps[resource_type].get(name)


class VerificationResult:
    """Stores verification results"""

    def __init__(self):
        self.counts: Dict[str, tuple[int, int]] = {}  # (mongo_count, pg_count)
        self.sample_checks: Dict[str, List[str]] = {}  # List of failed checks
        self.relationship_checks: Dict[str, List[str]] = (
            {}
        )  # List of failed relationships


class DatabaseMigration:
    def __init__(
        self,
        mongo_uri: str,
        postgres_uri: str,
        batch_size: int = 1000,
    ):

        self.pg_engine = None
        self.mongo_client = None
        self.mongo_uri = mongo_uri
        self.postgres_uri = postgres_uri
        self.batch_size = batch_size
        self.resource_tracker = ResourceTracker()
        self.verification_result = VerificationResult()

    async def init_connections(self, connect_mongo: bool = True):
        """Initialize database connections"""
        # Initialize MongoDB
        if connect_mongo:
            self.mongo_client = AsyncIOMotorClient(self.mongo_uri)
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

        # Create database if not exists

        postgres_url = make_url(self.postgres_uri)
        database_name = postgres_url.database
        # PostgreSQL connection for creating database
        temp_engine = create_async_engine(
            postgres_url.set(database="postgres"), echo=False
        )

        async with temp_engine.connect() as conn:
            # Close any open transactions
            await conn.execute(sqlalchemy.text("COMMIT"))

            result = await conn.execute(
                sqlalchemy.text(
                    f"SELECT 1 FROM pg_database WHERE datname='{database_name}'"
                )
            )

            if not result.scalar():
                await conn.execute(sqlalchemy.text(f"CREATE DATABASE {database_name}"))
                logger.info(f"Database '{database_name}' created.")

        await temp_engine.dispose()

        # Initialize PostgreSQL
        self.pg_engine = create_async_engine(
            self.postgres_uri, echo=False, pool_size=20, max_overflow=30
        )

        # Create tables if not exists
        async with self.pg_engine.begin() as conn:
            # Create extensions first
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gin;"))

            await conn.run_sync(SQLModel.metadata.create_all)

    async def initialize_resources(self):
        # Initialize resource tracker
        async with AsyncSession(self.pg_engine) as session:
            await self.resource_tracker.initialize_from_db(session)

    async def reset_database(self):
        """Reset PostgreSQL database"""
        async with self.pg_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)
            await conn.run_sync(SQLModel.metadata.create_all)

    async def close_connections(self):
        """Close database connections"""
        try:
            if self.mongo_client:
                self.mongo_client.close()
        except Exception as e:
            logger.error(f"Error closing MongoDB connection: {str(e)}")

        try:
            if self.pg_engine:
                await self.pg_engine.dispose()
        except Exception as e:
            logger.error(f"Error closing PostgreSQL connection: {str(e)}")

    async def migrate_metadata(self):
        """Migrate metadata to separate tables using cursor-based pagination"""
        collections = [
            (OldMovieMetaData, MovieMetadata, "movies"),
            (OldSeriesMetaData, SeriesMetadata, "series"),
            (OldTVMetaData, TVMetadata, "tv"),
        ]

        for old_model, new_model_class, collection_name in collections:
            total = await old_model.find().count()
            if total == 0:
                logger.info(f"No {collection_name} to migrate")
                continue

            processed = 0
            cursor = old_model.find()

            with tqdm(total=total) as pbar:
                pbar.set_description(f"Migrating {collection_name}")

                async for old_doc in cursor:
                    try:
                        async with AsyncSession(self.pg_engine) as session:
                            # Handle base metadata first
                            base_data = await self.transform_base_metadata(
                                old_doc, new_model_class.type
                            )
                            stmt = select(BaseMetadata).where(
                                BaseMetadata.id == old_doc.id
                            )
                            result = await session.exec(stmt)
                            base_meta = result.first()

                            if base_meta:
                                for key, value in base_data.items():
                                    setattr(base_meta, key, value)
                            else:
                                base_meta = BaseMetadata(**base_data)
                                session.add(base_meta)

                            await session.commit()

                            # Handle type-specific metadata
                            specific_data = await self.transform_specific_metadata(
                                old_doc, new_model_class.type
                            )
                            stmt = select(new_model_class).where(
                                new_model_class.id == old_doc.id
                            )
                            result = await session.exec(stmt)
                            specific_meta = result.first()

                            if specific_meta:
                                for key, value in specific_data.items():
                                    if key != "id" and specific_meta.__fields__.get(
                                        key
                                    ):
                                        setattr(specific_meta, key, value)
                            else:
                                specific_meta = new_model_class(**specific_data)
                                session.add(specific_meta)

                            await session.commit()

                            # Handle relationships
                            await self.migrate_metadata_relationships(
                                session, old_doc, old_doc.id, new_model_class.type
                            )

                            processed += 1
                            pbar.update(1)

                    except Exception as e:
                        logger.exception(
                            f"Error processing document {old_doc.id}: {str(e)}"
                        )
                        await session.rollback()
                        continue

    @staticmethod
    async def transform_base_metadata(
        old_doc: OldMetaData, media_type: MediaType
    ) -> dict:
        """Transform metadata to base table format"""
        # Ensure timezone-aware datetimes
        created_at = (
            old_doc.created_at.replace(tzinfo=timezone.utc)
            if old_doc.created_at
            else None
        )
        updated_at = (
            old_doc.last_updated_at.replace(tzinfo=timezone.utc)
            if old_doc.last_updated_at
            else None
        )

        return {
            "id": old_doc.id,
            "title": old_doc.title,
            "year": old_doc.year,
            "poster": old_doc.poster,
            "is_poster_working": old_doc.is_poster_working,
            "is_add_title_to_poster": old_doc.is_add_title_to_poster,
            "background": old_doc.background,
            "description": old_doc.description,
            "runtime": old_doc.runtime,
            "website": old_doc.website,
            "type": media_type,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    async def transform_specific_metadata(
        old_doc: OldMetaData, media_type: MediaType
    ) -> dict:
        """Transform metadata to specific table format"""
        # Ensure timezone-aware datetimes
        created_at = (
            old_doc.created_at.replace(tzinfo=timezone.utc)
            if old_doc.created_at
            else None
        )
        updated_at = (
            old_doc.last_updated_at.replace(tzinfo=timezone.utc)
            if old_doc.last_updated_at
            else None
        )

        data = {
            "id": old_doc.id,
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
        }

        # Add type-specific fields
        if media_type == MediaType.MOVIE:
            data.update(
                {
                    "imdb_rating": getattr(old_doc, "imdb_rating", None),
                    "parent_guide_nudity_status": getattr(
                        old_doc, "parent_guide_nudity_status"
                    ),
                }
            )
        elif media_type == MediaType.SERIES:
            data.update(
                {
                    "end_year": getattr(old_doc, "end_year", None),
                    "imdb_rating": getattr(old_doc, "imdb_rating", None),
                    "parent_guide_nudity_status": getattr(
                        old_doc, "parent_guide_nudity_status"
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

    async def migrate_metadata_relationships(
        self,
        session: AsyncSession,
        old_doc: OldMetaData,
        media_id: str,
        media_type: MediaType,
    ):
        """Migrate all relationships for a metadata record"""
        try:
            # Migrate genres
            existing_genres_result = await session.exec(
                select(MediaGenreLink.genre_id).where(
                    MediaGenreLink.media_id == media_id
                )
            )
            existing_genre_ids = set(existing_genres_result.all())

            for genre in old_doc.genres or []:
                genre_id = self.resource_tracker.get_resource_id("genre", genre)
                if genre_id and genre_id not in existing_genre_ids:
                    link = MediaGenreLink(media_id=media_id, genre_id=genre_id)
                    session.add(link)

            # Migrate AKA titles
            stmt = select(AkaTitle.title).where(AkaTitle.media_id == media_id)
            aka_title = await session.exec(stmt)
            existing_aka_titles = set(aka_title.all())

            for title in getattr(old_doc, "aka_titles", None) or []:
                if title not in existing_aka_titles:
                    aka = AkaTitle(title=title, media_id=media_id)
                    session.add(aka)

            # Migrate stars
            if hasattr(old_doc, "stars"):
                existing_stars_result = await session.exec(
                    select(MediaStarLink.star_id).where(
                        MediaStarLink.media_id == media_id,
                    )
                )
                existing_star_ids = set(existing_stars_result.all())

                for star_name in old_doc.stars or []:
                    star_id = self.resource_tracker.get_resource_id("star", star_name)
                    if star_id and star_id not in existing_star_ids:
                        link = MediaStarLink(
                            media_id=media_id,
                            star_id=star_id,
                        )
                        session.add(link)

            # Migrate certificates
            if hasattr(old_doc, "parent_guide_certificates"):
                stmt = select(ParentalCertificate).where(
                    ParentalCertificate.name.in_(
                        old_doc.parent_guide_certificates or []
                    )
                )
                existing_certs = await session.exec(stmt)
                existing_certificates = {cert.name: cert.id for cert in existing_certs}

                for cert in old_doc.parent_guide_certificates or []:
                    if cert not in existing_certificates:
                        new_cert = ParentalCertificate(name=cert)
                        session.add(new_cert)
                        await session.commit()
                        await session.refresh(new_cert)
                        existing_certificates[cert] = new_cert.id

                    stmt = select(MediaParentalCertificateLink).where(
                        MediaParentalCertificateLink.media_id == media_id,
                        MediaParentalCertificateLink.certificate_id
                        == existing_certificates[cert],
                    )
                    existing_link = await session.exec(stmt)
                    if not existing_link.first():
                        link = MediaParentalCertificateLink(
                            media_id=media_id,
                            certificate_id=existing_certificates[cert],
                        )
                        session.add(link)

            # Migrate catalogs from torrent streams
            existing_catalogs_result = await session.exec(
                select(MediaCatalogLink.catalog_id).where(
                    MediaCatalogLink.media_id == media_id
                )
            )
            existing_catalog_ids = set(existing_catalogs_result.all())

            torrent_streams = await OldTorrentStreams.find(
                {"meta_id": media_id}
            ).to_list()

            for stream in torrent_streams:
                catalogs = (
                    [stream.catalog]
                    if isinstance(stream.catalog, str)
                    else (stream.catalog or [])
                )
                for catalog in catalogs:
                    catalog_id = self.resource_tracker.get_resource_id(
                        "catalog", catalog
                    )
                    if catalog_id and catalog_id not in existing_catalog_ids:
                        link = MediaCatalogLink(
                            media_id=media_id, catalog_id=catalog_id
                        )
                        session.add(link)
                        existing_catalog_ids.add(catalog_id)

            await session.commit()

        except Exception as e:
            logger.error(f"Error migrating relationships for {media_id}: {str(e)}")
            await session.rollback()
            raise

    async def migrate_torrent_streams(self):
        """Migrate torrent streams using cursor-based pagination"""
        total = await OldTorrentStreams.find().count()
        if total == 0:
            logger.info("No torrent streams to migrate")
            return

        processed = 0
        cursor = OldTorrentStreams.find()

        with tqdm(total=total) as pbar:
            pbar.set_description("Migrating torrent streams")

            async for old_stream in cursor:
                try:
                    async with AsyncSession(self.pg_engine) as session:
                        # Track all resources first
                        for lang in old_stream.languages or []:
                            self.resource_tracker.track_resource("language", lang)
                        for url in old_stream.announce_list or []:
                            self.resource_tracker.track_resource("announce_url", url)

                        # Ensure all resources exist
                        await self.resource_tracker.ensure_resources(session)

                        # Validate metadata exists
                        result = await session.exec(
                            select(BaseMetadata).where(
                                BaseMetadata.id == old_stream.meta_id
                            )
                        )
                        if not result.first():
                            logger.warning(
                                f"Skipping stream {old_stream.id} as metadata {old_stream.meta_id} does not exist"
                            )
                            continue

                        # Check if stream exists
                        stmt = select(TorrentStream).where(
                            TorrentStream.id == old_stream.id.lower()
                        )
                        result = await session.exec(stmt)
                        existing_stream = result.first()

                        # Transform stream data
                        stream_data = self.transform_torrent_stream(old_stream)

                        if existing_stream:
                            for key, value in stream_data.items():
                                setattr(existing_stream, key, value)
                            stream = existing_stream
                        else:
                            stream = TorrentStream(**stream_data)
                            session.add(stream)

                        stream_id = stream.id
                        await session.commit()

                        # Handle season and episodes if present
                        if old_stream.season:
                            # Delete existing season and episodes
                            existing_seasons = await session.exec(
                                select(Season).where(
                                    Season.torrent_stream_id == stream_id
                                )
                            )
                            for existing_season in existing_seasons:
                                await session.delete(existing_season)
                            await session.commit()

                            # Create new season and episodes
                            await self.migrate_season_and_episodes(
                                session, old_stream.season, stream_id
                            )

                        # Update relationships
                        await self.migrate_torrent_relationships(
                            session, old_stream, stream_id
                        )

                        processed += 1
                        pbar.update(1)

                except Exception as e:
                    logger.exception(
                        f"Error processing torrent stream {old_stream.id}: {str(e)}"
                    )
                    await session.rollback()
                    continue

    async def migrate_torrent_relationships(
        self, session: AsyncSession, old_stream: OldTorrentStreams, stream_id: str
    ):
        """Migrate torrent stream relationships"""
        try:
            # Migrate languages
            existing_languages = await session.exec(
                select(TorrentLanguageLink.language_id).where(
                    TorrentLanguageLink.torrent_id == stream_id
                )
            )
            existing_language_ids = set(existing_languages.all())
            added_languages = set()
            for lang in old_stream.languages or []:
                lang_id = self.resource_tracker.get_resource_id("language", lang)
                if (
                    lang_id
                    and lang_id not in existing_language_ids
                    and lang_id not in added_languages
                ):
                    link = TorrentLanguageLink(
                        torrent_id=stream_id, language_id=lang_id
                    )
                    session.add(link)
                    added_languages.add(lang_id)

            # Migrate announce URLs
            existing_announces = await session.exec(
                select(TorrentAnnounceLink.announce_id).where(
                    TorrentAnnounceLink.torrent_id == stream_id
                )
            )
            existing_announce_ids = set(existing_announces.all())
            added_announces = set()
            for url in set(old_stream.announce_list):
                url_id = self.resource_tracker.get_resource_id("announce_url", url)
                if (
                    url_id
                    and url_id not in existing_announce_ids
                    and url_id not in added_announces
                ):
                    link = TorrentAnnounceLink(torrent_id=stream_id, announce_id=url_id)
                    session.add(link)
                    added_announces.add(url_id)

            await session.commit()

        except Exception as e:
            logger.exception(
                f"Error migrating relationships for stream {stream_id}: {str(e)}"
            )
            await session.rollback()
            raise

    async def migrate_season_and_episodes(
        self, session: AsyncSession, old_season, stream_id: str
    ):
        """Migrate season and its episodes"""
        try:
            # Create season
            season = Season(
                torrent_stream_id=stream_id, season_number=old_season.season_number
            )
            session.add(season)
            await session.commit()  # Commit to get season ID
            await session.refresh(season, ["id"])

            added_episodes = set()
            # Create episodes
            for old_ep in old_season.episodes or []:
                if old_ep.filename and not is_video_file(old_ep.filename):
                    logger.warning(
                        f"Skipping non-video file {old_ep.filename} for episode in {stream_id}"
                    )
                    continue

                if old_ep.episode_number in added_episodes:
                    logger.warning(
                        f"Skipping duplicate episode {old_ep.episode_number} for torrent {stream_id}"
                    )
                    continue
                episode = Episode(
                    season_id=season.id,
                    episode_number=old_ep.episode_number,
                    filename=old_ep.filename,
                    size=old_ep.size,
                    file_index=old_ep.file_index,
                    title=old_ep.title,
                    released=(
                        old_ep.released.replace(tzinfo=timezone.utc)
                        if old_ep.released
                        else None
                    ),
                )
                session.add(episode)
                added_episodes.add(old_ep.episode_number)

            await session.commit()
        except Exception as e:
            logger.error(
                f"Error migrating season and episodes for stream {stream_id}: {str(e)}"
            )
            await session.rollback()
            raise

    async def migrate_tv_streams(self):
        """Migrate TV streams using cursor-based pagination"""
        total = await OldTVStreams.find().count()
        if total == 0:
            logger.info("No TV streams to migrate")
            return

        processed = 0
        cursor = OldTVStreams.find()

        with tqdm(total=total) as pbar:
            pbar.set_description("Migrating TV streams")

            async for old_stream in cursor:
                try:
                    async with AsyncSession(self.pg_engine) as session:
                        # Track namespaces
                        for namespace in old_stream.namespaces or ["mediafusion"]:
                            self.resource_tracker.track_resource("namespace", namespace)

                        await self.resource_tracker.ensure_resources(session)

                        # Validate metadata exists
                        result = await session.exec(
                            select(BaseMetadata).where(
                                BaseMetadata.id == old_stream.meta_id
                            )
                        )
                        if not result.first():
                            continue

                        # Transform and insert TV stream
                        stream_data = await self.transform_tv_stream(old_stream)

                        # Check for existing stream with the same URL
                        existing_stream = await session.exec(
                            select(TVStream).where(
                                TVStream.url == stream_data["url"],
                                TVStream.ytId == stream_data["ytId"],
                            )
                        )
                        existing_stream = existing_stream.first()

                        if not existing_stream:
                            stream = TVStream(**stream_data)
                            session.add(stream)
                            await session.commit()
                            await session.refresh(stream, ["id"])
                        else:
                            stream = existing_stream

                        # Add namespace relationships
                        await self.migrate_tv_stream_namespaces(
                            session, old_stream, stream.id
                        )
                        await session.commit()

                        processed += 1
                        pbar.update(1)

                except Exception as e:
                    logger.exception(f"Error processing TV stream: {str(e)}")
                    await session.rollback()
                    continue

    async def migrate_tv_stream_namespaces(
        self, session: AsyncSession, old_stream: OldTVStreams, stream_id: int
    ):
        """Migrate TV stream namespace relationships"""
        # validate existing namespaces
        stmt = select(TVStreamNamespaceLink.namespace_id).where(
            TVStreamNamespaceLink.stream_id == stream_id
        )
        existing_namespaces = await session.exec(stmt)
        existing_namespace_ids = set(existing_namespaces.all())

        for namespace in old_stream.namespaces or ["mediafusion"]:
            namespace_id = self.resource_tracker.get_resource_id("namespace", namespace)
            if namespace_id and namespace_id not in existing_namespace_ids:
                link = TVStreamNamespaceLink(
                    stream_id=stream_id, namespace_id=namespace_id
                )
                session.add(link)

        await session.commit()

    @staticmethod
    def transform_torrent_stream(old_stream: OldTorrentStreams) -> dict:
        """Transform torrent stream to new format"""
        return {
            "id": old_stream.id.lower(),
            "meta_id": old_stream.meta_id,
            "torrent_name": old_stream.torrent_name,
            "size": old_stream.size,
            "filename": old_stream.filename,
            "file_index": old_stream.file_index,
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
            "created_at": old_stream.created_at,
            "updated_at": old_stream.updated_at,
            "indexer_flag": (
                old_stream.indexer_flags[0] if old_stream.indexer_flags else "freeleech"
            ),
        }

    async def transform_tv_stream(self, old_stream: OldTVStreams) -> dict:
        """Transform TV stream to new format"""
        # Get next ID if not exists
        if not hasattr(old_stream, "id") or not old_stream.id:
            async with AsyncSession(self.pg_engine) as session:
                result = await session.exec(
                    select(func.coalesce(func.max(TVStream.id), 0))
                )
                max_id = result.one() or 0
                stream_id = max_id + 1
        else:
            try:
                stream_id = int(old_stream.id)
            except (ValueError, TypeError):
                async with AsyncSession(self.pg_engine) as session:
                    result = await session.exec(
                        select(func.coalesce(func.max(TVStream.id), 0))
                    )
                    max_id = result.one() or 0
                    stream_id = max_id + 1

        return {
            "id": stream_id,
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
            "created_at": old_stream.created_at,
            "updated_at": old_stream.updated_at,
        }

    async def verify_migration(self) -> VerificationResult:
        """Verify the migration by comparing document counts and sampling data"""
        logger.info("Starting migration verification...")

        # Document count verification
        await self.verify_counts()

        # Data sampling verification
        await self.verify_samples()

        # Relationship verification
        await self.verify_relationships()

        # Log verification results
        self.log_verification_results()

        return self.verification_result

    async def verify_counts(self):
        """Verify document counts between MongoDB and PostgreSQL"""
        logger.info("Verifying document counts...")

        async with AsyncSession(self.pg_engine) as session:
            # Metadata counts
            mongo_movie_count = await OldMovieMetaData.count()
            mongo_series_count = await OldSeriesMetaData.count()
            mongo_tv_count = await OldTVMetaData.count()

            pg_movie_count = (
                await session.exec(select(func.count()).select_from(MovieMetadata))
            ).first()
            pg_series_count = (
                await session.exec(select(func.count()).select_from(SeriesMetadata))
            ).first()
            pg_tv_count = (
                await session.exec(select(func.count()).select_from(TVMetadata))
            ).first()

            # Stream counts
            mongo_torrent_count = await OldTorrentStreams.count()
            mongo_tv_streams_count = await OldTVStreams.count()

            pg_torrent_count = (
                await session.exec(select(func.count()).select_from(TorrentStream))
            ).first()
            pg_tv_streams_count = (
                await session.exec(select(func.count()).select_from(TVStream))
            ).first()

            self.verification_result.counts.update(
                {
                    "movies": (mongo_movie_count, pg_movie_count),
                    "series": (mongo_series_count, pg_series_count),
                    "tv": (mongo_tv_count, pg_tv_count),
                    "torrent_streams": (mongo_torrent_count, pg_torrent_count),
                    "tv_streams": (mongo_tv_streams_count, pg_tv_streams_count),
                }
            )

    async def verify_samples(self, sample_size: int = 10):
        """Verify data integrity by sampling records"""
        logger.info("Verifying data samples...")

        collections = [
            (OldMovieMetaData, MovieMetadata, "movies"),
            (OldSeriesMetaData, SeriesMetadata, "series"),
            (OldTVMetaData, TVMetadata, "tv"),
        ]

        for old_model, new_model, collection_name in collections:
            failed_checks = []
            samples = await old_model.aggregate(
                [{"$sample": {"size": sample_size}}], projection_model=old_model
            ).to_list()

            async with AsyncSession(self.pg_engine) as session:
                for sample in samples:
                    # Check base metadata
                    base_result = await session.exec(
                        select(BaseMetadata).where(BaseMetadata.id == sample.id)
                    )
                    base_meta = base_result.first()

                    # Check specific metadata
                    specific_result = await session.exec(
                        select(new_model).where(new_model.id == sample.id)
                    )
                    specific_meta = specific_result.first()

                    if not all([base_meta, specific_meta]):
                        failed_checks.append(f"Missing metadata for {sample.id}")
                        continue

                    # Compare fields
                    if base_meta.title != sample.title or base_meta.year != sample.year:
                        failed_checks.append(f"Mismatch in base fields for {sample.id}")

                    # Compare type-specific fields
                    if (
                        hasattr(sample, "imdb_rating")
                        and specific_meta.imdb_rating != sample.imdb_rating
                    ):
                        failed_checks.append(f"Mismatch in imdb_rating for {sample.id}")

            self.verification_result.sample_checks[collection_name] = failed_checks

    async def verify_relationships(self):
        """Verify relationship integrity"""
        logger.info("Verifying relationships...")

        async with AsyncSession(self.pg_engine) as session:
            # Verify genre relationships
            genre_issues = []
            genre_links = await session.exec(select(MediaGenreLink))
            for link in genre_links:
                meta = (
                    await session.exec(
                        select(BaseMetadata).where(BaseMetadata.id == link.media_id)
                    )
                ).first()
                genre = (
                    await session.exec(select(Genre).where(Genre.id == link.genre_id))
                ).first()
                if not all([meta, genre]):
                    genre_issues.append(
                        f"Invalid genre link: {link.media_id}-{link.genre_id}"
                    )

            # Verify torrent stream relationships
            stream_issues = []
            torrent_streams = await session.exec(select(TorrentStream))
            for stream in torrent_streams:
                meta = (
                    await session.exec(
                        select(BaseMetadata).where(BaseMetadata.id == stream.meta_id)
                    )
                ).first()
                if not meta:
                    stream_issues.append(f"Invalid metadata reference: {stream.id}")

            self.verification_result.relationship_checks.update(
                {"genres": genre_issues, "streams": stream_issues}
            )

    def log_verification_results(self):
        """Log verification results"""
        logger.info("\nVerification Results:")
        logger.info("=" * 50)

        # Log count comparisons
        logger.info("\nDocument Counts:")
        for category, (
            mongo_count,
            pg_count,
        ) in self.verification_result.counts.items():
            status = "✅" if mongo_count == pg_count else "❌"
            logger.info(
                f"{status} {category}: MongoDB={mongo_count}, PostgreSQL={pg_count}"
            )

        # Log sample check results
        logger.info("\nSample Checks:")
        for category, issues in self.verification_result.sample_checks.items():
            status = "✅" if not issues else "❌"
            logger.info(f"{status} {category}: {len(issues)} issues")
            for issue in issues:
                logger.info(f"  - {issue}")

        # Log relationship check results
        logger.info("\nRelationship Checks:")
        for category, issues in self.verification_result.relationship_checks.items():
            status = "✅" if not issues else "❌"
            logger.info(f"{status} {category}: {len(issues)} issues")
            for issue in issues:
                logger.info(f"  - {issue}")


@app.command()
def migrate(
    mongo_uri: str = typer.Option(..., help="MongoDB connection URI"),
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
    batch_size: int = typer.Option(1000, help="Batch size for processing documents"),
    skip_verification: bool = typer.Option(
        False, help="Skip verification after migration"
    ),
):
    """
    Migrate data from MongoDB to PostgreSQL
    """

    async def run_migration():
        migration = DatabaseMigration(mongo_uri, postgres_uri, batch_size)
        try:
            await migration.init_connections()
            await migration.initialize_resources()

            # Migrate data
            await migration.migrate_metadata()
            await migration.migrate_torrent_streams()
            await migration.migrate_tv_streams()

            # Verify migration
            if not skip_verification:
                verification_result = await migration.verify_migration()

                # Check for critical issues
                if any(
                    len(issues) > 0
                    for issues in verification_result.relationship_checks.values()
                ):
                    logger.error("Critical issues found during verification!")
                    raise typer.Exit(code=1)

            logger.info("Migration completed successfully!")
        except Exception as e:
            logger.error(f"Migration failed: {str(e)}")
            logger.exception("Detailed error:")
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
    """
    Verify migration between MongoDB and PostgreSQL
    """

    async def run_verification():
        migration = DatabaseMigration(mongo_uri, postgres_uri)
        try:
            await migration.init_connections()
            await migration.verify_migration()
        finally:
            await migration.close_connections()

    typer.echo("Starting verification...")
    asyncio.run(run_verification())


@app.command()
def reset(
    postgres_uri: str = typer.Option(..., help="PostgreSQL connection URI"),
):
    """
    Reset PostgreSQL database
    """

    async def run_reset():
        migration = DatabaseMigration("", postgres_uri)
        try:
            await migration.init_connections(connect_mongo=False)
            await migration.reset_database()
        finally:
            await migration.close_connections()

    typer.echo("Resetting database...")
    asyncio.run(run_reset())


if __name__ == "__main__":
    app()
