"""
Stream CRUD operations.

Handles unified Stream architecture: Stream, TorrentStream, HTTPStream,
YouTubeStream, UsenetStream, TelegramStream, ExternalLinkStream.

New in v5:
- StreamFile: Pure file structure (replaces TorrentFile)
- FileMediaLink: Flexible file-to-media linking (replaces StreamEpisodeFile)
"""

import logging
from collections.abc import Sequence
from datetime import datetime

import pytz
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.sql.functions import coalesce
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.crud.media import decrement_stream_count, increment_stream_count
from db.crud.stream_services import invalidate_media_stream_cache
from db.enums import TorrentType
from db.models import (
    AceStreamStream,
    AudioChannel,
    AudioFormat,
    ExternalLinkStream,
    FileMediaLink,
    FileType,
    HDRFormat,
    HTTPStream,
    # Reference
    Language,
    LinkSource,
    # Base stream
    Stream,
    StreamAudioLink,
    StreamChannelLink,
    # File structure and linking (NEW in v5)
    StreamFile,
    StreamHDRLink,
    StreamLanguageLink,
    # Links
    StreamMediaLink,
    StreamType,
    # Telegram
    TelegramStream,
    TelegramUserForward,
    # Type-specific streams
    TorrentStream,
    TorrentTrackerLink,
    # Tracker
    Tracker,
    TrackerStatus,
    # Usenet
    UsenetStream,
    User,
    YouTubeStream,
)

logger = logging.getLogger(__name__)


# =============================================================================
# BASE STREAM CRUD
# =============================================================================


async def get_stream_by_id(
    session: AsyncSession,
    stream_id: int,
    *,
    load_media: bool = False,
    load_languages: bool = False,
) -> Stream | None:
    """Get stream by ID with optional eager loading."""
    query = select(Stream).where(Stream.id == stream_id)

    options = []
    if load_media:
        options.append(selectinload(Stream.media_links))
    if load_languages:
        options.append(selectinload(Stream.languages))

    if options:
        query = query.options(*options)

    result = await session.exec(query)
    return result.first()


async def get_streams_for_media(
    session: AsyncSession,
    media_id: int,
    stream_type: StreamType | None = None,
    *,
    only_working: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[Stream]:
    """Get all streams linked to a media entry."""
    query = select(Stream).join(StreamMediaLink).where(StreamMediaLink.media_id == media_id)

    if stream_type:
        query = query.where(Stream.stream_type == stream_type)

    if only_working:
        query = query.where(Stream.is_active.is_(True), Stream.is_blocked.is_(False))

    query = query.order_by(Stream.created_at.desc())
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


async def delete_stream(
    session: AsyncSession,
    stream_id: int,
) -> bool:
    """Delete a stream and all related data."""
    # Get media links before deletion for count update
    links_query = select(StreamMediaLink.media_id).where(StreamMediaLink.stream_id == stream_id)
    links_result = await session.exec(links_query)
    media_ids = links_result.all()

    # Delete the stream (cascades to links)
    result = await session.exec(sa_delete(Stream).where(Stream.id == stream_id))

    # Decrement stream counts
    for media_id in media_ids:
        await decrement_stream_count(session, media_id)

    await session.flush()
    return result.rowcount > 0


async def link_stream_to_media(
    session: AsyncSession,
    stream_id: int,
    media_id: int,
    *,
    file_index: int | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> StreamMediaLink:
    """Link a stream to a media entry."""
    link = StreamMediaLink(
        stream_id=stream_id,
        media_id=media_id,
        file_index=file_index,
        season=season,
        episode=episode,
    )
    session.add(link)
    await increment_stream_count(session, media_id)
    await session.flush()
    # Invalidate stream cache for this media so next request fetches fresh data
    await invalidate_media_stream_cache(media_id)
    return link


async def unlink_stream_from_media(
    session: AsyncSession,
    stream_id: int,
    media_id: int,
) -> bool:
    """Remove link between stream and media."""
    result = await session.exec(
        sa_delete(StreamMediaLink).where(
            StreamMediaLink.stream_id == stream_id,
            StreamMediaLink.media_id == media_id,
        )
    )
    if result.rowcount > 0:
        await decrement_stream_count(session, media_id)
    await session.flush()
    # Invalidate stream cache for this media
    await invalidate_media_stream_cache(media_id)
    return result.rowcount > 0


# =============================================================================
# TORRENT STREAM CRUD
# =============================================================================


async def get_torrent_by_info_hash(
    session: AsyncSession,
    info_hash: str,
    *,
    load_files: bool = False,
    load_trackers: bool = False,
    load_languages: bool = False,
) -> TorrentStream | None:
    """Get torrent stream by info hash."""
    query = select(TorrentStream).join(Stream).where(TorrentStream.info_hash == info_hash.lower())

    options = []
    if load_files:
        options.append(selectinload(TorrentStream.files))
    if load_trackers:
        options.append(selectinload(TorrentStream.trackers))
    if load_languages:
        options.append(joinedload(TorrentStream.stream).selectinload(Stream.languages))

    if options:
        query = query.options(*options)

    result = await session.exec(query)
    return result.first()


async def create_torrent_stream(
    session: AsyncSession,
    *,
    info_hash: str,
    name: str,
    size: int,
    source: str,
    media_id: int,
    torrent_type: TorrentType = TorrentType.PUBLIC,
    # Single-value quality attributes
    resolution: str | None = None,
    codec: str | None = None,
    quality: str | None = None,
    bit_depth: str | None = None,
    uploader: str | None = None,
    # Boolean flags
    is_remastered: bool = False,
    is_upscaled: bool = False,
    is_proper: bool = False,
    is_repack: bool = False,
    is_extended: bool = False,
    is_complete: bool = False,
    is_dubbed: bool = False,
    is_subbed: bool = False,
    # TorrentStream specific
    seeders: int = 0,
    # Relationships (IDs for direct linking)
    languages: list[Language] | None = None,
    audio_formats: list["AudioFormat"] | None = None,
    channels: list["AudioChannel"] | None = None,
    hdr_formats: list["HDRFormat"] | None = None,
    trackers: list[Tracker] | None = None,
    files: list[dict] | None = None,
    **kwargs,
) -> TorrentStream:
    """Create a new torrent stream with normalized quality attributes."""
    # Create base stream
    stream = Stream(
        stream_type=StreamType.TORRENT,
        name=name,
        source=source,
        resolution=resolution,
        codec=codec,
        quality=quality,
        bit_depth=bit_depth,
        uploader=uploader,
        is_remastered=is_remastered,
        is_upscaled=is_upscaled,
        is_proper=is_proper,
        is_repack=is_repack,
        is_extended=is_extended,
        is_complete=is_complete,
        is_dubbed=is_dubbed,
        is_subbed=is_subbed,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    # Create torrent-specific data
    torrent = TorrentStream(
        stream_id=stream.id,
        info_hash=info_hash.lower(),
        torrent_type=torrent_type,
        seeders=seeders,
        total_size=size,
    )
    session.add(torrent)

    # Link to media
    await link_stream_to_media(session, stream.id, media_id)

    # Add languages (many-to-many via StreamLanguageLink)
    if languages:
        for lang in languages:
            link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
            session.add(link)

    # Add audio formats (many-to-many via StreamAudioLink)
    if audio_formats:
        for af in audio_formats:
            link = StreamAudioLink(stream_id=stream.id, audio_format_id=af.id)
            session.add(link)

    # Add channels (many-to-many via StreamChannelLink)
    if channels:
        for ch in channels:
            link = StreamChannelLink(stream_id=stream.id, channel_id=ch.id)
            session.add(link)

    # Add HDR formats (many-to-many via StreamHDRLink)
    if hdr_formats:
        for hf in hdr_formats:
            link = StreamHDRLink(stream_id=stream.id, hdr_format_id=hf.id)
            session.add(link)

    # Add trackers
    if trackers:
        for tracker in trackers:
            link = TorrentTrackerLink(torrent_id=torrent.stream_id, tracker_id=tracker.id)
            session.add(link)

    # Add files (StreamFile - links to Stream, not TorrentStream)
    if files:
        for file_data in files:
            stream_file = StreamFile(
                stream_id=stream.id,
                file_index=file_data.get("file_index", 0),
                filename=file_data.get("filename", ""),
                size=file_data.get("size", 0),
                file_type=FileType.VIDEO,  # Default to video, can be updated
            )
            session.add(stream_file)

    await session.flush()
    return torrent


async def update_torrent_seeders(
    session: AsyncSession,
    info_hash: str,
    seeders: int,
) -> bool:
    """Update seeders count for a torrent."""
    result = await session.exec(
        sa_update(TorrentStream).where(TorrentStream.info_hash == info_hash.lower()).values(seeders=seeders)
    )
    await session.flush()
    return result.rowcount > 0


async def update_torrent_stream(
    session: AsyncSession,
    info_hash: str,
    updates: dict,
) -> bool:
    """Update a torrent stream by info hash.

    Updates both the base Stream and TorrentStream records.
    """
    torrent = await get_torrent_by_info_hash(session, info_hash)
    if not torrent:
        return False

    # Separate stream-level and torrent-level updates
    # Note: Stream doesn't have 'size' - that's on TorrentStream.total_size or StreamFile.size
    stream_fields = {
        "name",
        "source",
        "resolution",
        "codec",
        "quality",
        "bit_depth",
        "uploader",
        "release_group",
        "is_blocked",
        "is_active",
        "updated_at",
        "is_remastered",
        "is_upscaled",
        "is_proper",
        "is_repack",
        "is_extended",
        "is_complete",
        "is_dubbed",
        "is_subbed",
    }
    torrent_fields = {"seeders", "leechers", "torrent_type", "total_size"}

    stream_updates = {k: v for k, v in updates.items() if k in stream_fields}
    torrent_updates = {k: v for k, v in updates.items() if k in torrent_fields}

    # Update Stream
    if stream_updates:
        await session.exec(sa_update(Stream).where(Stream.id == torrent.stream_id).values(**stream_updates))

    # Update TorrentStream
    if torrent_updates:
        await session.exec(
            sa_update(TorrentStream).where(TorrentStream.info_hash == info_hash.lower()).values(**torrent_updates)
        )

    await session.flush()
    return True


async def update_stream_files(
    session: AsyncSession,
    info_hash: str,
    files: list,
) -> bool:
    """Update stream files for a torrent.

    v5 schema: Creates/updates StreamFile and FileMediaLink records.

    Args:
        session: Database session
        info_hash: Torrent info_hash
        files: List of StreamFileData objects (or dicts with file info)
    """
    torrent = await get_torrent_by_info_hash(session, info_hash)
    if not torrent:
        return False

    stream_id = torrent.stream_id

    # Get the media_id from existing StreamMediaLink
    media_link = await session.exec(select(StreamMediaLink).where(StreamMediaLink.stream_id == stream_id))
    media_link = media_link.first()
    media_id = media_link.media_id if media_link else None

    # Delete existing StreamFile records (CASCADE will delete FileMediaLink)
    await session.exec(sa_delete(StreamFile).where(StreamFile.stream_id == stream_id))

    # Create new StreamFile records
    for file_data in files:
        if isinstance(file_data, dict):
            file_index = file_data.get("file_index", 0)
            filename = file_data.get("filename", "")
            size = file_data.get("size", 0)
            file_type_str = file_data.get("file_type", "video")
            season_number = file_data.get("season_number")
            episode_number = file_data.get("episode_number")
            episode_end = file_data.get("episode_end")
        else:
            # StreamFileData object
            file_index = file_data.file_index
            filename = file_data.filename
            size = file_data.size
            file_type_str = file_data.file_type
            season_number = file_data.season_number
            episode_number = file_data.episode_number
            episode_end = getattr(file_data, "episode_end", None)

        # Determine FileType enum value
        try:
            file_type = FileType(file_type_str)
        except ValueError:
            file_type = FileType.VIDEO

        stream_file = StreamFile(
            stream_id=stream_id,
            file_index=file_index,
            filename=filename,
            size=size,
            file_type=file_type,
        )
        session.add(stream_file)
        await session.flush()

        # Create FileMediaLink if we have media and episode info
        if media_id and (season_number is not None or episode_number is not None):
            file_link = FileMediaLink(
                file_id=stream_file.id,
                media_id=media_id,
                season_number=season_number or 1,
                episode_number=episode_number or 1,
                episode_end=episode_end,
                link_source=LinkSource.PTT_PARSER,
                confidence=1.0,
            )
            session.add(file_link)
        elif media_id:
            # For movies - create primary link without episode info
            file_link = FileMediaLink(
                file_id=stream_file.id,
                media_id=media_id,
                is_primary=True,
                link_source=LinkSource.PTT_PARSER,
                confidence=1.0,
            )
            session.add(file_link)

    await session.flush()
    return True


async def delete_torrent_by_info_hash(
    session: AsyncSession,
    info_hash: str,
) -> bool:
    """Delete torrent stream by info hash."""
    torrent = await get_torrent_by_info_hash(session, info_hash)
    if not torrent:
        return False
    return await delete_stream(session, torrent.stream_id)


# =============================================================================
# USENET STREAM CRUD
# =============================================================================


async def get_usenet_stream_by_guid(
    session: AsyncSession,
    nzb_guid: str,
    *,
    load_relations: bool = False,
) -> UsenetStream | None:
    """Get Usenet stream by NZB GUID with optional relationship loading."""
    query = select(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid)

    if load_relations:
        query = query.options(
            selectinload(UsenetStream.stream).selectinload(Stream.languages),
            selectinload(UsenetStream.stream).selectinload(Stream.audio_formats),
            selectinload(UsenetStream.stream).selectinload(Stream.channels),
            selectinload(UsenetStream.stream).selectinload(Stream.hdr_formats),
            selectinload(UsenetStream.stream).selectinload(Stream.files).selectinload(StreamFile.media_links),
        )

    result = await session.exec(query)
    return result.first()


async def create_usenet_stream(
    session: AsyncSession,
    *,
    nzb_guid: str,
    name: str,
    size: int,
    indexer: str,
    media_id: int,
    nzb_url: str | None = None,
    group_name: str | None = None,
    uploader: str | None = None,
    files_count: int | None = None,
    parts_count: int | None = None,
    posted_at: datetime | None = None,
    is_passworded: bool = False,
    # Quality attributes
    resolution: str | None = None,
    codec: str | None = None,
    quality: str | None = None,
    bit_depth: str | None = None,
    # Boolean flags
    is_remastered: bool = False,
    is_upscaled: bool = False,
    is_proper: bool = False,
    is_repack: bool = False,
    is_extended: bool = False,
    is_complete: bool = False,
    is_dubbed: bool = False,
    is_subbed: bool = False,
    # Relationships
    languages: list[Language] | None = None,
    audio_formats: list[AudioFormat] | None = None,
    channels: list[AudioChannel] | None = None,
    hdr_formats: list[HDRFormat] | None = None,
    files: list[dict] | None = None,
    uploader_user_id: int | None = None,
    is_public: bool = True,
    **kwargs,
) -> UsenetStream:
    """Create a new Usenet/NZB stream.

    Args:
        nzb_guid: Unique NZB identifier from indexer
        name: Display name
        size: Total size in bytes
        indexer: Indexer source name
        media_id: Associated media ID
        nzb_url: URL to NZB file
        group_name: Usenet group name
        uploader: Usenet poster/uploader name
        files_count: Number of files in NZB
        parts_count: Number of parts in NZB
        posted_at: When the NZB was posted
        is_passworded: Whether content is password protected
        ... quality and relationship args ...
    """
    # Create base stream
    stream = Stream(
        stream_type=StreamType.USENET,
        name=name,
        source=indexer,
        total_size=size,
        resolution=resolution,
        codec=codec,
        quality=quality,
        bit_depth=bit_depth,
        is_remastered=is_remastered,
        is_upscaled=is_upscaled,
        is_proper=is_proper,
        is_repack=is_repack,
        is_extended=is_extended,
        is_complete=is_complete,
        is_dubbed=is_dubbed,
        is_subbed=is_subbed,
        uploader_user_id=uploader_user_id,
        is_public=is_public,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    # Create Usenet-specific data
    usenet_stream = UsenetStream(
        stream_id=stream.id,
        nzb_guid=nzb_guid,
        nzb_url=nzb_url,
        size=size,
        indexer=indexer,
        group_name=group_name,
        uploader=uploader,
        files_count=files_count,
        parts_count=parts_count,
        posted_at=posted_at,
        is_passworded=is_passworded,
    )
    session.add(usenet_stream)

    # Link to media
    await link_stream_to_media(session, stream.id, media_id)

    # Add relationships
    if languages:
        for lang in languages:
            session.add(StreamLanguageLink(stream_id=stream.id, language_id=lang.id))

    if audio_formats:
        for af in audio_formats:
            session.add(StreamAudioLink(stream_id=stream.id, audio_format_id=af.id))

    if channels:
        for ch in channels:
            session.add(StreamChannelLink(stream_id=stream.id, channel_id=ch.id))

    if hdr_formats:
        for hdr in hdr_formats:
            session.add(StreamHDRLink(stream_id=stream.id, hdr_format_id=hdr.id))

    # Add files if provided
    if files:
        for file_data in files:
            stream_file = StreamFile(
                stream_id=stream.id,
                file_index=file_data.get("file_index", 0),
                filename=file_data.get("filename", ""),
                size=file_data.get("size", 0),
                file_type=file_data.get("file_type", FileType.VIDEO),
            )
            session.add(stream_file)

    await session.flush()
    return usenet_stream


async def update_usenet_stream(
    session: AsyncSession,
    nzb_guid: str,
    updates: dict,
) -> bool:
    """Update Usenet stream by NZB GUID."""
    usenet_stream = await get_usenet_stream_by_guid(session, nzb_guid)
    if not usenet_stream:
        return False

    # Separate stream updates from usenet-specific updates
    stream_fields = {
        "name",
        "resolution",
        "codec",
        "quality",
        "bit_depth",
        "is_remastered",
        "is_upscaled",
        "is_proper",
        "is_repack",
        "is_extended",
        "is_complete",
        "is_dubbed",
        "is_subbed",
        "is_active",
        "is_blocked",
        "updated_at",
    }
    usenet_fields = {
        "nzb_url",
        "size",
        "indexer",
        "group_name",
        "uploader",
        "files_count",
        "parts_count",
        "posted_at",
        "is_passworded",
    }

    stream_updates = {k: v for k, v in updates.items() if k in stream_fields}
    usenet_updates = {k: v for k, v in updates.items() if k in usenet_fields}

    if stream_updates:
        await session.exec(sa_update(Stream).where(Stream.id == usenet_stream.stream_id).values(**stream_updates))

    if usenet_updates:
        await session.exec(sa_update(UsenetStream).where(UsenetStream.nzb_guid == nzb_guid).values(**usenet_updates))

    await session.flush()
    return True


async def delete_usenet_stream_by_guid(
    session: AsyncSession,
    nzb_guid: str,
) -> bool:
    """Delete Usenet stream by NZB GUID."""
    usenet = await get_usenet_stream_by_guid(session, nzb_guid)
    if not usenet:
        return False
    return await delete_stream(session, usenet.stream_id)


async def get_usenet_streams_for_media(
    session: AsyncSession,
    media_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[UsenetStream]:
    """Get all Usenet streams linked to a media entry."""
    query = (
        select(UsenetStream)
        .join(Stream, Stream.id == UsenetStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(
            StreamMediaLink.media_id == media_id,
            Stream.is_active.is_(True),
            Stream.is_blocked.is_(False),
        )
        .order_by(UsenetStream.size.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.exec(query)
    return list(result.all())


# =============================================================================
# HTTP STREAM CRUD
# =============================================================================


async def get_http_stream_by_url_for_media(
    session: AsyncSession,
    url: str,
    media_id: int,
) -> HTTPStream | None:
    """Check if an HTTP stream with this URL already exists for the given media.

    Used for deduplication when importing M3U playlists - prevents adding
    the same stream URL multiple times to the same channel.
    """
    query = (
        select(HTTPStream)
        .join(Stream, Stream.id == HTTPStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(
            HTTPStream.url == url,
            StreamMediaLink.media_id == media_id,
        )
    )
    result = await session.exec(query)
    return result.first()


async def create_http_stream(
    session: AsyncSession,
    *,
    url: str,
    name: str,
    media_id: int,
    source: str = "direct",
    size: int | None = None,
    format: str | None = None,
    behavior_hints: dict | None = None,
    drm_key_id: str | None = None,
    drm_key: str | None = None,
    extractor_name: str | None = None,
    uploader_user_id: int | None = None,
    is_public: bool = True,
    **kwargs,
) -> HTTPStream:
    """Create a new HTTP/direct stream.

    Args:
        url: Stream URL
        name: Display name
        media_id: Associated media ID
        source: Source identifier
        size: File size in bytes
        format: Stream format (mp4, mkv, hls, dash)
        behavior_hints: Stremio behavior hints (contains headers, proxy settings)
        drm_key_id: DRM key ID for encrypted streams
        drm_key: DRM decryption key
        extractor_name: MediaFlow extractor name (e.g., doodstream, filemoon)
        uploader_user_id: User who uploaded this stream
        is_public: Whether stream is publicly visible
        **kwargs: Additional Stream fields
    """
    stream = Stream(
        stream_type=StreamType.HTTP,
        name=name,
        source=source,
        uploader_user_id=uploader_user_id,
        is_public=is_public,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    http_stream = HTTPStream(
        stream_id=stream.id,
        url=url,
        size=size,
        format=format,
        behavior_hints=behavior_hints,
        drm_key_id=drm_key_id,
        drm_key=drm_key,
        extractor_name=extractor_name,
    )
    session.add(http_stream)

    await link_stream_to_media(session, stream.id, media_id)
    await session.flush()
    return http_stream


# =============================================================================
# YOUTUBE STREAM CRUD
# =============================================================================


async def create_youtube_stream(
    session: AsyncSession,
    *,
    video_id: str,
    name: str,
    media_id: int,
    source: str = "youtube",
    is_live: bool = False,
    **kwargs,
) -> YouTubeStream:
    """Create a new YouTube stream."""
    stream = Stream(
        stream_type=StreamType.YOUTUBE,
        name=name,
        source=source,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    yt_stream = YouTubeStream(
        stream_id=stream.id,
        video_id=video_id,
        is_live=is_live,
    )
    session.add(yt_stream)

    await link_stream_to_media(session, stream.id, media_id)
    await session.flush()
    return yt_stream


# =============================================================================
# EXTERNAL LINK STREAM CRUD
# =============================================================================


async def create_external_link_stream(
    session: AsyncSession,
    *,
    url: str,
    name: str,
    media_id: int,
    service_name: str | None = None,
    source: str = "external",
    **kwargs,
) -> ExternalLinkStream:
    """Create a new external link stream (Netflix, Prime, etc.)."""
    stream = Stream(
        stream_type=StreamType.EXTERNAL,
        name=name,
        source=source,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    ext_stream = ExternalLinkStream(
        stream_id=stream.id,
        url=url,
        service_name=service_name,
    )
    session.add(ext_stream)

    await link_stream_to_media(session, stream.id, media_id)
    await session.flush()
    return ext_stream


# =============================================================================
# ACESTREAM STREAM CRUD
# =============================================================================


async def get_acestream_by_content_id(
    session: AsyncSession,
    content_id: str,
) -> AceStreamStream | None:
    """Get AceStream by content ID."""
    query = select(AceStreamStream).where(AceStreamStream.content_id == content_id)
    result = await session.exec(query)
    return result.first()


async def get_acestream_by_info_hash(
    session: AsyncSession,
    info_hash: str,
) -> AceStreamStream | None:
    """Get AceStream by info hash."""
    query = select(AceStreamStream).where(AceStreamStream.info_hash == info_hash)
    result = await session.exec(query)
    return result.first()


async def get_acestream_by_identifier(
    session: AsyncSession,
    content_id: str | None = None,
    info_hash: str | None = None,
) -> AceStreamStream | None:
    """Get AceStream by either content_id or info_hash."""
    if content_id:
        result = await get_acestream_by_content_id(session, content_id)
        if result:
            return result
    if info_hash:
        return await get_acestream_by_info_hash(session, info_hash)
    return None


async def create_acestream_stream(
    session: AsyncSession,
    *,
    name: str,
    media_id: int,
    content_id: str | None = None,
    info_hash: str | None = None,
    source: str = "acestream",
    uploader_user_id: int | None = None,
    is_public: bool = True,
    **kwargs,
) -> AceStreamStream:
    """Create a new AceStream stream.

    At least one of content_id or info_hash must be provided.

    Args:
        name: Display name
        media_id: Associated media ID
        content_id: AceStream content ID (40-char hex)
        info_hash: Torrent info hash (40-char hex)
        source: Source identifier
        uploader_user_id: User who uploaded this stream
        is_public: Whether stream is publicly visible
        **kwargs: Additional Stream fields

    Raises:
        ValueError: If neither content_id nor info_hash is provided
    """
    if not content_id and not info_hash:
        raise ValueError("At least one of content_id or info_hash is required")

    stream = Stream(
        stream_type=StreamType.ACESTREAM,
        name=name,
        source=source,
        uploader_user_id=uploader_user_id,
        is_public=is_public,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    acestream = AceStreamStream(
        stream_id=stream.id,
        content_id=content_id,
        info_hash=info_hash,
    )
    session.add(acestream)

    await link_stream_to_media(session, stream.id, media_id)
    await session.flush()
    return acestream


# =============================================================================
# TRACKER CRUD
# =============================================================================


async def get_tracker_by_url(
    session: AsyncSession,
    url: str,
) -> Tracker | None:
    """Get tracker by URL."""
    query = select(Tracker).where(Tracker.url == url)
    result = await session.exec(query)
    return result.first()


async def create_tracker(
    session: AsyncSession,
    url: str,
    status: TrackerStatus = TrackerStatus.UNKNOWN,
) -> Tracker:
    """Create a new tracker."""
    tracker = Tracker(url=url, status=status)
    session.add(tracker)
    await session.flush()
    return tracker


async def get_or_create_tracker(
    session: AsyncSession,
    url: str,
) -> Tracker:
    """Get existing tracker or create new one."""
    existing = await get_tracker_by_url(session, url)
    if existing:
        return existing
    return await create_tracker(session, url)


async def update_tracker_status(
    session: AsyncSession,
    tracker_id: int,
    status: TrackerStatus,
) -> bool:
    """Update tracker status."""
    result = await session.exec(
        sa_update(Tracker).where(Tracker.id == tracker_id).values(status=status, last_checked=datetime.now(pytz.UTC))
    )
    await session.flush()
    return result.rowcount > 0


async def get_working_trackers(
    session: AsyncSession,
) -> Sequence[Tracker]:
    """Get all working trackers."""
    query = select(Tracker).where(Tracker.status == TrackerStatus.WORKING)
    result = await session.exec(query)
    return result.all()


# =============================================================================
# STREAM FILE CRUD (NEW in v5 - replaces TorrentFile)
# =============================================================================


async def add_files_to_stream(
    session: AsyncSession,
    stream_id: int,
    files: list[dict],
) -> list[StreamFile]:
    """Add file records to a stream."""
    created = []
    for file_data in files:
        stream_file = StreamFile(
            stream_id=stream_id,
            file_index=file_data.get("file_index"),
            filename=file_data.get("filename", ""),
            file_path=file_data.get("file_path"),
            size=file_data.get("size"),
            file_type=file_data.get("file_type", FileType.VIDEO),
        )
        session.add(stream_file)
        created.append(stream_file)

    await session.flush()
    return created


async def get_files_for_stream(
    session: AsyncSession,
    stream_id: int,
) -> Sequence[StreamFile]:
    """Get all files for a stream."""
    query = select(StreamFile).where(StreamFile.stream_id == stream_id).order_by(StreamFile.file_index)
    result = await session.exec(query)
    return result.all()


async def delete_files_for_stream(
    session: AsyncSession,
    stream_id: int,
) -> int:
    """Delete all files for a stream."""
    result = await session.exec(sa_delete(StreamFile).where(StreamFile.stream_id == stream_id))
    await session.flush()
    return result.rowcount


# =============================================================================
# FILE MEDIA LINK CRUD (NEW in v5 - replaces StreamEpisodeFile)
# =============================================================================


async def link_file_to_media(
    session: AsyncSession,
    file_id: int,
    media_id: int,
    *,
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_end: int | None = None,
    link_source: LinkSource = LinkSource.USER,
    confidence: float = 1.0,
) -> FileMediaLink:
    """Link a file to a media entry with optional episode info."""
    link = FileMediaLink(
        file_id=file_id,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
        episode_end=episode_end,
        link_source=link_source,
        confidence=confidence,
    )
    session.add(link)
    await session.flush()
    return link


async def get_file_media_links_for_stream(
    session: AsyncSession,
    stream_id: int,
) -> Sequence[FileMediaLink]:
    """Get all file-media links for files in a stream."""
    query = (
        select(FileMediaLink)
        .join(StreamFile)
        .where(StreamFile.stream_id == stream_id)
        .order_by(FileMediaLink.season_number, FileMediaLink.episode_number)
    )
    result = await session.exec(query)
    return result.all()


async def get_files_for_episode(
    session: AsyncSession,
    media_id: int,
    season_number: int,
    episode_number: int,
) -> Sequence[StreamFile]:
    """Get all files linked to a specific episode."""
    query = (
        select(StreamFile)
        .join(FileMediaLink)
        .where(
            FileMediaLink.media_id == media_id,
            FileMediaLink.season_number == season_number,
            FileMediaLink.episode_number == episode_number,
        )
    )
    result = await session.exec(query)
    return result.all()


async def delete_file_media_links_for_stream(
    session: AsyncSession,
    stream_id: int,
) -> int:
    """Delete all file-media links for files in a stream."""
    # Get file IDs first
    file_ids_query = select(StreamFile.id).where(StreamFile.stream_id == stream_id)
    file_ids_result = await session.exec(file_ids_query)
    file_ids = file_ids_result.all()

    if not file_ids:
        return 0

    result = await session.exec(sa_delete(FileMediaLink).where(FileMediaLink.file_id.in_(file_ids)))
    await session.flush()
    return result.rowcount


# =============================================================================
# LANGUAGE LINKS
# =============================================================================


async def add_languages_to_stream(
    session: AsyncSession,
    stream_id: int,
    language_ids: list[int],
) -> None:
    """Add languages to a stream (ignores duplicates)."""
    for lang_id in language_ids:
        stmt = (
            pg_insert(StreamLanguageLink)
            .values(
                stream_id=stream_id,
                language_id=lang_id,
            )
            .on_conflict_do_nothing()
        )
        await session.exec(stmt)
    await session.flush()


# =============================================================================
# TELEGRAM STREAM CRUD
# =============================================================================


async def get_telegram_stream_by_chat_message(
    session: AsyncSession,
    chat_id: str,
    message_id: int,
    *,
    load_relations: bool = False,
) -> TelegramStream | None:
    """Get Telegram stream by chat_id and message_id.

    Args:
        session: Database session
        chat_id: Telegram channel/group ID
        message_id: Message ID
        load_relations: Whether to load related data (languages, etc.)

    Returns:
        TelegramStream or None if not found
    """
    query = select(TelegramStream).where(
        TelegramStream.chat_id == chat_id,
        TelegramStream.message_id == message_id,
    )

    if load_relations:
        query = query.options(
            selectinload(TelegramStream.stream).selectinload(Stream.languages),
            selectinload(TelegramStream.stream).selectinload(Stream.audio_formats),
            selectinload(TelegramStream.stream).selectinload(Stream.channels),
            selectinload(TelegramStream.stream).selectinload(Stream.hdr_formats),
            selectinload(TelegramStream.stream).selectinload(Stream.files).selectinload(StreamFile.media_links),
        )

    result = await session.exec(query)
    return result.first()


async def get_telegram_stream_by_file_id(
    session: AsyncSession,
    file_id: str,
    *,
    load_relations: bool = False,
) -> TelegramStream | None:
    """Get Telegram stream by Telegram file_id.

    Args:
        session: Database session
        file_id: Telegram file_id from Bot API
        load_relations: Whether to load related data

    Returns:
        TelegramStream or None if not found
    """
    query = select(TelegramStream).where(TelegramStream.file_id == file_id)

    if load_relations:
        query = query.options(
            selectinload(TelegramStream.stream).selectinload(Stream.languages),
            selectinload(TelegramStream.stream).selectinload(Stream.audio_formats),
            selectinload(TelegramStream.stream).selectinload(Stream.channels),
            selectinload(TelegramStream.stream).selectinload(Stream.hdr_formats),
        )

    result = await session.exec(query)
    return result.first()


async def get_telegram_stream_by_file_unique_id(
    session: AsyncSession,
    file_unique_id: str,
    *,
    load_relations: bool = False,
) -> TelegramStream | None:
    """Get Telegram stream by universal file_unique_id.

    Useful for matching content across different bots (e.g., during bot migration).
    The file_unique_id remains the same regardless of which bot accesses the file.

    Args:
        session: Database session
        file_unique_id: Universal Telegram file identifier
        load_relations: Whether to load related data

    Returns:
        TelegramStream or None if not found
    """
    query = select(TelegramStream).where(TelegramStream.file_unique_id == file_unique_id)

    if load_relations:
        query = query.options(
            selectinload(TelegramStream.stream).selectinload(Stream.languages),
            selectinload(TelegramStream.stream).selectinload(Stream.audio_formats),
            selectinload(TelegramStream.stream).selectinload(Stream.channels),
            selectinload(TelegramStream.stream).selectinload(Stream.hdr_formats),
        )

    result = await session.exec(query)
    return result.first()


async def update_telegram_stream_file_id(
    session: AsyncSession,
    file_unique_id: str,
    new_file_id: str,
) -> bool:
    """Update the file_id for a TelegramStream matched by file_unique_id.

    Used during bot migration to update file_ids when a new bot scrapes
    the backup channel. The file_unique_id remains constant across bots,
    but file_id changes per bot.

    Args:
        session: Database session
        file_unique_id: Universal file identifier to match
        new_file_id: New bot-specific file_id

    Returns:
        True if a record was updated, False otherwise
    """
    from sqlalchemy import update as sa_update

    stmt = sa_update(TelegramStream).where(TelegramStream.file_unique_id == file_unique_id).values(file_id=new_file_id)
    result = await session.exec(stmt)
    await session.commit()
    return result.rowcount > 0


async def create_telegram_stream(
    session: AsyncSession,
    *,
    chat_id: str,
    message_id: int,
    name: str,
    media_id: int,
    chat_username: str | None = None,
    file_id: str | None = None,
    file_unique_id: str | None = None,
    file_name: str | None = None,
    mime_type: str | None = None,
    size: int | None = None,
    posted_at: datetime | None = None,
    # Backup channel info
    backup_chat_id: str | None = None,
    backup_message_id: int | None = None,
    source: str = "telegram",
    # Quality attributes
    resolution: str | None = None,
    codec: str | None = None,
    quality: str | None = None,
    bit_depth: str | None = None,
    uploader: str | None = None,
    release_group: str | None = None,
    # Boolean flags
    is_remastered: bool = False,
    is_upscaled: bool = False,
    is_proper: bool = False,
    is_repack: bool = False,
    is_extended: bool = False,
    is_complete: bool = False,
    is_dubbed: bool = False,
    is_subbed: bool = False,
    # Relationships
    languages: list[Language] | None = None,
    audio_formats: list[AudioFormat] | None = None,
    channels: list[AudioChannel] | None = None,
    hdr_formats: list[HDRFormat] | None = None,
    uploader_user_id: int | None = None,
    is_public: bool = True,
    # Episode info for series
    season_number: int | None = None,
    episode_number: int | None = None,
    episode_end: int | None = None,
    **kwargs,
) -> TelegramStream:
    """Create a new Telegram stream.

    Args:
        chat_id: Telegram channel/group ID
        message_id: Message ID containing the file
        name: Display name for the stream
        media_id: Associated media ID
        chat_username: Channel @username (without @)
        file_id: Telegram file_id for Bot API downloads (bot-specific)
        file_unique_id: Universal file identifier (same across all bots)
        file_name: Original filename
        mime_type: MIME type of the file
        size: File size in bytes
        posted_at: When the message was posted
        backup_chat_id: Backup channel ID for redundancy
        backup_message_id: Message ID in backup channel
        source: Source identifier (default: "telegram")
        ... quality and relationship args ...
        season_number: Season number for series
        episode_number: Episode number for series
        episode_end: End episode for multi-episode files

    Returns:
        Created TelegramStream
    """
    # Create base stream
    stream = Stream(
        stream_type=StreamType.TELEGRAM,
        name=name,
        source=source,
        resolution=resolution,
        codec=codec,
        quality=quality,
        bit_depth=bit_depth,
        uploader=uploader,
        release_group=release_group,
        is_remastered=is_remastered,
        is_upscaled=is_upscaled,
        is_proper=is_proper,
        is_repack=is_repack,
        is_extended=is_extended,
        is_complete=is_complete,
        is_dubbed=is_dubbed,
        is_subbed=is_subbed,
        uploader_user_id=uploader_user_id,
        is_public=is_public,
        **kwargs,
    )
    session.add(stream)
    await session.flush()

    # Create Telegram-specific data
    telegram_stream = TelegramStream(
        stream_id=stream.id,
        chat_id=chat_id,
        chat_username=chat_username,
        message_id=message_id,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_name=file_name,
        mime_type=mime_type,
        size=size,
        posted_at=posted_at,
        backup_chat_id=backup_chat_id,
        backup_message_id=backup_message_id,
    )
    session.add(telegram_stream)

    # Link to media
    await link_stream_to_media(session, stream.id, media_id)

    # Add languages (many-to-many via StreamLanguageLink)
    if languages:
        for lang in languages:
            link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
            session.add(link)

    # Add audio formats (many-to-many via StreamAudioLink)
    if audio_formats:
        for af in audio_formats:
            link = StreamAudioLink(stream_id=stream.id, audio_format_id=af.id)
            session.add(link)

    # Add channels (many-to-many via StreamChannelLink)
    if channels:
        for ch in channels:
            link = StreamChannelLink(stream_id=stream.id, channel_id=ch.id)
            session.add(link)

    # Add HDR formats (many-to-many via StreamHDRLink)
    if hdr_formats:
        for hf in hdr_formats:
            link = StreamHDRLink(stream_id=stream.id, hdr_format_id=hf.id)
            session.add(link)

    # Create a StreamFile entry for the Telegram file
    if file_name:
        stream_file = StreamFile(
            stream_id=stream.id,
            file_index=0,
            filename=file_name,
            size=size or 0,
            file_type=FileType.VIDEO,
        )
        session.add(stream_file)
        await session.flush()

        # Create FileMediaLink for episode info if provided
        if season_number is not None or episode_number is not None:
            file_link = FileMediaLink(
                file_id=stream_file.id,
                media_id=media_id,
                season_number=season_number,
                episode_number=episode_number,
                episode_end=episode_end,
                link_source=LinkSource.PTT_PARSER,
                confidence=1.0,
            )
            session.add(file_link)

    await session.flush()
    return telegram_stream


async def update_telegram_stream(
    session: AsyncSession,
    chat_id: str,
    message_id: int,
    updates: dict,
) -> bool:
    """Update Telegram stream by chat_id and message_id.

    Updates both the base Stream and TelegramStream records.

    Args:
        session: Database session
        chat_id: Telegram channel/group ID
        message_id: Message ID
        updates: Dictionary of fields to update

    Returns:
        True if updated, False if not found
    """
    telegram_stream = await get_telegram_stream_by_chat_message(session, chat_id, message_id)
    if not telegram_stream:
        return False

    # Separate stream-level and telegram-specific updates
    stream_fields = {
        "name",
        "source",
        "resolution",
        "codec",
        "quality",
        "bit_depth",
        "uploader",
        "release_group",
        "is_blocked",
        "is_active",
        "updated_at",
        "is_remastered",
        "is_upscaled",
        "is_proper",
        "is_repack",
        "is_extended",
        "is_complete",
        "is_dubbed",
        "is_subbed",
    }
    telegram_fields = {
        "chat_username",
        "file_id",
        "file_name",
        "mime_type",
        "size",
        "posted_at",
    }

    stream_updates = {k: v for k, v in updates.items() if k in stream_fields}
    telegram_updates = {k: v for k, v in updates.items() if k in telegram_fields}

    # Update Stream
    if stream_updates:
        await session.exec(sa_update(Stream).where(Stream.id == telegram_stream.stream_id).values(**stream_updates))

    # Update TelegramStream
    if telegram_updates:
        await session.exec(
            sa_update(TelegramStream)
            .where(
                TelegramStream.chat_id == chat_id,
                TelegramStream.message_id == message_id,
            )
            .values(**telegram_updates)
        )

    await session.flush()
    return True


async def delete_telegram_stream(
    session: AsyncSession,
    chat_id: str,
    message_id: int,
) -> bool:
    """Delete Telegram stream by chat_id and message_id.

    Args:
        session: Database session
        chat_id: Telegram channel/group ID
        message_id: Message ID

    Returns:
        True if deleted, False if not found
    """
    telegram_stream = await get_telegram_stream_by_chat_message(session, chat_id, message_id)
    if not telegram_stream:
        return False
    return await delete_stream(session, telegram_stream.stream_id)


async def get_telegram_streams_for_media(
    session: AsyncSession,
    media_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[TelegramStream]:
    """Get all Telegram streams linked to a media entry.

    Args:
        session: Database session
        media_id: Media ID to get streams for
        limit: Maximum number of streams to return
        offset: Offset for pagination

    Returns:
        List of TelegramStream records
    """
    query = (
        select(TelegramStream)
        .join(Stream, Stream.id == TelegramStream.stream_id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .where(
            StreamMediaLink.media_id == media_id,
            Stream.is_active.is_(True),
            Stream.is_blocked.is_(False),
        )
        .options(
            selectinload(TelegramStream.stream).selectinload(Stream.languages),
            selectinload(TelegramStream.stream).selectinload(Stream.audio_formats),
            selectinload(TelegramStream.stream).selectinload(Stream.channels),
            selectinload(TelegramStream.stream).selectinload(Stream.hdr_formats),
        )
        .order_by(TelegramStream.posted_at.desc().nullsfirst())
        .limit(limit)
        .offset(offset)
    )

    result = await session.exec(query)
    return list(result.all())


async def telegram_stream_exists(
    session: AsyncSession,
    chat_id: str,
    message_id: int,
) -> bool:
    """Check if a Telegram stream already exists.

    Args:
        session: Database session
        chat_id: Telegram channel/group ID
        message_id: Message ID

    Returns:
        True if exists, False otherwise
    """
    query = select(func.count(TelegramStream.id)).where(
        TelegramStream.chat_id == chat_id,
        TelegramStream.message_id == message_id,
    )
    result = await session.exec(query)
    count = result.first() or 0
    return count > 0


# =============================================================================
# TELEGRAM USER FORWARD CRUD
# =============================================================================


async def get_telegram_user_forward(
    session: AsyncSession,
    telegram_stream_id: int,
    user_id: int,
) -> "TelegramUserForward | None":
    """Get existing forward record for a user and stream.

    Args:
        session: Database session
        telegram_stream_id: ID of the TelegramStream
        user_id: MediaFusion user ID

    Returns:
        TelegramUserForward record or None if not found
    """
    from db.models.streams import TelegramUserForward

    query = select(TelegramUserForward).where(
        TelegramUserForward.telegram_stream_id == telegram_stream_id,
        TelegramUserForward.user_id == user_id,
    )
    result = await session.exec(query)
    return result.first()


async def create_telegram_user_forward(
    session: AsyncSession,
    *,
    telegram_stream_id: int,
    user_id: int,
    telegram_user_id: int,
    forwarded_chat_id: str,
    forwarded_message_id: int,
) -> "TelegramUserForward":
    """Create a record of a forwarded Telegram stream for a user.

    Args:
        session: Database session
        telegram_stream_id: ID of the TelegramStream
        user_id: MediaFusion user ID
        telegram_user_id: User's Telegram ID
        forwarded_chat_id: Chat ID where content was forwarded (user's DM with bot)
        forwarded_message_id: Message ID of the forwarded copy

    Returns:
        Created TelegramUserForward record
    """
    from db.models.streams import TelegramUserForward

    forward = TelegramUserForward(
        telegram_stream_id=telegram_stream_id,
        user_id=user_id,
        telegram_user_id=telegram_user_id,
        forwarded_chat_id=forwarded_chat_id,
        forwarded_message_id=forwarded_message_id,
    )
    session.add(forward)
    await session.commit()
    await session.refresh(forward)
    return forward


async def delete_telegram_user_forwards_for_stream(
    session: AsyncSession,
    telegram_stream_id: int,
) -> int:
    """Delete all forward records for a Telegram stream.

    Called when a TelegramStream is deleted.

    Args:
        session: Database session
        telegram_stream_id: ID of the TelegramStream

    Returns:
        Number of records deleted
    """
    from db.models.streams import TelegramUserForward
    from sqlmodel import delete

    stmt = delete(TelegramUserForward).where(TelegramUserForward.telegram_stream_id == telegram_stream_id)
    result = await session.exec(stmt)
    await session.commit()
    return result.rowcount or 0


async def delete_telegram_user_forwards_for_user(
    session: AsyncSession,
    user_id: int,
) -> int:
    """Delete all forward records for a user.

    Called when a user account is deleted.

    Args:
        session: Database session
        user_id: MediaFusion user ID

    Returns:
        Number of records deleted
    """
    from db.models.streams import TelegramUserForward
    from sqlmodel import delete

    stmt = delete(TelegramUserForward).where(TelegramUserForward.user_id == user_id)
    result = await session.exec(stmt)
    await session.commit()
    return result.rowcount or 0


# =============================================================================
# METRICS CRUD FUNCTIONS
# =============================================================================


async def get_torrent_count(session: AsyncSession) -> int:
    """Get total count of torrent streams."""
    query = select(func.count(TorrentStream.id))
    result = await session.exec(query)
    return result.first() or 0


async def get_torrents_by_source(
    session: AsyncSession,
    limit: int = 20,
) -> list[dict]:
    """Get torrent counts grouped by source."""
    query = (
        select(Stream.source, func.count(Stream.id).label("count"))
        .where(Stream.stream_type == StreamType.TORRENT)
        .where(Stream.source.isnot(None))
        .group_by(Stream.source)
        .order_by(func.count(Stream.id).desc())
        .limit(limit)
    )

    result = await session.exec(query)
    return [{"name": row.source or "Unknown", "count": row.count} for row in result.all()]


async def get_torrents_by_uploader(
    session: AsyncSession,
    limit: int = 20,
) -> list[dict]:
    """Get torrent counts grouped by uploader.

    Handles both:
    - Legacy data with uploader string field
    - New data with uploader_user_id linked to User table

    Uses COALESCE to prefer linked user's username over legacy uploader field.
    """
    # Use COALESCE: prefer User.username when uploader_user_id is set,
    # otherwise fall back to Stream.uploader
    uploader_name = coalesce(User.username, Stream.uploader).label("uploader_name")

    query = (
        select(
            uploader_name,
            Stream.uploader_user_id,
            func.count(Stream.id).label("count"),
        )
        .outerjoin(User, Stream.uploader_user_id == User.id)
        .where(Stream.stream_type == StreamType.TORRENT)
        .where(
            # Either has linked user OR has legacy uploader string
            (Stream.uploader_user_id.isnot(None)) | ((Stream.uploader.isnot(None)) & (Stream.uploader != ""))
        )
        .group_by(uploader_name, Stream.uploader_user_id)
        .order_by(func.count(Stream.id).desc())
        .limit(limit)
    )

    result = await session.exec(query)
    return [
        {
            "name": row.uploader_name or "Anonymous",
            "count": row.count,
            "user_id": row.uploader_user_id,
            "is_linked": row.uploader_user_id is not None,
        }
        for row in result.all()
    ]


async def get_weekly_top_uploaders(
    session: AsyncSession,
    start_of_week: datetime,
    end_of_week: datetime,
    limit: int = 20,
) -> list[dict]:
    """Get top uploaders for a specific week.

    Handles both legacy uploader strings and linked user accounts.
    """
    uploader_name = coalesce(User.username, Stream.uploader).label("uploader_name")

    query = (
        select(
            uploader_name,
            Stream.uploader_user_id,
            func.count(Stream.id).label("count"),
            func.max(Stream.created_at).label("latest_upload"),
        )
        .outerjoin(User, Stream.uploader_user_id == User.id)
        .where(Stream.stream_type == StreamType.TORRENT)
        .where(Stream.created_at >= start_of_week)
        .where(Stream.created_at < end_of_week)
        .where((Stream.uploader_user_id.isnot(None)) | ((Stream.uploader.isnot(None)) & (Stream.uploader != "")))
        .group_by(uploader_name, Stream.uploader_user_id)
        .order_by(func.count(Stream.id).desc())
        .limit(limit)
    )

    result = await session.exec(query)
    return [
        {
            "name": row.uploader_name or "Anonymous",
            "count": row.count,
            "user_id": row.uploader_user_id,
            "is_linked": row.uploader_user_id is not None,
            "latest_upload": row.latest_upload.isoformat() if row.latest_upload else None,
        }
        for row in result.all()
    ]
