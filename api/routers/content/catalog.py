"""
Catalog API endpoints for the React frontend.
Provides browsing, searching, and stream fetching capabilities.
"""

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, union
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.dependencies import get_profile_context
from api.routers.user.auth import require_auth
from db import crud
from db.config import settings
from db.database import get_read_session
from db.enums import MediaType, UserRole
from db.models import (
    Catalog,
    Episode,
    EpisodeImage,
    FileMediaLink,
    Genre,
    Media,
    MediaCast,
    MediaCatalogLink,
    MediaCrew,
    MediaGenreLink,
    MediaImage,
    MediaRating,
    MetadataVote,
    Person,
    RatingProvider,
    Season,
    SeriesMetadata,
    Stream,
    StreamFile,
    StreamMediaLink,
    User,
)
from db.models.reference import format_catalog_name
from db.models.streams import StreamType
from db.schemas import StreamTemplate, TorrentStreamData
from streaming_providers import mapper
from streaming_providers.cache_helpers import get_cached_status, store_cached_info_hashes
from utils.const import (
    ADULT_GENRE_NAMES,
    CERTIFICATION_MAPPING,
    LANGUAGE_COUNTRY_FLAGS,
    STREAMING_PROVIDERS_SHORT_NAMES,
)
from utils.crypto import crypto_utils
from utils.network import encode_mediaflow_acestream_url, get_user_public_ip
from utils.parser import render_stream_template
from utils.profile_context import ProfileContext, ProfileDataProvider

logger = logging.getLogger(__name__)


def get_certification_category(certificates: list[str]) -> str | None:
    """
    Get the highest certification category from a list of certificates.
    Returns: All Ages, Children, Parental Guidance, Teens, Adults, Adults+, or None
    """
    if not certificates:
        return None

    # Order from most restrictive to least
    levels = ["Adults+", "Adults", "Teens", "Parental Guidance", "Children", "All Ages"]

    for level in levels:
        for cert in certificates:
            if cert in CERTIFICATION_MAPPING.get(level, []):
                return level

    return "Unknown"


router = APIRouter(prefix="/api/v1/catalog", tags=["Catalog"])


# ============================================
# Pydantic Schemas
# ============================================


class GenreResponse(BaseModel):
    id: int
    name: str


class CatalogResponse(BaseModel):
    id: int
    name: str
    display_name: str  # Human-readable name


class ProviderRating(BaseModel):
    """Rating from an external provider"""

    provider: str  # imdb, tmdb, trakt, rottentomatoes, metacritic, letterboxd
    provider_display_name: str  # IMDb, TMDB, etc.
    rating: float  # Normalized 0-10 scale
    rating_raw: float | None = None  # Original value (e.g., 87% for RT)
    max_rating: float = 10.0  # Original scale max (10, 100, 5)
    is_percentage: bool = False  # True for RT, Metacritic
    vote_count: int | None = None
    rating_type: str | None = None  # audience, critic, fresh
    certification: str | None = None  # fresh, rotten, certified_fresh


class CommunityRating(BaseModel):
    """MediaFusion community rating"""

    average_rating: float = 0.0  # 1-10 scale
    total_votes: int = 0
    upvotes: int = 0
    downvotes: int = 0
    user_vote: int | None = None  # Current user's vote if authenticated


class AllRatings(BaseModel):
    """All ratings for a media item"""

    external_ratings: list[ProviderRating] = []  # IMDb, TMDB, RT, etc.
    community_rating: CommunityRating | None = None  # MediaFusion votes
    # Convenience fields for quick display
    imdb_rating: float | None = None  # IMDb rating for backward compatibility
    tmdb_rating: float | None = None


class TrailerInfo(BaseModel):
    """Trailer/video information"""

    key: str  # YouTube video ID
    site: str = "YouTube"
    name: str | None = None
    type: str = "trailer"  # trailer, teaser, clip, etc.
    is_official: bool = True


class ExternalIds(BaseModel):
    """External IDs for a media item - all as strings for consistency"""

    imdb: str | None = None  # IMDb ID (tt...)
    tmdb: str | None = None  # TMDB ID
    tvdb: str | None = None  # TVDB ID
    mal: str | None = None  # MyAnimeList ID
    kitsu: str | None = None  # Kitsu ID

    @classmethod
    def from_dict(cls, data: dict) -> "ExternalIds":
        """Create from a dict of external IDs"""
        return cls(
            imdb=data.get("imdb"),
            tmdb=str(data["tmdb"]) if data.get("tmdb") else None,
            tvdb=str(data["tvdb"]) if data.get("tvdb") else None,
            mal=str(data["mal"]) if data.get("mal") else None,
            kitsu=str(data["kitsu"]) if data.get("kitsu") else None,
        )


class CatalogItemBase(BaseModel):
    """Base model for catalog items"""

    id: int  # Internal database ID (media_id)
    external_ids: ExternalIds  # All external IDs (imdb, tmdb, tvdb, mal)
    title: str
    type: str
    year: int | None = None
    # Images - frontend decides which to use based on user config (RPDB, etc.)
    poster: str | None = None  # Primary poster URL from database
    background: str | None = None  # Background/fanart URL from database
    description: str | None = None
    runtime: str | None = None
    genres: list[str] = []
    # All ratings
    ratings: AllRatings | None = None
    # Convenience - kept for backward compatibility
    imdb_rating: float | None = None
    last_stream_added: datetime | None = None
    likes_count: int = 0
    # Content guidance
    certification: str | None = None  # All Ages, Children, Parental Guidance, Teens, Adults, Adults+
    nudity: str | None = None  # None, Mild, Moderate, Severe
    # Content moderation (visible to admins/moderators only when blocked)
    is_blocked: bool = False
    block_reason: str | None = None


class CatalogItemDetail(CatalogItemBase):
    """Detailed model including additional information"""

    catalogs: list[str] = []
    aka_titles: list[str] = []
    # Series specific
    seasons: list[dict] | None = None
    # TV specific
    country: str | None = None
    tv_language: str | None = None
    # Credits
    cast: list[str] | None = None  # Cast members
    directors: list[str] | None = None  # Directors
    writers: list[str] | None = None  # Writers
    stars: list[str] | None = None  # Legacy - same as cast
    # Trailers/Videos
    trailers: list[TrailerInfo] = []
    # Metadata tracking
    last_refreshed_at: datetime | None = None
    last_scraped_at: datetime | None = None


class CatalogListResponse(BaseModel):
    """Paginated catalog list response"""

    items: list[CatalogItemBase]
    total: int
    page: int
    page_size: int
    has_more: bool


class StreamVoteSummary(BaseModel):
    """Summary of votes for a stream"""

    upvotes: int = 0
    downvotes: int = 0
    score: int = 0  # upvotes - downvotes
    user_vote: int | None = None  # Current user's vote: 1, -1, or None


class StreamInfo(BaseModel):
    """Stream information for display - combines Stremio stream with rich metadata"""

    # Core identifiers
    id: int | None = None  # Stream database ID
    torrent_stream_id: int | None = None  # TorrentStream ID (for torrent admin actions)
    info_hash: str | None = None  # Torrent info hash
    nzb_guid: str | None = None  # Usenet/NZB GUID

    # Stremio-compatible fields
    name: str  # Formatted name with provider, resolution, status
    description: str | None = None  # Formatted description
    url: str | None = None  # Playback URL (for debrid users)
    behavior_hints: dict | None = None

    # Rich metadata for frontend UI
    stream_name: str | None = None  # Raw torrent title/stream name
    stream_type: str | None = None  # Stream type: torrent, http, youtube, usenet, telegram, external_link
    # Single-value quality attributes
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    bit_depth: str | None = None
    # Multi-value quality attributes (formatted as strings for display)
    audio_formats: str | None = None  # Formatted audio string (e.g., "Atmos|DTS")
    channels: str | None = None  # Formatted channels string (e.g., "5.1|7.1")
    hdr_formats: str | None = None  # Formatted HDR string (e.g., "DV|HDR10")
    # Other metadata
    source: str | None = None
    languages: list[str] | None = None
    size: str | None = None  # Formatted size (e.g., "2.5 GB")
    size_bytes: int | None = None  # Raw size in bytes
    seeders: int | None = None
    uploader: str | None = None
    release_group: str | None = None  # Release group name
    cached: bool | None = None  # Whether stream is cached in debrid (None if not checked)
    # Release flags
    is_remastered: bool = False
    is_upscaled: bool = False
    is_proper: bool = False
    is_repack: bool = False
    is_extended: bool = False
    is_complete: bool = False
    is_dubbed: bool = False
    is_subbed: bool = False
    # Duration (primarily from Telegram streams)
    duration_seconds: int | None = None
    # Voting data
    votes: StreamVoteSummary | None = None
    # Episode links for series (for editing incorrect detections)
    episode_links: list[dict] | None = None


class EpisodeLinkInfo(BaseModel):
    """Episode link info for series streams"""

    file_id: int
    file_name: str
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None


class StreamingProviderInfo(BaseModel):
    """Info about a configured streaming provider"""

    service: str  # Provider service name (realdebrid, alldebrid, etc.)
    name: str | None = None  # User-defined display name
    enabled: bool = True


class StreamListResponse(BaseModel):
    """Response for streams endpoint"""

    streams: list[StreamInfo]
    season: int | None = None
    episode: int | None = None
    # Web playback requires MediaFlow - this indicates if it's available
    web_playback_enabled: bool = False  # Whether web browser playback is enabled (requires MediaFlow)
    # Multi-provider support
    streaming_providers: list[StreamingProviderInfo] = []  # All configured providers
    selected_provider: str | None = None  # Currently selected provider service name
    profile_id: int | None = None  # Currently selected profile ID


class CatalogInfo(BaseModel):
    """Catalog info with display name"""

    name: str  # Internal name (used for filtering)
    display_name: str  # Human-readable name


class AvailableCatalogsResponse(BaseModel):
    """Available catalogs grouped by type"""

    movies: list[CatalogInfo]
    series: list[CatalogInfo]
    tv: list[CatalogInfo]


# ============================================
# Helper Functions
# ============================================


def format_runtime(runtime: str | None) -> str | None:
    """Format runtime - returns as-is since it's already stored as a formatted string"""
    if not runtime:
        return None
    return runtime


def format_size(size_bytes: int | None) -> str | None:
    """Format file size in human-readable format"""
    if not size_bytes or size_bytes <= 0:
        return None
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# ============================================
# API Endpoints
# ============================================


@router.get("/available", response_model=AvailableCatalogsResponse)
async def get_available_catalogs(
    session: AsyncSession = Depends(get_read_session),
):
    """Get list of available catalogs grouped by type"""
    # Query distinct catalog names by type
    query = (
        select(Catalog.name, Catalog.display_name, Media.type)
        .join(MediaCatalogLink, MediaCatalogLink.catalog_id == Catalog.id)
        .join(Media, Media.id == MediaCatalogLink.media_id)
        .distinct()
    )
    result = await session.exec(query)
    catalogs = result.all()

    movies = {}  # name -> CatalogInfo
    series = {}
    tv = {}

    for name, display_name, media_type in catalogs:
        catalog_info = CatalogInfo(name=name, display_name=display_name or format_catalog_name(name))
        if media_type == MediaType.MOVIE:
            if name not in movies:
                movies[name] = catalog_info
        elif media_type == MediaType.SERIES:
            if name not in series:
                series[name] = catalog_info
        elif media_type == MediaType.TV:
            if name not in tv:
                tv[name] = catalog_info

    # Sort by display name
    return AvailableCatalogsResponse(
        movies=sorted(movies.values(), key=lambda c: c.display_name),
        series=sorted(series.values(), key=lambda c: c.display_name),
        tv=sorted(tv.values(), key=lambda c: c.display_name),
    )


@router.get("/genres", response_model=list[GenreResponse])
async def get_genres(
    catalog_type: Literal["movie", "series", "tv"] = Query("movie"),
    session: AsyncSession = Depends(get_read_session),
):
    """Get list of available genres for a catalog type"""
    media_type = MediaType(catalog_type)

    query = (
        select(Genre.id, Genre.name)
        .join(MediaGenreLink, MediaGenreLink.genre_id == Genre.id)
        .join(Media, Media.id == MediaGenreLink.media_id)
        .where(Media.type == media_type)
        .distinct()
        .order_by(Genre.name)
    )
    result = await session.exec(query)
    genres = result.all()

    return [GenreResponse(id=g[0], name=g[1]) for g in genres if g[1].lower() not in ADULT_GENRE_NAMES]


@router.get("/{catalog_type}", response_model=CatalogListResponse)
async def browse_catalog(
    catalog_type: Literal["movie", "series", "tv"],
    catalog: str | None = Query(None, description="Filter by catalog name"),
    genre: str | None = Query(None, description="Filter by genre"),
    search: str | None = Query(None, description="Search query"),
    sort: Literal["latest", "popular", "rating", "year", "title", "release_date"] | None = Query("latest"),
    sort_dir: Literal["asc", "desc"] | None = Query("desc", description="Sort direction"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    include_upcoming: bool = Query(False, description="Include unreleased/upcoming content"),
    has_streams: bool = Query(True, description="Only show media with available streams"),
    # TV-specific filters
    working_only: bool = Query(False, description="[TV only] Only show channels with working/active streams"),
    my_channels: bool = Query(False, description="[TV only] Only show channels I imported"),
    include_blocked: bool = Query(False, description="[Moderators/Admins only] Include blocked content"),
    profile_ctx: ProfileContext = Depends(get_profile_context),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Browse catalog items with filtering, search and pagination.
    Requires authentication.

    By default, only shows released content with available streams.
    Set include_upcoming=true to see unreleased/upcoming content.
    Set has_streams=false to see all media regardless of stream availability.
    Blocked content is hidden unless you're a moderator/admin with include_blocked=true.

    For TV channels:
    - working_only: Filter to only show channels with active (working) streams
    - my_channels: Filter to only show channels you imported
    """
    from datetime import date as date_type

    media_type = MediaType(catalog_type)
    is_moderator_or_admin = current_user.role in (UserRole.MODERATOR, UserRole.ADMIN)
    offset = (page - 1) * page_size
    today = date_type.today()

    # For popular sorting, use IMDb rating from MediaRating table via subquery
    imdb_rating_subq = (
        select(MediaRating.rating)
        .join(RatingProvider, RatingProvider.id == MediaRating.rating_provider_id)
        .where(
            MediaRating.media_id == Media.id,
            RatingProvider.name == "imdb",
        )
        .correlate(Media)
        .scalar_subquery()
    )

    # Base query - for popular/rating sorting, include the rating subquery
    if sort in ("popular", "rating"):
        base_query = select(Media, imdb_rating_subq.label("imdb_rating")).where(Media.type == media_type)
    else:
        base_query = select(Media).where(Media.type == media_type)

    count_query = select(func.count(Media.id)).where(Media.type == media_type)

    # Filter out blocked content for regular users
    if not (is_moderator_or_admin and include_blocked):
        base_query = base_query.where(Media.is_blocked == False)
        count_query = count_query.where(Media.is_blocked == False)

    # Filter for released content only (unless include_upcoming is True)
    # Skip release filter for TV channels - they don't have traditional release dates
    if not include_upcoming and media_type != MediaType.TV:
        # Show content that:
        # 1. Has release_date <= today, OR
        # 2. Has status = 'released', OR
        # 3. Has no release_date but has year <= current year (for older entries)
        from sqlalchemy import or_

        release_filter = or_(
            Media.release_date <= today,
            Media.status == "released",
            (Media.release_date.is_(None)) & (Media.year <= today.year),
        )
        base_query = base_query.where(release_filter)
        count_query = count_query.where(release_filter)

    # Filter for media with streams only (unless has_streams is False)
    if has_streams:
        base_query = base_query.where(Media.total_streams > 0)
        count_query = count_query.where(Media.total_streams > 0)

    # TV-specific filters
    if media_type == MediaType.TV:
        # Filter for channels with working/active streams
        if working_only:
            # Subquery to check if media has at least one active stream
            active_stream_exists = (
                select(StreamMediaLink.media_id)
                .join(Stream, Stream.id == StreamMediaLink.stream_id)
                .where(
                    StreamMediaLink.media_id == Media.id,
                    Stream.is_active.is_(True),
                    Stream.is_blocked.is_(False),
                )
                .correlate(Media)
                .exists()
            )
            base_query = base_query.where(active_stream_exists)
            count_query = count_query.where(active_stream_exists)

        # Filter for user's own imported channels
        if my_channels and profile_ctx.user_id:
            from sqlalchemy import or_

            # User-created media OR media with streams uploaded by user
            user_stream_exists = (
                select(StreamMediaLink.media_id)
                .join(Stream, Stream.id == StreamMediaLink.stream_id)
                .where(
                    StreamMediaLink.media_id == Media.id,
                    Stream.uploader_user_id == profile_ctx.user_id,
                )
                .correlate(Media)
                .exists()
            )
            user_filter = or_(
                Media.created_by_user_id == profile_ctx.user_id,
                user_stream_exists,
            )
            base_query = base_query.where(user_filter)
            count_query = count_query.where(user_filter)

    # Apply catalog filter
    if catalog:
        base_query = (
            base_query.join(MediaCatalogLink, MediaCatalogLink.media_id == Media.id)
            .join(Catalog, Catalog.id == MediaCatalogLink.catalog_id)
            .where(Catalog.name == catalog)
        )
        count_query = (
            count_query.join(MediaCatalogLink, MediaCatalogLink.media_id == Media.id)
            .join(Catalog, Catalog.id == MediaCatalogLink.catalog_id)
            .where(Catalog.name == catalog)
        )

    # Apply genre filter
    if genre:
        base_query = (
            base_query.join(MediaGenreLink, MediaGenreLink.media_id == Media.id, isouter=not catalog)
            .join(Genre, Genre.id == MediaGenreLink.genre_id)
            .where(Genre.name == genre)
        )
        count_query = (
            count_query.join(MediaGenreLink, MediaGenreLink.media_id == Media.id, isouter=not catalog)
            .join(Genre, Genre.id == MediaGenreLink.genre_id)
            .where(Genre.name == genre)
        )

    # Apply search filter - searches in titles, cast, and crew names
    # Uses UNION-based subquery for optimal performance with trigram indexes
    if search:
        search_pattern = f"%{search}%"

        # Find media IDs matching by title (uses idx_media_title_trgm)
        title_matches = select(Media.id).where(Media.type == media_type, Media.title.ilike(search_pattern))

        # Find media IDs via cast person names (uses idx_person_name_trgm)
        cast_matches = (
            select(MediaCast.media_id.label("id"))
            .join(Person, Person.id == MediaCast.person_id)
            .where(Person.name.ilike(search_pattern))
        )

        # Find media IDs via crew person names (uses idx_person_name_trgm)
        crew_matches = (
            select(MediaCrew.media_id.label("id"))
            .join(Person, Person.id == MediaCrew.person_id)
            .where(Person.name.ilike(search_pattern))
        )

        # Combine all matching media IDs with UNION (auto-deduplicates)
        all_matches = union(title_matches, cast_matches, crew_matches).subquery()

        # Filter to only media in the combined results
        search_filter = Media.id.in_(select(all_matches.c.id))
        base_query = base_query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Apply sorting with direction
    order_func = asc if sort_dir == "asc" else desc
    nulls_position = "nulls_first" if sort_dir == "asc" else "nulls_last"

    if sort == "latest":
        order_expr = order_func(Media.last_stream_added)
        base_query = base_query.order_by(getattr(order_expr, nulls_position)())
    elif sort == "popular":
        # Sort by IMDb rating from the subquery, fall back to total_streams for nulls
        rating_order = order_func(imdb_rating_subq)
        streams_order = order_func(Media.total_streams)
        base_query = base_query.order_by(
            getattr(rating_order, nulls_position)(),
            getattr(streams_order, nulls_position)(),
        )
    elif sort == "rating":
        # Explicit rating sort (same as popular but more semantic)
        rating_order = order_func(imdb_rating_subq)
        streams_order = order_func(Media.total_streams)
        base_query = base_query.order_by(
            getattr(rating_order, nulls_position)(),
            getattr(streams_order, nulls_position)(),
        )
    elif sort == "year":
        order_expr = order_func(Media.year)
        base_query = base_query.order_by(getattr(order_expr, nulls_position)())
    elif sort == "release_date":
        order_expr = order_func(Media.release_date)
        base_query = base_query.order_by(getattr(order_expr, nulls_position)())
    elif sort == "title":
        # Title sort: asc = A-Z, desc = Z-A
        base_query = base_query.order_by(order_func(Media.title))

    # Always load genres to avoid lazy loading in async context (MissingGreenlet error)
    base_query = base_query.options(selectinload(Media.genres))

    # Apply pagination
    base_query = base_query.offset(offset).limit(page_size)

    # Execute queries
    result = await session.exec(base_query)
    raw_items = result.unique().all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Collect media IDs for batch fetching
    media_ids = []
    for raw_item in raw_items:
        if sort in ("popular", "rating"):
            item, _ = raw_item
        else:
            item = raw_item
        media_ids.append(item.id)

    # Batch fetch all external IDs for each media
    external_ids_by_media = await crud.get_all_external_ids_batch(session, media_ids)

    # Batch fetch likes counts using media_id
    likes_counts: dict[int, int] = {}
    if media_ids:
        likes_query = (
            select(MetadataVote.media_id, func.count().label("count"))
            .where(MetadataVote.media_id.in_(media_ids))
            .group_by(MetadataVote.media_id)
        )
        likes_result = await session.exec(likes_query)
        likes_counts = {row.media_id: row.count for row in likes_result.all()}

    # Batch fetch images (poster and background) from MediaImage table
    images_by_media: dict[int, dict[str, str]] = {mid: {} for mid in media_ids}
    if media_ids:
        images_query = select(MediaImage).where(
            MediaImage.media_id.in_(media_ids),
            MediaImage.image_type.in_(["poster", "background"]),
            MediaImage.is_primary.is_(True),
        )
        images_result = await session.exec(images_query)
        for img in images_result.all():
            images_by_media[img.media_id][img.image_type] = img.url

    # Format response - handle both query types (with and without rating subquery)
    catalog_items = []
    for raw_item in raw_items:
        # Handle tuple result when we included the rating subquery
        if sort in ("popular", "rating"):
            item, imdb_rating = raw_item
        else:
            item = raw_item
            imdb_rating = None

        # Get images from batch-fetched data
        item_images = images_by_media.get(item.id, {})

        # Get genres - need to load them if we didn't do selectinload
        item_genres = []
        if hasattr(item, "genres") and item.genres:
            item_genres = [g.name for g in item.genres if g.name.lower() not in ADULT_GENRE_NAMES]

        # Get external IDs from batch-fetched data
        ext_ids_dict = external_ids_by_media.get(item.id, {})

        catalog_items.append(
            CatalogItemBase(
                id=item.id,
                external_ids=ExternalIds.from_dict(ext_ids_dict),
                title=item.title,
                type=catalog_type,
                year=item.year,
                poster=item_images.get("poster"),
                background=item_images.get("background"),
                description=item.description,
                runtime=format_runtime(f"{item.runtime_minutes} min") if item.runtime_minutes else None,
                genres=item_genres,
                imdb_rating=imdb_rating,
                last_stream_added=item.last_stream_added,
                likes_count=likes_counts.get(item.id, 0),
            )
        )

    return CatalogListResponse(
        items=catalog_items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(raw_items)) < total,
    )


async def _fetch_media_images(
    session: AsyncSession,
    media_id: int,
    series_metadata_id: int | None = None,
) -> tuple[str | None, str | None]:
    """Get primary poster and background URLs for a media item.

    For series, if no poster is found, falls back to episode still image.
    """
    from db.models import Episode, EpisodeImage, Season

    poster_query = select(MediaImage).where(
        MediaImage.media_id == media_id,
        MediaImage.image_type == "poster",
        MediaImage.is_primary.is_(True),
    )
    poster_result = await session.exec(poster_query)
    poster_img = poster_result.first()

    bg_query = select(MediaImage).where(
        MediaImage.media_id == media_id,
        MediaImage.image_type == "background",
        MediaImage.is_primary.is_(True),
    )
    bg_result = await session.exec(bg_query)
    bg_img = bg_result.first()

    poster_url = poster_img.url if poster_img else None
    bg_url = bg_img.url if bg_img else None

    # For series without poster, try to get first episode still as fallback
    if not poster_url and series_metadata_id:
        # Get first episode's still image
        episode_still_query = (
            select(EpisodeImage)
            .join(Episode, EpisodeImage.episode_id == Episode.id)
            .join(Season, Episode.season_id == Season.id)
            .where(
                Season.series_id == series_metadata_id,
                EpisodeImage.is_primary.is_(True),
            )
            .order_by(Season.season_number, Episode.episode_number)
            .limit(1)
        )
        still_result = await session.exec(episode_still_query)
        still_img = still_result.first()
        if still_img:
            poster_url = still_img.url

    return (poster_url, bg_url)


def _get_all_ratings(media, user_vote: int | None = None) -> AllRatings:
    """Extract all ratings from MediaRating and MediaFusionRating relationships."""
    external_ratings = []
    imdb_rating = None
    tmdb_rating = None

    # Get external provider ratings
    if media.ratings:
        for media_rating in media.ratings:
            if not media_rating.provider:
                continue

            provider_name = media_rating.provider.name.lower()
            provider_rating = ProviderRating(
                provider=provider_name,
                provider_display_name=media_rating.provider.display_name,
                rating=media_rating.rating,
                rating_raw=media_rating.rating_raw,
                max_rating=media_rating.provider.max_rating,
                is_percentage=media_rating.provider.is_percentage,
                vote_count=media_rating.vote_count,
                rating_type=media_rating.rating_type,
                certification=media_rating.certification,
            )
            external_ratings.append(provider_rating)

            # Extract convenience values
            if provider_name == "imdb":
                imdb_rating = media_rating.rating
            elif provider_name == "tmdb":
                tmdb_rating = media_rating.rating

    # Get MediaFusion community rating
    community_rating = None
    if media.mediafusion_rating:
        mf_rating = media.mediafusion_rating
        community_rating = CommunityRating(
            average_rating=mf_rating.average_rating,
            total_votes=mf_rating.total_votes,
            upvotes=mf_rating.upvotes,
            downvotes=mf_rating.downvotes,
            user_vote=user_vote,
        )

    return AllRatings(
        external_ratings=external_ratings,
        community_rating=community_rating,
        imdb_rating=imdb_rating,
        tmdb_rating=tmdb_rating,
    )


def _get_imdb_rating(media) -> float | None:
    """Extract IMDb rating from MediaRating relationship (backward compatibility)."""
    if not media.ratings:
        return None
    for media_rating in media.ratings:
        if media_rating.provider and media_rating.provider.name.lower() == "imdb":
            return media_rating.rating
    return None


async def _get_user_vote(session: AsyncSession, user_id: int | None, media_id: int) -> int | None:
    """Get user's vote on metadata if authenticated."""
    if not user_id or not media_id:
        return None
    vote_query = select(MetadataVote).where(
        MetadataVote.user_id == user_id,
        MetadataVote.media_id == media_id,
    )
    result = await session.exec(vote_query)
    vote = result.first()
    return int(vote.vote_type == "like") if vote else None


async def _get_all_external_ids(session: AsyncSession, media_id: int) -> dict:
    """Get all external IDs for a media item from the MediaExternalID table.

    Returns dict like: {"imdb": "tt1234567", "tmdb": "550", "tvdb": "81189"}
    """
    external_ids_dict = await crud.get_all_external_ids_dict(session, media_id)
    return external_ids_dict


@router.get("/{catalog_type}/{media_id}", response_model=CatalogItemDetail)
async def get_catalog_item(
    catalog_type: Literal["movie", "series", "tv"],
    media_id: int,
    profile_ctx: ProfileContext = Depends(get_profile_context),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get detailed information about a specific catalog item.
    Requires authentication.
    Uses media_id (internal ID) instead of external_id.

    Blocked content is only visible to moderators and admins.
    """
    user_id = profile_ctx.user_id if profile_ctx else None
    is_moderator_or_admin = current_user.role in (UserRole.MODERATOR, UserRole.ADMIN)

    # Map catalog_type to MediaType
    type_map = {
        "movie": MediaType.MOVIE,
        "series": MediaType.SERIES,
        "tv": MediaType.TV,
    }
    expected_type = type_map[catalog_type]

    # Load media with relationships based on type (single query with type validation)
    if catalog_type == "movie":
        query = (
            select(Media)
            .where(Media.id == media_id, Media.type == expected_type)
            .options(
                selectinload(Media.genres),
                selectinload(Media.catalogs),
                selectinload(Media.aka_titles),
                selectinload(Media.cast).selectinload(MediaCast.person),
                selectinload(Media.crew).selectinload(MediaCrew.person),
                selectinload(Media.parental_certificates),
                selectinload(Media.trailers),
                selectinload(Media.movie_metadata),
                selectinload(Media.ratings).selectinload(MediaRating.provider),
                selectinload(Media.mediafusion_rating),
            )
        )
        result = await session.exec(query)
        media = result.first()
        if not media:
            raise HTTPException(status_code=404, detail="Movie not found")

        # Check if content is blocked and user doesn't have permission
        if media.is_blocked and not is_moderator_or_admin:
            raise HTTPException(status_code=403, detail="This content has been blocked by moderators")

        poster, background = await _fetch_media_images(session, media.id)

        # Get stars from cast relationship
        stars = None
        if hasattr(media, "cast") and media.cast:
            stars = [c.person.name for c in media.cast[:10] if c.person]  # Top 10 cast

        # Get directors and writers from crew relationship
        directors = None
        writers = None
        if hasattr(media, "crew") and media.crew:
            directors = [c.person.name for c in media.crew if c.person and c.job and c.job.lower() == "director"]
            writers = [
                c.person.name
                for c in media.crew
                if c.person and c.job and c.job.lower() in ("writer", "screenplay", "story")
            ]

        # Get all external IDs from the MediaExternalID table
        ext_ids = await _get_all_external_ids(session, media.id)

        # Get user's vote if authenticated
        user_vote = await _get_user_vote(session, user_id, media.id)

        # Get all ratings
        all_ratings = _get_all_ratings(media, user_vote)

        # Get certification and nudity
        cert_names = [c.name for c in media.parental_certificates] if media.parental_certificates else []
        certification = get_certification_category(cert_names) or "Unknown"
        nudity = media.nudity_status.value if media.nudity_status else "Unknown"

        # Get trailers
        trailers = []
        if hasattr(media, "trailers") and media.trailers:
            trailers = [
                TrailerInfo(
                    key=t.video_key,
                    site=t.site,
                    name=t.name,
                    type=t.trailer_type or "trailer",
                    is_official=t.is_official,
                )
                for t in media.trailers
                if t.video_key
            ]

        return CatalogItemDetail(
            id=media.id,
            external_ids=ExternalIds.from_dict(ext_ids),
            title=media.title,
            type=catalog_type,
            year=media.year,
            poster=poster,
            background=background,
            description=media.description,
            runtime=format_runtime(f"{media.runtime_minutes} min") if media.runtime_minutes else None,
            genres=[g.name for g in media.genres if g.name.lower() not in ADULT_GENRE_NAMES] if media.genres else [],
            ratings=all_ratings,
            imdb_rating=all_ratings.imdb_rating,
            catalogs=[c.name for c in media.catalogs] if media.catalogs else [],
            aka_titles=[t.title for t in media.aka_titles] if media.aka_titles else [],
            cast=stars,  # cast is same as stars
            directors=directors,
            writers=writers,
            stars=stars,
            certification=certification,
            nudity=nudity,
            trailers=trailers,
            last_refreshed_at=media.last_refreshed_at,
            last_scraped_at=media.last_scraped_at,
            is_blocked=media.is_blocked,
            block_reason=media.block_reason if is_moderator_or_admin else None,
        )

    elif catalog_type == "series":
        query = (
            select(Media)
            .where(Media.id == media_id, Media.type == expected_type)
            .options(
                selectinload(Media.genres),
                selectinload(Media.catalogs),
                selectinload(Media.aka_titles),
                selectinload(Media.cast).selectinload(MediaCast.person),
                selectinload(Media.crew).selectinload(MediaCrew.person),
                selectinload(Media.parental_certificates),
                selectinload(Media.trailers),
                selectinload(Media.series_metadata)
                .selectinload(SeriesMetadata.seasons)
                .selectinload(Season.episodes)
                .selectinload(Episode.source_provider),
                selectinload(Media.ratings).selectinload(MediaRating.provider),
                selectinload(Media.mediafusion_rating),
            )
        )
        result = await session.exec(query)
        media = result.first()
        if not media:
            raise HTTPException(status_code=404, detail="Series not found")

        # Check if content is blocked and user doesn't have permission
        if media.is_blocked and not is_moderator_or_admin:
            raise HTTPException(status_code=403, detail="This content has been blocked by moderators")

        # Format seasons and episodes with MERGE strategy from multiple providers
        # Fetch all episode images for this series in one query
        episode_images_map: dict[int, str] = {}
        if media.series_metadata:
            episode_images_query = (
                select(EpisodeImage.episode_id, EpisodeImage.url)
                .join(Episode, EpisodeImage.episode_id == Episode.id)
                .join(Season, Episode.season_id == Season.id)
                .where(
                    Season.series_id == media.series_metadata.id,
                    EpisodeImage.is_primary.is_(True),
                )
            )
            images_result = await session.exec(episode_images_query)
            episode_images_map = {ep_id: url for ep_id, url in images_result.all()}

        # Build merged seasons/episodes data
        # The database already stores merged episodes with source_provider tracking
        # Priority: user_created > user_addition > provider episodes (by priority)
        seasons_data = []
        if media.series_metadata and media.series_metadata.seasons:
            for season in sorted(media.series_metadata.seasons, key=lambda s: s.season_number):
                # Group episodes by episode_number and pick best based on priority
                episode_map: dict[int, dict] = {}
                if season.episodes:
                    for ep in season.episodes:
                        ep_num = ep.episode_number
                        # Calculate priority: user_created=100, user_addition=90, else use provider priority (lower = higher priority)
                        if ep.is_user_created:
                            priority = 100  # Highest priority
                        elif ep.is_user_addition:
                            priority = 90  # High priority
                        elif ep.source_provider:
                            # Provider priority: lower value = higher priority, so invert for comparison
                            # Use 100 - provider.priority so higher provider priority = higher episode priority
                            priority = 100 - ep.source_provider.priority
                        else:
                            priority = 0  # Default/lowest priority

                        # Keep episode with highest priority
                        if ep_num not in episode_map or priority > episode_map[ep_num].get("_priority", 0):
                            episode_map[ep_num] = {
                                "id": ep.id,  # Include episode ID for moderator actions
                                "episode_number": ep.episode_number,
                                "title": ep.title,
                                "released": ep.air_date.isoformat() if ep.air_date else None,
                                "thumbnail": episode_images_map.get(ep.id),
                                "overview": ep.overview,
                                "is_user_created": ep.is_user_created,
                                "is_user_addition": ep.is_user_addition,
                                "_priority": priority,
                            }

                # Sort episodes by number and remove internal priority field
                episodes = []
                for ep_num in sorted(episode_map.keys()):
                    ep_data = episode_map[ep_num]
                    ep_data.pop("_priority", None)
                    episodes.append(ep_data)

                seasons_data.append(
                    {
                        "season_number": season.season_number,
                        "episodes": episodes,
                    }
                )

        # Pass series_metadata.id for episode still fallback
        series_meta_id = media.series_metadata.id if media.series_metadata else None
        poster, background = await _fetch_media_images(session, media.id, series_metadata_id=series_meta_id)

        # Get stars from cast relationship
        stars = None
        if hasattr(media, "cast") and media.cast:
            stars = [c.person.name for c in media.cast[:10] if c.person]  # Top 10 cast

        # Get directors and writers from crew relationship
        directors = None
        writers = None
        if hasattr(media, "crew") and media.crew:
            directors = [c.person.name for c in media.crew if c.person and c.job and c.job.lower() == "director"]
            writers = [
                c.person.name
                for c in media.crew
                if c.person and c.job and c.job.lower() in ("writer", "screenplay", "story")
            ]

        # Get all external IDs from the MediaExternalID table
        ext_ids = await _get_all_external_ids(session, media.id)

        # Get user's vote if authenticated
        user_vote = await _get_user_vote(session, user_id, media.id)

        # Get all ratings
        all_ratings = _get_all_ratings(media, user_vote)

        # Get certification and nudity
        cert_names = [c.name for c in media.parental_certificates] if media.parental_certificates else []
        certification = get_certification_category(cert_names) or "Unknown"
        nudity = media.nudity_status.value if media.nudity_status else "Unknown"

        # Get trailers
        trailers = []
        if hasattr(media, "trailers") and media.trailers:
            trailers = [
                TrailerInfo(
                    key=t.video_key,
                    site=t.site,
                    name=t.name,
                    type=t.trailer_type or "trailer",
                    is_official=t.is_official,
                )
                for t in media.trailers
                if t.video_key
            ]

        return CatalogItemDetail(
            id=media.id,
            external_ids=ExternalIds.from_dict(ext_ids),
            title=media.title,
            type=catalog_type,
            year=media.year,
            poster=poster,
            background=background,
            description=media.description,
            runtime=format_runtime(f"{media.runtime_minutes} min") if media.runtime_minutes else None,
            genres=[g.name for g in media.genres if g.name.lower() not in ADULT_GENRE_NAMES] if media.genres else [],
            ratings=all_ratings,
            imdb_rating=all_ratings.imdb_rating,
            catalogs=[c.name for c in media.catalogs] if media.catalogs else [],
            aka_titles=[t.title for t in media.aka_titles] if media.aka_titles else [],
            seasons=seasons_data,
            cast=stars,  # cast is same as stars
            directors=directors,
            writers=writers,
            stars=stars,
            certification=certification,
            nudity=nudity,
            trailers=trailers,
            last_refreshed_at=media.last_refreshed_at,
            last_scraped_at=media.last_scraped_at,
            is_blocked=media.is_blocked,
            block_reason=media.block_reason if is_moderator_or_admin else None,
        )

    elif catalog_type == "tv":
        query = (
            select(Media)
            .where(Media.id == media_id, Media.type == expected_type)
            .options(
                selectinload(Media.genres),
                selectinload(Media.catalogs),
                selectinload(Media.aka_titles),
                selectinload(Media.parental_certificates),
                selectinload(Media.tv_metadata),
                selectinload(Media.ratings).selectinload(MediaRating.provider),
                selectinload(Media.mediafusion_rating),
            )
        )
        result = await session.exec(query)
        media = result.first()
        if not media:
            raise HTTPException(status_code=404, detail="TV channel not found")

        # Check if content is blocked and user doesn't have permission
        if media.is_blocked and not is_moderator_or_admin:
            raise HTTPException(status_code=403, detail="This content has been blocked by moderators")

        # Get TV-specific metadata (one-to-one relationship)
        tv_meta = media.tv_metadata

        poster, background = await _fetch_media_images(session, media.id)

        # Get all external IDs
        ext_ids = await _get_all_external_ids(session, media.id)

        # Get user's vote if authenticated
        user_vote = await _get_user_vote(session, user_id, media.id)

        # Get all ratings
        all_ratings = _get_all_ratings(media, user_vote)

        # Get nudity (TV channels now support nudity_status)
        nudity = media.nudity_status.value if media.nudity_status else "Unknown"

        return CatalogItemDetail(
            id=media.id,
            external_ids=ExternalIds.from_dict(ext_ids),
            title=media.title,
            type=catalog_type,
            poster=poster,
            background=background,
            description=media.description,
            genres=[g.name for g in media.genres if g.name.lower() not in ADULT_GENRE_NAMES] if media.genres else [],
            ratings=all_ratings,
            imdb_rating=all_ratings.imdb_rating,
            catalogs=[c.name for c in media.catalogs] if media.catalogs else [],
            aka_titles=[],  # TV channels don't have aka_titles typically
            country=tv_meta.country if tv_meta else None,
            tv_language=tv_meta.tv_language if tv_meta else None,
            certification="Unknown",  # TV channels typically don't have certification
            nudity=nudity,
            is_blocked=media.is_blocked,
            block_reason=media.block_reason if is_moderator_or_admin else None,
        )

    raise HTTPException(status_code=400, detail="Invalid catalog type")


# Stream sorting and filtering options
RESOLUTION_SORT_ORDER = {"4K": 0, "2160p": 0, "1080p": 1, "720p": 2, "480p": 3, "SD": 4}
QUALITY_SORT_ORDER = {
    "WEB-DL": 0,
    "BluRay": 1,
    "BDRip": 2,
    "WEBRip": 3,
    "HDRip": 4,
    "HDTV": 5,
    "DVDRip": 6,
}


def parse_size_for_sorting(size_str: str | None) -> int:
    """Parse size string to bytes for sorting"""
    if not size_str:
        return 0
    try:
        size_str = size_str.upper().replace(" ", "")
        if "GB" in size_str:
            return int(float(size_str.replace("GB", "")) * 1024 * 1024 * 1024)
        elif "MB" in size_str:
            return int(float(size_str.replace("MB", "")) * 1024 * 1024)
        elif "KB" in size_str:
            return int(float(size_str.replace("KB", "")) * 1024)
        return int(size_str)
    except (ValueError, TypeError):
        return 0


def sort_streams(
    streams: list[StreamInfo],
    sort_by: str,
    sort_order: str,
) -> list[StreamInfo]:
    """Sort streams by the specified criteria"""
    reverse = sort_order == "desc"

    if sort_by == "quality":
        # Sort by resolution first, then quality
        def quality_key(s):
            res_order = RESOLUTION_SORT_ORDER.get(s.resolution or "", 99)
            qual_order = QUALITY_SORT_ORDER.get(s.quality or "", 99)
            return (res_order, qual_order)

        return sorted(streams, key=quality_key, reverse=not reverse)  # Lower is better

    elif sort_by == "size":
        return sorted(streams, key=lambda s: parse_size_for_sorting(s.size), reverse=reverse)

    elif sort_by == "seeders":
        return sorted(streams, key=lambda s: s.seeders or 0, reverse=reverse)

    elif sort_by == "source":
        return sorted(streams, key=lambda s: s.source or "", reverse=reverse)

    # Default: quality
    return streams


def filter_streams(
    streams: list[StreamInfo],
    quality_filter: list[str] | None = None,
    resolution_filter: list[str] | None = None,
    source_filter: list[str] | None = None,
    codec_filter: list[str] | None = None,
) -> list[StreamInfo]:
    """Filter streams by the specified criteria"""
    result = streams

    if quality_filter:
        quality_filter_lower = [q.lower() for q in quality_filter]
        result = [s for s in result if s.quality and s.quality.lower() in quality_filter_lower]

    if resolution_filter:
        resolution_filter_lower = [r.lower() for r in resolution_filter]
        result = [s for s in result if s.resolution and s.resolution.lower() in resolution_filter_lower]

    if source_filter:
        source_filter_lower = [src.lower() for src in source_filter]
        result = [s for s in result if s.source and s.source.lower() in source_filter_lower]

    if codec_filter:
        codec_filter_lower = [c.lower() for c in codec_filter]
        result = [s for s in result if s.codec and s.codec.lower() in codec_filter_lower]

    return result


def group_streams_by_quality(streams: list[StreamInfo]) -> dict:
    """Group streams by quality tier for frontend display"""
    groups = {
        "4K/UHD": [],
        "1080p": [],
        "720p": [],
        "SD/Other": [],
    }

    for stream in streams:
        res = (stream.resolution or "").upper()
        if "4K" in res or "2160" in res:
            groups["4K/UHD"].append(stream)
        elif "1080" in res:
            groups["1080p"].append(stream)
        elif "720" in res:
            groups["720p"].append(stream)
        else:
            groups["SD/Other"].append(stream)

    return groups


@router.get("/{catalog_type}/{media_id}/streams", response_model=StreamListResponse)
async def get_catalog_item_streams(
    catalog_type: Literal["movie", "series", "tv"],
    media_id: int,
    request: Request,
    season: int | None = Query(None, description="Season number (required for series)"),
    episode: int | None = Query(None, description="Episode number (required for series)"),
    profile_id: int | None = Query(None, description="Specific profile ID to use (defaults to user's default)"),
    provider: str | None = Query(
        None,
        description="Specific provider service name to use (defaults to primary provider)",
    ),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get available streams for a catalog item.
    Requires authentication. A streaming provider is recommended but not required.

    Returns streams with full metadata for frontend display.
    Uses media_id (internal ID) instead of external_id.

    Query parameters:
    - profile_id: Use a specific profile instead of the default
    - provider: Generate playback URLs for a specific provider from the profile
    """
    # Get profile context with optional profile_id
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Validate series parameters
    if catalog_type == "series" and (season is None or episode is None):
        raise HTTPException(
            status_code=400,
            detail="Season and episode parameters are required for series",
        )

    # Map catalog_type to MediaType
    type_map = {
        "movie": MediaType.MOVIE,
        "series": MediaType.SERIES,
        "tv": MediaType.TV,
    }
    expected_type = type_map[catalog_type]

    # Get metadata directly by media_id with type validation (single query)
    query = select(Media).where(Media.id == media_id, Media.type == expected_type)
    result = await session.exec(query)
    metadata = result.first()
    if not metadata:
        raise HTTPException(status_code=404, detail=f"{catalog_type.capitalize()} not found")

    # Build stream query - different paths for movies/tv vs series
    if catalog_type == "series":
        # For series: use FileMediaLink with season/episode filtering
        stream_query = (
            select(Stream)
            .join(StreamFile, StreamFile.stream_id == Stream.id)
            .join(FileMediaLink, FileMediaLink.file_id == StreamFile.id)
            .where(
                FileMediaLink.media_id == metadata.id,
                FileMediaLink.season_number == season,
                FileMediaLink.episode_number == episode,
            )
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
        )
    else:
        # For movies and TV: use StreamMediaLink (stream-level linking)
        stream_query = (
            select(Stream)
            .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
            .where(StreamMediaLink.media_id == metadata.id)
            .where(Stream.is_active.is_(True))
            .where(Stream.is_blocked.is_(False))
        )

    # Eagerly load relationships - include http_stream for TV channels, usenet_stream for NZBs, telegram_stream, and acestream_stream
    stream_query = stream_query.options(
        selectinload(Stream.torrent_stream),
        selectinload(Stream.http_stream),
        selectinload(Stream.usenet_stream),
        selectinload(Stream.telegram_stream),
        selectinload(Stream.acestream_stream),
        selectinload(Stream.languages),
        selectinload(Stream.audio_formats),
        selectinload(Stream.channels),
        selectinload(Stream.hdr_formats),
    )

    # Increase limit to account for filtering (e.g., Usenet streams filtered for non-Usenet providers)
    stream_query = stream_query.order_by(Stream.created_at.desc()).limit(500)

    stream_result = await session.exec(stream_query)
    streams = stream_result.unique().all()

    # Filter out globally disabled content types
    disabled = set(settings.disabled_content_types)
    if disabled:
        disabled_stream_types: set[StreamType] = set()
        if "torrent" in disabled or "magnet" in disabled:
            disabled_stream_types.add(StreamType.TORRENT)
        if "nzb" in disabled:
            disabled_stream_types.add(StreamType.USENET)
        if "iptv" in disabled or "http" in disabled:
            disabled_stream_types.add(StreamType.HTTP)
        if "youtube" in disabled:
            disabled_stream_types.add(StreamType.YOUTUBE)
        if "acestream" in disabled:
            disabled_stream_types.add(StreamType.ACESTREAM)
        if "telegram" in disabled:
            disabled_stream_types.add(StreamType.TELEGRAM)
        if disabled_stream_types:
            streams = [s for s in streams if s.stream_type not in disabled_stream_types]

    # Build list of available streaming providers for the response
    available_providers: list[StreamingProviderInfo] = []
    active_providers = profile_ctx.user_data.get_active_providers()
    for sp in active_providers:
        available_providers.append(
            StreamingProviderInfo(
                service=sp.service,
                name=sp.name,
                enabled=True,
            )
        )

    # Determine which provider to use for generating URLs
    selected_provider_obj = None
    selected_provider_service = None

    if provider:
        # Use the specified provider if it exists and is enabled
        selected_provider_obj = profile_ctx.user_data.get_provider_by_name(provider)
        if not selected_provider_obj:
            # Try to find by service name
            for sp in active_providers:
                if sp.service == provider:
                    selected_provider_obj = sp
                    break
        if selected_provider_obj:
            selected_provider_service = selected_provider_obj.service

    # Fall back to primary provider if no specific provider selected or not found
    if not selected_provider_obj:
        selected_provider_obj = profile_ctx.user_data.get_primary_provider()
        if selected_provider_obj:
            selected_provider_service = selected_provider_obj.service

    # Check if selected provider is P2P (no playback URLs for P2P)
    is_p2p_provider = selected_provider_obj and selected_provider_obj.service == "p2p"

    # Generate playback URLs only for non-P2P debrid providers
    secret_str = None
    primary_provider_name = "default"
    if selected_provider_obj and not is_p2p_provider:
        # Extract API key from header and include it in config (for private instances)
        user_data = profile_ctx.user_data.model_copy()
        api_password = request.headers.get("X-API-Key")
        if api_password:
            user_data.api_password = api_password

        # Generate encrypted secret string for playback URLs
        secret_str = await crypto_utils.process_user_data(user_data)

        # Use the selected provider's name for playback URLs
        if selected_provider_obj.name:
            primary_provider_name = selected_provider_obj.name
        else:
            # Generate a default name based on service
            primary_provider_name = selected_provider_obj.service
    elif is_p2p_provider:
        # For P2P, use the provider name or "P2P" as default
        primary_provider_name = selected_provider_obj.name or "P2P"

    # Get streaming provider info for formatting
    streaming_provider_name = (
        STREAMING_PROVIDERS_SHORT_NAMES.get(selected_provider_obj.service, "P2P") if selected_provider_obj else "P2P"
    )

    # Pre-fetch episode links for series (for editing UI)
    stream_episode_links: dict = {}
    if catalog_type == "series":
        stream_ids = [s.id for s in streams]
        if stream_ids:
            links_query = (
                select(FileMediaLink, StreamFile)
                .join(StreamFile, StreamFile.id == FileMediaLink.file_id)
                .where(
                    StreamFile.stream_id.in_(stream_ids),
                    FileMediaLink.media_id == metadata.id,
                )
            )
            links_result = await session.exec(links_query)
            for link, file in links_result.all():
                if file.stream_id not in stream_episode_links:
                    stream_episode_links[file.stream_id] = []
                stream_episode_links[file.stream_id].append(
                    {
                        "file_id": link.file_id,
                        "file_name": file.filename or f"File {link.file_id}",
                        "season_number": link.season_number,
                        "episode_number": link.episode_number,
                        "episode_end": link.episode_end,
                    }
                )

    # Collect torrent info hashes for debrid cache checking
    torrent_info_hashes: dict[str, int] = {}  # info_hash -> stream index
    for idx, stream in enumerate(streams):
        if stream.torrent_stream and stream.torrent_stream.info_hash:
            torrent_info_hashes[stream.torrent_stream.info_hash] = idx

    # Check debrid cache status for torrent streams (only for non-P2P debrid providers)
    cached_statuses: dict[str, bool] = {}
    if selected_provider_obj and not is_p2p_provider and torrent_info_hashes:
        info_hashes = list(torrent_info_hashes.keys())

        # First check Redis cache
        cached_statuses = await get_cached_status(selected_provider_obj, info_hashes)

        # For streams not found in Redis cache, use provider's cache check
        uncached_hashes = [h for h in info_hashes if not cached_statuses.get(h, False)]
        if uncached_hashes:
            cache_update_function = mapper.CACHE_UPDATE_FUNCTIONS.get(selected_provider_obj.service)
            if cache_update_function:
                try:
                    # Build a minimal stream data structure for the cache check
                    # TorrentStreamData requires: info_hash, name, size, source, meta_id
                    # The cache functions use stream.id which is set via extra="allow"
                    uncached_streams: list[TorrentStreamData] = []
                    for stream in streams:
                        if stream.torrent_stream and stream.torrent_stream.info_hash in uncached_hashes:
                            # Create TorrentStreamData with all required fields
                            stream_data = TorrentStreamData(
                                info_hash=stream.torrent_stream.info_hash,
                                name=stream.name or "",
                                size=stream.torrent_stream.total_size or 0,
                                source=stream.source or "unknown",
                                meta_id=str(media_id),
                                languages=[lang.name for lang in stream.languages] if stream.languages else [],
                                resolution=stream.resolution,
                                quality=stream.quality,
                                seeders=stream.torrent_stream.seeders,
                                cached=False,
                            )
                            # Set id attribute for cache functions (uses extra="allow")
                            stream_data.id = stream.torrent_stream.info_hash
                            uncached_streams.append(stream_data)

                    if uncached_streams:
                        user_ip = await get_user_public_ip(request, profile_ctx.user_data)
                        service_name = await cache_update_function(
                            streams=uncached_streams,
                            streaming_provider=selected_provider_obj,
                            user_ip=user_ip,
                            stremio_video_id=str(media_id),
                        )

                        # Update cached_statuses with results and store in Redis
                        cached_info_hashes = [s.id for s in uncached_streams if getattr(s, "cached", False)]
                        for s in uncached_streams:
                            cached_statuses[s.id] = getattr(s, "cached", False)

                        if cached_info_hashes:
                            await store_cached_info_hashes(
                                selected_provider_obj,
                                cached_info_hashes,
                                service_name,
                            )
                except Exception as error:
                    logging.warning(f"Failed to check debrid cache status: {error}")

    # Get template (user's or default)
    template = profile_ctx.user_data.stream_template or StreamTemplate()

    # Check if selected provider supports Usenet
    provider_supports_usenet = (
        selected_provider_obj and selected_provider_obj.service in mapper.USENET_CAPABLE_PROVIDERS
    )

    # Check if user has MediaFlow configured for Telegram streams
    has_mediaflow_telegram = (
        profile_ctx.user_data.enable_telegram_streams
        and profile_ctx.user_data.mediaflow_config
        and profile_ctx.user_data.mediaflow_config.proxy_url
        and profile_ctx.user_data.mediaflow_config.api_password
    )

    # Check if user has MediaFlow configured for AceStream streams
    has_mediaflow_acestream = (
        profile_ctx.user_data.enable_acestream_streams
        and profile_ctx.user_data.mediaflow_config
        and profile_ctx.user_data.mediaflow_config.proxy_url
        and profile_ctx.user_data.mediaflow_config.api_password
    )

    # Format streams for API response
    formatted_streams = []
    for stream in streams:
        # Get type-specific data
        torrent = stream.torrent_stream
        http_stream = stream.http_stream
        usenet = stream.usenet_stream
        telegram = stream.telegram_stream
        acestream = stream.acestream_stream

        # Skip Usenet streams if provider doesn't support them
        if usenet and not provider_supports_usenet:
            continue

        # Skip Telegram streams if user doesn't have MediaFlow configured
        if telegram and not has_mediaflow_telegram:
            continue

        # Skip AceStream streams if user doesn't have MediaFlow configured
        if acestream and not has_mediaflow_acestream:
            continue

        # Format multi-value attributes
        audio_formats = [af.name for af in stream.audio_formats] if stream.audio_formats else []
        channels = [ch.name for ch in stream.channels] if stream.channels else []
        hdr_formats = [hdr.name for hdr in stream.hdr_formats] if stream.hdr_formats else []
        languages = [lang.name for lang in stream.languages] if stream.languages else []

        # Calculate file size (torrent has total_size, HTTP stream has size, usenet has size, telegram has size)
        file_size = (
            torrent.total_size
            if torrent
            else (
                usenet.size if usenet else (telegram.size if telegram else (http_stream.size if http_stream else None))
            )
        )

        # Get episode links for this stream (series only)
        episode_links = stream_episode_links.get(stream.id, None) if catalog_type == "series" else None

        # Build language flags from language names
        language_flags = list(
            filter(
                None,
                [LANGUAGE_COUNTRY_FLAGS.get(lang) for lang in languages],
            )
        )

        # Get cached status for this stream
        is_cached = cached_statuses.get(torrent.info_hash, False) if torrent and torrent.info_hash else None

        # Build stream context for template rendering
        stream_context = {
            "name": stream.name,
            "type": stream.stream_type.value,  # torrent, http, youtube, usenet, etc.
            "resolution": stream.resolution,
            "quality": stream.quality,
            "codec": stream.codec,
            "bit_depth": stream.bit_depth,
            "audio_formats": audio_formats,
            "channels": channels,
            "hdr_formats": hdr_formats,
            "languages": languages,
            "language_flags": language_flags,  # Country flag emojis for languages
            "size": file_size,
            "seeders": torrent.seeders if torrent else None,
            "source": stream.source,
            "release_group": stream.release_group,
            "uploader": stream.uploader,
            "cached": is_cached,
        }

        # Build service context for template
        service_context = {
            "name": streaming_provider_name,
            "shortName": STREAMING_PROVIDERS_SHORT_NAMES.get(
                selected_provider_obj.service if selected_provider_obj else "", "P2P"
            ),
            "cached": is_cached or False,
        }

        # Build addon context
        addon_context = {"name": settings.addon_name}

        # Format name and description using templates
        try:
            formatted_name = render_stream_template(
                template.title,
                stream_context,
                service=service_context,
                addon=addon_context,
            )
            formatted_description = render_stream_template(
                template.description,
                stream_context,
                service=service_context,
                addon=addon_context,
            )
        except Exception as e:
            # Fall back to hardcoded format on error
            logging.warning(f"Template rendering failed in catalog: {e}")
            resolution = stream.resolution.upper() if stream.resolution else "N/A"
            formatted_name = f"{settings.addon_name} {streaming_provider_name} {resolution}"
            formatted_description = stream.name or ""

        # Build playback URL based on stream type
        playback_url = None
        if telegram and telegram.chat_id and telegram.message_id:
            # Telegram streams - don't require debrid provider, always generate URL
            # Generate secret_str if not already generated (for Telegram streams, we always need it)
            if not secret_str:
                user_data = profile_ctx.user_data.model_copy()
                api_password = request.headers.get("X-API-Key")
                if api_password:
                    user_data.api_password = api_password
                secret_str = await crypto_utils.process_user_data(user_data)
            playback_url = (
                f"{settings.host_url}/streaming_provider/{secret_str}/telegram/{telegram.chat_id}/{telegram.message_id}"
            )
        elif acestream and (acestream.content_id or acestream.info_hash):
            # AceStream streams - use MediaFlow proxy (MPEG-TS), don't require debrid
            try:
                mediaflow_config = profile_ctx.user_data.mediaflow_config
                playback_url = encode_mediaflow_acestream_url(
                    mediaflow_proxy_url=mediaflow_config.proxy_url,
                    content_id=acestream.content_id,
                    info_hash=acestream.info_hash,
                    api_password=mediaflow_config.api_password,
                )
            except Exception as e:
                logger.warning(f"Failed to generate AceStream URL for stream {stream.id}: {e}")
                playback_url = None
        elif http_stream:
            # HTTP streams (TV channels, direct links) - use the URL directly
            playback_url = http_stream.url
        elif secret_str and usenet and usenet.nzb_guid:
            # Usenet streams with debrid
            if catalog_type == "series" and season is not None and episode is not None:
                playback_url = f"{settings.host_url}/streaming_provider/{secret_str}/usenet/{primary_provider_name}/{usenet.nzb_guid}/{season}/{episode}"
            else:
                playback_url = f"{settings.host_url}/streaming_provider/{secret_str}/usenet/{primary_provider_name}/{usenet.nzb_guid}"
        elif secret_str and torrent and torrent.info_hash:
            # Torrent streams with debrid
            if catalog_type == "series" and season is not None and episode is not None:
                playback_url = f"{settings.host_url}/streaming_provider/{secret_str}/playback/{primary_provider_name}/{torrent.info_hash}/{season}/{episode}"
            else:
                playback_url = f"{settings.host_url}/streaming_provider/{secret_str}/playback/{primary_provider_name}/{torrent.info_hash}"

        formatted_streams.append(
            StreamInfo(
                # Core identifiers
                id=stream.id,
                torrent_stream_id=torrent.id if torrent else None,
                info_hash=torrent.info_hash if torrent else None,
                nzb_guid=usenet.nzb_guid if usenet else None,
                # Display fields - now formatted!
                name=formatted_name,
                description=formatted_description,
                url=playback_url,
                behavior_hints=http_stream.behavior_hints if http_stream else None,
                # Rich metadata for frontend
                stream_name=stream.name,
                stream_type=stream.stream_type.value,  # torrent, http, youtube, usenet, etc.
                resolution=stream.resolution,
                quality=stream.quality,
                codec=stream.codec,
                bit_depth=stream.bit_depth,
                audio_formats="|".join(audio_formats) if audio_formats else None,
                channels="|".join(channels) if channels else None,
                hdr_formats="|".join(hdr_formats) if hdr_formats else None,
                source=stream.source,
                languages=languages,
                size=format_size(file_size) if file_size else None,
                size_bytes=file_size,
                seeders=torrent.seeders if torrent else None,
                uploader=stream.uploader,
                release_group=stream.release_group,
                cached=is_cached,
                # Release flags
                is_remastered=stream.is_remastered,
                is_upscaled=stream.is_upscaled,
                is_proper=stream.is_proper,
                is_repack=stream.is_repack,
                is_extended=stream.is_extended,
                is_complete=stream.is_complete,
                is_dubbed=stream.is_dubbed,
                is_subbed=stream.is_subbed,
                # Episode links for series (for editing UI)
                episode_links=episode_links,
            )
        )

    # Return streams response (metadata already available from catalog endpoint)
    return StreamListResponse(
        streams=formatted_streams,
        season=season,
        episode=episode,
        web_playback_enabled=profile_ctx.web_playback_enabled,
        streaming_providers=available_providers,
        selected_provider=selected_provider_service,
        profile_id=profile_ctx.profile_id,
    )
