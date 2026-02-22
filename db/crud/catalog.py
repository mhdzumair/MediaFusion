"""
Catalog and Search CRUD operations for Stremio API.

These functions handle catalog queries, MDBList integration, and search
for the Stremio addon endpoints.

ARCHITECTURE NOTE:
- Internally, all queries use media_id (integer) for performance
- External ID translation (to IMDb/TMDB) happens only at the Stremio boundary
- get_canonical_external_ids_batch() is used for batch translation
"""

import logging
from typing import Literal

from fastapi import BackgroundTasks
from sqlalchemy import asc, desc, func, union
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db import public_schemas, schemas
from db.crud.media import get_canonical_external_ids_batch
from db.enums import MediaType
from db.models import (
    AkaTitle,
    Catalog,
    Genre,
    Media,
    MediaCatalogLink,
    MediaExternalID,
    MediaGenreLink,
    MediaParentalCertificateLink,
    MediaRating,
    MovieMetadata,
    ParentalCertificate,
    RatingProvider,
    SeriesMetadata,
    Stream,
    StreamMediaLink,
    TorrentStream,
    TVMetadata,
    UserLibraryItem,
)
from db.schemas import UserData

logger = logging.getLogger(__name__)

# Type alias for catalog sort options
CatalogSortOption = Literal["latest", "popular", "rating", "year", "title", "release_date"]
MY_LIBRARY_CATALOG_TYPE_MAP: dict[str, MediaType] = {
    "my_library_movies": MediaType.MOVIE,
    "my_library_series": MediaType.SERIES,
    "my_library_tv": MediaType.TV,
}


async def get_catalog_meta_list(
    session: AsyncSession,
    catalog_type: MediaType,
    catalog_id: str,
    user_data: UserData,
    skip: int = 0,
    limit: int = 25,
    genre: str | None = None,
    namespace: str | None = None,
    is_watchlist_catalog: bool = False,
    info_hashes: list[str] | None = None,
    sort: CatalogSortOption | None = None,
    sort_dir: Literal["asc", "desc"] | None = None,
) -> public_schemas.Metas:
    """
    Get metadata list for catalog with efficient filtering and pagination.

    Args:
        session: Database session
        catalog_type: Type of media (movie, series, tv)
        catalog_id: Catalog identifier
        user_data: User preferences and filters
        skip: Pagination offset
        limit: Max results to return
        genre: Optional genre filter
        namespace: Optional namespace for TV (deprecated in v5)
        is_watchlist_catalog: Whether this is a watchlist query
        info_hashes: List of torrent info hashes for watchlist queries
        sort: Sort field. Options: latest, popular, rating, year, title, release_date.
              Defaults to "latest" if not specified.
        sort_dir: Sort direction. Options: asc, desc. Defaults to "desc".

    Returns:
        Metas object containing list of Meta items for Stremio
    """
    # Build base query - use media.id internally, external_id translation happens at Stremio boundary
    query = select(Media.id, Media.title).where(Media.type == catalog_type)
    is_my_library_catalog = catalog_id in MY_LIBRARY_CATALOG_TYPE_MAP

    # Handle personal My Library catalogs
    if is_my_library_catalog:
        if not user_data.user_id:
            return public_schemas.Metas(metas=[])

        expected_type = MY_LIBRARY_CATALOG_TYPE_MAP[catalog_id]
        if catalog_type != expected_type:
            return public_schemas.Metas(metas=[])

        query = query.join(UserLibraryItem, UserLibraryItem.media_id == Media.id).where(
            UserLibraryItem.user_id == user_data.user_id
        )
    # Handle watchlist catalog
    elif is_watchlist_catalog and info_hashes:
        # Join through TorrentStream to find media by info_hash
        query = (
            query.join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
            .join(Stream, Stream.id == StreamMediaLink.stream_id)
            .join(TorrentStream, TorrentStream.stream_id == Stream.id)
            .where(TorrentStream.info_hash.in_([h.lower() for h in info_hashes]))
        )
    else:
        # Standard catalog filter
        if catalog_type != MediaType.TV:
            query = (
                query.join(MediaCatalogLink, MediaCatalogLink.media_id == Media.id)
                .join(Catalog, Catalog.id == MediaCatalogLink.catalog_id)
                .where(Catalog.name == catalog_id)
            )

    # Add content filters for movies and series
    if catalog_type in [MediaType.MOVIE, MediaType.SERIES] and not is_my_library_catalog:
        specific_model = MovieMetadata if catalog_type == MediaType.MOVIE else SeriesMetadata
        query = query.join(specific_model, specific_model.media_id == Media.id)

        # Apply nudity filter
        if "Disable" not in user_data.nudity_filter:
            query = query.where(Media.nudity_status.notin_(user_data.nudity_filter))

        # Apply certification filter
        if "Disable" not in user_data.certification_filter:
            blocked_cert_exists = (
                select(MediaParentalCertificateLink.media_id)
                .join(ParentalCertificate)
                .where(
                    MediaParentalCertificateLink.media_id == Media.id,
                    ParentalCertificate.name.in_(user_data.certification_filter),
                )
                .exists()
            )
            query = query.where(~blocked_cert_exists)

    # Add TV-specific filters
    if catalog_type == MediaType.TV and not is_my_library_catalog:
        query = (
            query.join(TVMetadata, TVMetadata.media_id == Media.id)
            .join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
            .join(Stream, Stream.id == StreamMediaLink.stream_id)
            .where(Stream.is_active.is_(True), Stream.is_blocked.is_(False))
        )

    # Add genre filter
    if genre:
        query = (
            query.join(MediaGenreLink, MediaGenreLink.media_id == Media.id)
            .join(Genre, Genre.id == MediaGenreLink.genre_id)
            .where(Genre.name == genre)
        )

    # Apply sorting based on user preferences
    sort = sort or "latest"
    sort_dir = sort_dir or "desc"
    order_func = asc if sort_dir == "asc" else desc
    nulls_position = "nulls_first" if sort_dir == "asc" else "nulls_last"

    if sort == "latest":
        order_expr = (
            order_func(UserLibraryItem.added_at) if is_my_library_catalog else order_func(Media.last_stream_added)
        )
        query = query.order_by(getattr(order_expr, nulls_position)())
    elif sort in ("popular", "rating"):
        # Sort by IMDb rating with fallback to total_streams
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
        rating_order = order_func(imdb_rating_subq)
        streams_order = order_func(Media.total_streams)
        query = query.order_by(
            getattr(rating_order, nulls_position)(),
            getattr(streams_order, nulls_position)(),
        )
    elif sort == "year":
        order_expr = order_func(Media.year)
        query = query.order_by(getattr(order_expr, nulls_position)())
    elif sort == "release_date":
        order_expr = order_func(Media.release_date)
        query = query.order_by(getattr(order_expr, nulls_position)())
    elif sort == "title":
        query = query.order_by(order_func(Media.title))
    else:
        # Fallback to latest
        order_expr = order_func(Media.last_stream_added)
        query = query.order_by(getattr(order_expr, nulls_position)())

    # Apply pagination
    query = query.offset(skip).limit(limit)

    result = await session.exec(query)
    data = result.unique().all()

    if not data:
        return public_schemas.Metas(metas=[])

    # Extract media_ids and titles - data is (media_id, title) tuples
    media_ids = [row[0] for row in data]
    titles_map = {row[0]: row[1] for row in data}

    # Batch translate media_ids to external_ids for Stremio response
    external_ids = await get_canonical_external_ids_batch(session, media_ids)

    # Build Stremio response with external_ids
    metas = [
        public_schemas.Meta(
            id=external_ids.get(media_id, f"mf:{media_id}"),
            name=titles_map[media_id],
            type=catalog_type,
        )
        for media_id in media_ids
    ]
    return public_schemas.Metas(metas=metas)


async def get_mdblist_meta_list(
    session: AsyncSession,
    user_data: UserData,
    background_tasks: BackgroundTasks,
    list_config: schemas.MDBListItem,
    catalog_type: str,
    genre: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[public_schemas.Meta]:
    """
    Get a list of metadata entries from MDBList.

    This function:
    1. Initializes the MDBList scraper with user's API key
    2. If not using filters, returns items directly from MDBList
    3. If using filters, fetches IMDb IDs and filters through PostgreSQL
       with user's parental guide filters applied
    4. Triggers background fetch for missing metadata

    Args:
        session: Database session
        user_data: User data with MDBList config
        background_tasks: FastAPI background tasks for async operations
        list_config: MDBList configuration for the specific list
        catalog_type: 'movie' or 'series'
        genre: Optional genre filter
        skip: Pagination offset
        limit: Max results to return

    Returns:
        List of Meta objects for Stremio
    """

    # Lazy import to avoid circular dependencies
    from scrapers.mdblist import initialize_mdblist_scraper

    if not user_data.mdblist_config:
        return []

    # Determine media type enum
    media_type_enum = MediaType.MOVIE if catalog_type == "movie" else MediaType.SERIES

    # Initialize MDBList scraper
    mdblist_scraper = await initialize_mdblist_scraper(user_data.mdblist_config.api_key)
    try:
        if not list_config.use_filters:
            # Return items directly from MDBList without filtering
            mdblist_items = await mdblist_scraper.get_list_items(
                list_config=list_config,
                skip=skip,
                limit=limit,
                genre=genre,
                use_filters=False,
            )
            # Convert to public_schemas.Meta for compatibility
            return [
                public_schemas.Meta(
                    id=item.id,
                    name=item.name,
                    type=item.type,
                    poster=item.poster,
                )
                for item in mdblist_items
            ]

        # For filtered results, get all IMDb IDs first
        imdb_ids = await mdblist_scraper.get_list_items(
            list_config=list_config,
            skip=0,
            limit=0,  # Ignored for filtered results
            genre=genre,
            use_filters=True,
        )

        if not imdb_ids:
            return []

        # Build PostgreSQL query with filters
        specific_model = MovieMetadata if media_type_enum == MediaType.MOVIE else SeriesMetadata

        # Build base query - join with MediaExternalID to match IMDb IDs
        # We select both media_id (for internal use) and external_id (for Stremio response)
        query = (
            select(Media.id, Media.title, MediaExternalID.external_id)
            .join(MediaExternalID, MediaExternalID.media_id == Media.id)
            .where(
                MediaExternalID.provider == "imdb",
                MediaExternalID.external_id.in_(imdb_ids),
                Media.type == media_type_enum,
                Media.total_streams > 0,
            )
            .join(specific_model, specific_model.media_id == Media.id)
        )

        # Apply nudity filter
        if "Disable" not in user_data.nudity_filter:
            query = query.where(Media.nudity_status.notin_(user_data.nudity_filter))

        # Apply certification filter
        if "Disable" not in user_data.certification_filter:
            blocked_cert_exists = (
                select(MediaParentalCertificateLink.media_id)
                .join(ParentalCertificate)
                .where(
                    MediaParentalCertificateLink.media_id == Media.id,
                    ParentalCertificate.name.in_(user_data.certification_filter),
                )
                .exists()
            )
            query = query.where(~blocked_cert_exists)

        # Add sorting and pagination
        query = query.order_by(Media.last_stream_added.desc().nullslast()).offset(skip).limit(limit)

        result = await session.exec(query)
        data = result.unique().all()

        if not data:
            # Check for missing metadata and trigger background fetch
            existing_query = select(MediaExternalID.external_id).where(
                MediaExternalID.provider == "imdb",
                MediaExternalID.external_id.in_(imdb_ids),
            )
            existing_result = await session.exec(existing_query)
            existing_ids = set(existing_result.all())
            missing_ids = list(set(imdb_ids) - existing_ids)

            # TODO: Implement batch metadata fetching for missing IDs
            # if missing_ids:
            #     background_tasks.add_task(fetch_metadata_batch, missing_ids, catalog_type)
            if missing_ids:
                logger.debug(f"Found {len(missing_ids)} missing metadata IDs")
            return []

        # Convert to Meta objects - data is (media_id, title, external_id)
        metas = [
            public_schemas.Meta(id=row[2], name=row[1], type=media_type_enum)
            for row in data
            if row[2]  # Skip entries without external_id
        ]
        return metas

    finally:
        await mdblist_scraper.close()


async def search_metadata(
    session: AsyncSession,
    catalog_type: MediaType,
    search_query: str,
    user_data: UserData,
    namespace: str | None = None,
    limit: int = 50,
) -> public_schemas.Metas:
    """
    Search metadata with efficient filtering and ranking.

    Uses PostgreSQL full-text search (GIN index on title_tsv) and
    trigram search (gin_trgm_ops) for fuzzy matching.

    Args:
        session: Database session
        catalog_type: Type of media to search
        search_query: Search text
        user_data: User preferences for filtering
        namespace: Optional namespace for TV (deprecated in v5)
        limit: Max results to return

    Returns:
        Metas object containing matching Meta items
    """
    search_query = search_query.strip()
    if not search_query:
        return public_schemas.Metas(metas=[])

    # Create search vector for full-text search
    search_vector = func.plainto_tsquery("simple", search_query.lower())

    # Phase 1: Full-text search matches (fastest - uses GIN index on title_tsv)
    fts_base_matches = (
        select(Media.id)
        .where(
            Media.title_tsv.op("@@")(search_vector),
            Media.type == catalog_type,
        )
        .limit(200)
    )

    fts_aka_matches = select(AkaTitle.media_id).where(AkaTitle.title_tsv.op("@@")(search_vector)).limit(200)

    # Phase 2: Trigram search using % operator (uses GIN index with gin_trgm_ops)
    trgm_base_matches = (
        select(Media.id)
        .where(
            Media.title.op("%")(search_query),
            Media.type == catalog_type,
        )
        .limit(100)
    )

    # Combine matches using UNION (deduplicates results)
    all_matches = union(fts_base_matches, fts_aka_matches, trgm_base_matches).subquery()

    # Build main query - use media_id internally, external_id translation at the end
    query = select(Media.id, Media.title).where(Media.type == catalog_type, Media.id.in_(select(all_matches.c.id)))

    # Add content filters for movies and series
    if catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
        specific_model = MovieMetadata if catalog_type == MediaType.MOVIE else SeriesMetadata
        query = query.join(specific_model, specific_model.media_id == Media.id)

        # Apply nudity filter
        if "Disable" not in user_data.nudity_filter:
            query = query.where(Media.nudity_status.notin_(user_data.nudity_filter))

        # Apply certification filter
        if "Disable" not in user_data.certification_filter:
            blocked_cert_exists = (
                select(MediaParentalCertificateLink.media_id)
                .join(ParentalCertificate)
                .where(
                    MediaParentalCertificateLink.media_id == Media.id,
                    ParentalCertificate.name.in_(user_data.certification_filter),
                )
                .exists()
            )
            query = query.where(~blocked_cert_exists)

        # Add stream existence filter for movies/series
        stream_exists = (
            select(StreamMediaLink.id)
            .join(Stream, Stream.id == StreamMediaLink.stream_id)
            .where(
                StreamMediaLink.media_id == Media.id,
                Stream.is_active.is_(True),
                Stream.is_blocked.is_(False),
            )
            .exists()
        )
        query = query.where(stream_exists)

    # Add TV-specific filters
    if catalog_type == MediaType.TV:
        query = (
            query.join(TVMetadata, TVMetadata.media_id == Media.id)
            .join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
            .join(Stream, Stream.id == StreamMediaLink.stream_id)
            .where(Stream.is_active.is_(True), Stream.is_blocked.is_(False))
        )

    # Add ordering by relevance and limit
    query = query.order_by(func.ts_rank_cd(Media.title_tsv, search_vector).desc(), Media.title).limit(limit)

    result = await session.exec(query)
    data = result.unique().all()

    if not data:
        return public_schemas.Metas(metas=[])

    # Extract media_ids and titles - data is (media_id, title) tuples
    media_ids = [row[0] for row in data]
    titles_map = {row[0]: row[1] for row in data}

    # Batch translate media_ids to external_ids for Stremio response
    external_ids = await get_canonical_external_ids_batch(session, media_ids)

    # Build Stremio response with external_ids
    metas = [
        public_schemas.Meta(
            id=external_ids.get(media_id, f"mf:{media_id}"),
            name=titles_map[media_id],
            type=catalog_type,
        )
        for media_id in media_ids
    ]
    return public_schemas.Metas(metas=metas)
