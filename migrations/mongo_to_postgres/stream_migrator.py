"""
Stream migrator for MongoDB to PostgreSQL migration.

Updated for MediaFusion 5.0 architecture:
- Stream base table with integer PKs
- TorrentStream, HTTPStream, YouTubeStream type-specific tables
- StreamFile for file structure (replaces TorrentFile)
- FileMediaLink for flexible file-to-media linking (replaces StreamEpisodeFile)
- StreamMediaLink for stream-level linking
- TorrentTrackerLink, StreamLanguageLink for relationships
"""

import logging
from typing import Any

import PTT
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tqdm.asyncio import tqdm

from db.enums import MediaType, TorrentType
from db.models import (
    FileMediaLink,
    HTTPStream,
    Media,
    MediaExternalID,
    Stream,
    StreamAudioLink,
    StreamChannelLink,
    StreamFile,
    StreamHDRLink,
    StreamLanguageLink,
    StreamMediaLink,
    TorrentStream,
    TorrentTrackerLink,
    Tracker,
    YouTubeStream,
)
from db.models.streams import FileType, LinkSource, StreamType
from migrations.mongo_models import (
    TorrentStreams as OldTorrentStreams,
)
from migrations.mongo_models import (
    TVStreams as OldTVStreams,
)
from migrations.mongo_to_postgres.metadata_migrator import DatabaseMigration
from migrations.mongo_to_postgres.series_migrator import SeriesDataMigrator
from migrations.mongo_to_postgres.stats import Stats
from utils.validation_helper import is_video_file

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class StreamMigrator:
    """
    Stream migrator for the new MediaFusion 5.0 architecture.

    Handles migration of:
    - Stream (base table)
    - TorrentStream (torrent-specific data)
    - HTTPStream (TV stream URLs)
    - YouTubeStream (YouTube videos)
    - StreamFile (file structure - replaces TorrentFile)
    - FileMediaLink (flexible file-to-media linking - replaces StreamEpisodeFile)
    - StreamMediaLink (stream-level linking)
    - StreamLanguageLink (languages)
    - TorrentTrackerLink (trackers)
    """

    def __init__(self, migration: DatabaseMigration, resume_mode: bool = False):
        self.migration = migration
        self.resource_tracker = migration.resource_tracker
        self.stats = Stats()
        self.series_migrator = SeriesDataMigrator(migration)
        self.resume_mode = resume_mode
        self._existing_info_hashes: set[str] = set()
        self._existing_http_stream_keys: set[tuple] = set()
        # Cache for trackers
        self._tracker_cache: dict[str, int] = {}

    async def _lookup_media_by_external_ids(self, session: AsyncSession, external_ids: set[str]) -> dict[str, tuple]:
        """Lookup media by external IDs (MongoDB format) via MediaExternalID table.

        Args:
            session: Database session
            external_ids: Set of MongoDB IDs (e.g., 'tt1234567', 'mftmdb123456')

        Returns:
            Dict mapping external_id to (media_id, media_type) tuple
        """
        if not external_ids:
            return {}

        media_map = {}

        # Group by provider for efficient lookup
        imdb_ids = [eid for eid in external_ids if eid.startswith("tt")]
        tmdb_ids = [(eid, eid.replace("mftmdb", "")) for eid in external_ids if eid.startswith("mftmdb")]
        other_ids = [(eid, eid.split(":", 1)) for eid in external_ids if ":" in eid and not eid.startswith("mf:")]

        # Lookup IMDb IDs
        if imdb_ids:
            stmt = (
                select(MediaExternalID.external_id, Media.id, Media.type)
                .join(Media, Media.id == MediaExternalID.media_id)
                .where(
                    MediaExternalID.provider == "imdb",
                    MediaExternalID.external_id.in_(imdb_ids),
                )
            )
            result = await session.exec(stmt)
            for ext_id, media_id, media_type in result.all():
                media_map[ext_id] = (media_id, media_type)

        # Lookup TMDB IDs (legacy mftmdb format)
        if tmdb_ids:
            tmdb_lookup = {numeric: original for original, numeric in tmdb_ids}
            stmt = (
                select(MediaExternalID.external_id, Media.id, Media.type)
                .join(Media, Media.id == MediaExternalID.media_id)
                .where(
                    MediaExternalID.provider == "tmdb",
                    MediaExternalID.external_id.in_([t[1] for t in tmdb_ids]),
                )
            )
            result = await session.exec(stmt)
            for ext_id, media_id, media_type in result.all():
                original_id = tmdb_lookup.get(ext_id)
                if original_id:
                    media_map[original_id] = (media_id, media_type)

        # Lookup other provider IDs (tvdb:123, mal:456, etc.)
        for original_id, parts in other_ids:
            if len(parts) == 2:
                provider, ext_id = parts[0].lower(), parts[1]
                stmt = (
                    select(MediaExternalID.external_id, Media.id, Media.type)
                    .join(Media, Media.id == MediaExternalID.media_id)
                    .where(
                        MediaExternalID.provider == provider,
                        MediaExternalID.external_id == ext_id,
                    )
                )
                result = await session.exec(stmt)
                row = result.first()
                if row:
                    media_map[original_id] = (row[1], row[2])

        return media_map

    async def _load_existing_torrent_ids(self):
        """Load existing info_hashes from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_info_hashes:
            return

        logger.info("📥 Loading existing torrent info_hashes from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            total_count = await session.scalar(select(func.count()).select_from(TorrentStream))
            logger.info(f"  Total torrent streams in PostgreSQL: {total_count:,}")

            chunk_size = 100000
            last_id = 0

            while True:
                stmt = (
                    select(TorrentStream.id, TorrentStream.info_hash)
                    .where(TorrentStream.id > last_id)
                    .order_by(TorrentStream.id)
                    .limit(chunk_size)
                )
                result = await session.exec(stmt)
                rows = result.all()

                if not rows:
                    break

                for _, info_hash in rows:
                    self._existing_info_hashes.add(info_hash.lower())

                last_id = rows[-1][0]

                if len(self._existing_info_hashes) % 500000 < chunk_size:
                    logger.info(f"  Loaded {len(self._existing_info_hashes):,} / {total_count:,} info_hashes...")

        logger.info(f"✅ Loaded {len(self._existing_info_hashes):,} existing info_hashes")

    async def _load_existing_http_stream_keys(self):
        """Load existing HTTP stream keys from PostgreSQL for resume mode"""
        if not self.resume_mode or self._existing_http_stream_keys:
            return

        logger.info("📥 Loading existing HTTP stream keys from PostgreSQL for resume mode...")
        async with self.migration.get_session() as session:
            total_count = await session.scalar(select(func.count()).select_from(HTTPStream))
            logger.info(f"  Total HTTP streams in PostgreSQL: {total_count:,}")

            chunk_size = 10000
            last_id = 0

            while True:
                stmt = (
                    select(HTTPStream.id, HTTPStream.url)
                    .where(HTTPStream.id > last_id)
                    .order_by(HTTPStream.id)
                    .limit(chunk_size)
                )
                result = await session.exec(stmt)
                rows = result.all()

                if not rows:
                    break

                for _, url in rows:
                    if url:
                        self._existing_http_stream_keys.add(url)

                last_id = rows[-1][0]

        logger.info(f"✅ Loaded {len(self._existing_http_stream_keys):,} existing HTTP stream URLs")

    async def migrate_torrent_streams(self):
        """Migrate torrent streams with enhanced error handling and batching"""
        total = await OldTorrentStreams.find().count()
        if total == 0:
            logger.info("No torrent streams to migrate")
            return

        if self.resume_mode:
            await self._load_existing_torrent_ids()

        async for batch in self._get_stream_batches(OldTorrentStreams, total):
            if self.resume_mode:
                batch = [s for s in batch if s.id.lower() not in self._existing_info_hashes]
                if not batch:
                    continue

            async with self.migration.get_session() as session:
                await self._process_torrent_batch(session, batch)

    async def migrate_tv_streams(self):
        """Migrate TV streams as HTTPStream records"""
        total = await OldTVStreams.find().count()
        if total == 0:
            logger.info("No TV streams to migrate")
            return

        if self.resume_mode:
            await self._load_existing_http_stream_keys()

        async for batch in self._get_stream_batches(OldTVStreams, total):
            if self.resume_mode:
                batch = [s for s in batch if s.url not in self._existing_http_stream_keys]
                if not batch:
                    continue

            async with self.migration.get_session() as session:
                await self._process_tv_stream_batch(session, batch)

    async def _get_stream_batches(self, model, total):
        """
        Yield stream batches efficiently, respecting sample_size limit.

        Uses skip/limit pagination to avoid cursor timeout issues on large collections.
        MongoDB cursors expire after 10 minutes by default.
        """
        sample_limit = self.migration.sample_size
        effective_total = min(total, sample_limit) if sample_limit else total

        # Use chunk-based fetching to avoid cursor timeout
        FETCH_SIZE = self.migration.batch_size * 5

        with tqdm(total=effective_total, desc=f"Migrating {model.__name__}") as pbar:
            skip = 0
            total_fetched = 0

            while total_fetched < effective_total:
                remaining = effective_total - total_fetched
                fetch_limit = min(FETCH_SIZE, remaining)

                try:
                    chunk = await model.find().skip(skip).limit(fetch_limit).to_list()
                except Exception as e:
                    logger.error(f"Error fetching streams at skip={skip}: {e}")
                    break

                if not chunk:
                    break

                current_batch = []
                for stream in chunk:
                    current_batch.append(stream)
                    total_fetched += 1

                    if len(current_batch) >= self.migration.batch_size:
                        yield current_batch
                        pbar.update(len(current_batch))
                        current_batch = []

                    if sample_limit and total_fetched >= sample_limit:
                        break

                if current_batch:
                    yield current_batch
                    pbar.update(len(current_batch))

                skip += len(chunk)

                if sample_limit and total_fetched >= sample_limit:
                    break

    async def _bulk_get_or_create_trackers(self, session: AsyncSession, urls: set) -> dict[str, int]:
        """BULK get or create tracker records."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        if not urls:
            return {}

        urls = {u for u in urls if u}  # Filter empty
        if not urls:
            return {}

        # Check cache first
        uncached = set()
        for url in urls:
            if url not in self._tracker_cache:
                uncached.add(url)

        if not uncached:
            return self._tracker_cache

        # Bulk query database
        uncached_list = list(uncached)
        CHUNK_SIZE = 5000

        for i in range(0, len(uncached_list), CHUNK_SIZE):
            chunk = uncached_list[i : i + CHUNK_SIZE]
            result = await session.exec(select(Tracker.id, Tracker.url).where(Tracker.url.in_(chunk)))
            for row in result.all():
                self._tracker_cache[row[1]] = row[0]
                uncached.discard(row[1])

        # Bulk create missing trackers
        if uncached:
            to_create = [{"url": url, "status": "unknown"} for url in uncached]
            stmt = pg_insert(Tracker).values(to_create)
            stmt = stmt.on_conflict_do_nothing(index_elements=["url"])
            await session.execute(stmt)
            await session.commit()

            # Re-fetch created trackers
            for i in range(0, len(list(uncached)), CHUNK_SIZE):
                chunk = list(uncached)[i : i + CHUNK_SIZE]
                result = await session.exec(select(Tracker.id, Tracker.url).where(Tracker.url.in_(chunk)))
                for row in result.all():
                    self._tracker_cache[row[1]] = row[0]

        return self._tracker_cache

    async def _get_or_create_tracker(self, session: AsyncSession, url: str) -> int | None:
        """Get or create a single tracker record - delegates to bulk."""
        await self._bulk_get_or_create_trackers(session, {url})
        return self._tracker_cache.get(url)

    def _transform_torrent_stream(self, old_stream: OldTorrentStreams) -> dict[str, Any]:
        """
        Transform old torrent stream to new Stream + TorrentStream format.

        Quality attribute merge strategy:
        - For single-value fields: PREFER MongoDB data (user contributions), fallback to PTT
        - For multi-value fields: MERGE both PTT and MongoDB values (union)
        - PTT provides fresh parsing but MongoDB may have user-corrected values
        """
        torrent_name = old_stream.torrent_name or ""

        # Parse torrent name using PTT for fresh metadata extraction
        # Use True for second param to get full language names (not codes)
        parsed = {}
        if torrent_name:
            try:
                parsed = PTT.parse_title(torrent_name, True)
            except Exception:
                parsed = {}

        # =========================================================================
        # SINGLE-VALUE FIELDS: Prefer MongoDB (user contribution), fallback to PTT
        # User-edited data in MongoDB is more reliable than auto-parsed
        # =========================================================================

        # Resolution - prefer MongoDB, fallback to PTT
        resolution = old_stream.resolution or parsed.get("resolution")

        # Codec - prefer MongoDB, fallback to PTT
        codec = old_stream.codec or parsed.get("codec")

        # Quality (web-dl, bluray, etc.) - prefer MongoDB, fallback to PTT
        quality = old_stream.quality or parsed.get("quality")

        # Bit depth - prefer MongoDB, fallback to PTT
        bit_depth = getattr(old_stream, "bit_depth", None) or parsed.get("bit_depth")

        # =========================================================================
        # UPLOADER vs RELEASE_GROUP - These are SEPARATE fields!
        # =========================================================================

        # Uploader: User-provided value from MongoDB (could be Anonymous, username, or mistaken source name)
        # This is the original value that contributors entered - DO NOT override with PTT
        uploader = old_stream.uploader  # Keep as-is, don't fallback to PTT group

        # Release Group: From PTT parsing (e.g., AFG, RARBG, MeGusta)
        # This is auto-extracted from torrent name
        release_group = parsed.get("group")

        # =========================================================================
        # MULTI-VALUE FIELDS: MERGE both PTT and MongoDB values (union/dedupe)
        # This captures both auto-parsed and user-contributed data
        # =========================================================================

        # Audio formats - merge PTT and MongoDB
        ptt_audio = parsed.get("audio", [])
        mongo_audio = []
        if old_stream.audio:
            if isinstance(old_stream.audio, list):
                mongo_audio = old_stream.audio
            else:
                mongo_audio = [old_stream.audio]
        audio_formats_list = list(set(ptt_audio + mongo_audio))

        # Channels - merge PTT and MongoDB (PTT returns like ["5.1", "7.1"])
        ptt_channels = parsed.get("channels", [])
        mongo_channels = []
        if hasattr(old_stream, "channels") and old_stream.channels:
            if isinstance(old_stream.channels, list):
                mongo_channels = old_stream.channels
            elif isinstance(old_stream.channels, str):
                mongo_channels = [old_stream.channels]
        channels_list = list(set(ptt_channels + mongo_channels))

        # HDR formats - merge PTT and MongoDB, filter out SDR
        ptt_hdr = parsed.get("hdr", [])
        mongo_hdr = []
        if old_stream.hdr:
            if isinstance(old_stream.hdr, list):
                mongo_hdr = old_stream.hdr
            elif isinstance(old_stream.hdr, str):
                mongo_hdr = [old_stream.hdr]
        # Merge and filter out SDR values
        hdr_merged = list(set(ptt_hdr + mongo_hdr))
        hdr_formats_list = [h for h in hdr_merged if h and h.upper() not in ("SDR", "NONE", "")]

        # Languages - merge PTT and MongoDB
        ptt_languages = parsed.get("languages", [])
        mongo_languages = old_stream.languages or []
        languages = list(set(ptt_languages + mongo_languages))

        # =========================================================================
        # BOOLEAN FLAGS: Use PTT if available, these are usually not user-edited
        # =========================================================================
        is_proper = parsed.get("proper", False)
        is_repack = parsed.get("repack", False)
        is_extended = parsed.get("extended", False)
        is_remastered = parsed.get("remastered", False)
        is_complete = parsed.get("complete", False)
        is_upscaled = parsed.get("upscaled", False)
        is_dubbed = parsed.get("dubbed", False)
        is_subbed = parsed.get("subbed", False)

        return {
            "info_hash": old_stream.id.lower(),
            "meta_id": old_stream.meta_id,
            # Stream base fields
            "name": torrent_name,
            "size": old_stream.size or 0,
            "source": old_stream.source or "unknown",
            "resolution": resolution,
            "codec": codec,
            "quality": quality,
            "bit_depth": bit_depth,
            "uploader": uploader,  # User-provided from MongoDB
            "release_group": release_group,  # From PTT parsing
            "is_blocked": old_stream.is_blocked or False,
            "is_proper": is_proper,
            "is_repack": is_repack,
            "is_extended": is_extended,
            "is_remastered": is_remastered,
            "is_complete": is_complete,
            "is_upscaled": is_upscaled,
            "is_dubbed": is_dubbed,
            "is_subbed": is_subbed,
            # Use MongoDB created_at as torrent creation date
            "created_at": old_stream.created_at,
            "updated_at": old_stream.updated_at,
            # TorrentStream specific fields
            "total_size": old_stream.size or 0,
            "seeders": old_stream.seeders,
            "leechers": getattr(old_stream, "leechers", None),
            "torrent_type": old_stream.torrent_type or TorrentType.PUBLIC,
            "uploaded_at": old_stream.uploaded_at,
            "torrent_file": old_stream.torrent_file,
            "file_count": len(old_stream.known_file_details) if old_stream.known_file_details else 1,
            # For relationships (normalized into separate tables)
            "languages": languages,
            "audio_formats": audio_formats_list,  # Will create StreamAudioLink entries
            "channels": channels_list,  # Will create StreamChannelLink entries
            "hdr_formats": hdr_formats_list,  # Will create StreamHDRLink entries
            "announce_list": old_stream.announce_list or [],
            "episode_files": old_stream.episode_files or [],
            "file_details": old_stream.known_file_details or [],
        }

    async def _process_torrent_batch(self, session: AsyncSession, batch: list[OldTorrentStreams]):
        """Process a batch of torrent streams."""
        try:
            # Pre-fetch media IDs for the batch via MediaExternalID lookup
            external_ids = {stream.meta_id for stream in batch}
            media_map = await self._lookup_media_by_external_ids(session, external_ids)

            valid_streams = []
            transformed_data = []

            for old_stream in batch:
                if old_stream.meta_id not in media_map:
                    self.stats.failed += 1
                    self.stats.add_error(
                        "fk_violation",
                        f"torrent {old_stream.id}: meta_id '{old_stream.meta_id}' not found",
                    )
                    continue

                try:
                    data = self._transform_torrent_stream(old_stream)
                    data["media_id"], data["media_type"] = media_map[old_stream.meta_id]
                    transformed_data.append(data)
                    valid_streams.append(old_stream)
                except Exception as e:
                    logger.error(f"Error transforming stream {old_stream.id}: {e}")
                    self.stats.add_error("torrent_transform", f"{old_stream.id}: {str(e)}")
                    self.stats.failed += 1

            if not transformed_data:
                return

            # Create Stream + TorrentStream records
            created_streams = await self._create_torrent_streams_batch(session, transformed_data)

            # COMMIT streams first to ensure they're persisted before creating links
            # This prevents FK violations if something fails later
            await session.commit()

            # BULK collect all relationships for newly created streams
            all_media_links = []
            all_language_links = []
            all_audio_links = []
            all_channel_links = []
            all_hdr_links = []
            all_tracker_links = []
            all_file_records = []
            episode_file_tasks = []

            # Pre-collect all resources for bulk lookup
            all_languages = set()
            all_trackers = set()
            all_audio_formats = set()
            all_channels = set()
            all_hdr_formats = set()
            for data, stream_result in zip(transformed_data, created_streams):
                if stream_result and stream_result[2]:  # is_new
                    all_languages.update(data.get("languages", []))
                    all_trackers.update(data.get("announce_list", []))
                    all_audio_formats.update(data.get("audio_formats", []))
                    all_channels.update(data.get("channels", []))
                    all_hdr_formats.update(data.get("hdr_formats", []))

            # BULK lookup/create all resources in parallel-style (single query each)
            language_ids = await self.resource_tracker.get_resource_ids_bulk(session, "language", all_languages)
            audio_format_ids = await self.resource_tracker.get_resource_ids_bulk(
                session, "audio_format", all_audio_formats
            )
            channel_ids = await self.resource_tracker.get_resource_ids_bulk(session, "audio_channel", all_channels)
            hdr_format_ids = await self.resource_tracker.get_resource_ids_bulk(session, "hdr_format", all_hdr_formats)

            # Bulk create/get tracker IDs
            await self._bulk_get_or_create_trackers(session, all_trackers)

            # Now process each stream with cached lookups
            for data, stream_result in zip(transformed_data, created_streams):
                if not stream_result:
                    continue

                stream_id, torrent_id, is_new = stream_result

                if not is_new:
                    # Skip existing streams
                    continue

                # Collect StreamMediaLink - preserve original stream created_at
                all_media_links.append(
                    {
                        "stream_id": stream_id,
                        "media_id": data["media_id"],
                        "created_at": data["created_at"],  # Preserve original stream time
                    }
                )

                # Collect language links
                for lang_name in data.get("languages", []):
                    if lang_name in language_ids:
                        all_language_links.append(
                            {
                                "stream_id": stream_id,
                                "language_id": language_ids[lang_name],
                            }
                        )

                # Collect audio format links
                for audio_name in data.get("audio_formats", []):
                    if audio_name in audio_format_ids:
                        all_audio_links.append(
                            {
                                "stream_id": stream_id,
                                "audio_format_id": audio_format_ids[audio_name],
                            }
                        )

                # Collect audio channel links
                for channel_name in data.get("channels", []):
                    if channel_name in channel_ids:
                        all_channel_links.append(
                            {
                                "stream_id": stream_id,
                                "channel_id": channel_ids[channel_name],
                            }
                        )

                # Collect HDR format links
                for hdr_name in data.get("hdr_formats", []):
                    if hdr_name in hdr_format_ids:
                        all_hdr_links.append(
                            {
                                "stream_id": stream_id,
                                "hdr_format_id": hdr_format_ids[hdr_name],
                            }
                        )

                # Collect tracker links
                for tracker_url in data.get("announce_list", []):
                    if tracker_url in self._tracker_cache:
                        all_tracker_links.append(
                            {
                                "torrent_id": torrent_id,
                                "tracker_id": self._tracker_cache[tracker_url],
                            }
                        )

                # Collect StreamFile records from known_file_details (KnownFile Pydantic objects)
                # StreamFile is the new model that replaces TorrentFile
                if data.get("file_details"):
                    for idx, file_info in enumerate(data["file_details"]):
                        # file_info is a KnownFile Pydantic object, use attribute access
                        filename = getattr(file_info, "filename", "") or ""
                        size = getattr(file_info, "size", 0) or 0
                        file_type = FileType.VIDEO if is_video_file(filename) else FileType.OTHER
                        all_file_records.append(
                            {
                                "stream_id": stream_id,  # StreamFile links to Stream, not TorrentStream
                                "file_index": idx,
                                "filename": filename,
                                "size": size,
                                "file_type": file_type.value,
                            }
                        )

                # Collect episode file tasks for series
                # These will create FileMediaLink records (replaces StreamEpisodeFile)
                if data.get("media_type") == MediaType.SERIES and data.get("episode_files"):
                    episode_file_tasks.append((data["episode_files"], stream_id, data["media_id"]))

            # BULK insert all relationships in chunks
            # PostgreSQL limit is ~32,767 params - chunk based on columns per table
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            # Helper to chunk insert
            async def chunked_insert(model, records, chunk_size=5000):
                for i in range(0, len(records), chunk_size):
                    chunk = records[i : i + chunk_size]
                    stmt = pg_insert(model).values(chunk)
                    stmt = stmt.on_conflict_do_nothing()
                    await session.execute(stmt)

            # Link tables have 2-3 columns, use chunk_size=5000 (safe: 3*5000=15000 params)
            if all_media_links:
                await chunked_insert(StreamMediaLink, all_media_links, 3000)  # More columns

            if all_language_links:
                await chunked_insert(StreamLanguageLink, all_language_links, 5000)

            if all_audio_links:
                await chunked_insert(StreamAudioLink, all_audio_links, 5000)

            if all_channel_links:
                await chunked_insert(StreamChannelLink, all_channel_links, 5000)

            if all_hdr_links:
                await chunked_insert(StreamHDRLink, all_hdr_links, 5000)

            if all_tracker_links:
                await chunked_insert(TorrentTrackerLink, all_tracker_links, 5000)

            if all_file_records:
                # StreamFile has 5+ columns - use smaller chunks
                await chunked_insert(StreamFile, all_file_records, 1000)

            # Process episode files - creates FileMediaLink records
            # This replaces the old StreamEpisodeFile model with flexible file-to-media linking
            for episode_files, stream_id, media_id in episode_file_tasks:
                await self._migrate_episode_files(session, episode_files, stream_id, media_id)

            await session.commit()
            self.stats.successful += len(valid_streams)

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(batch)
            raise

    async def _create_torrent_streams_batch(
        self, session: AsyncSession, transformed_data: list[dict]
    ) -> list[tuple | None]:
        """
        Create Stream + TorrentStream records using TRUE BULK inserts.
        Returns list of (stream_id, torrent_id, is_new) tuples.

        HIGHLY OPTIMIZED: Uses raw SQL bulk inserts instead of ORM one-by-one.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        if not transformed_data:
            return []

        # Deduplicate within batch - keep first occurrence only
        seen_in_batch = {}
        deduplicated_data = []

        for idx, data in enumerate(transformed_data):
            info_hash = data["info_hash"]
            if info_hash not in seen_in_batch:
                seen_in_batch[info_hash] = idx
                deduplicated_data.append(data)

        # BULK check for existing info_hashes (chunked to avoid parameter limits)
        all_info_hashes = [d["info_hash"] for d in deduplicated_data]
        existing_map = {}

        CHUNK_SIZE = 5000
        for i in range(0, len(all_info_hashes), CHUNK_SIZE):
            chunk = all_info_hashes[i : i + CHUNK_SIZE]
            existing_result = await session.exec(
                select(TorrentStream.info_hash, TorrentStream.stream_id, TorrentStream.id).where(
                    TorrentStream.info_hash.in_(chunk)
                )
            )
            existing_map.update({row[0]: (row[1], row[2]) for row in existing_result.all()})

        # Separate into existing vs to-create
        to_create = []
        for data in deduplicated_data:
            if data["info_hash"] not in existing_map:
                to_create.append(data)

        # Pre-fill results array
        results = [None] * len(transformed_data)

        # Fill in existing streams
        for data in deduplicated_data:
            info_hash = data["info_hash"]
            orig_idx = seen_in_batch[info_hash]
            if info_hash in existing_map:
                stream_id, torrent_id = existing_map[info_hash]
                results[orig_idx] = (stream_id, torrent_id, False)

        if not to_create:
            return results

        # BULK INSERT streams using raw SQL for maximum performance
        stream_values = []
        for data in to_create:
            stream_values.append(
                {
                    "stream_type": StreamType.TORRENT.value,
                    "name": data["name"],
                    "source": data["source"],
                    "resolution": data["resolution"],
                    "codec": data["codec"],
                    "quality": data["quality"],
                    "bit_depth": data["bit_depth"],
                    "uploader": data["uploader"],  # User-provided from MongoDB
                    "release_group": data.get("release_group"),  # From PTT parsing
                    "is_remastered": data.get("is_remastered", False),
                    "is_upscaled": data.get("is_upscaled", False),
                    "is_proper": data.get("is_proper", False),
                    "is_repack": data.get("is_repack", False),
                    "is_extended": data.get("is_extended", False),
                    "is_complete": data.get("is_complete", False),
                    "is_dubbed": data.get("is_dubbed", False),
                    "is_subbed": data.get("is_subbed", False),
                    "is_blocked": data["is_blocked"],
                    "is_active": True,
                    "created_at": data["created_at"],
                    "updated_at": data["updated_at"],
                }
            )

        # Bulk insert Stream records in chunks and get IDs
        created_stream_ids = []
        STREAM_CHUNK = 500  # Smaller chunks for INSERT with RETURNING

        for i in range(0, len(stream_values), STREAM_CHUNK):
            chunk = stream_values[i : i + STREAM_CHUNK]
            stmt = pg_insert(Stream).values(chunk).returning(Stream.id)
            result = await session.execute(stmt)
            created_stream_ids.extend([row[0] for row in result.fetchall()])

        # Build torrent stream values with the new stream IDs
        torrent_values = []
        for idx, data in enumerate(to_create):
            torrent_values.append(
                {
                    "stream_id": created_stream_ids[idx],
                    "info_hash": data["info_hash"],
                    "total_size": data["total_size"],
                    "seeders": data["seeders"],
                    "leechers": data["leechers"],
                    "torrent_type": data["torrent_type"].value
                    if hasattr(data["torrent_type"], "value")
                    else data["torrent_type"],
                    "uploaded_at": data["uploaded_at"],
                    "torrent_file": data["torrent_file"],
                    "file_count": data["file_count"],
                }
            )

        # Bulk insert TorrentStream records
        created_torrent_ids = []
        for i in range(0, len(torrent_values), STREAM_CHUNK):
            chunk = torrent_values[i : i + STREAM_CHUNK]
            stmt = pg_insert(TorrentStream).values(chunk).returning(TorrentStream.id)
            result = await session.execute(stmt)
            created_torrent_ids.extend([row[0] for row in result.fetchall()])

        # Map back to results
        created_map = {}
        for idx, data in enumerate(to_create):
            info_hash = data["info_hash"]
            stream_id = created_stream_ids[idx]
            torrent_id = created_torrent_ids[idx]
            created_map[info_hash] = (stream_id, torrent_id)

        # Fill in newly created streams
        for data in to_create:
            info_hash = data["info_hash"]
            orig_idx = seen_in_batch[info_hash]
            stream_id, torrent_id = created_map[info_hash]
            results[orig_idx] = (stream_id, torrent_id, True)

        return results

    async def _migrate_episode_files(self, session: AsyncSession, episode_files: list, stream_id: int, media_id: int):
        """
        Migrate episode files to StreamFile + FileMediaLink.

        This replaces the old StreamEpisodeFile with a two-table approach:
        - StreamFile: Pure file structure (filename, size, index)
        - FileMediaLink: Flexible linking to media with season/episode context

        This allows:
        - 1 file → 1 episode (normal case)
        - 1 file → multiple episodes (combined episodes like S01E01-E03.mkv)
        - Multiple files → 1 episode (split files)
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        # Deduplicate episodes by season/episode
        deduplicated = {}
        for episode in episode_files:
            filename = getattr(episode, "filename", None)
            if filename and not is_video_file(filename):
                continue

            key = (
                getattr(episode, "season_number", None),
                getattr(episode, "episode_number", None),
            )
            if key in deduplicated:
                existing = deduplicated[key]
                if self._is_better_episode(episode, existing):
                    deduplicated[key] = episode
            else:
                deduplicated[key] = episode

        if not deduplicated:
            return

        # First, create StreamFile records for each unique file
        # Group by file_index to avoid duplicates
        file_index_map = {}  # file_index -> episode
        for (season_num, episode_num), episode in deduplicated.items():
            if season_num is None or episode_num is None:
                continue
            file_index = getattr(episode, "file_index", None)
            if file_index is not None:
                file_index_map[file_index] = episode

        # Create StreamFile records
        file_records = []
        for file_index, episode in file_index_map.items():
            filename = getattr(episode, "filename", None) or f"file_{file_index}"
            size = getattr(episode, "size", None)
            file_type = FileType.VIDEO if is_video_file(filename) else FileType.OTHER

            file_records.append(
                {
                    "stream_id": stream_id,
                    "file_index": file_index,
                    "filename": filename,
                    "size": size,
                    "file_type": file_type.value,
                }
            )

        # Bulk insert StreamFile records
        if file_records:
            stmt = pg_insert(StreamFile).values(file_records)
            stmt = stmt.on_conflict_do_nothing(index_elements=["stream_id", "file_index"])
            await session.execute(stmt)
            await session.flush()

        # Query back the created StreamFile IDs
        file_id_map = {}  # file_index -> stream_file.id
        if file_index_map:
            stmt = select(StreamFile.id, StreamFile.file_index).where(
                StreamFile.stream_id == stream_id,
                StreamFile.file_index.in_(list(file_index_map.keys())),
            )
            result = await session.exec(stmt)
            for row in result.all():
                file_id_map[row[1]] = row[0]

        # Create FileMediaLink records for each episode
        link_records = []
        for (season_num, episode_num), episode in deduplicated.items():
            if season_num is None or episode_num is None:
                continue

            file_index = getattr(episode, "file_index", None)
            file_id = file_id_map.get(file_index) if file_index is not None else None

            if file_id:
                link_records.append(
                    {
                        "file_id": file_id,
                        "media_id": media_id,
                        "season_number": season_num,
                        "episode_number": episode_num,
                        "link_source": LinkSource.TORRENT_METADATA.value,
                        "confidence": 1.0,
                    }
                )

        # Bulk insert FileMediaLink records
        if link_records:
            stmt = pg_insert(FileMediaLink).values(link_records)
            stmt = stmt.on_conflict_do_nothing()
            await session.execute(stmt)

    def _is_better_episode(self, new_ep, existing_ep) -> bool:
        """Determine if the new episode data is better than the existing one."""

        def score_episode(ep):
            score = 0
            if getattr(ep, "filename", None):
                score += 1
            if getattr(ep, "file_index", None) is not None:
                score += 1
            if getattr(ep, "size", None):
                score += 1
            return score

        new_score = score_episode(new_ep)
        existing_score = score_episode(existing_ep)

        if new_score == existing_score:
            return bool(getattr(new_ep, "filename", None) and getattr(new_ep, "size", None))

        return new_score > existing_score

    async def _process_tv_stream_batch(self, session: AsyncSession, batch: list[OldTVStreams]):
        """Process a batch of TV streams as HTTPStream records."""
        try:
            # Pre-fetch media IDs for the batch via MediaExternalID lookup
            external_ids = {stream.meta_id for stream in batch if stream.meta_id}
            # For TV streams, we only need media_id, not type
            full_map = await self._lookup_media_by_external_ids(session, external_ids)
            media_map = {ext_id: info[0] for ext_id, info in full_map.items()}

            valid_streams = []
            transformed_data = []

            for old_stream in batch:
                if old_stream.meta_id not in media_map:
                    self.stats.failed += 1
                    self.stats.add_error(
                        "fk_violation",
                        f"tv_stream url={old_stream.url[:50] if old_stream.url else 'N/A'}...: "
                        f"meta_id '{old_stream.meta_id}' not found",
                    )
                    continue

                try:
                    data = self._transform_tv_stream(old_stream)
                    data["media_id"] = media_map[old_stream.meta_id]
                    transformed_data.append(data)
                    valid_streams.append(old_stream)
                except Exception as e:
                    logger.error(f"Error transforming TV stream: {e}")
                    self.stats.add_error("tv_stream_transform", str(e))
                    self.stats.failed += 1

            if not transformed_data:
                return

            # Deduplicate by URL
            seen_urls = {}
            deduped_data = []
            deduped_streams = []
            for data, old_stream in zip(transformed_data, valid_streams):
                url = data.get("url")
                if url and url in seen_urls:
                    idx = seen_urls[url]
                    deduped_data[idx] = data
                    deduped_streams[idx] = old_stream
                else:
                    if url:
                        seen_urls[url] = len(deduped_data)
                    deduped_data.append(data)
                    deduped_streams.append(old_stream)

            # Create Stream + HTTPStream/YouTubeStream records
            for data, old_stream in zip(deduped_data, deduped_streams):
                try:
                    url = data.get("url")
                    yt_id = data.get("yt_id")
                    is_youtube = self._is_youtube_url(url) or yt_id

                    # Determine stream type
                    stream_type = StreamType.YOUTUBE if is_youtube else StreamType.HTTP

                    # Create base Stream record
                    stream = Stream(
                        stream_type=stream_type,
                        name=data["name"],
                        source=data["source"],
                        is_active=data["is_working"],
                    )
                    session.add(stream)
                    await session.flush()

                    if is_youtube:
                        # Create YouTubeStream record
                        video_id = yt_id or self._extract_youtube_video_id(url)
                        if video_id:
                            youtube_stream = YouTubeStream(
                                stream_id=stream.id,
                                video_id=video_id,
                                is_live=True,  # TV streams are typically live
                            )
                            session.add(youtube_stream)
                        else:
                            # Fallback to HTTPStream if we can't extract video ID
                            logger.warning(f"Could not extract YouTube video ID from: {url}")
                            http_stream = HTTPStream(
                                stream_id=stream.id,
                                url=url,
                                format="youtube",
                                behavior_hints=data.get("behavior_hints"),
                                drm_key_id=data.get("drm_key_id"),
                                drm_key=data.get("drm_key"),
                            )
                            session.add(http_stream)
                    else:
                        # Create HTTPStream record
                        http_stream = HTTPStream(
                            stream_id=stream.id,
                            url=url,
                            format=self._detect_stream_format(url),
                            behavior_hints=data.get("behavior_hints"),
                            drm_key_id=data.get("drm_key_id"),
                            drm_key=data.get("drm_key"),
                        )
                        session.add(http_stream)

                    # Create StreamMediaLink
                    link = StreamMediaLink(
                        stream_id=stream.id,
                        media_id=data["media_id"],
                    )
                    session.add(link)

                    # Note: Namespace is no longer used - streams are now user-based
                    # through the created_by_user_id field on Media

                except Exception as e:
                    logger.error(f"Error creating HTTP/YouTube stream: {e}")
                    self.stats.failed += 1

            await session.commit()
            self.stats.successful += len(deduped_data)

        except Exception as e:
            logger.exception(f"Batch processing failed: {str(e)}")
            await session.rollback()
            self.stats.failed += len(batch)
            raise

    def _transform_tv_stream(self, old_stream: OldTVStreams) -> dict[str, Any]:
        """Transform old TV stream to new Stream + HTTPStream format."""
        return {
            "name": old_stream.name or "",
            "url": old_stream.url,
            "yt_id": old_stream.ytId,
            "external_url": old_stream.externalUrl,
            "source": old_stream.source or "unknown",
            "is_working": old_stream.is_working if old_stream.is_working is not None else True,
            "country": old_stream.country,
            "drm_key_id": old_stream.drm_key_id,
            "drm_key": old_stream.drm_key,
            "behavior_hints": old_stream.behaviorHints,
        }

    def _detect_stream_format(self, url: str | None) -> str | None:
        """Detect stream format from URL."""
        if not url:
            return None
        url_lower = url.lower()
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            return "youtube"
        elif ".m3u8" in url_lower:
            return "hls"
        elif ".mpd" in url_lower:
            return "dash"
        elif ".mp4" in url_lower:
            return "mp4"
        elif ".mkv" in url_lower:
            return "mkv"
        elif ".webm" in url_lower:
            return "webm"
        return None

    def _is_youtube_url(self, url: str | None) -> bool:
        """Check if URL is a YouTube URL."""
        if not url:
            return False
        url_lower = url.lower()
        return "youtube.com" in url_lower or "youtu.be" in url_lower

    def _extract_youtube_video_id(self, url: str) -> str | None:
        """Extract YouTube video ID from URL."""
        import re

        patterns = [
            r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
            r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
            r"youtube\.com/v/([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
