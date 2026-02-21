"""
Scraper helper CRUD operations.

These functions support the scrapers and legacy code that expects
certain CRUD patterns from the old sql_crud.py.
"""

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import pytz
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.crud.media import (
    add_external_id,
    get_media_by_external_id,
    get_or_create_episode,
    get_or_create_metadata_provider,
    get_or_create_season,
    invalidate_meta_cache,
    parse_external_id,
)
from db.crud.providers import get_or_create_provider
from db.crud.reference import (
    get_or_create_catalog,
    get_or_create_genre,
)
from db.crud.stream_services import invalidate_media_stream_cache
from db.enums import MediaType, TorrentType
from db.models import (
    AkaTitle,
    AudioChannel,
    AudioFormat,
    Catalog,
    Episode,
    EpisodeImage,
    FileMediaLink,
    FileType,
    HDRFormat,
    HTTPStream,
    Keyword,
    Language,
    LinkSource,
    Media,
    UsenetStream,
    MediaCast,
    MediaCatalogLink,
    MediaCrew,
    MediaFusionRating,
    MediaGenreLink,
    MediaImage,
    MediaKeywordLink,
    MediaParentalCertificateLink,
    MediaRating,
    MediaTrailer,
    MovieMetadata,
    Person,
    PlaybackTracking,
    ProviderMetadata,
    RSSFeed,
    RSSFeedCatalogPattern,
    Season,
    SeriesMetadata,
    Stream,
    StreamAudioLink,
    StreamChannelLink,
    StreamFile,
    StreamHDRLink,
    StreamLanguageLink,
    StreamMediaLink,
    StreamType,
    TorrentStream,
    TVMetadata,
    WatchHistory,
)
from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)


# =============================================================================
# SCRAPER SCHEDULER HELPERS
# =============================================================================


async def fetch_last_run(spider_id: str, spider_name: str) -> dict:
    """Fetch last run info for a scrapy spider from Redis.

    Args:
        spider_id: The spider identifier (e.g., "formula_ext")
        spider_name: Human-readable spider name (e.g., "Formula EXT")

    Returns:
        Dict with spider info and last run details
    """
    import humanize

    task_key = f"background_tasks:run_spider:{spider_id}"
    last_run_timestamp = await REDIS_ASYNC_CLIENT.get(task_key)

    last_run = None
    time_since_last_run = None
    time_since_last_run_seconds = 0

    if last_run_timestamp:
        try:
            last_run_ts = float(last_run_timestamp)
            last_run_dt = datetime.fromtimestamp(last_run_ts, tz=pytz.UTC)
            last_run = last_run_dt.isoformat()
            delta = datetime.now(tz=pytz.UTC) - last_run_dt
            time_since_last_run = humanize.precisedelta(delta, minimum_unit="minutes")
            time_since_last_run_seconds = delta.total_seconds()
        except (ValueError, TypeError):
            pass

    return {
        "spider_id": spider_id,
        "name": spider_name,
        "last_run": last_run,
        "time_since_last_run": time_since_last_run,
        "time_since_last_run_seconds": time_since_last_run_seconds,
    }


# =============================================================================
# METADATA HELPERS
# =============================================================================


async def get_metadata_by_id(
    session: AsyncSession,
    meta_id: str,
    *,
    load_relations: bool = False,
) -> Media | None:
    """Get metadata by external ID (e.g., IMDb ID)."""
    return await get_media_by_external_id(session, meta_id)


def _metadata_base_options():
    """Shared selectinload options for all Media queries that feed into MetadataData.from_db().

    Every relationship that from_db() accesses must be listed here.
    Callers may extend this with type-specific options (e.g. series_metadata.seasons).
    """
    return [
        selectinload(Media.genres),
        selectinload(Media.catalogs),
        selectinload(Media.aka_titles),
        selectinload(Media.keywords),
        selectinload(Media.images).selectinload(MediaImage.provider),
        selectinload(Media.ratings).selectinload(MediaRating.provider),
        selectinload(Media.mediafusion_rating),
        selectinload(Media.external_ids),
        selectinload(Media.movie_metadata),
        selectinload(Media.series_metadata),
        selectinload(Media.tv_metadata),
    ]


async def get_movie_data_by_id(
    session: AsyncSession,
    meta_id: str,
    *,
    load_relations: bool = True,
) -> Media | None:
    """Get movie metadata by external ID using MediaExternalID lookup."""
    media = await get_media_by_external_id(session, meta_id, MediaType.MOVIE)
    if not media:
        return None

    if not load_relations:
        return media

    query = (
        select(Media)
        .where(Media.id == media.id)
        .options(
            *_metadata_base_options(),
            selectinload(Media.cast).selectinload(MediaCast.person),
            selectinload(Media.crew).selectinload(MediaCrew.person),
            selectinload(Media.parental_certificates),
            selectinload(Media.trailers),
        )
    )

    result = await session.exec(query)
    return result.first()


async def get_series_data_by_id(
    session: AsyncSession,
    meta_id: str,
    *,
    load_relations: bool = True,
) -> Media | None:
    """Get series metadata by external ID using MediaExternalID lookup."""
    media = await get_media_by_external_id(session, meta_id, MediaType.SERIES)
    if not media:
        return None

    if not load_relations:
        return media

    query = (
        select(Media)
        .where(Media.id == media.id)
        .options(
            *_metadata_base_options(),
            selectinload(Media.series_metadata).selectinload(SeriesMetadata.seasons).selectinload(Season.episodes),
            selectinload(Media.cast).selectinload(MediaCast.person),
            selectinload(Media.crew).selectinload(MediaCrew.person),
            selectinload(Media.parental_certificates),
            selectinload(Media.trailers),
        )
    )

    result = await session.exec(query)
    return result.first()


async def get_tv_data_by_id(
    session: AsyncSession,
    meta_id: str,
    *,
    load_relations: bool = True,
) -> Media | None:
    """Get TV channel metadata by external ID using MediaExternalID lookup."""
    media = await get_media_by_external_id(session, meta_id, MediaType.TV)
    if not media:
        return None

    if not load_relations:
        return media

    query = (
        select(Media)
        .where(Media.id == media.id)
        .options(
            *_metadata_base_options(),
        )
    )

    result = await session.exec(query)
    return result.first()


async def get_or_create_metadata(
    session: AsyncSession,
    metadata_data: dict[str, Any],
    media_type: str,
    *,
    is_search_imdb_title: bool = True,
    is_imdb_only: bool = False,
) -> Media | None:
    """
    Get or create metadata from scraped data.

    Args:
        metadata_data: Dictionary containing metadata fields
        media_type: 'movie', 'series', or 'tv'
        is_search_imdb_title: Whether to search by IMDB title
        is_imdb_only: Whether to only use IMDB data

    Returns:
        Created or existing Media object
    """
    external_id = metadata_data.get("id") or metadata_data.get("imdb_id")
    if not external_id:
        return None

    # Map string type to enum
    type_map = {
        "movie": MediaType.MOVIE,
        "series": MediaType.SERIES,
        "tv": MediaType.TV,
    }
    media_type_enum = type_map.get(media_type, MediaType.MOVIE)

    # Check if exists
    existing = await get_media_by_external_id(session, external_id, media_type_enum)
    if existing:
        return existing

    # Determine end_date from end_year if not provided directly
    end_date = metadata_data.get("end_date")
    if not end_date and (end_year := metadata_data.get("end_year")):
        end_date = date(end_year, 12, 31)

    # Create new media (external_id is now stored in MediaExternalID table)
    media = Media(
        type=media_type_enum,
        title=metadata_data.get("title", "Unknown"),
        year=metadata_data.get("year"),
        description=metadata_data.get("description") or metadata_data.get("overview"),
        runtime_minutes=metadata_data.get("runtime"),
        end_date=end_date,
        is_add_title_to_poster=metadata_data.get("is_add_title_to_poster", False),
    )
    session.add(media)
    await session.flush()

    # Store poster/background as MediaImage records (images are not stored
    # on the Media table itself — they live in the media_image table).
    poster_url = metadata_data.get("poster")
    background_url = metadata_data.get("background") or metadata_data.get("backdrop")
    if poster_url or background_url:
        mf_provider = await get_or_create_metadata_provider(session, "mediafusion", "MediaFusion")
        if poster_url:
            session.add(
                MediaImage(
                    media_id=media.id,
                    provider_id=mf_provider.id,
                    image_type="poster",
                    url=poster_url,
                    is_primary=True,
                )
            )
        if background_url:
            session.add(
                MediaImage(
                    media_id=media.id,
                    provider_id=mf_provider.id,
                    image_type="background",
                    url=background_url,
                    is_primary=True,
                )
            )

    # Add genres
    if genres := metadata_data.get("genres"):
        for genre_name in genres:
            genre = await get_or_create_genre(session, genre_name)
            link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
            session.add(link)

    # Add catalogs
    if catalogs := metadata_data.get("catalogs"):
        if isinstance(catalogs, str):
            catalogs = [catalogs]
        await session.flush()
        catalog_links = []
        for catalog_name in catalogs:
            catalog = await get_or_create_catalog(session, catalog_name)
            catalog_links.append({"media_id": media.id, "catalog_id": catalog.id})
        if catalog_links:
            stmt = pg_insert(MediaCatalogLink).values(catalog_links).on_conflict_do_nothing()
            await session.exec(stmt)

    # Create type-specific metadata
    if media_type_enum == MediaType.MOVIE:
        movie_meta = MovieMetadata(media_id=media.id)
        session.add(movie_meta)
    elif media_type_enum == MediaType.SERIES:
        series_meta = SeriesMetadata(media_id=media.id)
        session.add(series_meta)
    elif media_type_enum == MediaType.TV:
        tv_meta = TVMetadata(
            media_id=media.id,
            country=metadata_data.get("country"),
            tv_language=metadata_data.get("tv_language"),
        )
        session.add(tv_meta)

    # Create MediaExternalID records for the new architecture
    await _create_external_id_from_metadata(session, media.id, external_id, metadata_data)

    await session.flush()
    return media


async def _create_external_id_from_metadata(
    session: AsyncSession,
    media_id: int,
    external_id: str,
    metadata_data: dict[str, Any],
) -> None:
    """Create MediaExternalID records from scraped metadata.

    This extracts all available provider IDs from the metadata and creates
    corresponding MediaExternalID records for the new multi-provider architecture.
    """
    # Parse and add the primary external_id
    provider, provider_id = parse_external_id(external_id)
    if provider and provider_id:
        await add_external_id(session, media_id, provider, provider_id)

    # Add additional provider IDs if available in metadata
    # IMDb ID
    imdb_id = metadata_data.get("imdb_id")
    if imdb_id and imdb_id != external_id:
        await add_external_id(session, media_id, "imdb", imdb_id)

    # TMDB ID
    tmdb_id = metadata_data.get("tmdb_id")
    if tmdb_id:
        await add_external_id(session, media_id, "tmdb", str(tmdb_id))

    # TVDB ID
    tvdb_id = metadata_data.get("tvdb_id")
    if tvdb_id:
        await add_external_id(session, media_id, "tvdb", str(tvdb_id))

    # MAL ID (for anime)
    mal_id = metadata_data.get("mal_id")
    if mal_id:
        await add_external_id(session, media_id, "mal", str(mal_id))

    # Kitsu ID (for anime)
    kitsu_id = metadata_data.get("kitsu_id")
    if kitsu_id:
        await add_external_id(session, media_id, "kitsu", str(kitsu_id))


async def update_metadata(
    session: AsyncSession,
    meta_id: str,
    update_fields: dict[str, Any],
) -> bool:
    """Update metadata fields."""
    media = await get_media_by_external_id(session, meta_id)
    if not media:
        return False

    update_fields["updated_at"] = datetime.now(pytz.UTC)

    await session.exec(sa_update(Media).where(Media.id == media.id).values(**update_fields))
    await session.flush()

    # Invalidate the cached Stremio meta response so the next request sees fresh data
    await invalidate_meta_cache(meta_id)

    return True


async def delete_metadata(
    session: AsyncSession,
    meta_id: str,
    new_media_id: int | None = None,
) -> bool:
    """Delete metadata by external ID.

    This properly handles cascade deletion of all related records
    including images, ratings, cast, crew, genres, etc.

    If new_media_id is provided, user-related records (library, history, etc.)
    will be migrated to the new media instead of being deleted.
    """

    media = await get_media_by_external_id(session, meta_id)
    if not media:
        return False

    old_media_id = media.id

    # If migrating, update user-related records to point to new media
    if new_media_id is not None:
        # Migrate user watch history (includes downloads since they're now unified)
        await session.exec(
            sa_update(WatchHistory).where(WatchHistory.media_id == old_media_id).values(media_id=new_media_id)
        )
        # Migrate playback tracking
        await session.exec(
            sa_update(PlaybackTracking).where(PlaybackTracking.media_id == old_media_id).values(media_id=new_media_id)
        )
        logger.info(f"Migrated user records from media_id={old_media_id} to media_id={new_media_id}")

    # Delete all related records that have foreign keys to media
    # Order matters - delete child records before parent

    # Delete episodes first (they reference seasons)
    if media.type == MediaType.SERIES:
        # Get all season IDs for this media's series metadata
        series_metadata_query = select(SeriesMetadata.id).where(SeriesMetadata.media_id == old_media_id)
        series_result = await session.exec(series_metadata_query)
        series_id = series_result.first()

        if series_id:
            season_query = select(Season.id).where(Season.series_id == series_id)
            season_result = await session.exec(season_query)
            season_ids = list(season_result.all())

            if season_ids:
                await session.exec(sa_delete(Episode).where(Episode.season_id.in_(season_ids)))
                await session.exec(sa_delete(Season).where(Season.id.in_(season_ids)))

    # Delete media-related tables (metadata, not user data)
    await session.exec(sa_delete(MediaImage).where(MediaImage.media_id == old_media_id))
    await session.exec(sa_delete(MediaRating).where(MediaRating.media_id == old_media_id))
    await session.exec(sa_delete(MediaFusionRating).where(MediaFusionRating.media_id == old_media_id))
    await session.exec(sa_delete(MediaCast).where(MediaCast.media_id == old_media_id))
    await session.exec(sa_delete(MediaCrew).where(MediaCrew.media_id == old_media_id))
    await session.exec(sa_delete(MediaTrailer).where(MediaTrailer.media_id == old_media_id))
    await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == old_media_id))
    await session.exec(sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == old_media_id))
    await session.exec(sa_delete(MediaCatalogLink).where(MediaCatalogLink.media_id == old_media_id))
    await session.exec(sa_delete(MediaKeywordLink).where(MediaKeywordLink.media_id == old_media_id))
    await session.exec(
        sa_delete(MediaParentalCertificateLink).where(MediaParentalCertificateLink.media_id == old_media_id)
    )
    await session.exec(sa_delete(ProviderMetadata).where(ProviderMetadata.media_id == old_media_id))
    await session.exec(sa_delete(StreamMediaLink).where(StreamMediaLink.media_id == old_media_id))

    # Delete type-specific metadata
    await session.exec(sa_delete(MovieMetadata).where(MovieMetadata.media_id == old_media_id))
    await session.exec(sa_delete(SeriesMetadata).where(SeriesMetadata.media_id == old_media_id))
    await session.exec(sa_delete(TVMetadata).where(TVMetadata.media_id == old_media_id))

    # Finally delete the media record
    await session.exec(sa_delete(Media).where(Media.id == old_media_id))
    await session.flush()

    logger.info(f"Deleted metadata {meta_id} (media_id={old_media_id}) and all related records")
    return True


async def update_meta_stream(
    session: AsyncSession,
    meta_id: str,
    media_type: str,
    recalculate_stream_time: bool = True,
) -> None:
    """Update stream counts for metadata.

    Args:
        meta_id: External ID (e.g., tt1234567)
        media_type: 'movie' or 'series'
        recalculate_stream_time: If True, recalculate last_stream_added from actual stream data.
                                 Useful for fixing incorrect values.
    """
    media = await get_media_by_external_id(session, meta_id)
    if not media:
        return

    # Count linked streams
    count_query = select(func.count(StreamMediaLink.id)).where(StreamMediaLink.media_id == media.id)
    result = await session.exec(count_query)
    count = result.first() or 0

    # Update total_streams
    await session.exec(sa_update(Media).where(Media.id == media.id).values(total_streams=count))
    await session.flush()

    # Optionally recalculate last_stream_added from actual stream data
    if recalculate_stream_time:
        await recalculate_last_stream_added(session, media.id)


async def recalculate_last_stream_added(
    session: AsyncSession,
    media_id: int,
) -> datetime | None:
    """
    Recalculate last_stream_added from actual stream data.

    Returns the calculated last_stream_added time, or None if no streams exist.
    Only updates the database if streams exist (to preserve NOT NULL constraint).
    """
    # Get the latest stream creation time for this media
    latest_query = select(func.max(StreamMediaLink.created_at)).where(StreamMediaLink.media_id == media_id)
    result = await session.exec(latest_query)
    latest_stream_time = result.first()

    # Only update if we have a valid stream time (preserve NOT NULL constraint)
    if latest_stream_time is not None:
        await session.exec(sa_update(Media).where(Media.id == media_id).values(last_stream_added=latest_stream_time))
        await session.flush()

    return latest_stream_time


async def update_single_imdb_metadata(
    session: AsyncSession,
    meta_id: str,
    media_type: str,
    user_id: int | None = None,
) -> bool:
    """
    Fetch fresh metadata from IMDB/TMDB and update the database record.

    Args:
        session: Database session
        meta_id: External ID (e.g., tt1234567)
        media_type: 'movie' or 'series'
        user_id: Optional user ID who initiated the refresh

    This updates:
    - Basic media fields (title, description, tagline, etc.)
    - Cast and crew
    - Trailers/videos
    - Genres, keywords
    - Ratings
    """
    # Lazy import to avoid circular dependency
    from scrapers.scraper_tasks import meta_fetcher

    # Get existing media via MediaExternalID lookup
    media = await get_media_by_external_id(session, meta_id)
    if not media:
        logger.warning(f"Media {meta_id} not found for refresh")
        return False

    # Reload with type-specific metadata
    query = (
        select(Media)
        .where(Media.id == media.id)
        .options(
            selectinload(Media.movie_metadata),
            selectinload(Media.series_metadata),
        )
    )
    result = await session.exec(query)
    media = result.first()

    # Fetch fresh data
    try:
        fetched_data = await meta_fetcher.get_metadata(meta_id, media_type=media_type)
        if not fetched_data:
            logger.warning(f"Could not fetch fresh metadata for {meta_id}")
            return False
    except Exception as e:
        logger.error(f"Error fetching metadata for {meta_id}: {e}")
        return False

    # Update basic media fields
    media.title = fetched_data.get("title") or media.title
    media.original_title = fetched_data.get("original_title") or media.original_title
    media.description = fetched_data.get("description") or fetched_data.get("overview") or media.description
    media.tagline = fetched_data.get("tagline") or media.tagline
    media.year = fetched_data.get("year") or media.year
    # Handle runtime - prefer runtime_minutes (int) over runtime (string)
    runtime = fetched_data.get("runtime_minutes") or fetched_data.get("runtime")
    if runtime:
        if isinstance(runtime, int):
            media.runtime_minutes = runtime
        elif isinstance(runtime, str):
            # Parse "120 min" format
            match = re.search(r"(\d+)", runtime)
            if match:
                media.runtime_minutes = int(match.group(1))
    media.status = fetched_data.get("status") or media.status
    media.original_language = fetched_data.get("original_language") or media.original_language
    media.popularity = fetched_data.get("popularity") or media.popularity
    media.website = fetched_data.get("homepage") or media.website
    media.adult = fetched_data.get("adult", False)

    # Update images via MediaImage table

    # Determine provider based on meta_id
    if meta_id.startswith("tt"):
        provider = await get_or_create_metadata_provider(session, "imdb", "IMDb")
    else:
        provider = await get_or_create_metadata_provider(session, "tmdb", "TMDB")

    # Update/create poster image
    if poster_url := fetched_data.get("poster"):
        # Check if poster already exists
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

    # Update/create background image
    if background_url := fetched_data.get("background"):
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

    # Update end_date for series
    if media_type == "series":
        end_year = fetched_data.get("end_year")
        if end_year:
            media.end_date = date(end_year, 12, 31)

        # Update series metadata
        if media.series_metadata:
            media.series_metadata.total_seasons = (
                fetched_data.get("number_of_seasons") or media.series_metadata.total_seasons
            )
            media.series_metadata.total_episodes = (
                fetched_data.get("number_of_episodes") or media.series_metadata.total_episodes
            )
            networks = fetched_data.get("network", [])
            if networks:
                media.series_metadata.network = networks[0] if isinstance(networks, list) else networks

    # Update movie metadata
    if media_type == "movie" and media.movie_metadata:
        media.movie_metadata.budget = fetched_data.get("budget") or media.movie_metadata.budget
        media.movie_metadata.revenue = fetched_data.get("revenue") or media.movie_metadata.revenue

    # Update genres
    if genres := fetched_data.get("genres"):
        # Clear existing genre links
        await session.exec(sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == media.id))
        for genre_name in genres:
            genre = await get_or_create_genre(session, genre_name)
            link = MediaGenreLink(media_id=media.id, genre_id=genre.id)
            session.add(link)

    # Update AKA titles
    if aka_titles := fetched_data.get("aka_titles"):
        # Clear and re-add
        await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == media.id))
        for title in set(aka_titles):
            session.add(AkaTitle(media_id=media.id, title=title))

    # Update keywords
    if keywords := fetched_data.get("keywords"):
        await session.exec(sa_delete(MediaKeywordLink).where(MediaKeywordLink.media_id == media.id))
        for keyword_name in keywords[:50]:  # Limit to 50 keywords
            # Get or create keyword
            keyword_result = await session.exec(select(Keyword).where(Keyword.name == keyword_name))
            keyword = keyword_result.first()
            if not keyword:
                keyword = Keyword(name=keyword_name)
                session.add(keyword)
                await session.flush()
            link = MediaKeywordLink(media_id=media.id, keyword_id=keyword.id)
            session.add(link)

    # Update cast
    if cast_list := fetched_data.get("cast"):
        # Clear existing cast
        await session.exec(sa_delete(MediaCast).where(MediaCast.media_id == media.id))

        for idx, cast_data in enumerate(cast_list[:30]):  # Limit to 30 cast members
            person_name = cast_data.get("name")
            if not person_name:
                continue

            # Get or create person
            imdb_id = cast_data.get("imdb_id")
            tmdb_id = cast_data.get("tmdb_id")
            # Ensure tmdb_id is an integer if present
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
                    profile_url=cast_data.get("profile_path"),
                )
                session.add(person)
                await session.flush()
            else:
                # Update person info if we have better data
                if imdb_id and not person.imdb_id:
                    person.imdb_id = imdb_id
                if tmdb_id and not person.tmdb_id:
                    person.tmdb_id = tmdb_id
                if cast_data.get("profile_path") and not person.profile_url:
                    person.profile_url = cast_data["profile_path"]

            # Create cast link
            media_cast = MediaCast(
                media_id=media.id,
                person_id=person.id,
                character_name=cast_data.get("character"),
                display_order=cast_data.get("order", idx),
            )
            session.add(media_cast)

    # Update crew
    if crew_list := fetched_data.get("crew"):
        # Clear existing crew
        await session.exec(sa_delete(MediaCrew).where(MediaCrew.media_id == media.id))

        for crew_data in crew_list[:20]:  # Limit to 20 crew members
            person_name = crew_data.get("name")
            if not person_name:
                continue

            # Get or create person
            imdb_id = crew_data.get("imdb_id")
            tmdb_id = crew_data.get("tmdb_id")
            # Ensure tmdb_id is an integer if present
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

            # Create crew link
            media_crew = MediaCrew(
                media_id=media.id,
                person_id=person.id,
                job=crew_data.get("job"),
                department=crew_data.get("department"),
            )
            session.add(media_crew)

    # Update trailers/videos
    if videos := fetched_data.get("videos"):
        # Clear existing trailers
        await session.exec(sa_delete(MediaTrailer).where(MediaTrailer.media_id == media.id))

        is_first = True
        for video in videos[:10]:  # Limit to 10 videos
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

    # Update series episodes
    if media_type == "series":
        episodes_data = fetched_data.get("episodes", [])
        if episodes_data:
            # Ensure series_metadata exists
            if not media.series_metadata:
                series_meta = SeriesMetadata(media_id=media.id)
                session.add(series_meta)
                await session.flush()
                media.series_metadata = series_meta

            series_id = media.series_metadata.id
            logger.info(f"Processing {len(episodes_data)} episodes for series {meta_id}")

            # Group episodes by season (including season 0 for specials)
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

            # Create/update episodes
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
                                air_date = date.fromisoformat(released[:10])
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(released, (date, datetime)):
                            air_date = released if isinstance(released, date) else released.date()

                    if episode:
                        # Update existing episode
                        episode.title = ep.get("title") or episode.title
                        episode.overview = ep.get("overview") or ep.get("description") or episode.overview
                        if air_date:
                            episode.air_date = air_date
                        episode.runtime_minutes = (
                            ep.get("runtime_minutes") or ep.get("runtime") or episode.runtime_minutes
                        )
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
                        )
                        session.add(episode)
                        await session.flush()

                    # Handle episode thumbnail
                    thumbnail_url = ep.get("thumbnail")
                    if thumbnail_url and episode.id:
                        # Check if image exists
                        existing_img = await session.exec(
                            select(EpisodeImage).where(
                                EpisodeImage.episode_id == episode.id,
                                EpisodeImage.is_primary.is_(True),
                            )
                        )
                        img = existing_img.first()
                        if img:
                            img.url = thumbnail_url
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

            logger.info(f"Updated {len(episodes_data)} episodes for {meta_id}")

    # Track who refreshed and when
    if user_id:
        media.last_refreshed_by_user_id = user_id
        media.last_refreshed_at = datetime.now(pytz.UTC)

    await session.flush()
    logger.info(f"Successfully refreshed metadata for {meta_id}")
    return True


# =============================================================================
# STREAM HELPERS
# =============================================================================


async def get_stream_by_info_hash(
    session: AsyncSession,
    info_hash: str,
    *,
    load_relations: bool = False,
) -> TorrentStream | None:
    """Get torrent stream by info hash with optional relationship loading."""
    query = select(TorrentStream).where(TorrentStream.info_hash == info_hash.lower())

    if load_relations:
        query = query.options(
            selectinload(TorrentStream.trackers),
            selectinload(TorrentStream.stream).selectinload(Stream.languages),
            selectinload(TorrentStream.stream).selectinload(Stream.audio_formats),
            selectinload(TorrentStream.stream).selectinload(Stream.channels),
            selectinload(TorrentStream.stream).selectinload(Stream.hdr_formats),
            selectinload(TorrentStream.stream).selectinload(Stream.files).selectinload(StreamFile.media_links),
        )

    result = await session.exec(query)
    return result.first()


async def _batch_get_existing_streams(
    session: AsyncSession,
    info_hashes: list[str],
) -> dict[str, TorrentStream]:
    """Batch check for existing streams by info_hash."""
    if not info_hashes:
        return {}

    query = select(TorrentStream).where(TorrentStream.info_hash.in_([h.lower() for h in info_hashes]))
    result = await session.exec(query)
    return {ts.info_hash: ts for ts in result.all()}


async def _batch_resolve_external_ids(
    session: AsyncSession,
    external_ids: list[str],
) -> dict[str, Media]:
    """Batch resolve external IDs to Media objects."""
    if not external_ids:
        return {}

    from db.crud.media import parse_external_id
    from db.models import MediaExternalID

    # Parse all external IDs to get provider/external_id pairs
    provider_map: dict[tuple, list[str]] = {}  # (provider, ext_id) -> [original_external_ids]
    mf_ids: list[int] = []

    for ext_id in external_ids:
        if ext_id.startswith("mf:"):
            try:
                mf_ids.append(int(ext_id[3:]))
                provider_map[(ext_id, None)] = [ext_id]  # Track original
            except ValueError:
                pass
        else:
            provider, provider_ext_id = parse_external_id(ext_id)
            if provider and provider_ext_id:
                key = (provider, provider_ext_id)
                if key not in provider_map:
                    provider_map[key] = []
                provider_map[key].append(ext_id)

    result_map: dict[str, Media] = {}

    # Handle MediaFusion internal IDs (direct PK lookup)
    if mf_ids:
        for mf_id in mf_ids:
            media = await session.get(Media, mf_id)
            if media:
                result_map[f"mf:{mf_id}"] = media

    # Batch query by provider/external_id pairs
    if provider_map:
        conditions = []
        for (provider, ext_id), orig_ids in provider_map.items():
            if provider and ext_id:
                conditions.append((MediaExternalID.provider == provider) & (MediaExternalID.external_id == ext_id))

        if conditions:
            query = (
                select(Media, MediaExternalID)
                .join(MediaExternalID, Media.id == MediaExternalID.media_id)
                .where(or_(*conditions))
            )
            result = await session.exec(query)

            # Map results back to original external IDs
            for media, ext_id_row in result.all():
                key = (ext_id_row.provider, ext_id_row.external_id)
                for orig_id in provider_map.get(key, []):
                    result_map[orig_id] = media

    return result_map


async def _batch_get_or_create_reference_data(
    session: AsyncSession,
    model_class,
    names: list[str],
    cache_prefix: str,
) -> dict[str, Any]:
    """Batch get or create reference data (languages, audio formats, etc.) with caching."""
    if not names:
        return {}

    # Remove duplicates while preserving order
    unique_names = list(dict.fromkeys(names))
    result_map: dict[str, Any] = {}
    missing_names: list[str] = []

    # Check Redis cache first
    for name in unique_names:
        cache_key = f"{cache_prefix}:{name}"
        cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached_id:
            query = select(model_class).where(model_class.id == int(cached_id))
            result = await session.exec(query)
            obj = result.one_or_none()
            if obj:
                result_map[name] = obj
            else:
                missing_names.append(name)
        else:
            missing_names.append(name)

    # Query database for missing items
    if missing_names:
        query = select(model_class).where(model_class.name.in_(missing_names))
        result = await session.exec(query)
        existing = {obj.name: obj for obj in result.all()}

        # Create missing items
        to_create = [name for name in missing_names if name not in existing]
        for name in to_create:
            obj = model_class(name=name)
            session.add(obj)
            result_map[name] = obj

        # Add existing items to result map
        result_map.update(existing)

        # Cache all items (existing and newly created)
        await session.flush()
        for name, obj in result_map.items():
            if name in missing_names:  # Only cache if it was missing
                cache_key = f"{cache_prefix}:{name}"
                await REDIS_ASYNC_CLIENT.set(cache_key, str(obj.id), ex=86400)

    return result_map


def _parse_datetime_field(value: Any) -> datetime | None:
    """Normalize uploaded_at / created_at values that may arrive as strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt
        except ValueError:
            pass
    return None


async def store_new_torrent_streams(
    session: AsyncSession,
    streams_data: list[dict[str, Any]],
) -> int:
    """
    Store multiple torrent streams from scraped data.

    Optimized version that uses batch operations to eliminate N+1 queries.

    Args:
        streams_data: List of stream dictionaries with torrent data

    Returns:
        Number of streams stored
    """
    if not streams_data:
        return 0

    # Step 1: Extract and normalize all info_hashes and external_ids
    valid_streams = []
    info_hashes = []
    external_ids = []

    for stream_data in streams_data:
        info_hash = stream_data.get("id") or stream_data.get("info_hash")
        meta_id = stream_data.get("meta_id")

        if not info_hash or not meta_id:
            continue

        info_hash = info_hash.lower()
        info_hashes.append(info_hash)
        external_ids.append(meta_id)
        valid_streams.append((info_hash, meta_id, stream_data))

    if not valid_streams:
        return 0

    # Step 2: Batch check for existing streams
    existing_streams = await _batch_get_existing_streams(session, info_hashes)

    # Step 3: Batch resolve external IDs to media
    media_map = await _batch_resolve_external_ids(session, external_ids)

    # Step 4: Collect all reference data names
    all_languages = set()
    all_audio_formats = set()
    all_channels = set()
    all_hdr_formats = set()
    all_catalogs = set()
    media_ids_to_update = set()

    streams_to_create = []

    for info_hash, meta_id, stream_data in valid_streams:
        # Skip if stream already exists
        if info_hash in existing_streams:
            continue

        # Skip if media not found
        media = media_map.get(meta_id)
        if not media:
            continue

        media_ids_to_update.add(media.id)

        # Collect reference data names
        if languages := stream_data.get("languages"):
            all_languages.update(languages)
        if audio_formats := stream_data.get("audio_formats"):
            all_audio_formats.update(audio_formats)
        if channels := stream_data.get("channels"):
            all_channels.update(channels)
        if hdr_formats := stream_data.get("hdr_formats"):
            all_hdr_formats.update(hdr_formats)
        if catalogs := stream_data.get("catalogs"):
            if isinstance(catalogs, str):
                catalogs = [catalogs]
            all_catalogs.update(c for c in catalogs if isinstance(c, str) and c)

        streams_to_create.append((info_hash, meta_id, stream_data, media))

    if not streams_to_create:
        return 0

    # Step 5: Batch get or create all reference data
    language_map = await _batch_get_or_create_reference_data(session, Language, list(all_languages), "lang")
    audio_format_map = await _batch_get_or_create_reference_data(
        session, AudioFormat, list(all_audio_formats), "audio_format"
    )
    channel_map = await _batch_get_or_create_reference_data(session, AudioChannel, list(all_channels), "audio_channel")
    hdr_format_map = await _batch_get_or_create_reference_data(session, HDRFormat, list(all_hdr_formats), "hdr_format")
    catalog_map = await _batch_get_or_create_reference_data(session, Catalog, list(all_catalogs), "catalog")

    # Step 6: Create all streams and links
    stored = 0
    catalog_links_to_insert = []

    for info_hash, meta_id, stream_data, media in streams_to_create:
        # Create base stream
        stream = Stream(
            stream_type=StreamType.TORRENT,
            name=stream_data.get("torrent_name", stream_data.get("name", "")),
            source=stream_data.get("source", "unknown"),
            resolution=stream_data.get("resolution"),
            codec=stream_data.get("codec"),
            quality=stream_data.get("quality"),
            bit_depth=stream_data.get("bit_depth"),
            uploader=stream_data.get("uploader"),
            uploader_user_id=stream_data.get("uploader_user_id"),
            release_group=stream_data.get("release_group"),
            # Boolean flags
            is_remastered=stream_data.get("is_remastered", False),
            is_upscaled=stream_data.get("is_upscaled", False),
            is_proper=stream_data.get("is_proper", False),
            is_repack=stream_data.get("is_repack", False),
            is_extended=stream_data.get("is_extended", False),
            is_complete=stream_data.get("is_complete", False),
            is_dubbed=stream_data.get("is_dubbed", False),
            is_subbed=stream_data.get("is_subbed", False),
        )
        session.add(stream)
        await session.flush()

        # Create torrent-specific data
        torrent_type_str = stream_data.get("torrent_type", "public")
        torrent_type = TorrentType(torrent_type_str) if torrent_type_str else TorrentType.PUBLIC

        torrent = TorrentStream(
            stream_id=stream.id,
            info_hash=info_hash,
            total_size=stream_data.get("size", 0) or stream_data.get("total_size", 0) or 0,
            torrent_type=torrent_type,
            seeders=stream_data.get("seeders", 0),
            leechers=stream_data.get("leechers"),
            uploaded_at=_parse_datetime_field(stream_data.get("uploaded_at") or stream_data.get("created_at")),
            torrent_file=stream_data.get("torrent_file"),
            file_count=len(stream_data.get("files", [])) or 1,
        )
        session.add(torrent)

        # Link to media
        link = StreamMediaLink(
            stream_id=stream.id,
            media_id=media.id,
            season=stream_data.get("season"),
            episode=stream_data.get("episode"),
        )
        session.add(link)

        # Media stream counts will be updated in batch at the end

        # Add languages (using pre-fetched map)
        if languages := stream_data.get("languages"):
            for lang_name in languages:
                lang = language_map.get(lang_name)
                if lang:
                    lang_link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
                    session.add(lang_link)

        # Add audio formats (using pre-fetched map)
        if audio_formats := stream_data.get("audio_formats"):
            for af_name in audio_formats:
                af = audio_format_map.get(af_name)
                if af:
                    af_link = StreamAudioLink(stream_id=stream.id, audio_format_id=af.id)
                    session.add(af_link)

        # Add audio channels (using pre-fetched map)
        if channels := stream_data.get("channels"):
            for ch_name in channels:
                ch = channel_map.get(ch_name)
                if ch:
                    ch_link = StreamChannelLink(stream_id=stream.id, channel_id=ch.id)
                    session.add(ch_link)

        # Add HDR formats (using pre-fetched map)
        if hdr_formats := stream_data.get("hdr_formats"):
            for hdr_name in hdr_formats:
                hdr = hdr_format_map.get(hdr_name)
                if hdr:
                    hdr_link = StreamHDRLink(stream_id=stream.id, hdr_format_id=hdr.id)
                    session.add(hdr_link)

        # Collect catalog links for batch insert
        if catalogs := stream_data.get("catalogs"):
            if isinstance(catalogs, str):
                catalogs = [catalogs]
            for catalog_name in catalogs:
                if isinstance(catalog_name, str) and catalog_name:
                    catalog = catalog_map.get(catalog_name)
                    if catalog:
                        catalog_links_to_insert.append(
                            {
                                "media_id": media.id,
                                "catalog_id": catalog.id,
                            }
                        )

        # Add stream files and episode links (v5 schema)
        # `files` is a list of StreamFileData with embedded episode info
        # Maps (season_number, episode_number) -> episode_title
        episode_info: dict[tuple[int, int], str] = {}
        if files := stream_data.get("files"):
            for idx, file_info in enumerate(files):
                if isinstance(file_info, dict):
                    # Create StreamFile
                    file_type_str = file_info.get("file_type", "video")
                    try:
                        file_type = FileType(file_type_str)
                    except ValueError:
                        file_type = FileType.VIDEO

                    stream_file = StreamFile(
                        stream_id=stream.id,
                        file_index=file_info.get("file_index", idx),
                        filename=file_info.get("filename", ""),
                        file_path=file_info.get("file_path"),
                        size=file_info.get("size", 0),
                        file_type=file_type,
                    )
                    session.add(stream_file)
                    await session.flush()

                    # Create FileMediaLink if file has episode info
                    season_number = file_info.get("season_number")
                    episode_number = file_info.get("episode_number")

                    if season_number is not None or episode_number is not None:
                        s_num = season_number or 1
                        e_num = episode_number or 1
                        file_link = FileMediaLink(
                            file_id=stream_file.id,
                            media_id=media.id,
                            season_number=s_num,
                            episode_number=e_num,
                            episode_end=file_info.get("episode_end"),
                            link_source=LinkSource.PTT_PARSER,
                            confidence=1.0,
                        )
                        session.add(file_link)
                        key = (s_num, e_num)
                        if key not in episode_info:
                            ep_title = file_info.get("episode_title") or f"Episode {e_num}"
                            episode_info[key] = ep_title
                    else:
                        # For movies - create a simple media link without episode info
                        file_link = FileMediaLink(
                            file_id=stream_file.id,
                            media_id=media.id,
                            is_primary=True,
                            link_source=LinkSource.PTT_PARSER,
                            confidence=1.0,
                        )
                        session.add(file_link)

        # Ensure Season/Episode metadata records exist for series
        if episode_info and media.type == MediaType.SERIES:
            sm_result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media.id))
            sm = sm_result.first()
            if sm:
                for (s_num, e_num), ep_title in episode_info.items():
                    season_obj = await get_or_create_season(session, sm.id, s_num)
                    await get_or_create_episode(
                        session,
                        season_obj.id,
                        e_num,
                        title=ep_title,
                    )

        stored += 1

    # Step 7: Batch insert catalog links
    if catalog_links_to_insert:
        stmt = pg_insert(MediaCatalogLink).values(catalog_links_to_insert).on_conflict_do_nothing()
        await session.exec(stmt)

    # Step 8: Batch update media stream counts
    if media_ids_to_update:
        now = datetime.now(pytz.UTC)
        # Count streams per media from the links we just created
        media_stream_counts = Counter()
        for _, _, _, media in streams_to_create:
            media_stream_counts[media.id] += 1

        # Update each media's stream count
        for media_id, count in media_stream_counts.items():
            await session.exec(
                sa_update(Media)
                .where(Media.id == media_id)
                .values(
                    total_streams=Media.total_streams + count,
                    last_stream_added=now,
                )
            )

    await session.flush()

    # Invalidate stream cache for all affected media
    if media_ids_to_update:
        from db.crud.stream_services import invalidate_media_stream_cache

        for media_id in media_ids_to_update:
            await invalidate_media_stream_cache(media_id)

    return stored


async def store_new_usenet_streams(
    session: AsyncSession,
    streams_data: list[dict[str, Any]],
) -> int:
    """
    Store multiple Usenet/NZB streams from scraped data.

    Similar to store_new_torrent_streams but for Usenet content.

    Args:
        streams_data: List of stream dictionaries with usenet data

    Returns:
        Number of streams stored
    """
    if not streams_data:
        return 0

    # Step 1: Extract and normalize all nzb_guids and external_ids
    valid_streams = []
    nzb_guids = []
    external_ids = []

    for stream_data in streams_data:
        nzb_guid = stream_data.get("nzb_guid") or stream_data.get("id")
        meta_id = stream_data.get("meta_id")

        if not nzb_guid or not meta_id:
            continue

        nzb_guids.append(nzb_guid)
        external_ids.append(meta_id)
        valid_streams.append((nzb_guid, meta_id, stream_data))

    if not valid_streams:
        return 0

    # Step 2: Batch check for existing Usenet streams
    existing_guids = set()
    if nzb_guids:
        query = select(UsenetStream.nzb_guid).where(UsenetStream.nzb_guid.in_(nzb_guids))
        result = await session.exec(query)
        existing_guids = set(result.all())

    # Step 3: Batch resolve external IDs to media
    media_map = await _batch_resolve_external_ids(session, external_ids)

    # Step 4: Collect all reference data names
    all_languages = set()
    all_audio_formats = set()
    all_channels = set()
    all_hdrs = set()

    for _, _, stream_data in valid_streams:
        if langs := stream_data.get("languages"):
            all_languages.update(langs)
        if audio := stream_data.get("audio_formats"):
            all_audio_formats.update(audio)
        if channels := stream_data.get("channels"):
            all_channels.update(channels)
        if hdrs := stream_data.get("hdr_formats"):
            all_hdrs.update(hdrs)

    # Step 5: Batch get/create reference data (using local helper function)
    lang_map = (
        await _batch_get_or_create_reference_data(session, Language, list(all_languages), "lang")
        if all_languages
        else {}
    )
    audio_map = (
        await _batch_get_or_create_reference_data(session, AudioFormat, list(all_audio_formats), "audio_format")
        if all_audio_formats
        else {}
    )
    channel_map = (
        await _batch_get_or_create_reference_data(session, AudioChannel, list(all_channels), "audio_channel")
        if all_channels
        else {}
    )
    hdr_map = (
        await _batch_get_or_create_reference_data(session, HDRFormat, list(all_hdrs), "hdr_format") if all_hdrs else {}
    )

    # Step 6: Create streams
    stored = 0
    streams_to_create = []
    media_ids_to_update = set()

    for nzb_guid, meta_id, stream_data in valid_streams:
        # Skip if already exists
        if nzb_guid in existing_guids:
            continue

        # Skip if we couldn't find media
        media = media_map.get(meta_id)
        if not media:
            logger.debug(f"Could not find media for meta_id={meta_id}")
            continue

        streams_to_create.append((nzb_guid, meta_id, stream_data, media))
        media_ids_to_update.add(media.id)

    # Create all streams
    for nzb_guid, meta_id, stream_data, media in streams_to_create:
        # Create base Stream
        stream = Stream(
            stream_type=StreamType.USENET,
            name=stream_data.get("name", ""),
            source=stream_data.get("source") or stream_data.get("indexer", ""),
            total_size=stream_data.get("size") or stream_data.get("total_size", 0),
            resolution=stream_data.get("resolution"),
            codec=stream_data.get("codec"),
            quality=stream_data.get("quality"),
            bit_depth=stream_data.get("bit_depth"),
            release_group=stream_data.get("release_group"),
            is_proper=stream_data.get("is_proper", False),
            is_repack=stream_data.get("is_repack", False),
            is_extended=stream_data.get("is_extended", False),
            is_dubbed=stream_data.get("is_dubbed", False),
            is_subbed=stream_data.get("is_subbed", False),
        )
        session.add(stream)
        await session.flush()

        # Create UsenetStream
        usenet_stream = UsenetStream(
            stream_id=stream.id,
            nzb_guid=nzb_guid,
            nzb_url=stream_data.get("nzb_url"),
            size=stream_data.get("size") or stream_data.get("total_size", 0),
            indexer=stream_data.get("indexer") or stream_data.get("source", ""),
            group_name=stream_data.get("group_name"),
            uploader=stream_data.get("uploader"),
            files_count=stream_data.get("files_count"),
            parts_count=stream_data.get("parts_count"),
            posted_at=stream_data.get("posted_at"),
            is_passworded=stream_data.get("is_passworded", False),
        )
        session.add(usenet_stream)

        # Create StreamMediaLink
        stream_link = StreamMediaLink(stream_id=stream.id, media_id=media.id)
        session.add(stream_link)

        # Add language links
        if languages := stream_data.get("languages"):
            for lang_name in languages:
                if lang := lang_map.get(lang_name):
                    lang_link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
                    session.add(lang_link)

        # Add audio format links
        if audio_formats := stream_data.get("audio_formats"):
            for audio_name in audio_formats:
                if audio := audio_map.get(audio_name):
                    audio_link = StreamAudioLink(stream_id=stream.id, audio_format_id=audio.id)
                    session.add(audio_link)

        # Add channel links
        if channels := stream_data.get("channels"):
            for channel_name in channels:
                if channel := channel_map.get(channel_name):
                    channel_link = StreamChannelLink(stream_id=stream.id, channel_id=channel.id)
                    session.add(channel_link)

        # Add HDR format links
        if hdr_formats := stream_data.get("hdr_formats"):
            for hdr_name in hdr_formats:
                if hdr := hdr_map.get(hdr_name):
                    hdr_link = StreamHDRLink(stream_id=stream.id, hdr_format_id=hdr.id)
                    session.add(hdr_link)

        # Add stream files and episode links
        if files := stream_data.get("files"):
            for idx, file_info in enumerate(files):
                if isinstance(file_info, dict):
                    file_type_str = file_info.get("file_type", "video")
                    try:
                        file_type = FileType(file_type_str)
                    except ValueError:
                        file_type = FileType.VIDEO

                    stream_file = StreamFile(
                        stream_id=stream.id,
                        file_index=file_info.get("file_index", idx),
                        filename=file_info.get("filename", ""),
                        file_path=file_info.get("file_path"),
                        size=file_info.get("size", 0),
                        file_type=file_type,
                    )
                    session.add(stream_file)
                    await session.flush()

                    # Create FileMediaLink if file has episode info
                    season_number = file_info.get("season_number")
                    episode_number = file_info.get("episode_number")

                    if season_number is not None or episode_number is not None:
                        file_link = FileMediaLink(
                            file_id=stream_file.id,
                            media_id=media.id,
                            season_number=season_number or 1,
                            episode_number=episode_number or 1,
                            episode_end=file_info.get("episode_end"),
                            link_source=LinkSource.PTT_PARSER,
                            confidence=1.0,
                        )
                        session.add(file_link)
                    else:
                        # For movies - create a simple media link without episode info
                        file_link = FileMediaLink(
                            file_id=stream_file.id,
                            media_id=media.id,
                            is_primary=True,
                            link_source=LinkSource.PTT_PARSER,
                            confidence=1.0,
                        )
                        session.add(file_link)

        stored += 1

    # Step 7: Batch update media stream counts
    if media_ids_to_update:
        now = datetime.now(pytz.UTC)
        media_stream_counts = Counter()
        for _, _, _, media in streams_to_create:
            media_stream_counts[media.id] += 1

        for media_id, count in media_stream_counts.items():
            await session.exec(
                sa_update(Media)
                .where(Media.id == media_id)
                .values(
                    total_streams=Media.total_streams + count,
                    last_stream_added=now,
                )
            )

    await session.flush()

    # Invalidate stream cache for all affected media
    if media_ids_to_update:
        for media_id in media_ids_to_update:
            await invalidate_media_stream_cache(media_id)

    return stored


async def delete_torrent_stream(
    session: AsyncSession,
    info_hash: str,
) -> bool:
    """Delete a torrent stream by info hash."""
    torrent = await get_stream_by_info_hash(session, info_hash)
    if not torrent:
        return False

    # Get linked media for updating counts
    links_query = select(StreamMediaLink.media_id).where(StreamMediaLink.stream_id == torrent.stream_id)
    links_result = await session.exec(links_query)
    media_ids = links_result.all()

    # Delete dependent records before the base stream
    await session.exec(sa_delete(StreamMediaLink).where(StreamMediaLink.stream_id == torrent.stream_id))
    await session.exec(sa_delete(TorrentStream).where(TorrentStream.stream_id == torrent.stream_id))
    await session.exec(sa_delete(Stream).where(Stream.id == torrent.stream_id))

    # Update media stream counts
    for media_id in media_ids:
        await session.exec(
            sa_update(Media).where(Media.id == media_id).values(total_streams=func.greatest(0, Media.total_streams - 1))
        )

    await session.flush()
    return True


async def block_torrent_stream(
    session: AsyncSession,
    info_hash: str,
) -> bool:
    """Mark a torrent stream as blocked/not working."""
    torrent = await get_stream_by_info_hash(session, info_hash)
    if not torrent:
        return False

    await session.exec(sa_update(Stream).where(Stream.id == torrent.stream_id).values(is_blocked=False))
    await session.flush()
    return True


async def update_torrent_seeders(
    session: AsyncSession,
    torrent_id: int,
    seeders: int,
) -> bool:
    """Update seeders count for a torrent."""
    await session.exec(sa_update(TorrentStream).where(TorrentStream.stream_id == torrent_id).values(seeders=seeders))
    await session.flush()
    return True


async def migrate_torrent_streams(
    session: AsyncSession,
    old_meta_id: str,
    new_meta_id: str,
) -> int:
    """Migrate torrent streams from one metadata to another."""
    old_media = await get_media_by_external_id(session, old_meta_id)
    new_media = await get_media_by_external_id(session, new_meta_id)

    if not old_media or not new_media:
        return 0

    # Update stream links
    result = await session.exec(
        sa_update(StreamMediaLink).where(StreamMediaLink.media_id == old_media.id).values(media_id=new_media.id)
    )

    await session.flush()
    return result.rowcount


# =============================================================================
# TV STREAM HELPERS
# =============================================================================


async def save_tv_channel_metadata(
    session: AsyncSession,
    metadata,
    user_id: int | None = None,
    is_public: bool = True,
) -> int:
    """
    Save TV channel metadata.

    Args:
        metadata: Can be a dict or a Pydantic model (TVMetaData)
        user_id: Optional user ID for user-created content
        is_public: Whether the content is publicly visible

    Returns:
        The media ID (int)
    """
    # Handle both dict and Pydantic model
    if hasattr(metadata, "model_dump"):
        data = metadata.model_dump()
    elif hasattr(metadata, "dict"):
        data = metadata.dict()
    else:
        data = metadata

    title = data.get("title", "Unknown")

    # First, check if a channel with this title already exists (case-insensitive)
    # This enables stream sharing across users for the same channel
    existing_by_title = await find_tv_channel_by_title(session, title)
    if existing_by_title:
        # Channel exists - return it so streams can be added to it
        # The caller is responsible for adding streams and handling deduplication
        return existing_by_title.id

    # Generate external_id for new channel (title-based, not user-based)
    # This ensures the same channel title maps to the same external_id
    title_hash = hashlib.md5(title.lower().encode()).hexdigest()[:12]
    external_id = data.get("id", f"mf_tv_{title_hash}")

    # Double-check by external_id (for edge cases with provided IDs)
    existing = await get_media_by_external_id(session, external_id, MediaType.TV)
    if existing:
        return existing.id

    # Create media (no poster/background fields - use MediaImage instead)
    # external_id is now stored in MediaExternalID table
    media = Media(
        type=MediaType.TV,
        title=title,
        description=data.get("description"),
        created_by_user_id=user_id,
        is_user_created=user_id is not None,
        is_public=is_public,
    )
    session.add(media)
    await session.flush()

    # Add external ID to MediaExternalID table
    if external_id:
        provider, ext_id = parse_external_id(external_id)
        if provider and ext_id:
            await add_external_id(session, media.id, provider, ext_id)

    # Create TV metadata
    tv_meta = TVMetadata(
        media_id=media.id,
        country=data.get("country"),
        tv_language=data.get("tv_language"),
    )
    session.add(tv_meta)

    # Add images via MediaImage table
    mediafusion_provider = await get_or_create_provider(session, "mediafusion")

    poster_url = data.get("poster")
    if poster_url:
        poster_image = MediaImage(
            media_id=media.id,
            provider_id=mediafusion_provider.id,
            image_type="poster",
            url=poster_url,
            is_primary=True,
        )
        session.add(poster_image)

    background_url = data.get("background")
    if background_url:
        background_image = MediaImage(
            media_id=media.id,
            provider_id=mediafusion_provider.id,
            image_type="background",
            url=background_url,
            is_primary=True,
        )
        session.add(background_image)

    logo_url = data.get("logo")
    if logo_url:
        logo_image = MediaImage(
            media_id=media.id,
            provider_id=mediafusion_provider.id,
            image_type="logo",
            url=logo_url,
            is_primary=True,
        )
        session.add(logo_image)

    await session.flush()
    return media.id


async def find_tv_channel_by_title(
    session: AsyncSession,
    title: str,
) -> Media | None:
    """Find TV channel by title."""
    query = select(Media).where(
        Media.type == MediaType.TV,
        func.lower(Media.title) == func.lower(title),
    )
    result = await session.exec(query)
    return result.first()


async def get_all_tv_streams_paginated(
    session: AsyncSession,
    offset: int,
    limit: int,
) -> Sequence[tuple[Stream, HTTPStream]]:
    """Get all TV HTTP streams with pagination."""
    query = (
        select(Stream, HTTPStream)
        .join(HTTPStream, HTTPStream.stream_id == Stream.id)
        .join(StreamMediaLink, StreamMediaLink.stream_id == Stream.id)
        .join(Media, Media.id == StreamMediaLink.media_id)
        .where(Media.type == MediaType.TV)
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(query)
    return result.all()


async def get_tv_streams_by_meta_id(
    session: AsyncSession,
    meta_id: int,
) -> Sequence[Stream]:
    """Get TV streams for a specific metadata ID."""
    query = select(Stream).join(StreamMediaLink).where(StreamMediaLink.media_id == meta_id)
    result = await session.exec(query)
    return result.all()


async def update_tv_stream_status(
    session: AsyncSession,
    stream_id: int,
    is_active: bool,
) -> None:
    """Update TV stream working status."""
    await session.exec(sa_update(Stream).where(Stream.id == stream_id).values(is_active=is_active))
    await session.flush()


async def delete_tv_stream(
    session: AsyncSession,
    stream_id: int,
) -> bool:
    """Delete a TV stream."""
    result = await session.exec(sa_delete(Stream).where(Stream.id == stream_id))
    await session.flush()
    return result.rowcount > 0


async def get_tv_metadata_not_working_posters(
    session: AsyncSession,
) -> Sequence[Media]:
    """Get TV metadata with non-working posters.

    Now uses MediaImage table to find TV media with poster images.
    """
    # Find TV media that has poster images
    query = (
        select(Media)
        .join(MediaImage, MediaImage.media_id == Media.id)
        .where(
            Media.type == MediaType.TV,
            MediaImage.image_type == "poster",
        )
        .distinct()
    )
    result = await session.exec(query)
    return result.all()


async def update_tv_metadata_poster(
    session: AsyncSession,
    media_id: int,
    poster: str,
    is_working: bool,
) -> None:
    """Update TV metadata poster in MediaImage table.

    If poster is not working, delete it. If working, update the URL.
    """
    # Find the primary poster for this media
    query = select(MediaImage).where(
        MediaImage.media_id == media_id,
        MediaImage.image_type == "poster",
        MediaImage.is_primary,
    )
    result = await session.exec(query)
    poster_image = result.first()

    if not is_working:
        # Delete non-working poster
        if poster_image:
            await session.delete(poster_image)
    else:
        # Update or create poster
        if poster_image:
            poster_image.url = poster
        else:
            provider = await get_or_create_provider(session, "mediafusion")
            new_poster = MediaImage(
                media_id=media_id,
                provider_id=provider.id,
                image_type="poster",
                url=poster,
                is_primary=True,
            )
            session.add(new_poster)

    await session.flush()


# =============================================================================
# RSS FEED HELPERS
# =============================================================================


async def update_user_rss_feed(
    session: AsyncSession,
    feed_id: int,
    _unused: Any,
    updates: dict[str, Any],
) -> None:
    """Update user RSS feed fields (for scraper use)."""
    await session.exec(sa_update(RSSFeed).where(RSSFeed.id == feed_id).values(**updates))
    await session.commit()


async def update_user_rss_feed_metrics(
    session: AsyncSession,
    feed_id: int,
    metrics: dict[str, Any],
) -> None:
    """Update user RSS feed metrics."""
    await session.exec(sa_update(RSSFeed).where(RSSFeed.id == feed_id).values(metrics=metrics))
    await session.commit()


async def list_all_active_user_rss_feeds(
    session: AsyncSession,
) -> Sequence[RSSFeed]:
    """List all active user RSS feeds with catalog patterns eagerly loaded."""
    query = select(RSSFeed).where(RSSFeed.is_active.is_(True)).options(selectinload(RSSFeed.catalog_patterns))
    result = await session.exec(query)
    return result.all()


async def list_user_rss_feeds(
    session: AsyncSession,
    user_id: int,
) -> Sequence[RSSFeed]:
    """List all RSS feeds for a specific user with catalog patterns."""
    query = select(RSSFeed).where(RSSFeed.user_id == user_id).options(selectinload(RSSFeed.catalog_patterns))
    result = await session.exec(query)
    return result.all()


async def list_all_user_rss_feeds_with_users(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """
    List all RSS feeds with user information for admin view.
    Returns a list of dictionaries with feed and user data.
    """
    query = select(RSSFeed).options(
        selectinload(RSSFeed.user),
        selectinload(RSSFeed.catalog_patterns),
    )
    result = await session.exec(query)
    feeds = result.all()

    feeds_data = []
    for feed in feeds:
        feed_dict = {
            "id": feed.uuid,  # Use UUID for external API
            "user_id": feed.user.uuid if feed.user else None,
            "name": feed.name,
            "url": feed.url,
            "is_active": feed.is_active,
            "source": feed.source,
            "torrent_type": feed.torrent_type,
            "auto_detect_catalog": feed.auto_detect_catalog,
            "parsing_patterns": feed.parsing_patterns,
            "filters": feed.filters,
            "metrics": feed.metrics,
            "catalog_patterns": [
                {
                    "id": p.uuid,
                    "name": p.name,
                    "regex": p.regex,
                    "enabled": p.enabled,
                    "case_sensitive": p.case_sensitive,
                    "target_catalogs": p.target_catalogs,
                }
                for p in (feed.catalog_patterns or [])
            ],
            "last_scraped_at": feed.last_scraped_at.isoformat() if feed.last_scraped_at else None,
            "created_at": feed.created_at.isoformat() if feed.created_at else None,
            "updated_at": feed.updated_at.isoformat() if feed.updated_at else None,
            "user": {
                "id": feed.user.uuid,
                "email": feed.user.email,
                "username": feed.user.username,
            }
            if feed.user
            else None,
        }
        feeds_data.append(feed_dict)

    return feeds_data


async def get_user_rss_feed(
    session: AsyncSession,
    feed_id: str,
    user_id: int | None = None,
) -> RSSFeed | None:
    """
    Get a specific RSS feed by UUID.
    If user_id is provided, only return if the feed belongs to that user.
    If user_id is None (admin), return regardless of owner.
    """
    query = (
        select(RSSFeed)
        .where(RSSFeed.uuid == feed_id)
        .options(
            selectinload(RSSFeed.user),
            selectinload(RSSFeed.catalog_patterns),
        )
    )

    if user_id is not None:
        query = query.where(RSSFeed.user_id == user_id)

    result = await session.exec(query)
    return result.first()


async def get_user_rss_feed_by_url(
    session: AsyncSession,
    url: str,
    user_id: int,
) -> RSSFeed | None:
    """Get an RSS feed by URL for a specific user (to check for duplicates)."""
    query = select(RSSFeed).where(
        RSSFeed.url == url,
        RSSFeed.user_id == user_id,
    )
    result = await session.exec(query)
    return result.first()


async def create_user_rss_feed(
    session: AsyncSession,
    user_id: int,
    feed_data: dict[str, Any],
) -> RSSFeed:
    """
    Create a new RSS feed for a user.

    Args:
        session: Database session
        user_id: The user's ID (int)
        feed_data: Dictionary containing feed configuration

    Returns:
        The created RSSFeed object with relationships loaded
    """
    # Extract catalog patterns before creating feed
    catalog_patterns_data = feed_data.pop("catalog_patterns", [])

    # Create the feed
    feed = RSSFeed(
        user_id=user_id,
        name=feed_data.get("name"),
        url=feed_data.get("url"),
        is_active=feed_data.get("is_active", True),
        source=feed_data.get("source"),
        torrent_type=feed_data.get("torrent_type", "public"),
        auto_detect_catalog=feed_data.get("auto_detect_catalog", False),
        parsing_patterns=feed_data.get("parsing_patterns"),
        filters=feed_data.get("filters"),
    )
    session.add(feed)
    await session.flush()

    # Create catalog patterns
    for pattern_data in catalog_patterns_data:
        pattern = RSSFeedCatalogPattern(
            rss_feed_id=feed.id,
            name=pattern_data.get("name"),
            regex=pattern_data.get("regex"),
            enabled=pattern_data.get("enabled", True),
            case_sensitive=pattern_data.get("case_sensitive", False),
            target_catalogs=pattern_data.get("target_catalogs", []),
        )
        session.add(pattern)

    await session.commit()

    # Reload with all relationships eagerly loaded
    query = (
        select(RSSFeed)
        .where(RSSFeed.id == feed.id)
        .options(
            selectinload(RSSFeed.user),
            selectinload(RSSFeed.catalog_patterns),
        )
    )
    result = await session.exec(query)
    return result.first()


async def update_user_rss_feed_by_uuid(
    session: AsyncSession,
    feed_id: str,
    user_id: int | None,
    updates: dict[str, Any],
) -> RSSFeed | None:
    """
    Update an RSS feed by UUID.
    If user_id is provided, only update if the feed belongs to that user.
    If user_id is None (admin), update regardless of owner.

    Returns the updated feed or None if not found.
    """
    # Get the feed first
    feed = await get_user_rss_feed(session, feed_id, user_id)
    if not feed:
        return None

    feed_internal_id = feed.id

    # Extract catalog patterns if present
    catalog_patterns_data = updates.pop("catalog_patterns", None)

    # Update scalar fields
    for key, value in updates.items():
        if hasattr(feed, key):
            setattr(feed, key, value)

    # Update catalog patterns if provided
    if catalog_patterns_data is not None:
        # Delete existing patterns
        await session.exec(
            sa_delete(RSSFeedCatalogPattern).where(RSSFeedCatalogPattern.rss_feed_id == feed_internal_id)
        )

        # Create new patterns
        for pattern_data in catalog_patterns_data:
            pattern = RSSFeedCatalogPattern(
                rss_feed_id=feed_internal_id,
                name=pattern_data.get("name"),
                regex=pattern_data.get("regex"),
                enabled=pattern_data.get("enabled", True),
                case_sensitive=pattern_data.get("case_sensitive", False),
                target_catalogs=pattern_data.get("target_catalogs", []),
            )
            session.add(pattern)

    await session.commit()

    # Reload with all relationships eagerly loaded
    query = (
        select(RSSFeed)
        .where(RSSFeed.id == feed_internal_id)
        .options(
            selectinload(RSSFeed.user),
            selectinload(RSSFeed.catalog_patterns),
        )
    )
    result = await session.exec(query)
    return result.first()


async def delete_user_rss_feed(
    session: AsyncSession,
    feed_id: str,
    user_id: int | None = None,
) -> bool:
    """
    Delete an RSS feed by UUID.
    If user_id is provided, only delete if the feed belongs to that user.
    If user_id is None (admin), delete regardless of owner.

    Returns True if deleted, False if not found.
    """
    feed = await get_user_rss_feed(session, feed_id, user_id)
    if not feed:
        return False

    await session.delete(feed)
    await session.commit()
    return True


async def bulk_update_user_rss_feed_status(
    session: AsyncSession,
    feed_ids: list[str],
    user_id: int | None,
    is_active: bool,
) -> int:
    """
    Bulk update the active status of multiple RSS feeds by UUID.
    If user_id is provided, only update feeds belonging to that user.
    If user_id is None (admin), update regardless of owner.

    Returns the number of feeds updated.
    """
    # Build the query
    query = sa_update(RSSFeed).where(RSSFeed.uuid.in_(feed_ids)).values(is_active=is_active)

    if user_id is not None:
        query = query.where(RSSFeed.user_id == user_id)

    result = await session.exec(query)
    await session.commit()
    return result.rowcount
