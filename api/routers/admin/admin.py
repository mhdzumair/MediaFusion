"""
Admin API endpoints for database management.
Provides CRUD operations for Metadata, Torrent Streams, and TV Streams.
Admin-only access.
"""

import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import selectinload
from sqlmodel import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db.crud import (
    add_external_id,
    get_all_external_ids_dict,
    get_canonical_external_id,
    get_media_by_external_id,
)
from db.database import get_async_session
from db.enums import MediaType, NudityStatus, TorrentType, UserRole
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
    Person,
    SeriesMetadata,
    Stream,
    StreamLanguageLink,
    StreamMediaLink,
    TorrentStream,
    TorrentTrackerLink,
    Tracker,
    TVMetadata,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin Database Management"])


# ============================================
# Pydantic Schemas - Reference Data
# ============================================


class ReferenceItem(BaseModel):
    """Base reference item with id and name."""

    id: int
    name: str
    usage_count: int = 0


class ReferenceItemCreate(BaseModel):
    """Create a new reference item."""

    name: str


class ReferenceListResponse(BaseModel):
    """Response for listing reference data with pagination."""

    items: list[ReferenceItem]
    total: int
    page: int = 1
    per_page: int = 50
    pages: int = 1
    has_more: bool = False


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
            selectinload(Media.images),
            selectinload(Media.ratings),
        )
    )
    result = await session.exec(query)
    base = result.first()

    if not base:
        return None

    # Get type-specific metadata with relationships
    specific = None
    if base.type == MediaType.MOVIE:
        query = (
            select(MovieMetadata)
            .where(MovieMetadata.media_id == base.id)
            .options(
                selectinload(MovieMetadata.stars),
                selectinload(MovieMetadata.parental_certificates),
            )
        )
        result = await session.exec(query)
        specific = result.first()
    elif base.type == MediaType.SERIES:
        query = (
            select(SeriesMetadata)
            .where(SeriesMetadata.media_id == base.id)
            .options(
                selectinload(SeriesMetadata.stars),
                selectinload(SeriesMetadata.parental_certificates),
            )
        )
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
        runtime=str(base.runtime_minutes) if base.runtime_minutes else None,
        website=None,  # No longer stored on Media
        total_streams=base.total_streams,
        created_at=base.created_at,
        updated_at=base.updated_at,
        last_stream_added=base.last_stream_added,
        genres=[g.name for g in base.genres] if base.genres else [],
        catalogs=[c.name for c in base.catalogs] if base.catalogs else [],
        aka_titles=[a.title for a in base.aka_titles] if base.aka_titles else [],
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
        if hasattr(specific, "stars"):
            response.stars = [s.name for s in specific.stars] if specific.stars else []
        if hasattr(specific, "parental_certificates"):
            response.parental_certificates = (
                [p.name for p in specific.parental_certificates] if specific.parental_certificates else []
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
    # Remove existing
    existing = (await session.exec(select(AkaTitle).where(AkaTitle.media_id == media_id))).all()
    for aka in existing:
        await session.delete(aka)

    # Add new
    for title in titles:
        aka = AkaTitle(media_id=media_id, title=title)
        session.add(aka)


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


@router.get("/stats", response_model=MetadataStatsResponse)
async def get_admin_stats(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get database statistics for admin dashboard."""
    # Count movies
    movie_count = await session.exec(
        select(func.count()).select_from(select(Media).where(Media.type == MediaType.MOVIE).subquery())
    )
    total_movies = movie_count.one()

    # Count series
    series_count = await session.exec(
        select(func.count()).select_from(select(Media).where(Media.type == MediaType.SERIES).subquery())
    )
    total_series = series_count.one()

    # Count TV
    tv_count = await session.exec(
        select(func.count()).select_from(select(Media).where(Media.type == MediaType.TV).subquery())
    )
    total_tv = tv_count.one()

    # Count torrent streams
    stream_count = await session.exec(select(func.count()).select_from(TorrentStream))
    total_streams = stream_count.one()

    # Count TV streams (HTTPStreams linked to TV type media)
    # In new architecture, count HTTPStream entries
    tv_stream_count = await session.exec(select(func.count()).select_from(HTTPStream))
    total_tv_streams = tv_stream_count.one()

    return MetadataStatsResponse(
        total_movies=total_movies,
        total_series=total_series,
        total_tv=total_tv,
        total_streams=total_streams,
        total_tv_streams=total_tv_streams,
    )


@router.get("/metadata", response_model=MetadataListResponse)
async def list_metadata(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    media_type: Literal["movie", "series", "tv"] | None = None,
    search: str | None = None,
    has_streams: bool | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all metadata with pagination and filters (Admin only)."""
    # Base query with eager loading of relationships
    query = select(Media).options(
        selectinload(Media.genres),
        selectinload(Media.catalogs),
        selectinload(Media.aka_titles),
        selectinload(Media.images),
        selectinload(Media.ratings).selectinload(MediaRating.provider),
    )

    # Apply filters
    if media_type:
        type_map = {
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "tv": MediaType.TV,
        }
        query = query.where(Media.type == type_map[media_type])

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Media.title.ilike(search_pattern),
                Media.id.ilike(search_pattern),
            )
        )

    if has_streams is not None:
        if has_streams:
            query = query.where(Media.total_streams > 0)
        else:
            query = query.where(Media.total_streams == 0)

    # Count total (without eager loading for efficiency)
    count_query = select(func.count(Media.id))
    if media_type:
        type_map = {
            "movie": MediaType.MOVIE,
            "series": MediaType.SERIES,
            "tv": MediaType.TV,
        }
        count_query = count_query.where(Media.type == type_map[media_type])
    if search:
        search_pattern = f"%{search}%"
        count_query = count_query.where(
            or_(
                Media.title.ilike(search_pattern),
                Media.id.ilike(search_pattern),
            )
        )
    if has_streams is not None:
        if has_streams:
            count_query = count_query.where(Media.total_streams > 0)
        else:
            count_query = count_query.where(Media.total_streams == 0)
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page).order_by(Media.created_at.desc())

    result = await session.exec(query)
    items = result.all()

    # Build responses
    responses = []
    for base in items:
        responses.append(format_metadata_response(base))

    return MetadataListResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 1,
    )


@router.get("/metadata/{media_id}", response_model=MetadataResponse)
async def get_metadata(
    media_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific metadata entry (Admin only). Uses media_id (internal ID)."""
    from db.crud.media import get_all_external_ids_dict

    result = await get_metadata_with_relations(session, media_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    base, specific = result
    ext_ids_dict = await get_all_external_ids_dict(session, media_id)
    return format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict)


@router.patch("/metadata/{media_id}", response_model=MetadataResponse)
async def update_metadata(
    media_id: int,
    update_data: MetadataUpdateRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update metadata fields (Admin only). Uses media_id (internal ID)."""
    base = await session.get(Media, media_id)
    if not base:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Extract relationship fields for special handling
    update_dict = update_data.model_dump(exclude_unset=True)
    genres_list = update_dict.pop("genres", None)
    catalogs_list = update_dict.pop("catalogs", None)
    stars_list = update_dict.pop("stars", None)
    parental_certificates_list = update_dict.pop("parental_certificates", None)
    aka_titles_list = update_dict.pop("aka_titles", None)

    # Handle end_date - parse from ISO string if provided
    if "end_date" in update_dict:
        end_date_str = update_dict.pop("end_date")
        if end_date_str is not None:
            from datetime import date

            try:
                update_dict["end_date"] = date.fromisoformat(end_date_str)
            except (ValueError, TypeError):
                pass  # Keep existing value if parsing fails
        else:
            update_dict["end_date"] = None

    # Handle nudity_status on Media table (moved from type-specific metadata)
    if "nudity_status" in update_dict:
        nudity_val = update_dict.pop("nudity_status")
        if nudity_val:
            try:
                base.nudity_status = NudityStatus(nudity_val)
            except ValueError:
                pass

    # Extract type-specific fields (nudity_status is now on Media, not here)
    type_specific_fields = {}
    for field in ["imdb_rating", "tmdb_rating", "country", "tv_language", "logo"]:
        if field in update_dict:
            type_specific_fields[field] = update_dict.pop(field)

    # Update base metadata fields
    for field, value in update_dict.items():
        if value is not None:
            setattr(base, field, value)

    session.add(base)

    # Update type-specific metadata if needed
    if type_specific_fields:
        if base.type == MediaType.MOVIE:
            specific = await session.get(MovieMetadata, media_id)
            if specific:
                for field, value in type_specific_fields.items():
                    if hasattr(specific, field) and value is not None:
                        setattr(specific, field, value)
                session.add(specific)
        elif base.type == MediaType.SERIES:
            specific = await session.get(SeriesMetadata, media_id)
            if specific:
                for field, value in type_specific_fields.items():
                    if hasattr(specific, field) and value is not None:
                        setattr(specific, field, value)
                session.add(specific)
        elif base.type == MediaType.TV:
            specific = await session.get(TVMetadata, media_id)
            if specific:
                for field, value in type_specific_fields.items():
                    if hasattr(specific, field) and value is not None:
                        setattr(specific, field, value)
                session.add(specific)

    # Update relationships if provided
    if genres_list is not None:
        await update_metadata_genres(session, media_id, genres_list)

    if catalogs_list is not None:
        await update_metadata_catalogs(session, media_id, catalogs_list)

    if stars_list is not None:
        await update_metadata_cast(session, media_id, stars_list)

    if parental_certificates_list is not None:
        await update_metadata_parental_certificates(session, media_id, parental_certificates_list)

    if aka_titles_list is not None:
        await update_metadata_aka_titles(session, media_id, aka_titles_list)

    await session.commit()

    # Re-fetch with all relationships for response
    result = await get_metadata_with_relations(session, media_id)
    base, specific = result
    ext_ids_dict = await get_all_external_ids_dict(session, media_id)

    logger.info(f"Admin updated metadata {media_id}")
    return format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict)


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

    # Delete associated streams via StreamMediaLink
    # In new architecture, streams are linked to media via StreamMediaLink table
    # First get stream IDs linked to this media
    stream_links = await session.exec(select(StreamMediaLink).where(StreamMediaLink.media_id == base.id))
    linked_stream_ids = [link.stream_id for link in stream_links.all()]

    # Delete the links first
    for link in stream_links.all():
        await session.delete(link)

    # Delete associated streams (this will cascade to type-specific tables)
    for stream_id in linked_stream_ids:
        stream = await session.get(Stream, stream_id)
        if stream:
            await session.delete(stream)

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


@router.get("/blocked-media", response_model=MetadataListResponse)
async def list_blocked_media(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List all blocked media (Moderator/Admin only).
    """
    # Count total blocked
    count_query = select(func.count(Media.id)).where(Media.is_blocked == True)
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Calculate pagination
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    offset = (page - 1) * per_page

    # Fetch blocked media with pagination
    query = (
        select(Media).where(Media.is_blocked == True).order_by(Media.blocked_at.desc()).offset(offset).limit(per_page)
    )
    result = await session.exec(query)
    blocked_media = result.all()

    # Convert to response format
    items = []
    for media in blocked_media:
        external_ids = await get_all_external_ids_dict(session, media.id)
        items.append(
            MetadataResponse(
                id=media.id,
                external_ids=external_ids,
                title=media.title,
                type=media.type.value,
                year=media.year,
                poster=None,  # We'd need to fetch from MediaImage
                total_streams=media.total_streams,
                is_blocked=media.is_blocked,
                blocked_at=media.blocked_at,
                block_reason=media.block_reason,
            )
        )

    return MetadataListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


# ============================================
# External Metadata Fetch & ID Migration Endpoints
# ============================================


class ExternalMetadataPreview(BaseModel):
    """Preview of metadata from external provider."""

    provider: str  # imdb, tmdb
    external_id: str  # The ID from the external provider
    title: str
    year: int | None = None
    description: str | None = None
    poster: str | None = None
    background: str | None = None
    genres: list[str] = []
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    nudity_status: str | None = None
    parental_certificates: list[str] = []
    stars: list[str] = []
    aka_titles: list[str] = []
    runtime: str | None = None
    # For linking
    imdb_id: str | None = None
    tmdb_id: str | None = None


class FetchExternalRequest(BaseModel):
    """Request to fetch metadata from external provider."""

    provider: Literal["imdb", "tmdb"]
    external_id: str  # IMDb ID (tt...) or TMDB ID


class MigrateIdRequest(BaseModel):
    """Request to migrate internal ID to external ID."""

    new_external_id: str  # The new IMDb or TMDB ID to use


class SearchExternalRequest(BaseModel):
    """Request to search for metadata on external providers."""

    provider: Literal["imdb", "tmdb"]
    title: str
    year: int | None = None
    media_type: Literal["movie", "series"] | None = None


class SearchExternalResponse(BaseModel):
    """Response with multiple search results."""

    results: list[ExternalMetadataPreview]


@router.post("/metadata/{media_id}/fetch-external", response_model=ExternalMetadataPreview)
async def fetch_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Fetch metadata from an external provider (IMDb/TMDB) for preview.
    This doesn't update the database - just returns what the external provider has.
    Use the update endpoint to apply changes.
    Uses media_id (internal ID).
    """
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    media_type = "movie" if base.type == MediaType.MOVIE else "series"

    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import get_imdb_title_data

            data = await get_imdb_title_data(request.external_id, media_type)
        else:  # tmdb
            from scrapers.tmdb_data import get_tmdb_data

            data = await get_tmdb_data(request.external_id, media_type, load_episodes=False)

        if not data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found on {request.provider.upper()} for ID {request.external_id}",
            )

        return ExternalMetadataPreview(
            provider=request.provider,
            external_id=request.external_id,
            title=data.get("title", ""),
            year=data.get("year"),
            description=data.get("description"),
            poster=data.get("poster"),
            background=data.get("background"),
            genres=data.get("genres", []),
            imdb_rating=data.get("imdb_rating"),
            tmdb_rating=data.get("tmdb_rating"),
            nudity_status=data.get("parent_guide_nudity_status"),
            parental_certificates=data.get("parent_guide_certificates", []),
            stars=data.get("stars", []),
            aka_titles=data.get("aka_titles", []),
            runtime=data.get("runtime"),
            imdb_id=data.get("imdb_id"),
            tmdb_id=data.get("tmdb_id"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching external metadata: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch metadata: {str(e)}")


@router.post("/metadata/{media_id}/apply-external", response_model=MetadataResponse)
async def apply_external_metadata(
    media_id: int,
    request: FetchExternalRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Fetch and apply metadata from an external provider (IMDb/TMDB).
    This will update the database with fresh data from the external source.
    Uses media_id (internal ID).
    """
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    media_type = "movie" if base.type == MediaType.MOVIE else "series"

    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import get_imdb_title_data

            data = await get_imdb_title_data(request.external_id, media_type)
        else:  # tmdb
            from scrapers.tmdb_data import get_tmdb_data

            data = await get_tmdb_data(request.external_id, media_type, load_episodes=False)

        if not data:
            raise HTTPException(
                status_code=404,
                detail=f"No data found on {request.provider.upper()} for ID {request.external_id}",
            )

        # Update base metadata
        if data.get("title"):
            base.title = data["title"]
        if data.get("year"):
            base.year = data["year"]
        if data.get("description"):
            base.description = data["description"]
        if data.get("runtime"):
            # Parse runtime to minutes
            runtime_str = data["runtime"]
            if runtime_str:
                try:
                    if "min" in runtime_str.lower():
                        base.runtime_minutes = int(runtime_str.lower().replace("min", "").strip())
                except (ValueError, TypeError):
                    pass

        # Update nudity status on Media
        nudity = data.get("parent_guide_nudity_status")
        if nudity:
            try:
                base.nudity_status = NudityStatus(nudity)
            except ValueError:
                pass

        # Update external IDs in MediaExternalID table
        from db.crud.media import add_external_id

        if request.provider == "imdb" and data.get("imdb_id"):
            await add_external_id(session, base.id, "imdb", data["imdb_id"])
        elif request.provider == "tmdb" and data.get("tmdb_id"):
            await add_external_id(session, base.id, "tmdb", str(data["tmdb_id"]))

        session.add(base)

        # Update relationships
        if data.get("genres"):
            await update_metadata_genres(session, base.id, data["genres"])

        if data.get("stars"):
            await update_metadata_cast(session, base.id, data["stars"])

        if data.get("parent_guide_certificates"):
            await update_metadata_parental_certificates(session, base.id, data["parent_guide_certificates"])

        if data.get("aka_titles"):
            await update_metadata_aka_titles(session, base.id, data["aka_titles"])

        await session.commit()

        # Re-fetch with all relationships for response
        result = await get_metadata_with_relations(session, base.id)
        base, specific = result
        ext_ids_dict = await get_all_external_ids_dict(session, base.id)

        logger.info(f"Admin applied external metadata from {request.provider} to {media_id}")
        return format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying external metadata: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to apply metadata: {str(e)}")


@router.post("/metadata/{media_id}/migrate-id", response_model=MetadataResponse)
async def migrate_metadata_id(
    media_id: int,
    request: MigrateIdRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Migrate an internal MediaFusion ID (mf* or mftmdb*) to a proper external ID.
    This adds or updates the external ID in the MediaExternalID table.
    Uses media_id (internal ID).
    """
    # Verify metadata exists
    base = await session.get(Media, media_id)

    if not base:
        raise HTTPException(status_code=404, detail="Metadata not found")

    # Get current canonical ID for logging
    old_id = await get_canonical_external_id(session, base.id)
    new_id = request.new_external_id.strip()

    # Validate new ID format and determine provider
    if new_id.startswith("tt"):
        provider = "imdb"
        provider_id = new_id
    elif new_id.startswith("tmdb:"):
        provider = "tmdb"
        provider_id = new_id[5:]  # Remove 'tmdb:' prefix
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid external ID format. Use 'tt1234567' for IMDb or 'tmdb:12345' for TMDB",
        )

    # Check if new ID already exists for another media
    existing_check = await session.exec(
        select(MediaExternalID).where(
            MediaExternalID.provider == provider,
            MediaExternalID.external_id == provider_id,
            MediaExternalID.media_id != base.id,
        )
    )
    if existing_check.first():
        raise HTTPException(
            status_code=409,
            detail=f"External ID {new_id} is already in use by another media item",
        )

    # Add or update the external ID in MediaExternalID table
    await add_external_id(session, base.id, provider, provider_id)
    await session.commit()

    # Re-fetch with all relationships for response
    result = await get_metadata_with_relations(session, base.id)
    base, specific = result
    ext_ids_dict = await get_all_external_ids_dict(session, base.id)

    logger.info(f"Admin migrated metadata ID from {old_id} to {new_id}")
    return format_metadata_response(base, specific, ext_ids_dict=ext_ids_dict)


@router.post("/metadata/search-external", response_model=SearchExternalResponse)
async def search_external_metadata(
    request: SearchExternalRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Search for metadata on external providers (IMDb/TMDB).
    Returns multiple results to choose from for ID migration.
    """
    try:
        if request.provider == "imdb":
            from scrapers.imdb_data import search_multiple_imdb

            results = await search_multiple_imdb(
                title=request.title,
                year=request.year,
                media_type=request.media_type,
                limit=10,
            )
        else:  # tmdb
            from scrapers.tmdb_data import search_multiple_tmdb

            results = await search_multiple_tmdb(
                title=request.title,
                year=request.year,
                media_type=request.media_type,
                limit=10,
            )

        previews = []
        for data in results:
            previews.append(
                ExternalMetadataPreview(
                    provider=request.provider,
                    external_id=data.get("imdb_id") or data.get("tmdb_id") or "",
                    title=data.get("title", ""),
                    year=data.get("year"),
                    description=data.get("description"),
                    poster=data.get("poster"),
                    background=data.get("background"),
                    genres=data.get("genres", []),
                    imdb_rating=data.get("imdb_rating"),
                    tmdb_rating=data.get("tmdb_rating"),
                    nudity_status=data.get("parent_guide_nudity_status"),
                    parental_certificates=data.get("parent_guide_certificates", []),
                    stars=data.get("stars", [])[:5],  # Limit stars for preview
                    aka_titles=data.get("aka_titles", [])[:5],  # Limit aka titles
                    runtime=data.get("runtime"),
                    imdb_id=data.get("imdb_id"),
                    tmdb_id=data.get("tmdb_id"),
                )
            )

        return SearchExternalResponse(results=previews)

    except Exception as e:
        logger.error(f"Error searching external metadata: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to search: {str(e)}")


# ============================================
# Torrent Stream Endpoints (New Architecture)
# TorrentStream joins Stream (base) for common attributes
# StreamMediaLink connects streams to media
# ============================================


@router.get("/torrent-streams", response_model=TorrentStreamListResponse)
async def list_torrent_streams(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    external_id: str | None = None,  # Filter by external ID (via MediaExternalID table)
    search: str | None = None,
    source: str | None = None,
    is_blocked: bool | None = None,
    resolution: str | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List torrent streams with pagination and filters (Admin only).

    Queries TorrentStream joined with Stream base for common attributes.
    Uses StreamMediaLink to filter by media.
    """
    # Base query - join TorrentStream with Stream
    query = (
        select(TorrentStream)
        .join(Stream, TorrentStream.stream_id == Stream.id)
        .options(
            selectinload(TorrentStream.stream).selectinload(Stream.languages),
            selectinload(TorrentStream.trackers),
            selectinload(TorrentStream.files),
        )
    )

    # Filter by media external_id if provided (via MediaExternalID lookup)
    if external_id:
        # Find media_id from external_id via MediaExternalID table
        media_for_ext = await get_media_by_external_id(session, external_id)
        if media_for_ext:
            query = query.join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id).where(
                StreamMediaLink.media_id == media_for_ext.id
            )
        else:
            # No media found for this external_id, return empty result
            return TorrentStreamListResponse(torrent_streams=[], total=0, page=page, per_page=per_page)

    # Search in Stream name and info_hash
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Stream.name.ilike(search_pattern),
                TorrentStream.info_hash.ilike(search_pattern),
            )
        )

    # Filters on Stream base
    if source:
        query = query.where(Stream.source == source)

    if is_blocked is not None:
        query = query.where(Stream.is_blocked == is_blocked)

    if resolution:
        query = query.where(Stream.resolution == resolution)

    # Count total
    count_subquery = query.subquery()
    count_query = select(func.count()).select_from(count_subquery)
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Apply pagination - sort by Stream.created_at
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page).order_by(Stream.created_at.desc())

    result = await session.exec(query)
    torrents = result.all()

    # Build responses
    responses = []
    for torrent in torrents:
        # Torrent already has stream relationship loaded
        responses.append(format_torrent_stream_response(torrent))

    return TorrentStreamListResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 1,
    )


@router.get("/torrent-streams/{stream_id}", response_model=TorrentStreamResponse)
async def get_torrent_stream(
    stream_id: int,  # Now integer
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific torrent stream by TorrentStream.id (Admin only)."""
    query = (
        select(TorrentStream)
        .where(TorrentStream.id == stream_id)
        .options(
            selectinload(TorrentStream.stream).selectinload(Stream.languages),
            selectinload(TorrentStream.trackers),
            selectinload(TorrentStream.files),
        )
    )
    result = await session.exec(query)
    torrent = result.first()

    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Torrent stream not found",
        )

    return format_torrent_stream_response(torrent)


@router.patch("/torrent-streams/{stream_id}", response_model=TorrentStreamResponse)
async def update_torrent_stream(
    stream_id: int,  # Now integer (TorrentStream.id)
    update_data: TorrentStreamUpdateRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update torrent stream fields (Admin only).

    Updates span multiple tables:
    - Stream: name, source, resolution, codec, quality, audio, hdr, uploader, is_blocked
    - TorrentStream: seeders, leechers, torrent_type, uploaded_at
    """
    # Get TorrentStream with Stream relationship
    query = select(TorrentStream).where(TorrentStream.id == stream_id).options(selectinload(TorrentStream.stream))
    result = await session.exec(query)
    torrent = result.first()

    if not torrent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Torrent stream not found",
        )

    update_dict = update_data.model_dump(exclude_unset=True)

    # Extract relationship fields
    languages_list = update_dict.pop("languages", None)
    trackers_list = update_dict.pop("trackers", None)

    # Stream base fields
    stream_fields = {
        "name",
        "source",
        "resolution",
        "codec",
        "quality",
        "audio",
        "hdr",
        "uploader",
        "is_blocked",
    }
    base_stream = torrent.stream

    for field in stream_fields:
        if field in update_dict and update_dict[field] is not None:
            setattr(base_stream, field, update_dict[field])
    session.add(base_stream)

    # TorrentStream fields
    torrent_fields = {"seeders", "leechers", "torrent_type", "uploaded_at"}

    # Handle torrent_type enum conversion
    if "torrent_type" in update_dict and update_dict["torrent_type"]:
        try:
            update_dict["torrent_type"] = TorrentType(update_dict["torrent_type"])
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid torrent_type. Must be one of: {[t.value for t in TorrentType]}",
            )

    for field in torrent_fields:
        if field in update_dict and update_dict[field] is not None:
            setattr(torrent, field, update_dict[field])
    session.add(torrent)

    # Update relationships if provided
    if languages_list is not None:
        await update_stream_languages(session, base_stream.id, languages_list)

    if trackers_list is not None:
        await update_torrent_trackers(session, torrent.id, trackers_list)

    await session.commit()

    # Re-fetch with all relationships loaded
    query = (
        select(TorrentStream)
        .where(TorrentStream.id == stream_id)
        .options(
            selectinload(TorrentStream.stream).selectinload(Stream.languages),
            selectinload(TorrentStream.trackers),
            selectinload(TorrentStream.files),
        )
    )
    result = await session.exec(query)
    torrent = result.first()

    logger.info(f"Admin updated torrent stream {stream_id}")
    return format_torrent_stream_response(torrent)


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


@router.post("/torrent-streams/{stream_id}/unblock")
async def unblock_torrent_stream(
    stream_id: int,  # TorrentStream.id
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Unblock a torrent stream (Admin only).

    Sets is_blocked=False on the Stream base table.
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
    torrent.stream.is_blocked = False
    session.add(torrent.stream)
    await session.commit()

    logger.info(f"Admin unblocked torrent stream {stream_id}")
    return {"message": "Torrent stream unblocked successfully"}


# ============================================
# TV Stream Endpoints (New Architecture)
# TV streams = Media (type=TV) + TVMetadata + Stream + HTTPStream + StreamMediaLink
# ============================================


@router.get("/tv-streams", response_model=TVStreamListResponse)
async def list_tv_streams(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    external_id: str | None = None,  # Filter by external ID (via MediaExternalID table)
    search: str | None = None,
    source: str | None = None,
    is_active: bool | None = None,
    country: str | None = None,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List TV streams with pagination and filters (Admin only).

    New architecture joins:
    - HTTPStream -> Stream (base)
    - StreamMediaLink -> Media (type=TV)
    - Media -> TVMetadata
    """
    # Build query joining HTTPStream -> Stream -> StreamMediaLink -> Media -> TVMetadata
    query = (
        select(HTTPStream, Stream, Media, TVMetadata)
        .join(Stream, HTTPStream.stream_id == Stream.id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, StreamMediaLink.media_id == Media.id)
        .outerjoin(TVMetadata, TVMetadata.media_id == Media.id)
        .where(Media.type == MediaType.TV)
    )

    # Apply filters
    if external_id:
        # Find media_id from external_id via MediaExternalID table
        media_for_ext = await get_media_by_external_id(session, external_id)
        if media_for_ext:
            query = query.where(Media.id == media_for_ext.id)
        else:
            # No media found for this external_id, return empty result
            return TVStreamListResponse(tv_streams=[], total=0, page=page, per_page=per_page)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Stream.name.ilike(search_pattern),
                Media.title.ilike(search_pattern),
                HTTPStream.url.ilike(search_pattern),
            )
        )

    if source:
        query = query.where(Stream.source == source)

    if is_active is not None:
        query = query.where(Stream.is_active == is_active)

    if country:
        query = query.where(TVMetadata.country == country)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page).order_by(Stream.created_at.desc())

    result = await session.exec(query)
    rows = result.all()

    # Build responses
    responses = []
    for http_stream, base_stream, media, tv_metadata in rows:
        responses.append(
            await format_tv_stream_response(
                session=session,
                http_stream=http_stream,
                base_stream=base_stream,
                media=media,
                tv_metadata=tv_metadata,
                namespaces=[],  # TODO: Load namespaces if needed
            )
        )

    return TVStreamListResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page if total > 0 else 1,
    )


@router.get("/tv-streams/{stream_id}", response_model=TVStreamResponse)
async def get_tv_stream(
    stream_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific TV stream by Stream.id (Admin only)."""
    query = (
        select(HTTPStream, Stream, Media, TVMetadata)
        .join(Stream, HTTPStream.stream_id == Stream.id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, StreamMediaLink.media_id == Media.id)
        .outerjoin(TVMetadata, TVMetadata.media_id == Media.id)
        .where(Stream.id == stream_id)
        .where(Media.type == MediaType.TV)
    )

    result = await session.exec(query)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TV stream not found",
        )

    http_stream, base_stream, media, tv_metadata = row
    return await format_tv_stream_response(
        session=session,
        http_stream=http_stream,
        base_stream=base_stream,
        media=media,
        tv_metadata=tv_metadata,
        namespaces=[],
    )


@router.patch("/tv-streams/{stream_id}", response_model=TVStreamResponse)
async def update_tv_stream(
    stream_id: int,
    update_data: TVStreamUpdateRequest,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update TV stream fields (Admin only).

    Updates span multiple tables:
    - Stream: name, source, is_active, is_blocked
    - HTTPStream: url, format
    - TVMetadata: country, tv_language
    """
    # First get the stream and related data
    query = (
        select(HTTPStream, Stream, Media, TVMetadata)
        .join(Stream, HTTPStream.stream_id == Stream.id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, StreamMediaLink.media_id == Media.id)
        .outerjoin(TVMetadata, TVMetadata.media_id == Media.id)
        .where(Stream.id == stream_id)
        .where(Media.type == MediaType.TV)
    )

    result = await session.exec(query)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TV stream not found",
        )

    http_stream, base_stream, media, tv_metadata = row
    update_dict = update_data.model_dump(exclude_unset=True)

    # Update Stream base fields
    stream_fields = {"name", "source", "is_active", "is_blocked"}
    for field in stream_fields:
        if field in update_dict and update_dict[field] is not None:
            setattr(base_stream, field, update_dict[field])
    session.add(base_stream)

    # Update HTTPStream fields
    http_fields = {"url", "format"}
    for field in http_fields:
        if field in update_dict and update_dict[field] is not None:
            setattr(http_stream, field, update_dict[field])
    session.add(http_stream)

    # Update TVMetadata fields (create if doesn't exist)
    tv_fields = {"country", "tv_language"}
    has_tv_updates = any(f in update_dict and update_dict[f] is not None for f in tv_fields)

    if has_tv_updates:
        if tv_metadata is None:
            tv_metadata = TVMetadata(media_id=media.id)
        for field in tv_fields:
            if field in update_dict and update_dict[field] is not None:
                setattr(tv_metadata, field, update_dict[field])
        session.add(tv_metadata)

    # Note: Namespace concept removed in v5 - TV streams are now user-based

    await session.commit()

    logger.info(f"Admin updated TV stream {stream_id}")
    return await format_tv_stream_response(
        session=session,
        http_stream=http_stream,
        base_stream=base_stream,
        media=media,
        tv_metadata=tv_metadata,
        namespaces=[],  # Namespace concept removed in v5
    )


@router.post("/tv-streams/{stream_id}/toggle-active")
async def toggle_tv_stream_active(
    stream_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Toggle TV stream active status (Admin only)."""
    base_stream = await session.get(Stream, stream_id)
    if not base_stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="TV stream not found",
        )

    base_stream.is_active = not base_stream.is_active
    session.add(base_stream)
    await session.commit()

    status_text = "active" if base_stream.is_active else "inactive"
    logger.info(f"Admin toggled TV stream {stream_id} to {status_text}")
    return {
        "message": f"TV stream marked as {status_text}",
        "is_active": base_stream.is_active,
    }


# ============================================
# Utility Endpoints
# ============================================


@router.get("/sources/torrent")
async def get_torrent_sources(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get distinct torrent stream sources for filtering.

    Source is on Stream base table, filtered to torrent type.
    """
    query = (
        select(Stream.source)
        .distinct()
        .join(TorrentStream, TorrentStream.stream_id == Stream.id)
        .where(Stream.source.isnot(None))
    )
    result = await session.exec(query)
    sources = result.all()
    return {"sources": sorted([s for s in sources if s])}


@router.get("/sources/tv")
async def get_tv_sources(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get distinct TV stream sources for filtering.

    Sources are on Stream table, filtered to TV type via join.
    """
    query = (
        select(Stream.source)
        .distinct()
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, StreamMediaLink.media_id == Media.id)
        .where(Media.type == MediaType.TV)
        .where(Stream.source.isnot(None))
    )
    result = await session.exec(query)
    sources = result.all()
    return {"sources": sorted([s for s in sources if s])}


@router.get("/countries")
async def get_tv_countries(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get distinct TV stream countries for filtering.

    Country is on TVMetadata table.
    """
    result = await session.exec(select(TVMetadata.country).distinct().where(TVMetadata.country.isnot(None)))
    countries = result.all()
    return {"countries": sorted([c for c in countries if c])}


@router.get("/resolutions")
async def get_resolutions(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get distinct stream resolutions for filtering.

    Resolution is on Stream base table.
    """
    result = await session.exec(select(Stream.resolution).distinct().where(Stream.resolution.isnot(None)))
    resolutions = result.all()
    return {"resolutions": sorted([r for r in resolutions if r])}


# ============================================
# Reference Data Endpoints
# ============================================


# --- Genres ---
@router.get("/reference/genres", response_model=ReferenceListResponse)
async def list_genres(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List genres with usage count and pagination."""
    # Count query
    count_query = select(func.count(Genre.id))
    if search:
        count_query = count_query.where(Genre.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Data query with pagination
    query = (
        select(
            Genre.id,
            Genre.name,
            func.count(MediaGenreLink.media_id).label("usage_count"),
        )
        .outerjoin(MediaGenreLink, Genre.id == MediaGenreLink.genre_id)
        .group_by(Genre.id, Genre.name)
    )

    if search:
        query = query.where(Genre.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Genre.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [
        ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count)
        for row in rows
        if row.name.lower() not in ["adult", "18+"]
    ]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/genres", response_model=ReferenceItem)
async def create_genre(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new genre."""
    # Check if exists
    result = await session.exec(select(Genre).where(Genre.name == data.name))
    if result.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Genre already exists")

    genre = Genre(name=data.name)
    session.add(genre)
    await session.commit()
    await session.refresh(genre)

    logger.info(f"Admin created genre: {data.name}")
    return ReferenceItem(id=genre.id, name=genre.name, usage_count=0)


@router.delete("/reference/genres/{genre_id}")
async def delete_genre(
    genre_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a genre."""
    genre = await session.get(Genre, genre_id)
    if not genre:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")

    await session.delete(genre)
    await session.commit()

    logger.info(f"Admin deleted genre: {genre.name}")
    return {"message": "Genre deleted successfully"}


# --- Catalogs ---
@router.get("/reference/catalogs", response_model=ReferenceListResponse)
async def list_catalogs(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List catalogs with usage count and pagination."""
    count_query = select(func.count(Catalog.id))
    if search:
        count_query = count_query.where(Catalog.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            Catalog.id,
            Catalog.name,
            func.count(MediaCatalogLink.media_id).label("usage_count"),
        )
        .outerjoin(MediaCatalogLink, Catalog.id == MediaCatalogLink.catalog_id)
        .group_by(Catalog.id, Catalog.name)
    )

    if search:
        query = query.where(Catalog.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Catalog.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/catalogs", response_model=ReferenceItem)
async def create_catalog(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new catalog."""
    result = await session.exec(select(Catalog).where(Catalog.name == data.name))
    if result.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Catalog already exists")

    catalog = Catalog(name=data.name)
    session.add(catalog)
    await session.commit()
    await session.refresh(catalog)

    logger.info(f"Admin created catalog: {data.name}")
    return ReferenceItem(id=catalog.id, name=catalog.name, usage_count=0)


@router.delete("/reference/catalogs/{catalog_id}")
async def delete_catalog(
    catalog_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a catalog."""
    catalog = await session.get(Catalog, catalog_id)
    if not catalog:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog not found")

    await session.delete(catalog)
    await session.commit()

    logger.info(f"Admin deleted catalog: {catalog.name}")
    return {"message": "Catalog deleted successfully"}


# --- Languages ---
@router.get("/reference/languages", response_model=ReferenceListResponse)
async def list_languages(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List languages with usage count and pagination."""
    count_query = select(func.count(Language.id))
    if search:
        count_query = count_query.where(Language.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            Language.id,
            Language.name,
            func.count(StreamLanguageLink.torrent_id).label("usage_count"),
        )
        .outerjoin(StreamLanguageLink, Language.id == StreamLanguageLink.language_id)
        .group_by(Language.id, Language.name)
    )

    if search:
        query = query.where(Language.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Language.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/languages", response_model=ReferenceItem)
async def create_language(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new language."""
    result = await session.exec(select(Language).where(Language.name == data.name))
    if result.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Language already exists")

    lang = Language(name=data.name)
    session.add(lang)
    await session.commit()
    await session.refresh(lang)

    logger.info(f"Admin created language: {data.name}")
    return ReferenceItem(id=lang.id, name=lang.name, usage_count=0)


@router.delete("/reference/languages/{language_id}")
async def delete_language(
    language_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a language."""
    lang = await session.get(Language, language_id)
    if not lang:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Language not found")

    await session.delete(lang)
    await session.commit()

    logger.info(f"Admin deleted language: {lang.name}")
    return {"message": "Language deleted successfully"}


# --- Stars (People) ---
# Note: Star is now Person in v5 architecture
@router.get("/reference/stars", response_model=ReferenceListResponse)
async def list_stars(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List people (actors/directors/writers) with usage count and pagination."""
    count_query = select(func.count(Person.id))
    if search:
        count_query = count_query.where(Person.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(Person.id, Person.name, func.count(MediaCast.media_id).label("usage_count"))
        .outerjoin(MediaCast, Person.id == MediaCast.person_id)
        .group_by(Person.id, Person.name)
    )

    if search:
        query = query.where(Person.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Person.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/stars", response_model=ReferenceItem)
async def create_star(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new person (actor/director/writer)."""
    result = await session.exec(select(Person).where(Person.name == data.name))
    if result.first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Person already exists")

    person = Person(name=data.name)
    session.add(person)
    await session.commit()
    await session.refresh(person)

    logger.info(f"Admin created person: {data.name}")
    return ReferenceItem(id=person.id, name=person.name, usage_count=0)


@router.delete("/reference/stars/{star_id}")
async def delete_star(
    star_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a person (actor/director/writer)."""
    person = await session.get(Person, star_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    await session.delete(person)
    await session.commit()

    logger.info(f"Admin deleted person: {person.name}")
    return {"message": "Person deleted successfully"}


# --- Parental Certificates ---
@router.get("/reference/parental-certificates", response_model=ReferenceListResponse)
async def list_parental_certificates(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List parental certificates with usage count and pagination."""
    count_query = select(func.count(ParentalCertificate.id))
    if search:
        count_query = count_query.where(ParentalCertificate.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            ParentalCertificate.id,
            ParentalCertificate.name,
            func.count(MediaParentalCertificateLink.media_id).label("usage_count"),
        )
        .outerjoin(
            MediaParentalCertificateLink,
            ParentalCertificate.id == MediaParentalCertificateLink.certificate_id,
        )
        .group_by(ParentalCertificate.id, ParentalCertificate.name)
    )

    if search:
        query = query.where(ParentalCertificate.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(ParentalCertificate.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/parental-certificates", response_model=ReferenceItem)
async def create_parental_certificate(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new parental certificate."""
    result = await session.exec(select(ParentalCertificate).where(ParentalCertificate.name == data.name))
    if result.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parental certificate already exists",
        )

    cert = ParentalCertificate(name=data.name)
    session.add(cert)
    await session.commit()
    await session.refresh(cert)

    logger.info(f"Admin created parental certificate: {data.name}")
    return ReferenceItem(id=cert.id, name=cert.name, usage_count=0)


@router.delete("/reference/parental-certificates/{cert_id}")
async def delete_parental_certificate(
    cert_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a parental certificate."""
    cert = await session.get(ParentalCertificate, cert_id)
    if not cert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parental certificate not found",
        )

    await session.delete(cert)
    await session.commit()

    logger.info(f"Admin deleted parental certificate: {cert.name}")
    return {"message": "Parental certificate deleted successfully"}


# Note: Namespace concept removed in v5 - TV streams are now user-based


# --- Announce URLs ---
@router.get("/reference/announce-urls", response_model=ReferenceListResponse)
async def list_announce_urls(
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """List announce URLs with usage count and pagination."""
    count_query = select(func.count(Tracker.id))
    if search:
        count_query = count_query.where(Tracker.name.ilike(f"%{search}%"))
    total_result = await session.exec(count_query)
    total = total_result.one()

    query = (
        select(
            Tracker.id,
            Tracker.name,
            func.count(TorrentTrackerLink.torrent_id).label("usage_count"),
        )
        .outerjoin(TorrentTrackerLink, Tracker.id == TorrentTrackerLink.announce_id)
        .group_by(Tracker.id, Tracker.name)
    )

    if search:
        query = query.where(Tracker.name.ilike(f"%{search}%"))

    offset = (page - 1) * per_page
    query = query.order_by(Tracker.name).offset(offset).limit(per_page)

    result = await session.exec(query)
    rows = result.all()

    items = [ReferenceItem(id=row.id, name=row.name, usage_count=row.usage_count) for row in rows]

    pages = (total + per_page - 1) // per_page if total > 0 else 1

    return ReferenceListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
        has_more=page < pages,
    )


@router.post("/reference/announce-urls", response_model=ReferenceItem)
async def create_announce_url(
    data: ReferenceItemCreate,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new announce URL."""
    result = await session.exec(select(Tracker).where(Tracker.name == data.name))
    if result.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Announce URL already exists",
        )

    url = Tracker(name=data.name)
    session.add(url)
    await session.commit()
    await session.refresh(url)

    logger.info(f"Admin created announce URL: {data.name}")
    return ReferenceItem(id=url.id, name=url.name, usage_count=0)


@router.delete("/reference/announce-urls/{url_id}")
async def delete_announce_url(
    url_id: int,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete an announce URL."""
    url = await session.get(Tracker, url_id)
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announce URL not found")

    await session.delete(url)
    await session.commit()

    logger.info(f"Admin deleted announce URL: {url.name}")
    return {"message": "Announce URL deleted successfully"}
