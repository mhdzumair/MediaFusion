"""
Series migrator for MongoDB to PostgreSQL migration.

Updated for MediaFusion 5.0 architecture:
- Uses Stream, TorrentStream instead of old models
- Uses StreamFile + FileMediaLink instead of StreamEpisodeFile
- Updated queries for the new schema
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from db.enums import MediaType
from db.models import (
    Episode,
    FileMediaLink,
    Media,
    MediaExternalID,
    Season,
    SeriesMetadata,
    Stream,
    StreamFile,
    TorrentStream,
)
from db.models.streams import FileType, LinkSource
from migrations.mongo_models import (
    MediaFusionSeriesMetaData as OldSeriesMetaData,
)
from migrations.mongo_models import (
    TorrentStreams as OldTorrentStreams,
)
from utils.validation_helper import is_video_file

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class SeriesDataMigrator:
    """
    Handle series-specific data migration with optimized batch processing.

    Updated for MediaFusion 5.0 architecture:
    - Media table with integer PKs and external_id
    - Season and Episode tables
    - StreamFile + FileMediaLink for torrent episode mappings (replaces StreamEpisodeFile)
    """

    def __init__(self, migration, resume_mode: bool = False):
        self.migration = migration
        self.stats = migration.stats
        self.resume_mode = resume_mode
        # Cache for prefetched torrent episodes
        self._torrent_episodes_cache: dict[str, list[dict]] = {}
        # Batch size for prefetching torrents
        self._prefetch_batch_size = 50
        # Cache for existing series with seasons (for resume mode)
        self._existing_series_with_seasons: set[int] = set()  # Now using int media_id
        # Cache for external_id -> media_id mapping
        self._external_id_to_media_id: dict[str, int] = {}

    async def _load_existing_series_with_seasons(self):
        """Load media_ids that already have seasons in PostgreSQL"""
        if not self.resume_mode or self._existing_series_with_seasons:
            return

        logger.info("📥 Loading series with existing seasons from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            # Season.series_id -> SeriesMetadata.id -> SeriesMetadata.media_id
            stmt = select(SeriesMetadata.media_id).join(Season, Season.series_id == SeriesMetadata.id).distinct()
            result = await session.exec(stmt)
            self._existing_series_with_seasons.update(result.all())

        logger.info(f"✅ Loaded {len(self._existing_series_with_seasons):,} series with existing seasons")

    async def _load_external_id_mapping(self, external_ids: list[str]):
        """Load external_id to media_id mapping for a batch of IDs.

        Uses MediaExternalID table to map MongoDB IDs to media_ids.
        Handles different formats: tt* (IMDb), mftmdb* (TMDB), provider:id.
        """
        if not external_ids:
            return

        async with self.migration.get_session() as session:
            CHUNK_SIZE = 30000
            for i in range(0, len(external_ids), CHUNK_SIZE):
                chunk = external_ids[i : i + CHUNK_SIZE]

                # Group by provider for efficient lookup
                imdb_ids = [eid for eid in chunk if eid.startswith("tt")]
                tmdb_ids = [(eid, eid.replace("mftmdb", "")) for eid in chunk if eid.startswith("mftmdb")]
                other_ids = [(eid, eid.split(":", 1)) for eid in chunk if ":" in eid and not eid.startswith("mf:")]

                # Lookup IMDb IDs
                if imdb_ids:
                    stmt = (
                        select(MediaExternalID.external_id, Media.id)
                        .join(Media, Media.id == MediaExternalID.media_id)
                        .where(
                            MediaExternalID.provider == "imdb",
                            MediaExternalID.external_id.in_(imdb_ids),
                            Media.type == MediaType.SERIES,
                        )
                    )
                    result = await session.exec(stmt)
                    for ext_id, media_id in result.all():
                        self._external_id_to_media_id[ext_id] = media_id

                # Lookup TMDB IDs (legacy mftmdb format)
                if tmdb_ids:
                    tmdb_lookup = {numeric: original for original, numeric in tmdb_ids}
                    stmt = (
                        select(MediaExternalID.external_id, Media.id)
                        .join(Media, Media.id == MediaExternalID.media_id)
                        .where(
                            MediaExternalID.provider == "tmdb",
                            MediaExternalID.external_id.in_([t[1] for t in tmdb_ids]),
                            Media.type == MediaType.SERIES,
                        )
                    )
                    result = await session.exec(stmt)
                    for ext_id, media_id in result.all():
                        original_id = tmdb_lookup.get(ext_id)
                        if original_id:
                            self._external_id_to_media_id[original_id] = media_id

                # Lookup other provider IDs
                for original_id, parts in other_ids:
                    if len(parts) == 2:
                        provider, ext_id = parts[0].lower(), parts[1]
                        stmt = (
                            select(MediaExternalID.external_id, Media.id)
                            .join(Media, Media.id == MediaExternalID.media_id)
                            .where(
                                MediaExternalID.provider == provider,
                                MediaExternalID.external_id == ext_id,
                                Media.type == MediaType.SERIES,
                            )
                        )
                        result = await session.exec(stmt)
                        row = result.first()
                        if row:
                            self._external_id_to_media_id[original_id] = row[1]

    async def migrate_series_metadata(self):
        """Migrate series metadata with optimized batch processing"""
        logger.info("Migrating series data...")

        # Get series metadata, respecting sample_size limit
        cursor = OldSeriesMetaData.find()
        if self.migration.sample_size:
            cursor = cursor.limit(self.migration.sample_size)
        series_docs = await cursor.to_list()

        # Load external_id to media_id mapping
        external_ids = [doc.id for doc in series_docs]
        await self._load_external_id_mapping(external_ids)

        # Filter to only process series that exist in PostgreSQL
        series_to_process = [doc for doc in series_docs if doc.id in self._external_id_to_media_id]

        # In resume mode, skip series that already have seasons
        if self.resume_mode:
            await self._load_existing_series_with_seasons()

            original_count = len(series_to_process)
            series_to_process = [
                doc
                for doc in series_to_process
                if self._external_id_to_media_id[doc.id] not in self._existing_series_with_seasons
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
                batch = series_to_process[i : i + self._prefetch_batch_size]

                # Prefetch torrent episodes for this batch
                await self._prefetch_torrent_episodes([doc.id for doc in batch])

                # Process each series in the batch
                for series_doc in batch:
                    try:
                        media_id = self._external_id_to_media_id.get(series_doc.id)
                        if media_id:
                            await self._migrate_series_optimized(session, series_doc, media_id)
                    except Exception as e:
                        logger.error(f"Error migrating series data for {series_doc.id}: {str(e)}")
                        self.stats.add_error("series_migration", f"Series {series_doc.id}: {str(e)}")
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

    async def _prefetch_torrent_episodes(self, external_ids: list[str]):
        """Prefetch torrent episodes for multiple series at once"""
        # Query all torrents for these series in one query
        torrent_streams = await OldTorrentStreams.find({"meta_id": {"$in": external_ids}}).to_list()

        # Group by external_id (meta_id)
        for stream in torrent_streams:
            external_id = stream.meta_id
            if external_id not in self._torrent_episodes_cache:
                self._torrent_episodes_cache[external_id] = []

            if stream.episode_files:
                for episode in stream.episode_files:
                    if episode.filename and is_video_file(episode.filename):
                        self._torrent_episodes_cache[external_id].append(
                            {
                                "season_number": episode.season_number,
                                "episode_number": episode.episode_number,
                                "filename": episode.filename,
                                "size": episode.size,
                                "file_index": episode.file_index,
                                "title": episode.title,
                                "released": episode.released,
                                "info_hash": stream.id,  # Store info_hash, not ID
                            }
                        )

    async def _migrate_series_optimized(self, session: AsyncSession, old_doc: OldSeriesMetaData, media_id: int):
        """Optimized series migration with batch operations"""
        external_id = old_doc.id

        # Get cached torrent episodes (already prefetched)
        torrent_episodes = self._torrent_episodes_cache.get(external_id, [])
        if not torrent_episodes:
            return  # No episodes to migrate

        # Group episodes by season
        seasons_data = self._organize_episodes_by_season(torrent_episodes)
        if not seasons_data:
            return

        # Batch upsert all seasons at once
        season_numbers = list(seasons_data.keys())
        seasons_map = await self._batch_upsert_seasons(session, media_id, season_numbers)

        # Get info_hash to stream_id mapping for episode files
        all_info_hashes = {ep["info_hash"] for eps in seasons_data.values() for ep in eps if ep.get("info_hash")}
        info_hash_to_stream_id = await self._get_stream_ids_for_info_hashes(session, all_info_hashes)

        # Batch upsert episodes and episode files for all seasons
        await self._batch_upsert_episodes_and_files(
            session, media_id, seasons_data, seasons_map, info_hash_to_stream_id
        )

    async def _get_stream_ids_for_info_hashes(self, session: AsyncSession, info_hashes: set[str]) -> dict[str, int]:
        """Get stream_id for each info_hash."""
        if not info_hashes:
            return {}

        result_map = {}
        info_hash_list = list(info_hashes)
        CHUNK_SIZE = 30000

        for i in range(0, len(info_hash_list), CHUNK_SIZE):
            chunk = info_hash_list[i : i + CHUNK_SIZE]
            stmt = (
                select(TorrentStream.info_hash, Stream.id)
                .join(Stream, TorrentStream.stream_id == Stream.id)
                .where(TorrentStream.info_hash.in_(chunk))
            )
            result = await session.exec(stmt)
            for info_hash, stream_id in result.all():
                result_map[info_hash] = stream_id

        return result_map

    async def _batch_upsert_seasons(
        self, session: AsyncSession, media_id: int, season_numbers: list[int]
    ) -> dict[int, int]:
        """Batch upsert seasons and return mapping of season_number -> season_id.

        Note: Season uses series_id (FK to series_metadata.id), not media_id directly.
        """
        if not season_numbers:
            return {}

        # Get series_metadata.id for this media_id
        stmt = select(SeriesMetadata.id).where(SeriesMetadata.media_id == media_id)
        result = await session.exec(stmt)
        series_id = result.first()

        if not series_id:
            logger.warning(f"No SeriesMetadata found for media_id={media_id}")
            return {}

        # Prepare season data
        seasons_data = [{"series_id": series_id, "season_number": sn} for sn in season_numbers]

        # Upsert using ON CONFLICT
        stmt = pg_insert(Season).values(seasons_data)
        stmt = stmt.on_conflict_do_nothing(index_elements=["series_id", "season_number"])
        await session.exec(stmt)

        # Get all season IDs (including existing ones)
        fetch_stmt = select(Season).where(Season.series_id == series_id, Season.season_number.in_(season_numbers))
        result = await session.exec(fetch_stmt)
        seasons = result.all()

        return {s.season_number: s.id for s in seasons}

    async def _batch_upsert_episodes_and_files(
        self,
        session: AsyncSession,
        media_id: int,
        seasons_data: dict[int, list[dict]],
        seasons_map: dict[int, int],
        info_hash_to_stream_id: dict[str, int],
    ):
        """
        Batch upsert episodes and create StreamFile + FileMediaLink.

        This creates MediaFusion's own episode metadata from torrent episode_files data.
        When series metadata doesn't have episode info from IMDB/TMDB, we use the
        torrent's season/episode information to build the series metadata.

        New architecture:
        - StreamFile: Pure file structure (filename, size, index)
        - FileMediaLink: Links files to media with season/episode context
        """
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
                    # Convert released datetime to air_date (date only)
                    released = ep_data.get("released")
                    air_date = None
                    if released:
                        if isinstance(released, datetime):
                            air_date = released.date()
                        else:
                            air_date = released
                    episodes_to_upsert.append(
                        {
                            "season_id": season_id,
                            "episode_number": ep_num,
                            "title": ep_data.get("title") or f"Episode {ep_num}",
                            "air_date": air_date,
                        }
                    )

        # Batch upsert episodes and FLUSH to ensure IDs are available
        EPISODE_CHUNK_SIZE = 2000
        if episodes_to_upsert:
            for i in range(0, len(episodes_to_upsert), EPISODE_CHUNK_SIZE):
                chunk = episodes_to_upsert[i : i + EPISODE_CHUNK_SIZE]
                stmt = pg_insert(Episode).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["season_id", "episode_number"])
                await session.exec(stmt)

            await session.flush()

        # =========================================================================
        # NEW: Create StreamFile + FileMediaLink instead of StreamEpisodeFile
        # =========================================================================

        # First, collect and create all StreamFile records
        stream_files_to_upsert = []
        seen_stream_files = set()

        for season_number, episodes in seasons_data.items():
            for ep_data in episodes:
                info_hash = ep_data.get("info_hash")
                stream_id = info_hash_to_stream_id.get(info_hash) if info_hash else None

                if not stream_id:
                    continue

                file_index = ep_data.get("file_index")
                if file_index is None:
                    continue

                file_key = (stream_id, file_index)
                if file_key not in seen_stream_files:
                    seen_stream_files.add(file_key)
                    filename = ep_data.get("filename") or f"file_{file_index}"
                    file_type = FileType.VIDEO if is_video_file(filename) else FileType.OTHER

                    stream_files_to_upsert.append(
                        {
                            "stream_id": stream_id,
                            "file_index": file_index,
                            "filename": filename,
                            "size": ep_data.get("size"),
                            "file_type": file_type.value,
                        }
                    )

        # Batch upsert StreamFile records
        STREAM_FILE_CHUNK_SIZE = 1000
        if stream_files_to_upsert:
            for i in range(0, len(stream_files_to_upsert), STREAM_FILE_CHUNK_SIZE):
                chunk = stream_files_to_upsert[i : i + STREAM_FILE_CHUNK_SIZE]
                stmt = pg_insert(StreamFile).values(chunk)
                stmt = stmt.on_conflict_do_nothing(index_elements=["stream_id", "file_index"])
                await session.exec(stmt)

            await session.flush()

        # Get all StreamFile IDs for this batch
        stream_file_ids = {}  # (stream_id, file_index) -> file_id
        if seen_stream_files:
            # Build query to get all file IDs
            stream_ids = list(set(sf[0] for sf in seen_stream_files))
            CHUNK_SIZE = 5000
            for i in range(0, len(stream_ids), CHUNK_SIZE):
                chunk = stream_ids[i : i + CHUNK_SIZE]
                stmt = select(StreamFile.id, StreamFile.stream_id, StreamFile.file_index).where(
                    StreamFile.stream_id.in_(chunk)
                )
                result = await session.exec(stmt)
                for file_id, stream_id, file_index in result.all():
                    stream_file_ids[(stream_id, file_index)] = file_id

        # Create FileMediaLink records
        file_media_links_to_upsert = []
        seen_links = set()

        for season_number, episodes in seasons_data.items():
            for ep_data in episodes:
                info_hash = ep_data.get("info_hash")
                stream_id = info_hash_to_stream_id.get(info_hash) if info_hash else None

                if not stream_id:
                    continue

                file_index = ep_data.get("file_index")
                file_id = stream_file_ids.get((stream_id, file_index))

                if not file_id:
                    continue

                ep_num = ep_data["episode_number"]

                # Create unique key for this file-media-episode link
                link_key = (file_id, media_id, season_number, ep_num)
                if link_key not in seen_links:
                    seen_links.add(link_key)
                    file_media_links_to_upsert.append(
                        {
                            "file_id": file_id,
                            "media_id": media_id,
                            "season_number": season_number,
                            "episode_number": ep_num,
                            "link_source": LinkSource.TORRENT_METADATA.value,
                            "confidence": 1.0,
                        }
                    )

        # Batch upsert FileMediaLink records
        FILE_MEDIA_LINK_CHUNK_SIZE = 1000
        if file_media_links_to_upsert:
            for i in range(0, len(file_media_links_to_upsert), FILE_MEDIA_LINK_CHUNK_SIZE):
                chunk = file_media_links_to_upsert[i : i + FILE_MEDIA_LINK_CHUNK_SIZE]
                stmt = pg_insert(FileMediaLink).values(chunk)
                stmt = stmt.on_conflict_do_nothing()
                await session.exec(stmt)

    async def verify_episode_linkage(self, session: AsyncSession) -> dict:
        """
        Verify FileMediaLink records for series episodes.
        Returns statistics on file-to-media linkage status.
        """
        # Count total FileMediaLink records with episode info
        total_stmt = (
            select(func.count())
            .select_from(FileMediaLink)
            .where(
                FileMediaLink.season_number.isnot(None),
                FileMediaLink.episode_number.isnot(None),
            )
        )
        total_count = await session.scalar(total_stmt) or 0

        # Count StreamFile records
        stream_file_stmt = select(func.count()).select_from(StreamFile)
        stream_file_count = await session.scalar(stream_file_stmt) or 0

        logger.info(f"✅ FileMediaLink records with episode info: {total_count:,}")
        logger.info(f"✅ StreamFile records: {stream_file_count:,}")

        return {
            "total_episode_links": total_count,
            "total_stream_files": stream_file_count,
        }

    def _organize_episodes_by_season(self, episodes: list[dict]) -> dict[int, list[dict]]:
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

        return {season: list(episodes.values()) for season, episodes in seasons_data.items()}

    @staticmethod
    def _is_better_episode(new_ep: dict, existing_ep: dict) -> bool:
        """Determine if new episode data is better than existing"""
        score_new = sum(1 for k, v in new_ep.items() if v is not None)
        score_existing = sum(1 for k, v in existing_ep.items() if v is not None)
        return score_new > score_existing

    @staticmethod
    def _ensure_timezone(dt: datetime | None) -> datetime | None:
        """Ensure datetime is timezone-aware"""
        if dt and dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
