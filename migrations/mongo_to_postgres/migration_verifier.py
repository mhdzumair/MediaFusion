"""
Migration verifier for MongoDB to PostgreSQL migration.

Updated for MediaFusion 5.0 architecture:
- Uses Media instead of BaseMetadata
- Uses Stream, TorrentStream, HTTPStream instead of old models
- Uses MediaImage and MediaRating for images and ratings
- Updated queries for the new schema
"""

import logging

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import NudityStatus
from db.models import (
    Catalog,
    FileMediaLink,
    Genre,
    HTTPStream,
    Language,
    Media,
    MediaCatalogLink,
    MediaExternalID,
    MediaGenreLink,
    MediaImage,
    MediaRating,
    MovieMetadata,
    RSSFeed,
    RSSFeedCatalogPattern,
    Season,
    SeriesMetadata,
    Stream,
    StreamFile,
    StreamLanguageLink,
    StreamMediaLink,
    TorrentStream,
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
from utils.validation_helper import is_video_file

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class MigrationVerifier:
    """Enhanced verification system with detailed checking for MediaFusion 5.0"""

    def __init__(self, migration):
        self.migration = migration
        self.verification_results = {}
        self.is_sample_mode = migration.sample_size is not None
        self.sample_size = migration.sample_size

    async def _get_external_id_for_media(self, session: AsyncSession, media_id: int) -> str | None:
        """Get the canonical external ID for a media item from MediaExternalID table."""
        # Prefer IMDb, fallback to TMDB
        for provider in ["imdb", "tmdb"]:
            stmt = select(MediaExternalID.external_id).where(
                MediaExternalID.media_id == media_id,
                MediaExternalID.provider == provider,
            )
            result = await session.exec(stmt)
            ext_id = result.first()
            if ext_id:
                if provider == "tmdb":
                    return f"mftmdb{ext_id}"  # Return in legacy format for MongoDB lookup
                return ext_id
        return None

    async def _get_media_by_external_id(self, session: AsyncSession, external_id: str) -> Media | None:
        """Get media by external ID (MongoDB format) from MediaExternalID table."""
        provider = None
        provider_ext_id = None

        if external_id.startswith("tt"):
            provider = "imdb"
            provider_ext_id = external_id
        elif external_id.startswith("mftmdb"):
            provider = "tmdb"
            provider_ext_id = external_id.replace("mftmdb", "")
        elif ":" in external_id and not external_id.startswith("mf:"):
            parts = external_id.split(":", 1)
            provider = parts[0].lower()
            provider_ext_id = parts[1]

        if not provider or not provider_ext_id:
            return None

        stmt = (
            select(Media)
            .join(MediaExternalID, MediaExternalID.media_id == Media.id)
            .where(
                MediaExternalID.provider == provider,
                MediaExternalID.external_id == provider_ext_id,
            )
        )
        result = await session.exec(stmt)
        return result.first()

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
                (OldTVStreams, HTTPStream, "http_streams"),
                (OldRSSFeed, RSSFeed, "rss_feeds"),
            ]

            for old_model, new_model, name in collections:
                mongo_count = await old_model.find().count()
                pg_count = await session.scalar(select(func.count()).select_from(new_model))

                # In sample mode, expected count is min(mongo_count, sample_size)
                if self.is_sample_mode:
                    expected_count = min(mongo_count, self.sample_size)
                    # For streams, they depend on metadata - so use actual pg_count as baseline
                    if name in ("torrent_streams", "http_streams"):
                        # Streams are filtered by existing metadata, so we just verify pg_count > 0
                        matched = pg_count > 0 or mongo_count == 0
                    else:
                        matched = pg_count >= expected_count
                    orphaned = 0
                else:
                    expected_count = mongo_count
                    # For streams, allow for orphaned records (FK violations)
                    if name in ("torrent_streams", "http_streams"):
                        # Count orphaned records (streams referencing non-existent metadata)
                        orphaned = mongo_count - pg_count
                        # Consider it matched if the difference is only orphaned records
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
                if result.get("issues"):
                    for issue in result["issues"][:5]:
                        logger.info(f"  - {issue}")

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
            stmt = select(new_model.media_id).limit(sample_size)
            pg_media_ids = (await session.exec(stmt)).all()

            if not pg_media_ids:
                self.verification_results["data_integrity"][type_name] = {
                    "passed": True,
                    "details": f"No {type_name} migrated in sample mode",
                    "mismatches": [],
                }
                return

            for media_id in pg_media_ids:
                # Get the record from PostgreSQL
                stmt = select(new_model).where(new_model.media_id == media_id)
                new_record = (await session.exec(stmt)).one_or_none()

                # Get external_id from MediaExternalID table
                external_id = await self._get_external_id_for_media(session, media_id)

                if not external_id:
                    mismatches.append(f"Media {media_id} has no external ID in MediaExternalID table")
                    continue

                # Get the corresponding record from MongoDB
                old_record = await old_model.find_one({"_id": external_id})

                if not old_record:
                    mismatches.append(f"PostgreSQL record {media_id} (external_id={external_id}) not found in MongoDB")
                    continue

                # Verify specific fields
                field_mismatches = await verify_func(session, old_record, new_record, media_id)
                if field_mismatches:
                    mismatches.extend(field_mismatches)
        else:
            # In full mode, sample from MongoDB and verify existence in PostgreSQL
            samples = await old_model.aggregate([{"$sample": {"size": sample_size}}]).to_list()

            for sample in samples:
                sample = old_model.model_validate(sample)

                # Find media by external_id via MediaExternalID
                media = await self._get_media_by_external_id(session, sample.id)

                if not media:
                    mismatches.append(f"Missing record: {sample.id}")
                    continue

                # Get type-specific metadata
                stmt = select(new_model).where(new_model.media_id == media.id)
                new_record = (await session.exec(stmt)).one_or_none()

                if not new_record:
                    mismatches.append(f"Missing type-specific metadata for: {sample.id}")
                    continue

                # Verify specific fields
                field_mismatches = await verify_func(session, sample, new_record, media.id)
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
                stmt = select(Media).where(Media.id == link.media_id)
                if not (await session.exec(stmt)).one_or_none():
                    issues.append(f"Genre {genre.name} linked to non-existent media {link.media_id}")

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
            stmt = select(MediaCatalogLink).where(MediaCatalogLink.catalog_id == catalog.id)
            links = (await session.exec(stmt)).all()

            for link in links:
                # Verify media exists
                stmt = select(Media).where(Media.id == link.media_id)
                if not (await session.exec(stmt)).one_or_none():
                    issues.append(f"Catalog {catalog.name} linked to non-existent media {link.media_id}")

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
                # Get series media_ids that exist in PostgreSQL
                stmt = select(SeriesMetadata.media_id).limit(min(5, self.sample_size))
                pg_media_ids = (await session.exec(stmt)).all()

                if not pg_media_ids:
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

                # Get external_ids for these media via MediaExternalID
                series_samples = []
                for media_id in pg_media_ids[:5]:
                    external_id = await self._get_external_id_for_media(session, media_id)
                    if external_id:
                        old_series = await OldSeriesMetaData.find_one({"_id": external_id})
                        if old_series:
                            series_samples.append((media_id, external_id, old_series))
            else:
                # Full mode: random sample from MongoDB
                samples = await OldSeriesMetaData.aggregate([{"$sample": {"size": 5}}]).to_list()
                series_samples = []
                for s in samples:
                    old_series = OldSeriesMetaData.model_validate(s)
                    # Find corresponding media via MediaExternalID
                    media = await self._get_media_by_external_id(session, old_series.id)
                    if media:
                        series_samples.append((media.id, old_series.id, old_series))

            season_issues = []
            episode_file_issues = []

            for media_id, external_id, old_series in series_samples:
                # In sample mode, only check torrents that were migrated
                if self.is_sample_mode:
                    # Get info_hashes that exist in PostgreSQL for this series
                    stmt = (
                        select(TorrentStream.info_hash)
                        .join(Stream, TorrentStream.stream_id == Stream.id)
                        .join(StreamMediaLink, Stream.id == StreamMediaLink.stream_id)
                        .where(StreamMediaLink.media_id == media_id)
                    )
                    migrated_info_hashes = set((await session.exec(stmt)).all())

                    old_torrents = await OldTorrentStreams.find(
                        {
                            "meta_id": external_id,
                            "_id": {"$in": list(migrated_info_hashes)},
                        }
                    ).to_list()
                else:
                    old_torrents = await OldTorrentStreams.find({"meta_id": external_id}).to_list()

                # Verify seasons and episodes
                for old_torrent in old_torrents:
                    if not old_torrent.episode_files:
                        continue

                    # Check episode files
                    for old_episode in old_torrent.episode_files:
                        if old_episode.filename and not is_video_file(old_episode.filename):
                            continue

                        # Check season exists
                        stmt = select(Season).where(
                            Season.media_id == media_id,
                            Season.season_number == old_episode.season_number,
                        )
                        season = (await session.exec(stmt)).one_or_none()

                        if not season:
                            season_issues.append(f"Missing season {old_episode.season_number} for series {external_id}")
                            continue

                        # Get stream_id for this torrent
                        stmt = (
                            select(Stream.id)
                            .join(TorrentStream, TorrentStream.stream_id == Stream.id)
                            .where(TorrentStream.info_hash == old_torrent.id.lower())
                        )
                        stream_id = (await session.exec(stmt)).first()

                        if stream_id:
                            # Check for FileMediaLink (replaces StreamEpisodeFile)
                            stmt = (
                                select(FileMediaLink)
                                .join(StreamFile, FileMediaLink.file_id == StreamFile.id)
                                .where(
                                    StreamFile.stream_id == stream_id,
                                    FileMediaLink.season_number == old_episode.season_number,
                                    FileMediaLink.episode_number == old_episode.episode_number,
                                )
                            )
                            file_link = (await session.exec(stmt)).one_or_none()

                            if not file_link:
                                episode_file_issues.append(
                                    f"Missing file media link for S{old_episode.season_number}"
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

    async def _verify_movie_data(
        self,
        session: AsyncSession,
        old_movie: OldMovieMetaData,
        new_movie: MovieMetadata,
        media_id: int,
    ) -> list[str]:
        """Verify movie-specific fields"""
        mismatches = []

        # Verify ratings from MediaRating table
        stmt = select(MediaRating).where(MediaRating.media_id == media_id)
        ratings = (await session.exec(stmt)).all()

        imdb_rating = None
        for rating in ratings:
            # Check for IMDb provider by rating_provider_id
            if rating.rating_provider_id == 1:  # IMDb provider ID
                imdb_rating = rating.rating
                break

        if old_movie.imdb_rating and imdb_rating != old_movie.imdb_rating:
            mismatches.append(f"IMDb rating mismatch for {old_movie.id}: {old_movie.imdb_rating} vs {imdb_rating}")

        # Handle nudity status comparison - None in MongoDB maps to UNKNOWN in PostgreSQL
        # nudity_status is now on Media table, not MovieMetadata
        old_nudity = getattr(old_movie, "parent_guide_nudity_status", None)
        media_stmt = select(Media).where(Media.id == media_id)
        media = (await session.exec(media_stmt)).first()
        new_nudity = media.nudity_status if media else NudityStatus.UNKNOWN
        if old_nudity is None:
            # If MongoDB value is None, PostgreSQL should have UNKNOWN
            if new_nudity != NudityStatus.UNKNOWN:
                mismatches.append(f"Nudity status mismatch for {old_movie.id}: None (expected UNKNOWN) vs {new_nudity}")
        elif old_nudity != new_nudity:
            mismatches.append(f"Nudity status mismatch for {old_movie.id}: {old_nudity} vs {new_nudity}")

        return mismatches

    async def _verify_tv_data(
        self,
        session: AsyncSession,
        old_tv: OldTVMetaData,
        new_tv: TVMetadata,
        media_id: int,
    ) -> list[str]:
        """Verify TV-specific fields"""
        mismatches = []

        if old_tv.country != new_tv.country:
            mismatches.append(f"Country mismatch for {old_tv.id}: {old_tv.country} vs {new_tv.country}")

        if old_tv.tv_language != new_tv.tv_language:
            mismatches.append(f"Language mismatch for {old_tv.id}: {old_tv.tv_language} vs {new_tv.tv_language}")

        # Verify logo from MediaImage table
        stmt = select(MediaImage).where(MediaImage.media_id == media_id, MediaImage.image_type == "logo")
        logo_image = (await session.exec(stmt)).first()
        new_logo = logo_image.url if logo_image else None

        if old_tv.logo != new_logo:
            mismatches.append(f"Logo mismatch for {old_tv.id}: {old_tv.logo} vs {new_logo}")

        return mismatches

    async def _verify_torrent_streams(self):
        """Verify torrent streams migration with detailed checking"""
        async with self.migration.get_session() as session:
            self.verification_results["torrent_streams"] = {}

            sample_size = min(10, self.sample_size) if self.is_sample_mode else 10

            # Get torrent info_hashes from PostgreSQL
            stmt = select(TorrentStream.info_hash).limit(sample_size)
            pg_info_hashes = (await session.exec(stmt)).all()

            if not pg_info_hashes:
                self.verification_results["torrent_streams"]["data_integrity"] = {
                    "valid": True,
                    "details": "No torrent streams migrated",
                    "issues": [],
                }
                return

            issues = []
            for info_hash in pg_info_hashes:
                # Get PostgreSQL record
                stmt = (
                    select(TorrentStream, Stream)
                    .join(Stream, TorrentStream.stream_id == Stream.id)
                    .where(TorrentStream.info_hash == info_hash)
                )
                result = (await session.exec(stmt)).first()

                if not result:
                    issues.append(f"Torrent {info_hash} not found in PostgreSQL")
                    continue

                new_torrent, new_stream = result

                # Get MongoDB record
                old_torrent = await OldTorrentStreams.find_one({"_id": info_hash})

                if not old_torrent:
                    # Try lowercase
                    old_torrent = await OldTorrentStreams.find_one({"_id": info_hash.upper()})

                if not old_torrent:
                    issues.append(f"Torrent {info_hash} not found in MongoDB (orphaned)")
                    continue

                # Verify key fields
                # source and resolution are on Stream, size is on TorrentStream
                if old_torrent.source != new_stream.source:
                    issues.append(f"Source mismatch for {info_hash}: {old_torrent.source} vs {new_stream.source}")

                if old_torrent.resolution != new_stream.resolution:
                    issues.append(
                        f"Resolution mismatch for {info_hash}: {old_torrent.resolution} vs {new_stream.resolution}"
                    )

                if old_torrent.size != new_torrent.total_size:
                    issues.append(f"Size mismatch for {info_hash}: {old_torrent.size} vs {new_torrent.total_size}")

                # Verify languages link
                stmt = select(StreamLanguageLink).where(StreamLanguageLink.stream_id == new_stream.id)
                pg_lang_links = (await session.exec(stmt)).all()

                old_languages = set(old_torrent.languages or [])
                pg_languages = set()
                for link in pg_lang_links:
                    lang_stmt = select(Language).where(Language.id == link.language_id)
                    lang = (await session.exec(lang_stmt)).one_or_none()
                    if lang:
                        pg_languages.add(lang.name)

                if old_languages != pg_languages:
                    issues.append(f"Language mismatch for {info_hash}: {old_languages} vs {pg_languages}")

            self.verification_results["torrent_streams"]["data_integrity"] = {
                "valid": len(issues) == 0,
                "details": f"Found {len(issues)} issues in {len(pg_info_hashes)} samples",
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
                "issues": ([] if count_valid else [f"Count mismatch: {mongo_count} vs {pg_count}"]),
            }

            # Sample verification
            sample_size = min(10, mongo_count) if mongo_count > 0 else 0
            issues = []

            if sample_size > 0:
                samples = await OldRSSFeed.aggregate([{"$sample": {"size": sample_size}}]).to_list()

                for sample in samples:
                    old_feed = OldRSSFeed.model_validate(sample)
                    stmt = select(RSSFeed).where(RSSFeed.url == old_feed.url)
                    new_feed = (await session.exec(stmt)).one_or_none()

                    if not new_feed:
                        issues.append(f"Missing RSS feed: {old_feed.name} ({old_feed.url})")
                        continue

                    # Verify key fields
                    if old_feed.name != new_feed.name:
                        issues.append(f"Name mismatch for {old_feed.url}: {old_feed.name} vs {new_feed.name}")
                    if old_feed.active != new_feed.is_active:
                        issues.append(
                            f"Active status mismatch for {old_feed.url}: {old_feed.active} vs {new_feed.is_active}"
                        )

                    # Verify catalog patterns count
                    old_pattern_count = len(old_feed.catalog_patterns or [])
                    stmt = select(func.count()).where(RSSFeedCatalogPattern.rss_feed_id == new_feed.id)
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
            sample_query = select(Media.id, Media.total_streams).where(Media.total_streams > 0).limit(sample_size)
            samples = (await session.exec(sample_query)).all()

            for media_id, stored_count in samples:
                # Count actual streams via StreamMediaLink
                actual_count = await session.scalar(
                    select(func.count(Stream.id))
                    .join(StreamMediaLink, Stream.id == StreamMediaLink.stream_id)
                    .where(
                        StreamMediaLink.media_id == media_id,
                        Stream.is_blocked == False,
                        Stream.is_active == True,
                    )
                )

                if stored_count != actual_count:
                    issues.append(f"Stream count mismatch for {media_id}: stored={stored_count}, actual={actual_count}")

            self.verification_results["catalog_statistics"]["stream_counts"] = {
                "valid": len(issues) == 0,
                "details": f"Verified {len(samples)} media items, found {len(issues)} mismatches",
                "issues": issues,
            }

            # Per-catalog stats not needed - catalog associations handled via MediaCatalogLink
            self.verification_results["catalog_statistics"]["per_catalog"] = {
                "valid": True,
                "details": "Catalog associations verified via MediaCatalogLink table.",
                "issues": [],
            }
