"""
Admin API endpoints for database management.
Provides CRUD operations for Metadata, Torrent Streams, and TV Streams.
Admin-only access.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import selectinload
from sqlmodel import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db.crud import (
    get_all_external_ids_dict,
)
from db.database import get_async_session
from db.enums import MediaType, UserRole
from db.models import (
    AkaTitle,
    Catalog,
    Genre,
    HTTPStream,
    Language,
    Media,
    MediaCast,
    MediaCatalogLink,
    MediaExternalID,
    MediaGenreLink,
    MediaParentalCertificateLink,
    MediaRating,
    MovieMetadata,
    ParentalCertificate,
    PlaybackTracking,
    Person,
    SeriesMetadata,
    Stream,
    StreamFile,
    StreamLanguageLink,
    StreamMediaLink,
    FileMediaLink,
    TorrentStream,
    TorrentTrackerLink,
    Tracker,
    TVMetadata,
    YouTubeStream,
    UsenetStream,
    TelegramStream,
    ExternalLinkStream,
    AceStreamStream,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin Database Management"])


# ============================================
# Pydantic Schemas - Metadata
# ============================================


class AkaTitleItem(BaseModel):
    """AKA title item."""

    id: int
    title: str


class EpisodeFileItem(BaseModel):
    """Episode file within a torrent stream."""

    id: int
    season_number: int
    episode_number: int
    file_index: int | None = None
    filename: str | None = None
    size: int | None = None


class MetadataResponse(BaseModel):
    """Full metadata response with all fields."""

    # Base fields
    id: int  # Internal media_id
    external_ids: dict | None = None  # All external IDs {"imdb": "tt...", "tmdb": 123}
    type: str
    title: str
    year: int | None = None
    poster: str | None = None
    is_poster_working: bool = True
    is_add_title_to_poster: bool = False
    background: str | None = None
    description: str | None = None

    # Content moderation
    is_user_created: bool = False
    is_blocked: bool = False
    blocked_at: datetime | None = None
    block_reason: str | None = None
    runtime: str | None = None
    website: str | None = None

    # Read-only computed fields
    total_streams: int = 0
    created_at: datetime
    updated_at: datetime | None = None
    last_stream_added: datetime | None = None

    # Type-specific fields (Movie/Series)
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    parent_guide_nudity_status: str | None = None

    # Series-specific
    end_date: str | None = None  # ISO format date string

    # TV-specific
    country: str | None = None
    tv_language: str | None = None
    logo: str | None = None

    # Relationships
    genres: list[str] = []
    catalogs: list[str] = []
    stars: list[str] = []
    parental_certificates: list[str] = []
    aka_titles: list[str] = []


class MetadataListResponse(BaseModel):
    items: list[MetadataResponse]
    total: int
    page: int
    per_page: int
    pages: int


class MetadataUpdateRequest(BaseModel):
    """Update request for metadata with all editable fields."""

    # Base fields
    title: str | None = None
    year: int | None = None
    poster: str | None = None
    is_poster_working: bool | None = None
    is_add_title_to_poster: bool | None = None
    background: str | None = None
    description: str | None = None
    runtime: str | None = None
    website: str | None = None

    # Type-specific fields (Movie/Series)
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    parent_guide_nudity_status: str | None = None
    nudity_status: str | None = None  # Nudity status on Media table

    # Series-specific
    end_date: str | None = None  # ISO format date string (YYYY-MM-DD)

    # TV-specific
    country: str | None = None
    tv_language: str | None = None
    logo: str | None = None

    # Relationships (list of names - will use get-or-create)
    genres: list[str] | None = None
    catalogs: list[str] | None = None
    stars: list[str] | None = None
    parental_certificates: list[str] | None = None
    aka_titles: list[str] | None = None


class MetadataStatsResponse(BaseModel):
    total_movies: int
    total_series: int
    total_tv: int
    total_streams: int
    total_tv_streams: int


# ============================================
# Pydantic Schemas - Torrent Streams
# ============================================


class TorrentStreamResponse(BaseModel):
    """Full torrent stream response with all fields.

    Updated for new Stream + TorrentStream architecture.
    """

    # Core identifiers
    id: int  # TorrentStream.id
    stream_id: int  # Base Stream.id
    info_hash: str

    # Stream info (from Stream base)
    name: str  # Stream.name (replaces torrent_name)
    source: str
    resolution: str | None = None
    codec: str | None = None
    quality: str | None = None
    bit_depth: str | None = None
    uploader: str | None = None
    release_group: str | None = None
    is_blocked: bool = False
    playback_count: int = 0

    # Normalized quality attributes (from related tables)
    audio_formats: list[str] = []  # e.g., ["DTS-HD MA", "Atmos"]
    channels: list[str] = []  # e.g., ["5.1", "7.1"]
    hdr_formats: list[str] = []  # e.g., ["HDR10", "Dolby Vision"]

    # TorrentStream-specific
    total_size: int  # Replaces size
    seeders: int | None = None
    leechers: int | None = None
    torrent_type: str = "public"
    uploaded_at: datetime | None = None
    file_count: int = 1

    # Timestamps
    created_at: datetime
    updated_at: datetime | None = None

    # Relationships
    languages: list[str] = []
    trackers: list[str] = []
    files: list[dict[str, Any]] = []  # StreamFile list


class TorrentStreamListResponse(BaseModel):
    items: list[TorrentStreamResponse]
    total: int
    page: int
    per_page: int
    pages: int


class TorrentStreamUpdateRequest(BaseModel):
    """Update request for torrent stream with all editable fields.

    Updates span multiple tables:
    - Stream: name, source, resolution, codec, quality, bit_depth, uploader, release_group, is_blocked
    - TorrentStream: seeders, leechers, torrent_type, uploaded_at
    - Normalized tables: audio_formats, channels, hdr_formats, languages
    """

    # Stream base fields
    name: str | None = None
    source: str | None = None
    resolution: str | None = None
    codec: str | None = None
    quality: str | None = None
    bit_depth: str | None = None
    uploader: str | None = None
    release_group: str | None = None
    is_blocked: bool | None = None

    # TorrentStream fields
    seeders: int | None = None
    leechers: int | None = None
    torrent_type: str | None = None
    uploaded_at: datetime | None = None

    # Relationships (list of names - will use get-or-create)
    languages: list[str] | None = None
    trackers: list[str] | None = None

    # Normalized quality attributes (list of names - will use get-or-create)
    audio_formats: list[str] | None = None  # e.g., ["DTS-HD MA", "Atmos"]
    channels: list[str] | None = None  # e.g., ["5.1", "7.1"]
    hdr_formats: list[str] | None = None  # e.g., ["HDR10", "Dolby Vision"]


# ============================================
# Pydantic Schemas - TV Streams (New Architecture)
# TV streams use: Media (type=TV) + TVMetadata + Stream + HTTPStream + StreamMediaLink
# ============================================


class ExternalIds(BaseModel):
    """External IDs for a media item"""

    imdb: str | None = None
    tmdb: int | None = None
    tvdb: int | None = None
    mal: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalIds":
        return cls(
            imdb=data.get("imdb"),
            tmdb=int(data["tmdb"]) if data.get("tmdb") else None,
            tvdb=int(data["tvdb"]) if data.get("tvdb") else None,
            mal=int(data["mal"]) if data.get("mal") else None,
        )


class TVStreamResponse(BaseModel):
    """Full TV stream response with all fields.

    New architecture combines:
    - Stream base (name, source, is_active/blocked)
    - HTTPStream (url, format)
    - Media + TVMetadata (channel metadata)
    """

    # Stream identifiers
    stream_id: int  # Stream.id (base)
    http_stream_id: int  # HTTPStream.id

    # Media (TV channel) info
    media_id: int  # Media.id
    external_ids: ExternalIds  # All external IDs from MediaExternalID table
    channel_title: str  # Media.title
    country: str | None = None  # TVMetadata.country
    tv_language: str | None = None  # TVMetadata.tv_language

    # Stream info (from Stream base)
    name: str  # Stream.name (display name)
    source: str  # Stream.source (scraper)

    # HTTPStream specific
    url: str
    format: str | None = None  # hls, dash, mp4

    # Status
    is_active: bool = True
    is_blocked: bool = False

    # Timestamps
    created_at: datetime
    updated_at: datetime | None = None

    # Relationships
    namespaces: list[str] = []


class TVStreamListResponse(BaseModel):
    items: list[TVStreamResponse]
    total: int
    page: int
    per_page: int
    pages: int


class TVStreamUpdateRequest(BaseModel):
    """Update request for TV stream with all editable fields.

    Updates can apply to:
    - Stream base (name, source, is_active/blocked)
    - HTTPStream (url, format)
    - TVMetadata (country, tv_language)
    """

    # Stream base fields
    name: str | None = None
    source: str | None = None
    is_active: bool | None = None
    is_blocked: bool | None = None

    # HTTPStream fields
    url: str | None = None
    format: str | None = None

    # TVMetadata fields
    country: str | None = None
    tv_language: str | None = None

    # Relationships (list of names - will use get-or-create)
    namespaces: list[str] | None = None


# ============================================
# Helper Functions
# ============================================


async def get_metadata_with_relations(session: AsyncSession, media_id: int) -> tuple | None:
    """Get metadata with its type-specific data and all relationships."""
    # Use eager loading for relationships
    query = (
        select(Media)
        .where(Media.id == media_id)
        .options(
            selectinload(Media.genres),
            selectinload(Media.catalogs),
            selectinload(Media.aka_titles),
            selectinload(Media.parental_certificates),
            selectinload(Media.cast).selectinload(MediaCast.person),
            selectinload(Media.images),
            selectinload(Media.ratings).selectinload(MediaRating.provider),
        )
    )
    result = await session.exec(query)
    base = result.first()

    if not base:
        return None

    # Get type-specific metadata with relationships
    specific = None
    if base.type == MediaType.MOVIE:
        query = select(MovieMetadata).where(MovieMetadata.media_id == base.id)
        result = await session.exec(query)
        specific = result.first()
    elif base.type == MediaType.SERIES:
        query = select(SeriesMetadata).where(SeriesMetadata.media_id == base.id)
        result = await session.exec(query)
        specific = result.first()
    elif base.type == MediaType.TV:
        query = select(TVMetadata).where(TVMetadata.media_id == base.id)
        result = await session.exec(query)
        specific = result.first()

    return base, specific


def format_metadata_response(
    base: Media,
    specific=None,
    aka_titles: list[AkaTitle] = None,
    ext_ids_dict: dict = None,
) -> MetadataResponse:
    """Format metadata for API response with all fields.

    Images are now stored in MediaImage table, ratings in MediaRating table.
    This function provides backward-compatible fields for the frontend.

    Args:
        ext_ids_dict: Dictionary of external IDs e.g. {"imdb": "tt123", "tmdb": "456"}
    """
    # Get primary images from MediaImage relationship
    poster = None
    background = None
    logo = None

    if base.images:
        for img in base.images:
            if img.is_primary:
                if img.image_type == "poster":
                    poster = img.url
                elif img.image_type == "background":
                    background = img.url
                elif img.image_type == "logo":
                    logo = img.url

    # Get IMDb/TMDB ratings from MediaRating relationship
    imdb_rating = None
    tmdb_rating = None

    if base.ratings:
        for media_rating in base.ratings:
            if media_rating.provider and media_rating.provider.name.lower() == "imdb":
                imdb_rating = media_rating.rating
            elif media_rating.provider and media_rating.provider.name.lower() == "tmdb":
                tmdb_rating = media_rating.rating

    response = MetadataResponse(
        id=base.id,
        external_ids=ext_ids_dict,  # Passed in from caller
        type=base.type.value,
        title=base.title,
        year=base.year,
        poster=poster,
        is_poster_working=True,  # Deprecated - always True for new architecture
        is_add_title_to_poster=False,  # Deprecated - handled differently now
        background=background,
        description=base.description,
        is_user_created=base.is_user_created,
        runtime=str(base.runtime_minutes) if base.runtime_minutes else None,
        website=None,  # No longer stored on Media
        total_streams=base.total_streams,
        created_at=base.created_at,
        updated_at=base.updated_at,
        last_stream_added=base.last_stream_added,
        genres=[g.name for g in base.genres] if base.genres else [],
        catalogs=[c.name for c in base.catalogs] if base.catalogs else [],
        aka_titles=[a.title for a in base.aka_titles] if base.aka_titles else [],
        stars=[cast.person.name for cast in sorted(base.cast or [], key=lambda c: c.display_order) if cast.person],
        parental_certificates=[p.name for p in base.parental_certificates] if base.parental_certificates else [],
        imdb_rating=imdb_rating,
        tmdb_rating=tmdb_rating,
    )

    # Add type-specific fields
    if specific:
        # Movie/Series common fields
        if hasattr(specific, "parent_guide_nudity_status"):
            response.parent_guide_nudity_status = (
                specific.parent_guide_nudity_status.value if specific.parent_guide_nudity_status else None
            )

        # Series-specific - end_date
        if base.type == MediaType.SERIES and base.end_date:
            response.end_date = base.end_date.isoformat()

        # TV-specific
        if hasattr(specific, "country"):
            response.country = specific.country
        if hasattr(specific, "tv_language"):
            response.tv_language = specific.tv_language
        if logo:
            response.logo = logo

    return response


def format_torrent_stream_response(
    torrent: TorrentStream,
) -> TorrentStreamResponse:
    """Format torrent stream for API response with all fields.

    Expects TorrentStream with stream relationship loaded.
    """
    base_stream = torrent.stream
    return TorrentStreamResponse(
        id=torrent.id,
        stream_id=base_stream.id,
        info_hash=torrent.info_hash,
        name=base_stream.name,
        source=base_stream.source,
        resolution=base_stream.resolution,
        codec=base_stream.codec,
        quality=base_stream.quality,
        bit_depth=base_stream.bit_depth,
        uploader=base_stream.uploader,
        release_group=base_stream.release_group,
        is_blocked=base_stream.is_blocked,
        playback_count=base_stream.playback_count,
        # Normalized quality attributes
        audio_formats=[af.name for af in base_stream.audio_formats] if base_stream.audio_formats else [],
        channels=[ch.name for ch in base_stream.channels] if base_stream.channels else [],
        hdr_formats=[hf.name for hf in base_stream.hdr_formats] if base_stream.hdr_formats else [],
        total_size=torrent.total_size,
        seeders=torrent.seeders,
        leechers=torrent.leechers,
        torrent_type=torrent.torrent_type.value if torrent.torrent_type else "public",
        uploaded_at=torrent.uploaded_at,
        file_count=torrent.file_count,
        created_at=torrent.created_at,
        updated_at=torrent.updated_at,
        languages=[lang.name for lang in base_stream.languages] if base_stream.languages else [],
        trackers=[t.url for t in torrent.trackers] if torrent.trackers else [],
        files=[
            {
                "id": f.id,
                "file_index": f.file_index,
                "filename": f.filename,
                "size": f.size,
                "file_type": f.file_type,
            }
            for f in (torrent.files or [])
        ],
    )


def _parse_media_id_search(search: str) -> int | None:
    normalized_search = search.strip()
    if normalized_search.startswith("#"):
        normalized_search = normalized_search[1:]
    if normalized_search.isdigit():
        return int(normalized_search)
    return None


def _parse_prefixed_media_id_search(search: str) -> int | None:
    normalized_search = search.strip().lower()
    if normalized_search.startswith("mf:"):
        candidate = normalized_search[3:]
        if candidate.isdigit():
            return int(candidate)
    return None


def _parse_external_id_search(search: str) -> tuple[str, str] | None:
    normalized_search = search.strip().lower()
    if not normalized_search:
        return None

    if normalized_search.startswith("tt"):
        return "imdb", normalized_search

    provider_prefixes = ("imdb", "tmdb", "tvdb", "mal", "kitsu")
    for provider in provider_prefixes:
        prefix = f"{provider}:"
        if normalized_search.startswith(prefix):
            external_id = normalized_search[len(prefix) :]
            if external_id:
                return provider, external_id

    return None


def _build_metadata_search_condition(search: str):
    search_pattern = f"%{search.strip()}%"
    conditions = [Media.title.ilike(search_pattern)]

    parsed_media_id = _parse_media_id_search(search)
    if parsed_media_id is not None:
        conditions.append(Media.id == parsed_media_id)

    prefixed_media_id = _parse_prefixed_media_id_search(search)
    if prefixed_media_id is not None:
        conditions.append(Media.id == prefixed_media_id)

    parsed_external = _parse_external_id_search(search)
    if parsed_external:
        provider, external_id = parsed_external
        conditions.append(
            Media.id.in_(
                select(MediaExternalID.media_id).where(
                    MediaExternalID.provider == provider,
                    func.lower(MediaExternalID.external_id) == external_id,
                )
            )
        )

    return or_(*conditions)


async def format_tv_stream_response(
    session: AsyncSession,
    http_stream: HTTPStream,
    base_stream: Stream,
    media: Media,
    tv_metadata: TVMetadata | None = None,
    namespaces: list[str] | None = None,
) -> TVStreamResponse:
    """Format TV stream for API response with new architecture.

    Args:
        http_stream: HTTPStream instance
        base_stream: Stream base instance (linked to http_stream)
        media: Media instance (TV channel metadata)
        tv_metadata: Optional TVMetadata for extended attributes
        namespaces: List of namespace names
    """
    # Get external IDs from MediaExternalID table
    ext_ids_dict = await get_all_external_ids_dict(session, media.id)
    ext_ids = ExternalIds.from_dict(ext_ids_dict) if ext_ids_dict else ExternalIds()

    return TVStreamResponse(
        stream_id=base_stream.id,
        http_stream_id=http_stream.id,
        media_id=media.id,
        external_ids=ext_ids,
        channel_title=media.title,
        country=tv_metadata.country if tv_metadata else None,
        tv_language=tv_metadata.tv_language if tv_metadata else None,
        name=base_stream.name,
        source=base_stream.source,
        url=http_stream.url,
        format=http_stream.format,
        is_active=base_stream.is_active,
        is_blocked=base_stream.is_blocked,
        created_at=base_stream.created_at,
        updated_at=base_stream.updated_at,
        namespaces=namespaces or [],
    )


# ============================================
# Relationship Helper Functions
# ============================================


async def get_or_create_genre(session: AsyncSession, name: str) -> Genre:
    """Get or create a genre by name."""
    result = await session.exec(select(Genre).where(Genre.name == name))
    genre = result.first()
    if not genre:
        genre = Genre(name=name)
        session.add(genre)
        await session.commit()
    return genre


async def get_or_create_catalog(session: AsyncSession, name: str) -> Catalog:
    """Get or create a catalog by name."""
    result = await session.exec(select(Catalog).where(Catalog.name == name))
    catalog = result.first()
    if not catalog:
        catalog = Catalog(name=name)
        session.add(catalog)
        await session.commit()
    return catalog


async def get_or_create_person(session: AsyncSession, name: str) -> Person:
    """Get or create a person by name."""
    result = await session.exec(select(Person).where(Person.name == name))
    person = result.first()
    if not person:
        person = Person(name=name)
        session.add(person)
        await session.commit()
    return person


async def get_or_create_parental_certificate(session: AsyncSession, name: str) -> ParentalCertificate:
    """Get or create a parental certificate by name."""
    result = await session.exec(select(ParentalCertificate).where(ParentalCertificate.name == name))
    cert = result.first()
    if not cert:
        cert = ParentalCertificate(name=name)
        session.add(cert)
        await session.commit()
    return cert


async def get_or_create_language(session: AsyncSession, name: str) -> Language:
    """Get or create a language by name."""
    result = await session.exec(select(Language).where(Language.name == name))
    lang = result.first()
    if not lang:
        lang = Language(name=name)
        session.add(lang)
        await session.commit()
    return lang


async def get_or_create_tracker(session: AsyncSession, url: str) -> Tracker:
    """Get or create a tracker by URL."""
    result = await session.exec(select(Tracker).where(Tracker.url == url))
    tracker = result.first()
    if not tracker:
        tracker = Tracker(url=url)
        session.add(tracker)
        await session.commit()
    return tracker


async def update_metadata_genres(session: AsyncSession, media_id: int, genre_names: list[str]):
    """Update metadata genres using the link table."""
    # Remove existing links
    existing = (await session.exec(select(MediaGenreLink).where(MediaGenreLink.media_id == media_id))).all()
    for link in existing:
        await session.delete(link)

    # Add new links
    for name in genre_names:
        genre = await get_or_create_genre(session, name)
        link = MediaGenreLink(media_id=media_id, genre_id=genre.id)
        session.add(link)


async def update_metadata_catalogs(session: AsyncSession, media_id: int, catalog_names: list[str]):
    """Update metadata catalogs using the link table."""
    existing = (await session.exec(select(MediaCatalogLink).where(MediaCatalogLink.media_id == media_id))).all()
    for link in existing:
        await session.delete(link)

    for name in catalog_names:
        catalog = await get_or_create_catalog(session, name)
        link = MediaCatalogLink(media_id=media_id, catalog_id=catalog.id)
        session.add(link)


async def update_metadata_cast(session: AsyncSession, media_id: int, star_names: list[str]):
    """Update metadata cast using the Person and MediaCast tables."""
    existing = (await session.exec(select(MediaCast).where(MediaCast.media_id == media_id))).all()
    for link in existing:
        await session.delete(link)

    for i, name in enumerate(star_names):
        person = await get_or_create_person(session, name)
        cast = MediaCast(media_id=media_id, person_id=person.id, role="actor", display_order=i)
        session.add(cast)


async def update_metadata_parental_certificates(session: AsyncSession, media_id: int, cert_names: list[str]):
    """Update metadata parental certificates using the link table."""
    existing = (
        await session.exec(
            select(MediaParentalCertificateLink).where(MediaParentalCertificateLink.media_id == media_id)
        )
    ).all()
    for link in existing:
        await session.delete(link)

    for name in cert_names:
        cert = await get_or_create_parental_certificate(session, name)
        link = MediaParentalCertificateLink(media_id=media_id, certificate_id=cert.id)
        session.add(link)


async def update_metadata_aka_titles(session: AsyncSession, media_id: int, titles: list[str]):
    """Update metadata AKA titles."""
    seen: set[str] = set()
    unique_titles: list[str] = []
    for title in titles:
        if title not in seen:
            seen.add(title)
            unique_titles.append(title)

    await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == media_id))
    await session.flush()

    for title in unique_titles:
        session.add(AkaTitle(media_id=media_id, title=title))


async def update_stream_languages(session: AsyncSession, stream_id: int, language_names: list[str]):
    """Update stream languages using the link table.

    Languages are linked to Stream base (not TorrentStream).
    """
    existing = (await session.exec(select(StreamLanguageLink).where(StreamLanguageLink.stream_id == stream_id))).all()
    for link in existing:
        await session.delete(link)

    for name in language_names:
        lang = await get_or_create_language(session, name)
        link = StreamLanguageLink(stream_id=stream_id, language_id=lang.id)
        session.add(link)


async def update_torrent_trackers(session: AsyncSession, torrent_id: int, tracker_urls: list[str]):
    """Update torrent trackers using the link table.

    Trackers are linked to TorrentStream (not Stream base).
    """
    existing = (await session.exec(select(TorrentTrackerLink).where(TorrentTrackerLink.torrent_id == torrent_id))).all()
    for link in existing:
        await session.delete(link)

    for url in tracker_urls:
        tracker = await get_or_create_tracker(session, url)
        link = TorrentTrackerLink(torrent_id=torrent_id, tracker_id=tracker.id)
        session.add(link)


# ============================================
# Metadata Endpoints
# ============================================


@router.delete("/metadata/{media_id}")
async def delete_metadata(
    media_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete metadata and all associated streams (Admin only). Uses media_id (internal ID)."""
    base = await session.get(Media, media_id)
    if not base:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Collect all stream IDs linked to this media:
    # - StreamMediaLink (direct stream→media links)
    # - FileMediaLink via StreamFile (series episode file links)
    linked_stream_ids: set[int] = set()

    stream_links_result = await session.exec(
        select(StreamMediaLink.stream_id).where(StreamMediaLink.media_id == base.id)
    )
    linked_stream_ids.update(stream_links_result.all())

    file_streams_result = await session.exec(
        select(StreamFile.stream_id)
        .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
        .where(FileMediaLink.media_id == base.id)
    )
    linked_stream_ids.update(file_streams_result.all())

    # Remove link rows to this media first.
    await session.exec(sa_delete(StreamMediaLink).where(StreamMediaLink.media_id == base.id))
    await session.exec(sa_delete(FileMediaLink).where(FileMediaLink.media_id == base.id))

    # Delete associated streams using explicit SQL order.
    # Avoid ORM 1:1 relationship de-association that can try setting
    # torrent_stream.stream_id = NULL (violates NOT NULL).
    if linked_stream_ids:
        stream_ids = list(linked_stream_ids)
        await session.exec(sa_delete(TorrentStream).where(TorrentStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(HTTPStream).where(HTTPStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(YouTubeStream).where(YouTubeStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(UsenetStream).where(UsenetStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(TelegramStream).where(TelegramStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(ExternalLinkStream).where(ExternalLinkStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(AceStreamStream).where(AceStreamStream.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(PlaybackTracking).where(PlaybackTracking.stream_id.in_(stream_ids)))
        await session.exec(sa_delete(Stream).where(Stream.id.in_(stream_ids)))

    # Delete type-specific metadata
    if base.type == MediaType.MOVIE:
        specific = await session.get(MovieMetadata, media_id)
        if specific:
            await session.delete(specific)
    elif base.type == MediaType.SERIES:
        specific = await session.get(SeriesMetadata, media_id)
        if specific:
            await session.delete(specific)
    elif base.type == MediaType.TV:
        specific = await session.get(TVMetadata, media_id)
        if specific:
            await session.delete(specific)

    # Delete base metadata
    await session.delete(base)
    await session.commit()

    logger.info(f"Admin deleted metadata {media_id}")
    return {"message": "Metadata and associated streams deleted successfully"}


# ============================================
# Content Moderation - Block/Unblock Endpoints
# ============================================


class BlockMediaRequest(BaseModel):
    """Request to block media content."""

    reason: str | None = None


class BlockMediaResponse(BaseModel):
    """Response after blocking/unblocking media."""

    media_id: int
    is_blocked: bool
    blocked_at: datetime | None = None
    blocked_by: str | None = None
    block_reason: str | None = None
    message: str


@router.post("/metadata/{media_id}/block", response_model=BlockMediaResponse)
async def block_media(
    media_id: int,
    request: BlockMediaRequest,
    admin: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Block media content (Moderator/Admin only).

    Blocked content will be hidden from regular users but still visible to admins/moderators.
    """
    import pytz

    media = await session.get(Media, media_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with id {media_id} not found",
        )

    if media.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Media is already blocked",
        )

    # Block the media
    media.is_blocked = True
    media.blocked_at = datetime.now(pytz.UTC)
    media.blocked_by_user_id = admin.id
    media.block_reason = request.reason

    await session.commit()
    await session.refresh(media)

    logger.info(f"User {admin.username} blocked media {media_id}: {request.reason}")

    return BlockMediaResponse(
        media_id=media.id,
        is_blocked=True,
        blocked_at=media.blocked_at,
        blocked_by=admin.username,
        block_reason=media.block_reason,
        message=f"Media '{media.title}' has been blocked",
    )


@router.post("/metadata/{media_id}/unblock", response_model=BlockMediaResponse)
async def unblock_media(
    media_id: int,
    admin: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Unblock media content (Moderator/Admin only).

    Makes the content visible to regular users again.
    """
    media = await session.get(Media, media_id)
    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media with id {media_id} not found",
        )

    if not media.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Media is not blocked",
        )

    # Get blocker info before unblocking
    previous_reason = media.block_reason

    # Unblock the media
    media.is_blocked = False
    media.blocked_at = None
    media.blocked_by_user_id = None
    media.block_reason = None

    await session.commit()
    await session.refresh(media)

    logger.info(f"User {admin.username} unblocked media {media_id} (was blocked for: {previous_reason})")

    return BlockMediaResponse(
        media_id=media.id,
        is_blocked=False,
        blocked_at=None,
        blocked_by=None,
        block_reason=None,
        message=f"Media '{media.title}' has been unblocked",
    )


class BlockedMediaItem(BaseModel):
    id: int
    title: str
    type: str
    year: int | None = None
    poster: str | None = None
    external_ids: dict
    blocked_at: datetime | None = None
    blocked_by: str | None = None
    block_reason: str | None = None


class BlockedMediaListResponse(BaseModel):
    items: list[BlockedMediaItem]
    total: int
    page: int
    page_size: int
    has_more: bool


@router.get("/media/blocked", response_model=BlockedMediaListResponse)
async def list_blocked_media(
    type: MediaType | None = Query(None, description="Filter by media type (movie, series, tv)"),
    search: str | None = Query(None, description="Search by title"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List all blocked media items (Admin only).

    Returns paginated blocked content with block details.
    """
    from sqlalchemy.orm import selectinload as sa_selectinload

    base_query = (
        select(Media)
        .where(Media.is_blocked == True)
        .options(
            sa_selectinload(Media.images),
        )
    )
    count_query = select(func.count(Media.id)).where(Media.is_blocked == True)

    if type:
        base_query = base_query.where(Media.type == type)
        count_query = count_query.where(Media.type == type)

    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(Media.title.ilike(pattern))
        count_query = count_query.where(Media.title.ilike(pattern))

    base_query = base_query.order_by(Media.blocked_at.desc()).offset((page - 1) * page_size).limit(page_size)

    result = await session.exec(base_query)
    media_items = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Batch fetch external IDs and blocked-by usernames.
    media_ids = [m.id for m in media_items]
    blocker_ids = [m.blocked_by_user_id for m in media_items if m.blocked_by_user_id]

    from db.crud import get_all_external_ids_batch

    all_external_ids: dict[int, dict] = {}
    if media_ids:
        all_external_ids = await get_all_external_ids_batch(session, media_ids)

    blocker_map: dict[int, str] = {}
    if blocker_ids:
        blocker_result = await session.exec(select(User.id, User.username).where(User.id.in_(blocker_ids)))
        blocker_map = {uid: uname for uid, uname in blocker_result.all()}

    items = []
    for m in media_items:
        poster = next(
            (img.url for img in (m.images or []) if img.is_primary and img.image_type == "poster"),
            None,
        )
        items.append(
            BlockedMediaItem(
                id=m.id,
                title=m.title,
                type=m.type.value,
                year=m.year,
                poster=poster,
                external_ids=all_external_ids.get(m.id, {}),
                blocked_at=m.blocked_at,
                blocked_by=blocker_map.get(m.blocked_by_user_id) if m.blocked_by_user_id else None,
                block_reason=m.block_reason,
            )
        )

    return BlockedMediaListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=((page - 1) * page_size + len(items)) < total,
    )


# ============================================
# Torrent Stream Endpoints (New Architecture)
# TorrentStream joins Stream (base) for common attributes
# StreamMediaLink connects streams to media
# ============================================


@router.post("/torrent-streams/{stream_id}/block")
async def block_torrent_stream(
    stream_id: int,  # TorrentStream.id
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Block a torrent stream (Admin only).

    Sets is_blocked=True on the Stream base table.
    """
    query = select(TorrentStream).where(TorrentStream.id == stream_id).options(selectinload(TorrentStream.stream))
    result = await session.exec(query)
    torrent = result.first()

    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Torrent stream not found",
        )

    # is_blocked is on Stream base
    torrent.stream.is_blocked = True
    session.add(torrent.stream)
    await session.commit()

    logger.info(f"Admin blocked torrent stream {stream_id}")
    return {"message": "Torrent stream blocked successfully"}


# ============================================
# TV Stream Endpoints (New Architecture)
# TV streams = Media (type=TV) + TVMetadata + Stream + HTTPStream + StreamMediaLink
# ============================================
