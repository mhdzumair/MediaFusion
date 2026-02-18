"""
Media CRUD operations.

Handles Media, MovieMetadata, SeriesMetadata, Season, Episode operations.
Supports new multi-provider architecture with MediaImage, MediaRating, etc.
"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

import pytz
from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import MediaType
from db.models import (
    AkaTitle,
    Catalog,
    Episode,
    Genre,
    Media,
    MediaCatalogLink,
    MediaExternalID,
    MediaFusionRating,
    MediaGenreLink,
    MediaImage,
    MediaRating,
    MetadataProvider,
    MovieMetadata,
    RatingProvider,
    Season,
    SeriesMetadata,
)
from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)


# =============================================================================
# MEDIA CRUD
# =============================================================================


async def get_media_by_id(
    session: AsyncSession,
    media_id: int,
    *,
    load_genres: bool = False,
    load_catalogs: bool = False,
    load_aka_titles: bool = False,
    load_stars: bool = False,
) -> Media | None:
    """Get media by internal ID with optional eager loading."""
    query = select(Media).where(Media.id == media_id)

    options = []
    if load_genres:
        options.append(selectinload(Media.genres))
    if load_catalogs:
        options.append(selectinload(Media.catalogs))
    if load_aka_titles:
        options.append(selectinload(Media.aka_titles))
    if load_stars:
        options.append(selectinload(Media.cast))

    if options:
        query = query.options(*options)

    result = await session.exec(query)
    return result.first()


async def get_media_by_external_id(
    session: AsyncSession,
    external_id: str,
    media_type: MediaType | None = None,
) -> Media | None:
    """Get media by external ID using the MediaExternalID table.

    Unified lookup via MediaExternalID table for all provider formats:
    - tt* -> IMDb (provider='imdb', external_id='tt*')
    - mftmdb* -> TMDB (legacy, provider='tmdb', external_id=numeric part)
    - tmdb:* -> TMDB (provider='tmdb', external_id=*)
    - tvdb:* -> TVDB (provider='tvdb', external_id=*)
    - mal:* -> MAL (provider='mal', external_id=*)
    - mf:* -> MediaFusion internal (direct PK lookup)

    Performance: ~0.02ms per query, 14,400 QPS at 50 concurrent connections.

    Falls back to direct external_id column lookup for backwards compatibility
    during migration period.
    """
    # Parse the external ID format to determine provider
    provider = None
    provider_external_id = None

    if external_id.startswith("tt"):
        # IMDb ID
        provider = "imdb"
        provider_external_id = external_id
    elif external_id.startswith("mf:"):
        # MediaFusion internal ID - direct PK lookup (fastest path)
        try:
            internal_id = int(external_id[3:])
            media = await session.get(Media, internal_id)
            if media and (media_type is None or media.type == media_type):
                return media
            return None
        except ValueError:
            return None
    elif external_id.startswith("mftmdb"):
        # Legacy TMDB format: mftmdb123456
        provider = "tmdb"
        provider_external_id = external_id.replace("mftmdb", "")
    elif ":" in external_id:
        # Provider prefix format: provider:id
        parts = external_id.split(":", 1)
        if len(parts) == 2:
            provider = parts[0].lower()
            provider_external_id = parts[1]

    if provider and provider_external_id:
        # JOIN lookup via MediaExternalID (benchmarked: best performance at scale)
        query = (
            select(Media)
            .join(MediaExternalID, Media.id == MediaExternalID.media_id)
            .where(
                MediaExternalID.provider == provider,
                MediaExternalID.external_id == provider_external_id,
            )
        )
        if media_type:
            query = query.where(Media.type == media_type)

        result = await session.exec(query)
        return result.first()

    return None


async def resolve_external_id(
    session: AsyncSession,
    external_id: str,
    media_type: MediaType | None = None,
) -> Media:
    """
    Resolve external_id to Media, raise HTTPException 404 if not found.

    This is the standard pattern for API routes:
    - Accept external_id (string like 'tt1234567', 'tmdb:123') in route
    - Resolve to Media object with internal id
    - Use media.id for all DB operations

    Args:
        session: Database session
        external_id: External identifier string (IMDb, TMDB, etc.)
        media_type: Optional filter by media type

    Returns:
        Media object with internal id

    Raises:
        HTTPException: 404 if media not found
    """
    media = await get_media_by_external_id(session, external_id, media_type)
    if not media:
        raise HTTPException(status_code=404, detail=f"Media not found: {external_id}")
    return media


async def get_media_by_title_year(
    session: AsyncSession,
    title: str,
    year: int | None,
    media_type: MediaType,
) -> Media | None:
    """Find media by title and year."""
    query = select(Media).where(
        Media.type == media_type,
        func.lower(Media.title) == func.lower(title),
    )
    if year:
        query = query.where(Media.year == year)

    result = await session.exec(query)
    return result.first()


async def create_media(
    session: AsyncSession,
    *,
    external_id: str,
    media_type: MediaType,
    title: str,
    year: int | None = None,
    description: str | None = None,
    runtime_minutes: int | None = None,
    release_date: datetime | None = None,
    adult: bool = False,
    genres: list[Genre] | None = None,
    catalogs: list[Catalog] | None = None,
    **kwargs,
) -> Media:
    """Create a new media entry.

    Note: Images are stored separately in MediaImage table via add_media_image().
    """
    media = Media(
        external_id=external_id,
        type=media_type,
        title=title,
        year=year,
        description=description,
        runtime_minutes=runtime_minutes,
        release_date=release_date,
        adult=adult,
        **kwargs,
    )

    session.add(media)
    await session.flush()

    # Add genre links
    if genres:
        for genre in genres:
            link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
            session.add(link)

    # Add catalog links
    if catalogs:
        await session.flush()
        catalog_links = [{"media_id": media.id, "catalog_id": catalog.id} for catalog in catalogs]
        stmt = pg_insert(MediaCatalogLink).values(catalog_links).on_conflict_do_nothing()
        await session.exec(stmt)

    await session.flush()
    return media


async def update_media(
    session: AsyncSession,
    media_id: int,
    **updates,
) -> Media | None:
    """Update media fields."""
    if not updates:
        return await get_media_by_id(session, media_id)

    updates["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(sa_update(Media).where(Media.id == media_id).values(**updates))
    await session.flush()

    return await get_media_by_id(session, media_id)


async def delete_media(
    session: AsyncSession,
    media_id: int,
) -> bool:
    """Delete media and all related data."""
    result = await session.exec(sa_delete(Media).where(Media.id == media_id))
    await session.flush()
    return result.rowcount > 0


async def increment_stream_count(
    session: AsyncSession,
    media_id: int,
    created_at: datetime | None = None,
) -> None:
    """Increment total_streams count and update last_stream_added."""
    if created_at is None:
        created_at = datetime.now(pytz.UTC)

    await session.exec(
        sa_update(Media)
        .where(Media.id == media_id)
        .values(
            total_streams=Media.total_streams + 1,
            last_stream_added=func.greatest(Media.last_stream_added, created_at),
        )
    )


async def decrement_stream_count(
    session: AsyncSession,
    media_id: int,
) -> None:
    """Decrement total_streams count (minimum 0)."""
    await session.exec(
        sa_update(Media).where(Media.id == media_id).values(total_streams=func.greatest(0, Media.total_streams - 1))
    )


async def search_media(
    session: AsyncSession,
    query_text: str,
    media_type: MediaType | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Sequence[Media]:
    """Full-text search for media by title."""
    query = select(Media).where(Media.title_tsv.match(query_text))

    if media_type:
        query = query.where(Media.type == media_type)

    query = query.order_by(Media.popularity.desc().nullslast())
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


async def get_media_count(
    session: AsyncSession,
    media_type: MediaType | None = None,
) -> int:
    """Get count of media entries."""
    query = select(func.count(Media.id))
    if media_type:
        query = query.where(Media.type == media_type)

    result = await session.exec(query)
    return result.first() or 0


async def get_metadata_counts(session: AsyncSession) -> dict:
    """Get counts of media entries by type."""
    movies = await get_media_count(session, MediaType.MOVIE)
    series = await get_media_count(session, MediaType.SERIES)
    tv_channels = await get_media_count(session, MediaType.TV)

    return {
        "movies": movies,
        "series": series,
        "tv_channels": tv_channels,
    }


# =============================================================================
# MOVIE METADATA CRUD
# =============================================================================


async def get_movie_metadata(
    session: AsyncSession,
    media_id: int,
) -> MovieMetadata | None:
    """Get movie-specific metadata."""
    query = select(MovieMetadata).where(MovieMetadata.media_id == media_id)
    result = await session.exec(query)
    return result.first()


async def create_movie_metadata(
    session: AsyncSession,
    media_id: int,
    **kwargs,
) -> MovieMetadata:
    """Create movie-specific metadata."""
    movie = MovieMetadata(media_id=media_id, **kwargs)
    session.add(movie)
    await session.flush()
    return movie


# =============================================================================
# SERIES METADATA CRUD
# =============================================================================


async def get_series_metadata(
    session: AsyncSession,
    media_id: int,
    *,
    load_seasons: bool = False,
) -> SeriesMetadata | None:
    """Get series-specific metadata with optional season loading."""
    query = select(SeriesMetadata).where(SeriesMetadata.media_id == media_id)

    if load_seasons:
        query = query.options(selectinload(SeriesMetadata.seasons).selectinload(Season.episodes))

    result = await session.exec(query)
    return result.first()


async def create_series_metadata(
    session: AsyncSession,
    media_id: int,
    **kwargs,
) -> SeriesMetadata:
    """Create series-specific metadata."""
    series = SeriesMetadata(media_id=media_id, **kwargs)
    session.add(series)
    await session.flush()
    return series


# =============================================================================
# SEASON CRUD
# =============================================================================


async def get_season(
    session: AsyncSession,
    series_id: int,
    season_number: int,
    *,
    load_episodes: bool = False,
) -> Season | None:
    """Get a specific season."""
    query = select(Season).where(
        Season.series_id == series_id,
        Season.season_number == season_number,
    )

    if load_episodes:
        query = query.options(selectinload(Season.episodes))

    result = await session.exec(query)
    return result.first()


async def get_seasons_for_series(
    session: AsyncSession,
    series_id: int,
    *,
    load_episodes: bool = False,
) -> Sequence[Season]:
    """Get all seasons for a series."""
    query = select(Season).where(Season.series_id == series_id).order_by(Season.season_number)

    if load_episodes:
        query = query.options(selectinload(Season.episodes))

    result = await session.exec(query)
    return result.all()


async def create_season(
    session: AsyncSession,
    series_id: int,
    season_number: int,
    **kwargs,
) -> Season:
    """Create a new season."""
    season = Season(
        series_id=series_id,
        season_number=season_number,
        **kwargs,
    )
    session.add(season)
    await session.flush()
    return season


async def get_or_create_season(
    session: AsyncSession,
    series_id: int,
    season_number: int,
    **kwargs,
) -> Season:
    """Get existing season or create new one."""
    existing = await get_season(session, series_id, season_number)
    if existing:
        return existing
    return await create_season(session, series_id, season_number, **kwargs)


# =============================================================================
# EPISODE CRUD
# =============================================================================


async def get_episode(
    session: AsyncSession,
    season_id: int,
    episode_number: int,
) -> Episode | None:
    """Get a specific episode."""
    query = select(Episode).where(
        Episode.season_id == season_id,
        Episode.episode_number == episode_number,
    )
    result = await session.exec(query)
    return result.first()


async def get_episodes_for_season(
    session: AsyncSession,
    season_id: int,
) -> Sequence[Episode]:
    """Get all episodes for a season."""
    query = select(Episode).where(Episode.season_id == season_id).order_by(Episode.episode_number)
    result = await session.exec(query)
    return result.all()


async def create_episode(
    session: AsyncSession,
    season_id: int,
    episode_number: int,
    title: str | None = None,
    **kwargs,
) -> Episode:
    """Create a new episode."""
    episode = Episode(
        season_id=season_id,
        episode_number=episode_number,
        title=title,
        **kwargs,
    )
    session.add(episode)
    await session.flush()
    return episode


async def get_or_create_episode(
    session: AsyncSession,
    season_id: int,
    episode_number: int,
    **kwargs,
) -> Episode:
    """Get existing episode or create new one."""
    existing = await get_episode(session, season_id, episode_number)
    if existing:
        return existing
    return await create_episode(session, season_id, episode_number, **kwargs)


# =============================================================================
# MEDIA LINKS (Genre, Catalog, etc.)
# =============================================================================


async def add_genres_to_media(
    session: AsyncSession,
    media_id: int,
    genre_ids: list[int],
) -> None:
    """Add genres to media (ignores duplicates)."""
    for genre_id in genre_ids:
        stmt = (
            pg_insert(MediaGenreLink)
            .values(
                media_id=media_id,
                genre_id=genre_id,
            )
            .on_conflict_do_nothing()
        )
        await session.exec(stmt)


async def add_catalogs_to_media(
    session: AsyncSession,
    media_id: int,
    catalog_ids: list[int],
) -> None:
    """Add catalogs to media (ignores duplicates)."""
    for catalog_id in catalog_ids:
        stmt = (
            pg_insert(MediaCatalogLink)
            .values(
                media_id=media_id,
                catalog_id=catalog_id,
            )
            .on_conflict_do_nothing()
        )
        await session.exec(stmt)


async def add_aka_title(
    session: AsyncSession,
    media_id: int,
    title: str,
) -> AkaTitle:
    """Add an alternative title to media."""
    aka = AkaTitle(media_id=media_id, title=title)
    session.add(aka)
    await session.flush()
    return aka


# =============================================================================
# MEDIA IMAGES CRUD (Multi-Provider)
# =============================================================================


async def get_media_images(
    session: AsyncSession,
    media_id: int,
    image_type: str | None = None,
) -> Sequence[MediaImage]:
    """Get all images for a media item, optionally filtered by type."""
    query = (
        select(MediaImage)
        .where(MediaImage.media_id == media_id)
        .options(selectinload(MediaImage.provider))
        .order_by(MediaImage.is_primary.desc(), MediaImage.display_order)
    )
    if image_type:
        query = query.where(MediaImage.image_type == image_type)

    result = await session.exec(query)
    return result.all()


async def add_media_image(
    session: AsyncSession,
    media_id: int,
    provider_id: int,
    image_type: str,
    url: str,
    *,
    language: str | None = None,
    width: int | None = None,
    height: int | None = None,
    aspect_ratio: float | None = None,
    vote_average: float | None = None,
    vote_count: int | None = None,
    is_primary: bool = False,
    display_order: int = 100,
) -> MediaImage:
    """Add an image for a media item."""
    image = MediaImage(
        media_id=media_id,
        provider_id=provider_id,
        image_type=image_type,
        url=url,
        language=language,
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        vote_average=vote_average,
        vote_count=vote_count,
        is_primary=is_primary,
        display_order=display_order,
    )
    session.add(image)
    await session.flush()
    return image


async def get_primary_image(
    session: AsyncSession,
    media_id: int,
    image_type: str,
) -> MediaImage | None:
    """Get the primary image of a specific type for a media item."""
    query = (
        select(MediaImage)
        .where(
            MediaImage.media_id == media_id,
            MediaImage.image_type == image_type,
        )
        .order_by(MediaImage.is_primary.desc(), MediaImage.display_order)
        .limit(1)
    )
    result = await session.exec(query)
    return result.first()


# =============================================================================
# MEDIA RATINGS CRUD (Multi-Provider)
# =============================================================================


async def get_media_ratings(
    session: AsyncSession,
    media_id: int,
) -> Sequence[MediaRating]:
    """Get all ratings for a media item from all providers."""
    query = (
        select(MediaRating)
        .where(MediaRating.media_id == media_id)
        .options(selectinload(MediaRating.provider))
        .order_by(MediaRating.rating.desc())
    )
    result = await session.exec(query)
    return result.all()


async def get_rating_by_provider(
    session: AsyncSession,
    media_id: int,
    provider_name: str,
) -> MediaRating | None:
    """Get rating from a specific provider."""
    query = (
        select(MediaRating)
        .join(RatingProvider)
        .where(
            MediaRating.media_id == media_id,
            func.lower(RatingProvider.name) == provider_name.lower(),
        )
        .options(selectinload(MediaRating.provider))
    )
    result = await session.exec(query)
    return result.first()


async def upsert_media_rating(
    session: AsyncSession,
    media_id: int,
    rating_provider_id: int,
    rating: float,
    *,
    rating_raw: float | None = None,
    vote_count: int | None = None,
    rating_type: str | None = None,
    certification: str | None = None,
) -> MediaRating:
    """Add or update a rating for a media item."""
    # Check if exists
    existing = await session.exec(
        select(MediaRating).where(
            MediaRating.media_id == media_id,
            MediaRating.rating_provider_id == rating_provider_id,
        )
    )
    existing = existing.first()

    if existing:
        existing.rating = rating
        existing.rating_raw = rating_raw
        existing.vote_count = vote_count
        existing.rating_type = rating_type
        existing.certification = certification
        existing.updated_at = datetime.now(pytz.UTC)
        await session.flush()
        return existing

    new_rating = MediaRating(
        media_id=media_id,
        rating_provider_id=rating_provider_id,
        rating=rating,
        rating_raw=rating_raw,
        vote_count=vote_count,
        rating_type=rating_type,
        certification=certification,
    )
    session.add(new_rating)
    await session.flush()
    return new_rating


async def get_mediafusion_rating(
    session: AsyncSession,
    media_id: int,
) -> MediaFusionRating | None:
    """Get MediaFusion community rating for a media item."""
    query = select(MediaFusionRating).where(MediaFusionRating.media_id == media_id)
    result = await session.exec(query)
    return result.first()


async def update_mediafusion_rating(
    session: AsyncSession,
    media_id: int,
    vote_value: int,  # 1-5 stars or -1/+1 for down/up
) -> MediaFusionRating:
    """Update MediaFusion community rating aggregate."""
    existing = await get_mediafusion_rating(session, media_id)

    if not existing:
        existing = MediaFusionRating(media_id=media_id)
        session.add(existing)

    # Update based on vote type
    existing.total_votes += 1
    if vote_value == -1:
        existing.downvotes += 1
    elif vote_value == 1:
        existing.upvotes += 1
        existing.one_star_count += 1
    elif vote_value == 2:
        existing.two_star_count += 1
    elif vote_value == 3:
        existing.three_star_count += 1
    elif vote_value == 4:
        existing.four_star_count += 1
    elif vote_value == 5:
        existing.five_star_count += 1

    # Recalculate average
    total_stars = (
        existing.one_star_count * 2  # 1 star = 2/10
        + existing.two_star_count * 4  # 2 stars = 4/10
        + existing.three_star_count * 6  # 3 stars = 6/10
        + existing.four_star_count * 8  # 4 stars = 8/10
        + existing.five_star_count * 10  # 5 stars = 10/10
    )
    total_star_votes = sum(
        [
            existing.one_star_count,
            existing.two_star_count,
            existing.three_star_count,
            existing.four_star_count,
            existing.five_star_count,
        ]
    )
    if total_star_votes > 0:
        existing.average_rating = total_stars / total_star_votes

    existing.updated_at = datetime.now(pytz.UTC)
    await session.flush()
    return existing


# =============================================================================
# COMPREHENSIVE MEDIA LOADERS
# =============================================================================


async def get_media_with_all_relations(
    session: AsyncSession,
    media_id: int,
) -> Media | None:
    """Get media with ALL relationships loaded for full data access.

    Loads: genres, catalogs, aka_titles, keywords, images, ratings, type-specific metadata
    """
    query = (
        select(Media)
        .where(Media.id == media_id)
        .options(
            selectinload(Media.genres),
            selectinload(Media.catalogs),
            selectinload(Media.aka_titles),
            selectinload(Media.keywords),
        )
    )
    result = await session.exec(query)
    media = result.first()

    if not media:
        return None

    # Load images with provider info
    images = await get_media_images(session, media_id)
    media._images = images  # Store for schema conversion

    # Load ratings with provider info
    ratings = await get_media_ratings(session, media_id)
    media._ratings = ratings

    # Load MF community rating
    mf_rating = await get_mediafusion_rating(session, media_id)
    media._mediafusion_rating = mf_rating

    return media


async def get_media_by_external_id_full(
    session: AsyncSession,
    external_id: str,
) -> Media | None:
    """Get media by external ID with all relationships loaded."""
    media = await get_media_by_external_id(session, external_id)

    if not media:
        return None

    return await get_media_with_all_relations(session, media.id)


# =============================================================================
# RATING PROVIDER CRUD
# =============================================================================


async def get_rating_provider(
    session: AsyncSession,
    name: str,
) -> RatingProvider | None:
    """Get rating provider by name."""
    query = select(RatingProvider).where(func.lower(RatingProvider.name) == name.lower())
    result = await session.exec(query)
    return result.first()


# =============================================================================
# METADATA PROVIDER CRUD
# =============================================================================


async def get_metadata_provider(
    session: AsyncSession,
    name: str,
) -> MetadataProvider | None:
    """Get metadata provider by name."""
    query = select(MetadataProvider).where(func.lower(MetadataProvider.name) == name.lower())
    result = await session.exec(query)
    return result.first()


async def get_or_create_metadata_provider(
    session: AsyncSession,
    name: str,
    display_name: str,
    *,
    api_base_url: str | None = None,
    is_external: bool = True,
    priority: int = 100,
) -> MetadataProvider:
    """Get or create a metadata provider."""
    existing = await get_metadata_provider(session, name)
    if existing:
        return existing

    provider = MetadataProvider(
        name=name,
        display_name=display_name,
        api_base_url=api_base_url,
        is_external=is_external,
        priority=priority,
    )
    session.add(provider)
    await session.flush()
    return provider


# =============================================================================
# MEDIA EXTERNAL ID CRUD
# =============================================================================


async def add_external_id(
    session: AsyncSession,
    media_id: int,
    provider: str,
    external_id: str,
) -> MediaExternalID:
    """Add an external ID for a media item.

    Args:
        media_id: Internal Media.id
        provider: Provider name (imdb, tmdb, tvdb, mal, kitsu, etc.)
        external_id: Provider-specific ID (tt1234567 for imdb, 550 for tmdb, etc.)

    Returns:
        The created or existing MediaExternalID
    """
    # Use upsert to handle duplicates gracefully
    stmt = (
        pg_insert(MediaExternalID)
        .values(
            media_id=media_id,
            provider=provider.lower(),
            external_id=external_id,
        )
        .on_conflict_do_nothing(constraint="uq_provider_external_id")
    )
    await session.execute(stmt)
    await session.flush()

    # Return the record (either new or existing)
    query = select(MediaExternalID).where(
        MediaExternalID.provider == provider.lower(),
        MediaExternalID.external_id == external_id,
    )
    result = await session.exec(query)
    return result.first()


async def get_external_ids_for_media(
    session: AsyncSession,
    media_id: int,
) -> Sequence[MediaExternalID]:
    """Get all external IDs for a media item."""
    query = select(MediaExternalID).where(MediaExternalID.media_id == media_id).order_by(MediaExternalID.provider)
    result = await session.exec(query)
    return result.all()


async def get_external_id(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> MediaExternalID | None:
    """Get MediaExternalID by provider and external_id."""
    query = select(MediaExternalID).where(
        MediaExternalID.provider == provider.lower(),
        MediaExternalID.external_id == external_id,
    )
    result = await session.exec(query)
    return result.first()


async def get_media_id_by_external(
    session: AsyncSession,
    provider: str,
    external_id: str,
) -> int | None:
    """Get Media.id for a given provider and external ID.

    This is a lightweight lookup that returns just the internal ID.
    Use get_media_by_external_id() if you need the full Media object.
    """
    query = select(MediaExternalID.media_id).where(
        MediaExternalID.provider == provider.lower(),
        MediaExternalID.external_id == external_id,
    )
    result = await session.exec(query)
    return result.first()


async def get_canonical_external_id(
    session: AsyncSession,
    media_id: int,
    preferred_provider: str = "imdb",
    use_cache: bool = True,
) -> str:
    """Get the canonical external ID for a media item with Redis caching.

    Returns the external ID from the preferred provider if available,
    otherwise returns the first available external ID in priority order.
    Uses Redis cache for better performance (1 hour TTL).

    Args:
        media_id: Internal Media.id
        preferred_provider: Preferred provider (default: imdb)
        use_cache: Whether to use Redis cache (default: True)

    Returns:
        Formatted external ID string (e.g., 'tt1234567', 'tmdb:550', 'mf:123')
    """
    # Check Redis cache first
    cache_key = f"ext_id:{media_id}"
    if use_cache:
        cached = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached:
            # Redis returns bytes, decode to string
            return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    # Fetch all external IDs in one query (more efficient)
    query = select(MediaExternalID).where(MediaExternalID.media_id == media_id)
    result = await session.exec(query)
    ext_ids = result.all()

    if not ext_ids:
        # No external ID found - return internal ID
        canonical = f"mf:{media_id}"
        if use_cache:
            await REDIS_ASYNC_CLIENT.set(cache_key, canonical, ex=3600)  # 1 hour
        return canonical

    # Build lookup by provider
    id_by_provider = {ext.provider: ext.external_id for ext in ext_ids}

    # Try preferred provider first
    if preferred_provider.lower() in id_by_provider:
        canonical = format_external_id(preferred_provider.lower(), id_by_provider[preferred_provider.lower()])
        if use_cache:
            await REDIS_ASYNC_CLIENT.set(cache_key, canonical, ex=3600)
        return canonical

    # Fallback: get first available by priority
    priority_order = ["imdb", "tvdb", "tmdb", "mal", "kitsu"]
    for provider in priority_order:
        if provider in id_by_provider:
            canonical = format_external_id(provider, id_by_provider[provider])
            if use_cache:
                await REDIS_ASYNC_CLIENT.set(cache_key, canonical, ex=3600)
            return canonical

    # Use first available
    first = ext_ids[0]
    canonical = format_external_id(first.provider, first.external_id)
    if use_cache:
        await REDIS_ASYNC_CLIENT.set(cache_key, canonical, ex=3600)
    return canonical


async def get_canonical_external_ids_batch(
    session: AsyncSession,
    media_ids: list[int],
    preferred_provider: str = "imdb",
) -> dict[int, str]:
    """Batch get canonical external IDs for multiple media items.

    Efficient batch version that checks Redis cache first, then fetches
    missing IDs from database in a single query.

    Args:
        media_ids: List of internal Media.id values
        preferred_provider: Preferred provider (default: imdb)

    Returns:
        Dict mapping media_id to canonical external ID string
    """
    if not media_ids:
        return {}

    result_map: dict[int, str] = {}
    missing_ids: list[int] = []

    # Check Redis cache for all IDs in a single MGET call
    cache_keys = [f"ext_id:{mid}" for mid in media_ids]
    cached_values = await REDIS_ASYNC_CLIENT.mget(cache_keys)

    for media_id, cached in zip(media_ids, cached_values):
        if cached is not None:
            result_map[media_id] = cached.decode("utf-8") if isinstance(cached, bytes) else cached
        else:
            missing_ids.append(media_id)

    if not missing_ids:
        return result_map

    # Fetch all missing external IDs in one query
    query = select(MediaExternalID).where(MediaExternalID.media_id.in_(missing_ids))
    db_result = await session.exec(query)
    all_ext_ids = db_result.all()

    # Group by media_id
    ext_ids_by_media: dict[int, list] = {mid: [] for mid in missing_ids}
    for ext in all_ext_ids:
        ext_ids_by_media[ext.media_id].append(ext)

    # Resolve canonical ID for each missing media
    priority_order = ["imdb", "tvdb", "tmdb", "mal", "kitsu"]

    # Collect resolved IDs for batch Redis write
    to_cache: dict[str, str] = {}

    for media_id in missing_ids:
        ext_ids = ext_ids_by_media.get(media_id, [])

        if not ext_ids:
            canonical = f"mf:{media_id}"
        else:
            id_by_provider = {ext.provider: ext.external_id for ext in ext_ids}

            # Try preferred provider first
            if preferred_provider.lower() in id_by_provider:
                canonical = format_external_id(
                    preferred_provider.lower(),
                    id_by_provider[preferred_provider.lower()],
                )
            else:
                # Fallback by priority
                canonical = None
                for provider in priority_order:
                    if provider in id_by_provider:
                        canonical = format_external_id(provider, id_by_provider[provider])
                        break

                if not canonical:
                    first = ext_ids[0]
                    canonical = format_external_id(first.provider, first.external_id)

        result_map[media_id] = canonical
        to_cache[f"ext_id:{media_id}"] = canonical

    # Batch cache all resolved IDs concurrently
    if to_cache:
        await asyncio.gather(*(REDIS_ASYNC_CLIENT.set(key, value, ex=3600) for key, value in to_cache.items()))

    return result_map


async def invalidate_external_id_cache(media_id: int) -> None:
    """Invalidate Redis cache for a media's external ID.

    Call this when external IDs are added/modified for a media item.
    """
    cache_key = f"ext_id:{media_id}"
    await REDIS_ASYNC_CLIENT.delete(cache_key)


META_CACHE_PREFIX = "meta:"


async def invalidate_meta_cache(meta_id: str) -> None:
    """Invalidate Redis cache for a media item's Stremio meta response.

    Deletes cached meta for all catalog types (movie, series, tv)
    so stale metadata is never served after updates.

    Args:
        meta_id: External ID string (e.g., 'tt1234567', 'mf:123')
    """
    types = ["movie", "series", "tv", "events"]
    keys = [f"{META_CACHE_PREFIX}{t}:{meta_id}" for t in types]
    await REDIS_ASYNC_CLIENT.delete(*keys)


def format_external_id(provider: str, external_id: str) -> str:
    """Format an external ID for display/API responses.

    Args:
        provider: Provider name (imdb, tmdb, tvdb, etc.)
        external_id: Provider-specific ID

    Returns:
        Formatted ID string (e.g., 'tt1234567' for IMDb, 'tmdb:550' for TMDB)
    """
    if provider == "imdb":
        # IMDb IDs are already prefixed with 'tt'
        return external_id if external_id.startswith("tt") else f"tt{external_id}"
    else:
        # Other providers use provider:id format
        return f"{provider}:{external_id}"


def parse_external_id(external_id_str: str) -> tuple[str | None, str | None]:
    """Parse an external ID string into provider and ID components.

    Args:
        external_id_str: External ID string (e.g., 'tt1234567', 'tmdb:550')

    Returns:
        Tuple of (provider, external_id) or (None, None) if unparseable
    """
    if external_id_str.startswith("tt"):
        return ("imdb", external_id_str)
    elif external_id_str.startswith("mftmdb"):
        return ("tmdb", external_id_str.replace("mftmdb", ""))
    elif external_id_str.startswith("mf:"):
        return ("mediafusion", external_id_str[3:])
    elif ":" in external_id_str:
        parts = external_id_str.split(":", 1)
        if len(parts) == 2:
            return (parts[0].lower(), parts[1])
    return (None, None)


async def bulk_add_external_ids(
    session: AsyncSession,
    external_ids: list[dict],
) -> int:
    """Bulk add external IDs.

    Args:
        external_ids: List of dicts with keys: media_id, provider, external_id

    Returns:
        Number of records inserted (excludes conflicts)
    """
    if not external_ids:
        return 0

    # Normalize provider names
    normalized = [
        {
            "media_id": eid["media_id"],
            "provider": eid["provider"].lower(),
            "external_id": eid["external_id"],
        }
        for eid in external_ids
    ]

    stmt = pg_insert(MediaExternalID).values(normalized)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_provider_external_id")
    result = await session.execute(stmt)
    await session.flush()

    return result.rowcount


async def get_all_external_ids_dict(
    session: AsyncSession,
    media_id: int,
) -> dict[str, str]:
    """Get all external IDs for a media as a dictionary.

    Returns:
        Dict mapping provider name to external_id (e.g., {'imdb': 'tt1234567', 'tmdb': '550'})
    """
    ext_ids = await get_external_ids_for_media(session, media_id)
    return {eid.provider: eid.external_id for eid in ext_ids}


async def get_all_external_ids_batch(
    session: AsyncSession,
    media_ids: list[int],
) -> dict[int, dict[str, str]]:
    """Batch get all external IDs for multiple media items.

    Efficient batch version that fetches all external IDs from database in a single query.

    Args:
        media_ids: List of internal Media.id values

    Returns:
        Dict mapping media_id to dict of provider -> external_id
        e.g., {123: {'imdb': 'tt1234567', 'tmdb': '550'}, 456: {'imdb': 'tt9999999'}}
    """
    if not media_ids:
        return {}

    # Query all external IDs for the given media IDs
    query = select(MediaExternalID).where(MediaExternalID.media_id.in_(media_ids))
    result = await session.exec(query)

    # Group by media_id
    result_map: dict[int, dict[str, str]] = {mid: {} for mid in media_ids}
    for ext_id in result.all():
        result_map[ext_id.media_id][ext_id.provider] = ext_id.external_id

    return result_map
