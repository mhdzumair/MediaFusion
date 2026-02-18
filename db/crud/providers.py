"""
Provider CRUD operations.

Handles MetadataProvider, RatingProvider, MediaImage, MediaRating.
Includes priority-based conflict resolution and waterfall fallback for multi-provider data.
"""

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
import traceback

import pytz
from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.orm import selectinload
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.models import (
    AkaTitle,
    Episode,
    EpisodeImage,
    Keyword,
    Media,
    MediaCast,
    MediaCrew,
    MediaExternalID,
    MediaFusionRating,
    MediaGenreLink,
    MediaImage,
    MediaKeywordLink,
    MediaParentalCertificateLink,
    MediaRating,
    MediaTrailer,
    MetadataProvider,
    ParentalCertificate,
    Person,
    ProviderMetadata,
    RatingProvider,
    Season,
    SeriesMetadata,
)
from db.crud.reference import get_or_create_genre

logger = logging.getLogger(__name__)


# =============================================================================
# PROVIDER PRIORITY CONFIGURATION
# =============================================================================

# Default provider priorities (lower = higher priority)
PROVIDER_PRIORITY = {
    "imdb": 10,  # Gold standard for movies
    "tvdb": 15,  # Best for TV series episodes
    "tmdb": 20,  # Best descriptions, good images
    "mal": 25,  # Best for anime
    "kitsu": 30,  # Anime alternative
    "fanart": 50,  # Images only
    "mediafusion": 100,  # User-created, lowest priority
}

# Field-specific priority overrides (use these providers in this order per field)
FIELD_PRIORITY = {
    # Title: IMDb is most authoritative
    "title": ["imdb", "tmdb", "tvdb", "mal"],
    # Description: TMDB writes better summaries
    "description": ["tmdb", "imdb", "tvdb", "mal"],
    # Runtime: IMDb is most accurate
    "runtime": ["imdb", "tmdb", "tvdb"],
    # Release date: TMDB has precise dates
    "release_date": ["tmdb", "imdb", "tvdb"],
    # Episode data: TVDB is the standard for series
    "episodes": ["tvdb", "tmdb", "imdb"],
    # Anime-specific: MAL/Kitsu are authoritative
    "anime_data": ["mal", "kitsu", "anidb"],
    # Images: Fanart > TMDB > others
    "poster": ["fanart", "tmdb", "tvdb", "imdb"],
    "background": ["fanart", "tmdb", "tvdb"],
}

# User content priority levels
USER_PRIORITY = {
    "user_created": 1,  # User created entire content - HIGHEST
    "user_addition": 5,  # User added episode/data to existing content
}


def make_json_serializable(obj: Any) -> Any:
    """
    Recursively convert non-JSON-serializable objects to JSON-serializable types.
    Handles datetime, date, and nested structures.
    """
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    # Check if it's a type that json can handle
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        # Convert to string as fallback
        return str(obj)


# =============================================================================
# METADATA PROVIDER CRUD
# =============================================================================


async def get_provider_by_id(
    session: AsyncSession,
    provider_id: int,
) -> MetadataProvider | None:
    """Get metadata provider by ID."""
    query = select(MetadataProvider).where(MetadataProvider.id == provider_id)
    result = await session.exec(query)
    return result.first()


async def get_provider_by_name(
    session: AsyncSession,
    name: str,
) -> MetadataProvider | None:
    """Get metadata provider by name (e.g., 'tmdb', 'imdb', 'mediafusion')."""
    query = select(MetadataProvider).where(func.lower(MetadataProvider.name) == func.lower(name))
    result = await session.exec(query)
    return result.first()


async def get_all_providers(
    session: AsyncSession,
    *,
    only_active: bool = True,
) -> Sequence[MetadataProvider]:
    """Get all metadata providers."""
    query = select(MetadataProvider)

    if only_active:
        query = query.where(MetadataProvider.is_active.is_(True))

    query = query.order_by(MetadataProvider.priority)
    result = await session.exec(query)
    return result.all()


async def create_provider(
    session: AsyncSession,
    name: str,
    *,
    display_name: str | None = None,
    base_url: str | None = None,
    is_active: bool = True,
    priority: int = 100,
) -> MetadataProvider:
    """Create a new metadata provider."""
    provider = MetadataProvider(
        name=name.lower(),
        display_name=display_name or name,
        base_url=base_url,
        is_active=is_active,
        priority=priority,
    )
    session.add(provider)
    await session.flush()
    return provider


async def get_or_create_provider(
    session: AsyncSession,
    name: str,
    **kwargs,
) -> MetadataProvider:
    """Get existing provider or create new one."""
    existing = await get_provider_by_name(session, name)
    if existing:
        return existing
    return await create_provider(session, name, **kwargs)


# =============================================================================
# RATING PROVIDER CRUD
# =============================================================================


async def get_rating_provider_by_name(
    session: AsyncSession,
    name: str,
) -> RatingProvider | None:
    """Get rating provider by name (e.g., 'imdb', 'rotten_tomatoes', 'trakt')."""
    query = select(RatingProvider).where(func.lower(RatingProvider.name) == func.lower(name))
    result = await session.exec(query)
    return result.first()


async def get_all_rating_providers(
    session: AsyncSession,
) -> Sequence[RatingProvider]:
    """Get all rating providers."""
    query = select(RatingProvider).order_by(RatingProvider.name)
    result = await session.exec(query)
    return result.all()


async def create_rating_provider(
    session: AsyncSession,
    name: str,
    *,
    display_name: str | None = None,
    max_rating: float = 10.0,
    rating_type: str = "score",  # score, percentage, stars
) -> RatingProvider:
    """Create a new rating provider."""
    provider = RatingProvider(
        name=name.lower(),
        display_name=display_name or name,
        max_rating=max_rating,
        rating_type=rating_type,
    )
    session.add(provider)
    await session.flush()
    return provider


async def get_or_create_rating_provider(
    session: AsyncSession,
    name: str,
    **kwargs,
) -> RatingProvider:
    """Get existing rating provider or create new one."""
    existing = await get_rating_provider_by_name(session, name)
    if existing:
        return existing
    return await create_rating_provider(session, name, **kwargs)


# =============================================================================
# MEDIA IMAGE CRUD
# =============================================================================


async def get_images_for_media(
    session: AsyncSession,
    media_id: int,
    *,
    image_type: str | None = None,  # poster, backdrop, logo, etc.
) -> Sequence[MediaImage]:
    """Get all images for a media entry."""
    query = select(MediaImage).where(MediaImage.media_id == media_id)

    if image_type:
        query = query.where(MediaImage.image_type == image_type)

    query = query.order_by(MediaImage.is_primary.desc(), MediaImage.vote_average.desc().nullslast())
    result = await session.exec(query)
    return result.all()


async def add_media_image(
    session: AsyncSession,
    media_id: int,
    url: str,
    image_type: str,
    *,
    provider_id: int | None = None,
    width: int | None = None,
    height: int | None = None,
    language: str | None = None,
    vote_average: float | None = None,
    is_primary: bool = False,
) -> MediaImage:
    """Add an image to media."""
    image = MediaImage(
        media_id=media_id,
        url=url,
        image_type=image_type,
        provider_id=provider_id,
        width=width,
        height=height,
        language=language,
        vote_average=vote_average,
        is_primary=is_primary,
    )
    session.add(image)
    await session.flush()
    return image


async def set_primary_image(
    session: AsyncSession,
    media_id: int,
    image_id: int,
    image_type: str,
) -> None:
    """Set an image as primary for its type."""
    # Unset other primary images of same type
    await session.exec(
        sa_update(MediaImage)
        .where(
            MediaImage.media_id == media_id,
            MediaImage.image_type == image_type,
            MediaImage.id != image_id,
        )
        .values(is_primary=False)
    )

    # Set this image as primary
    await session.exec(sa_update(MediaImage).where(MediaImage.id == image_id).values(is_primary=True))
    await session.flush()


# =============================================================================
# MEDIA RATING CRUD
# =============================================================================


async def get_ratings_for_media(
    session: AsyncSession,
    media_id: int,
) -> Sequence[MediaRating]:
    """Get all ratings for a media entry from different providers."""
    query = select(MediaRating).where(MediaRating.media_id == media_id)
    result = await session.exec(query)
    return result.all()


async def add_or_update_rating(
    session: AsyncSession,
    media_id: int,
    provider_id: int,
    rating: float,
    *,
    vote_count: int | None = None,
) -> MediaRating:
    """Add or update a rating for media from a provider."""
    # Check for existing
    query = select(MediaRating).where(
        MediaRating.media_id == media_id,
        MediaRating.provider_id == provider_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if existing:
        await session.exec(
            sa_update(MediaRating)
            .where(MediaRating.id == existing.id)
            .values(
                rating=rating,
                vote_count=vote_count,
                updated_at=datetime.now(pytz.UTC),
            )
        )
        await session.flush()
        return existing

    # Create new
    media_rating = MediaRating(
        media_id=media_id,
        provider_id=provider_id,
        rating=rating,
        vote_count=vote_count,
    )
    session.add(media_rating)
    await session.flush()
    return media_rating


# =============================================================================
# MEDIAFUSION RATING CRUD (User voting)
# =============================================================================


async def vote_on_media(
    session: AsyncSession,
    user_id: int,
    media_id: int,
    rating: float,  # 1-10
) -> MediaFusionRating:
    """User votes on media quality."""
    # Check for existing vote
    query = select(MediaFusionRating).where(
        MediaFusionRating.user_id == user_id,
        MediaFusionRating.media_id == media_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if existing:
        await session.exec(
            sa_update(MediaFusionRating)
            .where(MediaFusionRating.id == existing.id)
            .values(rating=rating, updated_at=datetime.now(pytz.UTC))
        )
        await session.flush()
        return existing

    # Create new vote
    vote = MediaFusionRating(
        user_id=user_id,
        media_id=media_id,
        rating=rating,
    )
    session.add(vote)
    await session.flush()
    return vote


async def get_mediafusion_rating(
    session: AsyncSession,
    media_id: int,
) -> dict:
    """Get aggregated MediaFusion user rating for media."""
    query = select(
        func.avg(MediaFusionRating.rating).label("average"),
        func.count(MediaFusionRating.id).label("count"),
    ).where(MediaFusionRating.media_id == media_id)

    result = await session.exec(query)
    row = result.first()

    return {
        "average": float(row.average) if row and row.average else None,
        "count": row.count if row else 0,
    }


# =============================================================================
# PROVIDER METADATA CRUD (Raw provider data)
# =============================================================================


async def store_provider_metadata(
    session: AsyncSession,
    media_id: int,
    provider_id: int,
    raw_data: dict,
) -> ProviderMetadata:
    """Store raw metadata from a provider."""
    # Make raw_data JSON serializable (handles datetime/date objects)
    serializable_raw_data = make_json_serializable(raw_data)

    # Check for existing
    query = select(ProviderMetadata).where(
        ProviderMetadata.media_id == media_id,
        ProviderMetadata.provider_id == provider_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if existing:
        await session.exec(
            sa_update(ProviderMetadata)
            .where(ProviderMetadata.id == existing.id)
            .values(
                raw_data=serializable_raw_data,
                fetched_at=datetime.now(pytz.UTC),
            )
        )
        await session.flush()
        return existing

    # Create new
    provider_meta = ProviderMetadata(
        media_id=media_id,
        provider_id=provider_id,
        raw_data=serializable_raw_data,
    )
    session.add(provider_meta)
    await session.flush()
    return provider_meta


async def get_provider_metadata(
    session: AsyncSession,
    media_id: int,
    provider_id: int,
) -> ProviderMetadata | None:
    """Get stored provider metadata."""
    query = select(ProviderMetadata).where(
        ProviderMetadata.media_id == media_id,
        ProviderMetadata.provider_id == provider_id,
    )
    result = await session.exec(query)
    return result.first()


# =============================================================================
# WATERFALL FALLBACK & PRIORITY RESOLUTION
# =============================================================================


def is_empty_value(value: Any) -> bool:
    """Check if a value should be considered 'empty' for fallback purposes.

    Empty values trigger waterfall to next provider.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


async def get_all_provider_metadata(
    session: AsyncSession,
    media_id: int,
) -> Sequence[ProviderMetadata]:
    """Get all provider metadata for a media item, ordered by priority."""
    query = (
        select(ProviderMetadata)
        .where(ProviderMetadata.media_id == media_id)
        .options(selectinload(ProviderMetadata.provider))
        .order_by(ProviderMetadata.priority)
    )
    result = await session.exec(query)
    return result.all()


async def resolve_canonical_metadata(
    session: AsyncSession,
    media_id: int,
    field: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Resolve canonical value for each field using WATERFALL FALLBACK.

    If top priority provider has empty data, automatically falls back to next.
    If field is specified, returns just that field.
    Otherwise, returns complete resolved metadata.

    Returns:
        Dict mapping field name to {"value": ..., "source": provider_name}
    """
    # Get all provider metadata for this media
    providers_list = await get_all_provider_metadata(session, media_id)

    if not providers_list:
        return {}

    # Build provider_name -> ProviderMetadata mapping
    provider_data: dict[str, ProviderMetadata] = {}
    for pm in providers_list:
        if pm.provider:
            provider_data[pm.provider.name.lower()] = pm

    if not provider_data:
        return {}

    resolved: dict[str, dict[str, Any]] = {}
    fields_to_resolve = [field] if field else list(FIELD_PRIORITY.keys())

    for f in fields_to_resolve:
        priority_order = FIELD_PRIORITY.get(f, list(PROVIDER_PRIORITY.keys()))

        for provider_name in priority_order:
            if provider_name in provider_data:
                pm = provider_data[provider_name]
                value = getattr(pm, f, None)

                # WATERFALL: Only return if value is NOT empty
                if not is_empty_value(value):
                    resolved[f] = {
                        "value": value,
                        "source": provider_name,
                    }
                    break  # Stop checking lower priority providers

        # If we get here without setting resolved[f], all providers were empty
        if f not in resolved:
            resolved[f] = {"value": None, "source": None}

    return resolved


async def resolve_field_value(
    session: AsyncSession,
    media_id: int,
    field: str,
) -> tuple[Any, str | None]:
    """
    Resolve a single field value using waterfall fallback.

    Returns:
        Tuple of (value, source_provider_name)
    """
    result = await resolve_canonical_metadata(session, media_id, field)
    if field in result:
        return result[field]["value"], result[field]["source"]
    return None, None


async def get_canonical_provider(
    session: AsyncSession,
    media_id: int,
) -> ProviderMetadata | None:
    """Get the canonical provider metadata for a media item."""
    query = (
        select(ProviderMetadata)
        .where(
            ProviderMetadata.media_id == media_id,
            ProviderMetadata.is_canonical.is_(True),
        )
        .options(selectinload(ProviderMetadata.provider))
    )
    result = await session.exec(query)
    return result.first()


async def update_canonical_from_provider(
    session: AsyncSession,
    media_id: int,
    provider_name: str,
    data: dict,
) -> None:
    """
    Update canonical Media data from a provider, respecting priorities.

    This stores data in ProviderMetadata and updates is_canonical based on priority.
    Does NOT update Media table directly - that should be done by the caller if needed.
    """

    # Get or create ProviderMetadata
    provider = await get_provider_by_name(session, provider_name)
    if not provider:
        logger.warning(f"Provider not found: {provider_name}")
        return

    pm = await get_provider_metadata(session, media_id, provider.id)

    provider_priority = PROVIDER_PRIORITY.get(provider_name.lower(), 100)

    if pm:
        # Update existing
        for field, value in data.items():
            if hasattr(pm, field):
                setattr(pm, field, value)
        pm.fetched_at = datetime.now(pytz.UTC)
        pm.priority = provider_priority
    else:
        # Create new
        pm = ProviderMetadata(
            media_id=media_id,
            provider_id=provider.id,
            provider_content_id=data.get("provider_content_id", ""),
            priority=provider_priority,
            fetched_at=datetime.now(pytz.UTC),
        )
        for field, value in data.items():
            if hasattr(pm, field):
                setattr(pm, field, value)
        session.add(pm)

    # Determine if this provider should become canonical
    current_canonical = await get_canonical_provider(session, media_id)

    if not current_canonical or provider_priority < current_canonical.priority:
        # This provider has higher priority - update canonical
        pm.is_canonical = True

        # Unset previous canonical
        if current_canonical and current_canonical.id != pm.id:
            current_canonical.is_canonical = False

    await session.flush()


def parse_date_value(value: Any) -> datetime | None:
    """Parse a date string or date/datetime object to datetime with timezone."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=pytz.UTC)
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=pytz.UTC)
    if isinstance(value, str):
        # Try common date formats
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.replace(tzinfo=pytz.UTC)
            except ValueError:
                continue
        # Try ISO format with time
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=pytz.UTC)
            return dt
        except ValueError:
            pass
    return None


async def store_provider_metadata_with_priority(
    session: AsyncSession,
    media_id: int,
    provider_id: int,
    provider_content_id: str,
    data: dict,
    raw_data: dict | None = None,
) -> ProviderMetadata:
    """
    Store provider metadata with automatic priority handling.

    Args:
        media_id: Media.id
        provider_id: MetadataProvider.id
        provider_content_id: ID used by this provider (e.g., TMDB ID)
        data: Structured data fields (title, description, runtime, etc.)
        raw_data: Optional raw API response

    Returns:
        Created or updated ProviderMetadata
    """
    # Get provider to determine priority
    provider = await get_provider_by_id(session, provider_id)
    priority = provider.default_priority if provider else 100

    # Make raw_data JSON serializable (handles datetime/date objects)
    serializable_raw_data = make_json_serializable(raw_data) if raw_data else None

    # Convert date fields from strings to datetime objects
    if "release_date" in data:
        data["release_date"] = parse_date_value(data["release_date"])

    # Check for existing
    existing = await get_provider_metadata(session, media_id, provider_id)

    if existing:
        # Update existing record
        for field, value in data.items():
            if hasattr(existing, field):
                setattr(existing, field, value)
        if serializable_raw_data:
            existing.raw_data = serializable_raw_data
        existing.fetched_at = datetime.now(pytz.UTC)
        existing.priority = priority
        await session.flush()
        pm = existing
    else:
        # Create new record
        pm = ProviderMetadata(
            media_id=media_id,
            provider_id=provider_id,
            provider_content_id=provider_content_id,
            priority=priority,
            raw_data=serializable_raw_data,
            fetched_at=datetime.now(pytz.UTC),
        )
        for field, value in data.items():
            if hasattr(pm, field):
                setattr(pm, field, value)
        session.add(pm)
        await session.flush()

    # Update canonical status
    current_canonical = await get_canonical_provider(session, media_id)

    if not current_canonical:
        pm.is_canonical = True
    elif pm.priority < current_canonical.priority:
        pm.is_canonical = True
        if current_canonical.id != pm.id:
            current_canonical.is_canonical = False

    await session.flush()
    return pm


# =============================================================================
# EPISODE MERGE HELPERS
# =============================================================================


async def get_provider_episodes(
    session: AsyncSession,
    series_id: int,
    provider_name: str,
) -> Sequence:
    """Get episodes from a specific provider's metadata.

    Note: This queries the Episode table filtered by source_provider.
    """
    provider = await get_provider_by_name(session, provider_name)
    if not provider:
        return []

    # Get series metadata
    series_query = select(SeriesMetadata).where(SeriesMetadata.media_id == series_id)
    result = await session.exec(series_query)
    series = result.first()

    if not series:
        return []

    # Get episodes from this provider
    query = (
        select(Episode)
        .join(Season)
        .where(
            Season.series_id == series.id,
            Episode.source_provider_id == provider.id,
        )
        .order_by(Season.season_number, Episode.episode_number)
    )
    result = await session.exec(query)
    return result.all()


async def get_user_created_episodes(
    session: AsyncSession,
    series_id: int,
) -> Sequence:
    """Get user-created episodes for a series."""
    # Get series metadata
    series_query = select(SeriesMetadata).where(SeriesMetadata.media_id == series_id)
    result = await session.exec(series_query)
    series = result.first()

    if not series:
        return []

    # Get user-created episodes
    query = (
        select(Episode)
        .join(Season)
        .where(
            Season.series_id == series.id,
            Episode.is_user_created.is_(True),
        )
        .order_by(Season.season_number, Episode.episode_number)
    )
    result = await session.exec(query)
    return result.all()


async def get_user_addition_episodes(
    session: AsyncSession,
    series_id: int,
) -> Sequence:
    """Get user-addition episodes for a series (episodes added by users to official series)."""
    # Get series metadata
    series_query = select(SeriesMetadata).where(SeriesMetadata.media_id == series_id)
    result = await session.exec(series_query)
    series = result.first()

    if not series:
        return []

    # Get user-addition episodes
    query = (
        select(Episode)
        .join(Season)
        .where(
            Season.series_id == series.id,
            Episode.is_user_addition.is_(True),
        )
        .order_by(Season.season_number, Episode.episode_number)
    )
    result = await session.exec(query)
    return result.all()


# =============================================================================
# UPDATE PROVIDER METADATA (for multi-provider refresh)
# =============================================================================


async def update_provider_metadata(
    session: AsyncSession,
    media_id: int,
    provider_name: str,
    data: dict[str, Any],
) -> ProviderMetadata | None:
    """
    Update or create provider metadata for a media item.

    This is the main entry point for storing metadata from external providers.
    It handles:
    - Creating/updating ProviderMetadata records
    - Managing canonical status based on priority
    - Storing raw provider data
    - Updating external IDs if provided

    Args:
        session: Database session
        media_id: Media.id
        provider_name: Provider name (imdb, tmdb, tvdb, mal, kitsu)
        data: Metadata dict from the provider scraper

    Returns:
        Created/updated ProviderMetadata or None on error
    """
    # Get or create the provider
    provider = await get_or_create_provider(
        session,
        provider_name,
        priority=PROVIDER_PRIORITY.get(provider_name.lower(), 100),
    )

    # Build the provider-specific content ID
    provider_content_id = ""
    if provider_name == "imdb":
        provider_content_id = data.get("imdb_id", "")
    elif provider_name == "tmdb":
        provider_content_id = data.get("tmdb_id", "")
    elif provider_name == "tvdb":
        provider_content_id = data.get("tvdb_id", "")
    elif provider_name == "mal":
        provider_content_id = data.get("mal_id", "")
    elif provider_name == "kitsu":
        provider_content_id = data.get("kitsu_id", "")

    # Extract structured data fields
    structured_data = {
        "title": data.get("title"),
        "description": data.get("description") or data.get("overview"),
        "runtime": data.get("runtime_minutes"),
        "release_date": data.get("release_date"),
    }
    # Filter out None values
    structured_data = {k: v for k, v in structured_data.items() if v is not None}

    # Store/update provider metadata
    try:
        pm = await store_provider_metadata_with_priority(
            session,
            media_id=media_id,
            provider_id=provider.id,
            provider_content_id=str(provider_content_id),
            data=structured_data,
            raw_data=data,
        )

        # Update external IDs from this provider if available
        external_ids = data.get("external_ids", {})
        if external_ids:
            for ext_provider, ext_id in external_ids.items():
                if ext_id:
                    # Check if this (media_id, provider) mapping already exists to update it
                    existing_query = select(MediaExternalID).where(
                        MediaExternalID.media_id == media_id,
                        MediaExternalID.provider == ext_provider,
                    )
                    result = await session.exec(existing_query)
                    existing = result.first()

                    if existing:
                        if existing.external_id != str(ext_id):
                            existing.external_id = str(ext_id)
                    else:
                        stmt = (
                            pg_insert(MediaExternalID)
                            .values(media_id=media_id, provider=ext_provider, external_id=str(ext_id))
                            .on_conflict_do_nothing(constraint="uq_provider_external_id")
                        )
                        await session.exec(stmt)

        # Also store the provider's own ID
        if provider_content_id:
            existing_query = select(MediaExternalID).where(
                MediaExternalID.media_id == media_id,
                MediaExternalID.provider == provider_name,
            )
            result = await session.exec(existing_query)
            existing = result.first()

            if not existing:
                stmt = (
                    pg_insert(MediaExternalID)
                    .values(media_id=media_id, provider=provider_name, external_id=str(provider_content_id))
                    .on_conflict_do_nothing(constraint="uq_provider_external_id")
                )
                await session.exec(stmt)

        await session.flush()
        return pm

    except Exception as e:
        logger.error(f"Error updating provider metadata for {provider_name}: {e}")
        return None


async def apply_multi_provider_metadata(
    session: AsyncSession,
    media_id: int,
    provider_data: dict[str, dict[str, Any]],
    media_type: str,
) -> bool:
    """
    Apply metadata from multiple providers to normalized tables using waterfall fallback.

    This function:
    1. Takes already-fetched provider data (no re-fetching)
    2. Uses waterfall fallback: first provider with data wins for each field
    3. Merges lists (cast, crew, genres) from all providers
    4. Updates Media, Person, MediaCast, MediaCrew, MediaImage, Genre links, etc.

    Args:
        session: Database session
        media_id: Media.id to update
        provider_data: Dict of {provider_name: data_dict} already fetched
        media_type: 'movie' or 'series'

    Returns:
        True if successful, False on error
    """

    # Provider priority order
    provider_priority = ["imdb", "tmdb", "tvdb", "mal", "kitsu"]

    def get_first_value(field: str, default=None):
        """Get first non-empty value from providers in priority order."""
        for provider in provider_priority:
            if provider in provider_data:
                value = provider_data[provider].get(field)
                if value is not None and value != "" and value != []:
                    return value
        return default

    def merge_lists(field: str, allow_duplicate_names: bool = False) -> list:
        """
        Merge lists from all providers, removing duplicates.

        For cast: deduplicate by name (same person shouldn't appear twice)
        For crew: allow same person with different jobs (Director + Writer)
        For episodes: deduplicate by season + episode number
        """
        seen = set()
        result = []
        for provider in provider_priority:
            if provider in provider_data:
                items = provider_data[provider].get(field, [])
                if isinstance(items, list):
                    for item in items:
                        # Handle dict items (cast/crew/episodes)
                        if isinstance(item, dict):
                            if field == "episodes":
                                # For episodes: use season + episode number as key
                                season = item.get("season_number") or item.get("season") or 0
                                episode = item.get("episode_number") or item.get("episode") or 0
                                key = f"s{season}e{episode}"
                            elif allow_duplicate_names:
                                # For crew: use name + job as key (same person can have multiple jobs)
                                key = item.get("name", "") + str(item.get("job", ""))
                            else:
                                # For cast: use name only (same person shouldn't appear twice)
                                key = item.get("name", "") + str(item.get("imdb_id", ""))
                            if key not in seen:
                                seen.add(key)
                                result.append(item)
                        else:
                            # String items (genres, etc.)
                            if item not in seen:
                                seen.add(item)
                                result.append(item)
        return result

    try:
        # Get the media record
        query = (
            select(Media)
            .where(Media.id == media_id)
            .options(
                selectinload(Media.movie_metadata),
                selectinload(Media.series_metadata),
            )
        )
        result = await session.exec(query)
        media = result.first()

        if not media:
            logger.error(f"Media {media_id} not found for multi-provider update")
            return False

        # Get or create primary provider
        primary_provider_name = next((p for p in provider_priority if p in provider_data), "imdb")
        provider = await get_or_create_provider(
            session,
            primary_provider_name,
            priority=PROVIDER_PRIORITY.get(primary_provider_name, 100),
        )

        # === Update basic media fields (waterfall) ===
        media.title = get_first_value("title") or media.title
        media.original_title = get_first_value("original_title") or media.original_title
        media.description = get_first_value("description") or get_first_value("overview") or media.description
        media.tagline = get_first_value("tagline") or media.tagline
        media.year = get_first_value("year") or media.year
        media.status = get_first_value("status") or media.status
        media.original_language = get_first_value("original_language") or media.original_language
        media.popularity = get_first_value("popularity") or media.popularity
        media.website = get_first_value("homepage") or media.website
        media.adult = get_first_value("adult", False)

        # Handle runtime
        runtime = get_first_value("runtime_minutes") or get_first_value("runtime")
        if runtime:
            if isinstance(runtime, int):
                media.runtime_minutes = runtime
            elif isinstance(runtime, str):
                import re

                match = re.search(r"(\d+)", runtime)
                if match:
                    media.runtime_minutes = int(match.group(1))

        # === Update images (waterfall with provider attribution) ===
        poster_url = get_first_value("poster")
        if poster_url:
            existing_poster = await session.exec(
                select(MediaImage).where(
                    MediaImage.media_id == media.id,
                    MediaImage.image_type == "poster",
                    MediaImage.is_primary.is_(True),
                )
            )
            existing = existing_poster.first()
            if existing:
                existing.url = poster_url
            else:
                session.add(
                    MediaImage(
                        media_id=media.id,
                        provider_id=provider.id,
                        image_type="poster",
                        url=poster_url,
                        is_primary=True,
                    )
                )

        background_url = get_first_value("background")
        if background_url:
            existing_bg = await session.exec(
                select(MediaImage).where(
                    MediaImage.media_id == media.id,
                    MediaImage.image_type == "background",
                    MediaImage.is_primary.is_(True),
                )
            )
            existing = existing_bg.first()
            if existing:
                existing.url = background_url
            else:
                session.add(
                    MediaImage(
                        media_id=media.id,
                        provider_id=provider.id,
                        image_type="background",
                        url=background_url,
                        is_primary=True,
                    )
                )

        # === Update type-specific metadata ===
        if media_type == "series":
            end_year = get_first_value("end_year")
            if end_year:
                media.end_date = date(end_year, 12, 31)

            if media.series_metadata:
                media.series_metadata.total_seasons = (
                    get_first_value("number_of_seasons") or media.series_metadata.total_seasons
                )
                media.series_metadata.total_episodes = (
                    get_first_value("number_of_episodes") or media.series_metadata.total_episodes
                )
                networks = get_first_value("network", [])
                if networks:
                    media.series_metadata.network = networks[0] if isinstance(networks, list) else networks

        if media_type == "movie" and media.movie_metadata:
            media.movie_metadata.budget = get_first_value("budget") or media.movie_metadata.budget
            media.movie_metadata.revenue = get_first_value("revenue") or media.movie_metadata.revenue

        # === Update genres (merged from all providers) ===
        genres = merge_lists("genres")
        if genres:
            await session.exec(sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == media.id))
            for genre_name in genres:
                genre = await get_or_create_genre(session, genre_name)
                link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
                session.add(link)

        # === Update AKA titles (merged) ===
        aka_titles = merge_lists("aka_titles")
        if aka_titles:
            await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == media.id))
            for title in set(aka_titles):
                session.add(AkaTitle(media_id=media.id, title=title))

        # === Update keywords (merged) ===
        keywords = merge_lists("keywords")
        if keywords:
            await session.exec(sa_delete(MediaKeywordLink).where(MediaKeywordLink.media_id == media.id))
            for keyword_name in keywords[:50]:
                keyword_result = await session.exec(select(Keyword).where(Keyword.name == keyword_name))
                keyword = keyword_result.first()
                if not keyword:
                    keyword = Keyword(name=keyword_name)
                    session.add(keyword)
                    await session.flush()
                link = MediaKeywordLink(media_id=media.id, keyword_id=keyword.id)
                session.add(link)

        # === Update cast (merged from all providers, deduplicated) ===
        cast_list = merge_lists("cast")
        if cast_list:
            await session.exec(sa_delete(MediaCast).where(MediaCast.media_id == media.id))

            for idx, cast_data in enumerate(cast_list[:30]):
                person_name = cast_data.get("name")
                if not person_name:
                    continue

                imdb_id = cast_data.get("imdb_id")
                tmdb_id = cast_data.get("tmdb_id")
                if tmdb_id is not None:
                    tmdb_id = int(tmdb_id) if not isinstance(tmdb_id, int) else tmdb_id

                # Find or create person
                person = None
                if imdb_id:
                    person_result = await session.exec(select(Person).where(Person.imdb_id == imdb_id))
                    person = person_result.first()
                elif tmdb_id:
                    person_result = await session.exec(select(Person).where(Person.tmdb_id == tmdb_id))
                    person = person_result.first()

                if not person:
                    person_result = await session.exec(select(Person).where(Person.name == person_name))
                    person = person_result.first()

                if not person:
                    person = Person(
                        name=person_name,
                        imdb_id=imdb_id,
                        tmdb_id=tmdb_id,
                        profile_url=cast_data.get("profile_path"),
                    )
                    session.add(person)
                    await session.flush()
                else:
                    if imdb_id and not person.imdb_id:
                        person.imdb_id = imdb_id
                    if tmdb_id and not person.tmdb_id:
                        person.tmdb_id = tmdb_id
                    if cast_data.get("profile_path") and not person.profile_url:
                        person.profile_url = cast_data["profile_path"]

                # Handle character name - could be string or list
                character = cast_data.get("character") or cast_data.get("characters")
                if isinstance(character, list):
                    character = ", ".join(character) if character else None

                media_cast = MediaCast(
                    media_id=media.id,
                    person_id=person.id,
                    character=character,
                    display_order=cast_data.get("order", idx),
                )
                session.add(media_cast)

        # === Update crew (merged from all providers, deduplicated) ===
        crew_list = merge_lists("crew", allow_duplicate_names=True)
        if crew_list:
            await session.exec(sa_delete(MediaCrew).where(MediaCrew.media_id == media.id))

            for crew_data in crew_list[:20]:
                person_name = crew_data.get("name")
                if not person_name:
                    continue

                imdb_id = crew_data.get("imdb_id")
                tmdb_id = crew_data.get("tmdb_id")
                if tmdb_id is not None:
                    tmdb_id = int(tmdb_id) if not isinstance(tmdb_id, int) else tmdb_id

                person = None
                if imdb_id:
                    person_result = await session.exec(select(Person).where(Person.imdb_id == imdb_id))
                    person = person_result.first()
                elif tmdb_id:
                    person_result = await session.exec(select(Person).where(Person.tmdb_id == tmdb_id))
                    person = person_result.first()

                if not person:
                    person_result = await session.exec(select(Person).where(Person.name == person_name))
                    person = person_result.first()

                if not person:
                    person = Person(
                        name=person_name,
                        imdb_id=imdb_id,
                        tmdb_id=tmdb_id,
                        profile_url=crew_data.get("profile_path"),
                    )
                    session.add(person)
                    await session.flush()

                media_crew = MediaCrew(
                    media_id=media.id,
                    person_id=person.id,
                    job=crew_data.get("job"),
                    department=crew_data.get("department"),
                )
                session.add(media_crew)

        # === Update trailers/videos (from first provider that has them) ===
        videos = get_first_value("videos", [])
        if videos:
            await session.exec(sa_delete(MediaTrailer).where(MediaTrailer.media_id == media.id))

            is_first = True
            for video in videos[:10]:
                video_key = video.get("key")
                if not video_key:
                    continue

                trailer = MediaTrailer(
                    media_id=media.id,
                    video_key=video_key,
                    site=video.get("site", "YouTube"),
                    name=video.get("name"),
                    trailer_type=video.get("type", "trailer").lower(),
                    is_official=video.get("official", True),
                    is_primary=is_first,
                    size=video.get("size"),
                )
                session.add(trailer)
                is_first = False

        # === Update parental certificates ===
        certificates = get_first_value("parent_guide_certificates", [])
        if certificates:
            await session.exec(
                sa_delete(MediaParentalCertificateLink).where(MediaParentalCertificateLink.media_id == media.id)
            )
            for cert_name in certificates[:10]:
                cert_result = await session.exec(
                    select(ParentalCertificate).where(ParentalCertificate.name == cert_name)
                )
                cert = cert_result.first()
                if not cert:
                    cert = ParentalCertificate(name=cert_name)
                    session.add(cert)
                    await session.flush()
                link = MediaParentalCertificateLink(media_id=media.id, certificate_id=cert.id)
                session.add(link)

        # === Update ratings from provider data ===
        for prov_name in provider_priority:
            if prov_name not in provider_data:
                continue
            data = provider_data[prov_name]

            # Get rating value and vote count
            rating_key = f"{prov_name}_rating"
            votes_key = f"{prov_name}_vote_count"

            rating_value = (
                data.get(rating_key) or data.get("imdb_rating") if prov_name == "imdb" else data.get(rating_key)
            )
            vote_count = (
                data.get(votes_key) or data.get("imdb_vote_count") if prov_name == "imdb" else data.get(votes_key)
            )

            if rating_value:
                # Get or create rating provider (defined in this module)
                rating_provider = await get_or_create_rating_provider(session, prov_name)

                # Update or create rating
                existing_rating = await session.exec(
                    select(MediaRating).where(
                        MediaRating.media_id == media.id,
                        MediaRating.rating_provider_id == rating_provider.id,
                    )
                )
                rating_record = existing_rating.first()

                if rating_record:
                    rating_record.rating = float(rating_value)
                    if vote_count:
                        rating_record.vote_count = int(vote_count)
                else:
                    new_rating = MediaRating(
                        media_id=media.id,
                        rating_provider_id=rating_provider.id,
                        rating=float(rating_value),
                        vote_count=int(vote_count) if vote_count else None,
                    )
                    session.add(new_rating)

        # === Update series episodes (for series only) ===
        if media_type == "series":
            episodes_data = merge_lists("episodes")
            if episodes_data and media.series_metadata:
                series_id = media.series_metadata.id
                logger.info(f"Processing {len(episodes_data)} episodes for media_id={media_id}")

                # Group episodes by season
                seasons_episodes: dict[int, list] = {}
                for ep in episodes_data:
                    season_num = ep.get("season_number")
                    if season_num is None:
                        season_num = ep.get("season")
                    if season_num is None:
                        continue
                    if season_num not in seasons_episodes:
                        seasons_episodes[season_num] = []
                    seasons_episodes[season_num].append(ep)

                # Create/update seasons
                for season_num in seasons_episodes.keys():
                    existing_season = await session.exec(
                        select(Season).where(
                            Season.series_id == series_id,
                            Season.season_number == season_num,
                        )
                    )
                    season = existing_season.first()
                    if not season:
                        season = Season(
                            series_id=series_id,
                            season_number=season_num,
                            episode_count=len(seasons_episodes[season_num]),
                        )
                        session.add(season)
                        await session.flush()

                # Get all seasons for this series
                seasons_result = await session.exec(select(Season).where(Season.series_id == series_id))
                seasons_map = {s.season_number: s.id for s in seasons_result.all()}

                # Create/update episodes with images
                for season_num, eps in seasons_episodes.items():
                    season_id = seasons_map.get(season_num)
                    if not season_id:
                        continue

                    for ep in eps:
                        episode_num = ep.get("episode_number")
                        if episode_num is None:
                            episode_num = ep.get("episode")
                        if episode_num is None:
                            continue

                        # Check if episode exists
                        existing_ep = await session.exec(
                            select(Episode).where(
                                Episode.season_id == season_id,
                                Episode.episode_number == episode_num,
                            )
                        )
                        episode = existing_ep.first()

                        # Parse air date
                        air_date = None
                        released = ep.get("released") or ep.get("release_date") or ep.get("air_date")
                        if released:
                            if isinstance(released, str):
                                try:
                                    air_date = date(
                                        int(released[:4]),
                                        int(released[5:7]),
                                        int(released[8:10]),
                                    )
                                except (ValueError, TypeError, IndexError):
                                    pass
                            elif isinstance(released, date):
                                air_date = released

                        if episode:
                            # Update existing episode
                            episode.title = ep.get("title") or episode.title
                            episode.overview = ep.get("overview") or ep.get("description") or episode.overview
                            if air_date:
                                episode.air_date = air_date
                            episode.runtime_minutes = (
                                ep.get("runtime_minutes") or ep.get("runtime") or episode.runtime_minutes
                            )
                            # Update external IDs
                            if ep.get("imdb_id"):
                                episode.imdb_id = ep.get("imdb_id")
                            if ep.get("tmdb_id"):
                                episode.tmdb_id = ep.get("tmdb_id")
                            if ep.get("tvdb_id"):
                                episode.tvdb_id = ep.get("tvdb_id")
                        else:
                            # Create new episode
                            episode = Episode(
                                season_id=season_id,
                                episode_number=episode_num,
                                title=ep.get("title") or f"Episode {episode_num}",
                                overview=ep.get("overview") or ep.get("description"),
                                air_date=air_date,
                                runtime_minutes=ep.get("runtime_minutes") or ep.get("runtime"),
                                imdb_id=ep.get("imdb_id"),
                                tmdb_id=ep.get("tmdb_id"),
                                tvdb_id=ep.get("tvdb_id"),
                                source_provider_id=provider.id,
                            )
                            session.add(episode)
                            await session.flush()

                        # Handle episode thumbnail
                        thumbnail_url = ep.get("thumbnail") or ep.get("still_path")
                        if thumbnail_url and episode.id:
                            # Find all existing images for this episode/provider/type
                            existing_imgs = await session.exec(
                                select(EpisodeImage).where(
                                    EpisodeImage.episode_id == episode.id,
                                    EpisodeImage.provider_id == provider.id,
                                    EpisodeImage.image_type == "still",
                                )
                            )
                            all_imgs = existing_imgs.all()
                            primary_img = next((i for i in all_imgs if i.is_primary), None)
                            url_match_img = next((i for i in all_imgs if i.url == thumbnail_url), None)

                            if primary_img and primary_img.url == thumbnail_url:
                                pass  # already correct, nothing to do
                            elif url_match_img:
                                # Another row already has the target URL — make it primary,
                                # delete any other primary to avoid duplicate.
                                if primary_img and primary_img.id != url_match_img.id:
                                    await session.delete(primary_img)
                                url_match_img.is_primary = True
                            elif primary_img:
                                primary_img.url = thumbnail_url
                            else:
                                session.add(
                                    EpisodeImage(
                                        episode_id=episode.id,
                                        provider_id=provider.id,
                                        image_type="still",
                                        url=thumbnail_url,
                                        is_primary=True,
                                    )
                                )

                logger.info(f"Updated {len(episodes_data)} episodes for media_id={media_id}")

        await session.flush()
        logger.info(
            f"Applied multi-provider metadata for media_id={media_id} from providers: {list(provider_data.keys())}"
        )
        return True

    except Exception as e:
        logger.error(f"Error applying multi-provider metadata for media_id={media_id}: {e}")
        traceback.print_exc()
        try:
            await session.rollback()
        except Exception:
            pass
        return False
