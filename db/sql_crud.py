import json
import logging
from abc import ABC
from datetime import datetime
from typing import Optional, List, TypeVar, Generic, Type, Sequence

import pytz
from fastapi import BackgroundTasks
from sqlalchemy import func, update as sa_update, delete as sa_delete, union, exists as sa_exists
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload, subqueryload, joinedload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql._expression_select_cls import Select

from db import data_models, public_schemas, sql_models, schemas
from db.config import settings
from db.enums import TorrentType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData, Stream, TorrentStreamData
from db.sql_models import (
    BaseMetadata,
    MovieMetadata,
    SeriesMetadata,
    TVMetadata,
    TVStream,
    MediaGenreLink,
    Genre,
    MediaCatalogLink,
    Catalog,
    MediaParentalCertificateLink,
    ParentalCertificate,
    TVStreamNamespaceLink,
    Namespace,
    MediaType,
    AkaTitle,
    TorrentStream,
    EpisodeFile,
    SeriesSeason,
    Star,
    MediaStarLink,
    Language,
    TorrentLanguageLink,
    AnnounceURL,
    TorrentAnnounceLink,
    CatalogStreamStats,
    RSSFeed,
    RSSFeedCatalogPattern,
)

logger = logging.getLogger(__name__)

B = TypeVar("B", bound="CatalogBaseQueryBuilder")


class CatalogBaseQueryBuilder(ABC, Generic[B]):
    """Base class for query builders with common functionality"""

    def __init__(
        self,
        catalog_type: MediaType,
        user_data: UserData,
    ):
        self.catalog_type = catalog_type
        self.user_data = user_data
        self.base_query = select(BaseMetadata.id, BaseMetadata.title)

    def add_type_filter(self) -> B:
        """Add media type filter"""
        self.base_query = self.base_query.where(BaseMetadata.type == self.catalog_type)
        return self

    def add_content_filters(self) -> B:
        """Add user preference based content filters"""
        if self.catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
            specific_model = (
                MovieMetadata
                if self.catalog_type == MediaType.MOVIE
                else SeriesMetadata
            )
            self.base_query = self.base_query.join(specific_model)

            if "Disable" not in self.user_data.nudity_filter:
                self.base_query = self.base_query.where(
                    specific_model.parent_guide_nudity_status.notin_(
                        self.user_data.nudity_filter
                    )
                )

            if "Disable" not in self.user_data.certification_filter:
                # Use NOT EXISTS to filter out blocked certificates
                # This allows movies without any certificates to pass through
                blocked_cert_exists = (
                    select(MediaParentalCertificateLink.media_id)
                    .join(ParentalCertificate)
                    .where(
                        MediaParentalCertificateLink.media_id == BaseMetadata.id,
                        ParentalCertificate.name.in_(self.user_data.certification_filter),
                    )
                    .exists()
                )
                self.base_query = self.base_query.where(~blocked_cert_exists)
        return self

    def add_tv_filters(self, namespace: str) -> B:
        """Add TV-specific filters"""
        if self.catalog_type == MediaType.TV:
            self.base_query = (
                self.base_query.join(TVMetadata)
                .join(TVStream)
                .join(TVStreamNamespaceLink)
                .join(Namespace)
                .where(
                    TVStream.is_working == True,
                    Namespace.name.in_([namespace, "mediafusion", None]),
                )
            )
        return self

    def add_pagination(self, skip: int = 0, limit: int = 25) -> B:
        """Add pagination"""
        self.base_query = self.base_query.offset(skip).limit(limit)
        return self

    def build(self) -> Select:
        """Build the final query"""
        return self.base_query


class CatalogQueryBuilder(CatalogBaseQueryBuilder["CatalogQueryBuilder"]):
    """Builder for constructing optimized catalog queries"""

    def __init__(
        self, catalog_type: MediaType, user_data: UserData, is_watchlist: bool = False
    ):
        super().__init__(catalog_type, user_data)
        self.is_watchlist = is_watchlist

    def add_watchlist_filter(self, info_hashes: List[str]) -> "CatalogQueryBuilder":
        """Add watchlist-specific filters"""
        if self.is_watchlist and info_hashes:
            self.base_query = self.base_query.join(TorrentStream).where(
                TorrentStream.id.in_(info_hashes)
            )
        return self

    def add_catalog_filter(self, catalog_id: str) -> "CatalogQueryBuilder":
        """Add catalog-specific filters"""
        # TV uses namespaces for filtering, not catalog links
        if not self.is_watchlist and self.catalog_type != MediaType.TV:
            self.base_query = (
                self.base_query.join(MediaCatalogLink)
                .join(Catalog)
                .where(Catalog.name == catalog_id)
            )
        return self

    def add_genre_filter(self, genre: Optional[str]) -> "CatalogQueryBuilder":
        """Add genre-specific filters"""
        if genre:
            self.base_query = (
                self.base_query.join(MediaGenreLink)
                .join(Genre)
                .where(Genre.name == genre)
            )
        return self

    def add_sorting(self) -> "CatalogQueryBuilder":
        """Add default sorting"""
        self.base_query = self.base_query.order_by(
            BaseMetadata.last_stream_added.desc()
        )
        return self


class SearchQueryBuilder(CatalogBaseQueryBuilder["SearchQueryBuilder"]):
    """Builder for constructing optimized search queries"""

    def __init__(
        self,
        catalog_type: MediaType,
        user_data: UserData,
        search_query: str,
    ):
        super().__init__(catalog_type, user_data)
        # pg_trgm similarity is case-insensitive, so we keep original case for better matching
        self.search_query = search_query.strip()

    def add_text_search(self) -> "SearchQueryBuilder":
        """
        Optimized text search using indexed operations:
        1. Full-text search with @@ operator (uses GIN index on title_tsv)
        2. Trigram search with % operator (uses GIN index with gin_trgm_ops)
        
        Key optimizations:
        - Use % operator NOT similarity() > 0.3 - the % operator uses the GIN index!
        - similarity() function does full table scan, % operator uses index
        - Use UNION to deduplicate across search methods
        - Limit subquery results to avoid processing too many rows
        """
        search_vector = func.plainto_tsquery("simple", self.search_query.lower())
        
        # Phase 1: Full-text search matches (fastest - uses GIN index on title_tsv)
        fts_base_matches = (
            select(BaseMetadata.id)
            .where(
                BaseMetadata.title_tsv.op("@@")(search_vector),
                BaseMetadata.type == self.catalog_type,
            )
            .limit(200)
        )
        
        fts_aka_matches = (
            select(AkaTitle.media_id)
            .where(AkaTitle.title_tsv.op("@@")(search_vector))
            .limit(200)
        )
        
        # Phase 2: Trigram search using % operator (uses GIN index with gin_trgm_ops)
        # IMPORTANT: % operator uses the index, similarity() > 0.3 does NOT!
        # The % operator uses pg_trgm.similarity_threshold (default 0.3)
        trgm_base_matches = (
            select(BaseMetadata.id)
            .where(
                BaseMetadata.title.op("%")(self.search_query),
                BaseMetadata.type == self.catalog_type,
            )
            .limit(100)
        )
        
        # Combine matches using UNION (deduplicates results)
        all_matches = union(
            fts_base_matches,
            fts_aka_matches,
            trgm_base_matches
        ).subquery()

        # Filter main query by matching IDs and order by relevance
        self.base_query = self.base_query.filter(
            BaseMetadata.id.in_(select(all_matches.c.id))
        ).order_by(
            # Use FTS rank for ordering (fast, uses pre-computed tsvector)
            func.ts_rank_cd(BaseMetadata.title_tsv, search_vector).desc(),
            BaseMetadata.title
        )
        return self

    def add_torrent_stream_filter(self) -> "SearchQueryBuilder":
        """Add torrent stream specific filters using EXISTS for better performance"""
        if self.catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
            # Use EXISTS instead of JOIN for better performance with large tables
            stream_exists = (
                select(TorrentStream.id)
                .where(
                    TorrentStream.meta_id == BaseMetadata.id,
                    TorrentStream.is_blocked.is_(False),
                )
                .exists()
            )
            self.base_query = self.base_query.where(stream_exists)
        return self


async def get_catalog_meta_list(
    session: AsyncSession,
    catalog_type: MediaType,
    catalog_id: str,
    user_data: UserData,
    skip: int = 0,
    limit: int = 25,
    genre: Optional[str] = None,
    namespace: Optional[str] = None,
    is_watchlist_catalog: bool = False,
    info_hashes: Optional[List[str]] = None,
) -> public_schemas.Metas:
    """Get metadata list for catalog with efficient filtering and pagination"""
    query = (
        CatalogQueryBuilder(catalog_type, user_data, is_watchlist_catalog)
        .add_type_filter()
        .add_content_filters()
        .add_watchlist_filter(info_hashes)
        .add_catalog_filter(catalog_id)
        .add_genre_filter(genre)
        .add_tv_filters(namespace)
        .add_sorting()
        .add_pagination(skip, limit)
        .build()
    )

    result = await session.exec(query)
    data = result.unique().all()
    metas = [
        public_schemas.Meta(id=meta[0], name=meta[1], type=catalog_type)
        for meta in data
    ]
    return public_schemas.Metas(metas=metas)


async def get_mdblist_meta_list(
    session: AsyncSession,
    user_data: UserData,
    background_tasks: BackgroundTasks,
    list_config: schemas.MDBListItem,
    catalog_type: str,
    genre: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[public_schemas.Meta]:
    """Get a list of metadata entries from MDBList
    
    This function:
    1. Initializes the MDBList scraper with user's API key
    2. If not using filters, returns items directly from MDBList
    3. If using filters, fetches IMDb IDs and filters through PostgreSQL
       with user's parental guide filters applied
    4. Triggers background fetch for missing metadata
    """
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
            # Convert schemas.Meta to public_schemas.Meta
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
        # Determine specific model based on type
        specific_model = (
            MovieMetadata if media_type_enum == MediaType.MOVIE else SeriesMetadata
        )
        
        # Build base query
        query = (
            select(BaseMetadata.id, BaseMetadata.title)
            .where(
                BaseMetadata.id.in_(imdb_ids),
                BaseMetadata.type == media_type_enum,
                BaseMetadata.total_streams > 0,
            )
            .join(specific_model)
        )
        
        # Apply nudity filter
        if "Disable" not in user_data.nudity_filter:
            query = query.where(
                specific_model.parent_guide_nudity_status.notin_(
                    user_data.nudity_filter
                )
            )
        
        # Apply certification filter
        if "Disable" not in user_data.certification_filter:
            # Use NOT EXISTS to filter out blocked certificates
            blocked_cert_exists = (
                select(MediaParentalCertificateLink.media_id)
                .join(ParentalCertificate)
                .where(
                    MediaParentalCertificateLink.media_id == BaseMetadata.id,
                    ParentalCertificate.name.in_(user_data.certification_filter),
                )
                .exists()
            )
            query = query.where(~blocked_cert_exists)
        
        # Add sorting and pagination
        query = (
            query.order_by(BaseMetadata.last_stream_added.desc())
            .offset(skip)
            .limit(limit)
        )
        
        result = await session.exec(query)
        data = result.unique().all()
        
        if not data:
            # Check for missing metadata and trigger background fetch
            existing_query = (
                select(BaseMetadata.id)
                .where(BaseMetadata.id.in_(imdb_ids))
            )
            existing_result = await session.exec(existing_query)
            existing_ids = set(existing_result.all())
            missing_ids = list(set(imdb_ids) - existing_ids)
            
            if missing_ids:
                background_tasks.add_task(
                    fetch_metadata_batch,
                    missing_ids,
                    catalog_type,
                )
            return []
        
        # Convert to Meta objects
        metas = [
            public_schemas.Meta(id=meta[0], name=meta[1], type=media_type_enum)
            for meta in data
        ]
        return metas
    
    finally:
        await mdblist_scraper.close()


async def search_metadata(
    session: AsyncSession,
    catalog_type: MediaType,
    search_query: str,
    user_data: UserData,
    namespace: Optional[str] = None,
    limit: int = 50,
) -> public_schemas.Metas:
    """Search metadata with efficient filtering and ranking"""
    query = (
        SearchQueryBuilder(catalog_type, user_data, search_query)
        .add_type_filter()
        .add_text_search()
        .add_content_filters()
        .add_torrent_stream_filter()
        .add_tv_filters(namespace)
        .add_pagination(limit=limit)
        .build()
    )

    result = await session.exec(query)
    data = result.unique().all()
    metas = [
        public_schemas.Meta(id=meta[0], name=meta[1], type=catalog_type)
        for meta in data
    ]
    return public_schemas.Metas(metas=metas)


T = TypeVar("T", data_models.MovieData, data_models.SeriesData, data_models.TVData)
M = TypeVar("M", MovieMetadata, SeriesMetadata, TVMetadata)


class MetadataRetriever(Generic[T, M]):
    """Generic class for retrieving metadata with caching support"""

    def __init__(
        self,
        data_model: Type[T],
        sql_model: Type[M],
        media_type: MediaType,
        cache_prefix: str,
    ):
        self.data_model = data_model
        self.sql_model = sql_model
        self.media_type = media_type
        self.cache_prefix = cache_prefix

    async def _get_from_cache(self, media_id: str) -> Optional[T]:
        """Retrieve metadata from cache"""
        cached_data = await REDIS_ASYNC_CLIENT.get(f"{self.cache_prefix}:{media_id}")
        if cached_data:
            try:
                return self.data_model.model_validate_json(cached_data)
            except Exception as e:
                logger.error(f"Error deserializing cached data for {media_id}: {e}")
        return None

    async def _set_cache(self, media_id: str, data: T) -> None:
        """Store metadata in cache"""
        try:
            await REDIS_ASYNC_CLIENT.set(
                f"{self.cache_prefix}:{media_id}",
                data.model_dump_json(exclude_none=True),
                ex=86400,  # 24 hours
            )
        except Exception as e:
            logger.error(f"Error caching data for {media_id}: {e}")

    async def _fetch_base_metadata(
        self, session: AsyncSession, media_id: str
    ) -> Optional[BaseMetadata]:
        """Fetch base metadata for a media item"""
        query = (
            select(BaseMetadata)
            .where(BaseMetadata.id == media_id)
            .options(
                selectinload(BaseMetadata.genres),
                selectinload(BaseMetadata.catalogs),
                selectinload(BaseMetadata.aka_titles),
            )
        )

        result = await session.exec(query)
        return result.one_or_none()

    async def _fetch_media_type_metadata(
        self, session: AsyncSession, media_id: str
    ) -> Optional[M]:
        """Fetch type-specific metadata"""

        if self.media_type in [MediaType.MOVIE, MediaType.SERIES]:
            query = (
                select(self.sql_model)
                .where(self.sql_model.id == media_id)
                .options(
                    selectinload(self.sql_model.parental_certificates),
                    selectinload(self.sql_model.stars),
                )
            )
        else:
            # TV metadata doesn't have parental_certificates or stars relationships
            query = select(self.sql_model).where(self.sql_model.id == media_id)

        result = await session.exec(query)
        return result.one_or_none()

    async def get_metadata(
        self, session: AsyncSession, media_id: str, bypass_cache: bool = False
    ) -> Optional[T]:
        """Main method to retrieve metadata with caching"""
        if not bypass_cache:
            cached_data = await self._get_from_cache(media_id)
            if cached_data:
                return cached_data

        # Fetch base metadata
        base_metadata = await self._fetch_base_metadata(session, media_id)
        if not base_metadata:
            return None

        media_metadata = await self._fetch_media_type_metadata(session, media_id)
        if not media_metadata:
            return None

        # Construct the full metadata object
        model_metadata = self.data_model.model_validate(media_metadata)

        await self._set_cache(media_id, model_metadata)
        return model_metadata


class SeriesMetadataRetriever(
    MetadataRetriever[data_models.SeriesData, SeriesMetadata]
):
    """Series-specific metadata retriever with episode information"""

    async def _fetch_media_type_metadata(self, session: AsyncSession, media_id: str):
        """Fetch type-specific metadata with seasons and episodes"""
        query = (
            select(self.sql_model)
            .where(self.sql_model.id == media_id)
            .options(
                selectinload(self.sql_model.parental_certificates),
                selectinload(self.sql_model.stars),
                subqueryload(self.sql_model.seasons).options(
                    subqueryload(sql_models.SeriesSeason.episodes)
                ),
            )
        )

        result = await session.exec(query)
        return result.one_or_none()

    async def _fetch_seasons_with_episodes(
        self, session: AsyncSession, series_id: str
    ) -> List[data_models.SeriesSeasonData]:
        """Fetch all seasons and episodes for a series with stream counts"""
        # First, get all seasons with their episodes
        season_query = (
            select(sql_models.SeriesSeason)
            .where(sql_models.SeriesSeason.series_id == series_id)
            .options(selectinload(sql_models.SeriesSeason.episodes))
            .order_by(
                sql_models.SeriesSeason.season_number,
            )
        )

        result = await session.exec(season_query)
        return result.all()

    async def get_episode_streams(
        self,
        session: AsyncSession,
        series_id: str,
        season_number: int,
        episode_number: int,
    ) -> List[dict]:
        """Get available streams for a specific episode"""
        query = (
            select(TorrentStream)
            .where(
                TorrentStream.meta_id == series_id, TorrentStream.is_blocked == False
            )
            .join(sql_models.EpisodeFile)
            .where(
                sql_models.EpisodeFile.season_number == season_number,
                sql_models.EpisodeFile.episode_number == episode_number,
            )
            .options(
                selectinload(TorrentStream.languages),
                selectinload(TorrentStream.episode_files).where(
                    sql_models.EpisodeFile.season_number == season_number,
                    sql_models.EpisodeFile.episode_number == episode_number,
                ),
            )
        )

        result = await session.exec(query)
        streams = result.unique().all()
        return [stream.to_dict() for stream in streams]


# Initialize retrievers
movie_metadata = MetadataRetriever(
    data_models.MovieData, MovieMetadata, MediaType.MOVIE, "movie_data"
)

series_metadata = SeriesMetadataRetriever(
    data_models.SeriesData, SeriesMetadata, MediaType.SERIES, "series_data"
)

tv_metadata = MetadataRetriever(data_models.TVData, TVMetadata, MediaType.TV, "tv_data")


async def get_metadata_by_type(
    session: AsyncSession,
    media_type: MediaType,
    media_id: str,
    bypass_cache: bool = False,
) -> data_models.MovieData | data_models.SeriesData | data_models.TVData | None:
    """Factory function to get metadata based on media type"""
    retrievers = {
        MediaType.MOVIE: movie_metadata,
        MediaType.SERIES: series_metadata,
        MediaType.TV: tv_metadata,
    }

    retriever = retrievers.get(media_type)
    if not retriever:
        raise ValueError(f"Unsupported media type: {media_type}")

    return await retriever.get_metadata(session, media_id, bypass_cache)


# =============================================================================
# Core Read Operations
# =============================================================================


async def get_movie_data_by_id(
    session: AsyncSession, movie_id: str, load_relations: bool = True, use_cache: bool = True
) -> Optional[MovieMetadata]:
    """Get movie metadata by ID with optional caching
    
    Args:
        session: Database session
        movie_id: Movie ID to fetch
        load_relations: If True, eagerly load all relations. If False, minimal query.
        use_cache: If True, check Redis cache first (for existence check only)
    """
    # Check cache for quick existence check (matches MongoDB behavior)
    cache_key = f"movie_exists:{movie_id}"
    if use_cache and not load_relations:
        cached = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached == "0":  # Cached as "not found"
            return None
    
    if load_relations:
        query = (
            select(MovieMetadata)
            .where(MovieMetadata.id == movie_id)
            .options(
                selectinload(MovieMetadata.base_metadata).options(
                    selectinload(BaseMetadata.genres),
                    selectinload(BaseMetadata.catalogs),
                    selectinload(BaseMetadata.aka_titles),
                ),
                selectinload(MovieMetadata.parental_certificates),
                selectinload(MovieMetadata.stars),
            )
        )
        result = await session.exec(query)
        return result.one_or_none()
    else:
        # Minimal query - use joinedload for single JOIN
        query = (
            select(MovieMetadata)
            .where(MovieMetadata.id == movie_id)
            .options(joinedload(MovieMetadata.base_metadata))
        )
        result = await session.exec(query)
        movie = result.unique().one_or_none()
        
        # Cache the existence result
        if use_cache:
            await REDIS_ASYNC_CLIENT.set(cache_key, "1" if movie else "0", ex=3600)
        
        return movie


async def get_series_data_by_id(
    session: AsyncSession, series_id: str, load_relations: bool = True, use_cache: bool = True
) -> Optional[SeriesMetadata]:
    """Get series metadata by ID with seasons and episodes
    
    Args:
        session: Database session
        series_id: Series ID to fetch
        load_relations: If True, eagerly load all relations. If False, minimal query.
        use_cache: If True, check Redis cache first (for existence check only)
    """
    # Check cache for quick existence check (matches movie behavior)
    cache_key = f"series_exists:{series_id}"
    if use_cache and not load_relations:
        cached = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached == "0":  # Cached as "not found"
            return None
    
    if load_relations:
        query = (
            select(SeriesMetadata)
            .where(SeriesMetadata.id == series_id)
            .options(
                selectinload(SeriesMetadata.base_metadata).options(
                    selectinload(BaseMetadata.genres),
                    selectinload(BaseMetadata.catalogs),
                    selectinload(BaseMetadata.aka_titles),
                ),
                selectinload(SeriesMetadata.parental_certificates),
                selectinload(SeriesMetadata.stars),
                subqueryload(SeriesMetadata.seasons).options(
                    subqueryload(SeriesSeason.episodes)
                ),
            )
        )
        result = await session.exec(query)
        return result.one_or_none()
    else:
        # Minimal query - use joinedload for single JOIN
        query = (
            select(SeriesMetadata)
            .where(SeriesMetadata.id == series_id)
            .options(joinedload(SeriesMetadata.base_metadata))
        )
        result = await session.exec(query)
        series = result.unique().one_or_none()
        
        # Cache the existence result
        if use_cache:
            await REDIS_ASYNC_CLIENT.set(cache_key, "1" if series else "0", ex=3600)
        
        return series


async def get_tv_data_by_id(
    session: AsyncSession, tv_id: str, use_cache: bool = True
) -> Optional[TVMetadata]:
    """Get TV metadata by ID
    
    Args:
        session: Database session
        tv_id: TV metadata ID to fetch
        use_cache: If True, check Redis cache first for existence
    """
    # Check cache for quick existence check
    cache_key = f"tv_exists:{tv_id}"
    if use_cache:
        cached = await REDIS_ASYNC_CLIENT.get(cache_key)
        if cached == "0":  # Cached as "not found"
            return None
    
    query = (
        select(TVMetadata)
        .where(TVMetadata.id == tv_id)
        .options(
            selectinload(TVMetadata.base_metadata).options(
                selectinload(BaseMetadata.genres),
                selectinload(BaseMetadata.catalogs),
            )
        )
    )
    result = await session.exec(query)
    tv_data = result.one_or_none()
    
    # Cache the existence result
    if use_cache:
        await REDIS_ASYNC_CLIENT.set(cache_key, "1" if tv_data else "0", ex=3600)
    
    return tv_data


async def get_stream_by_info_hash(
    session: AsyncSession, info_hash: str, load_relations: bool = False
) -> Optional[TorrentStream]:
    """Get torrent stream by info hash (ID)
    
    Args:
        session: Database session
        info_hash: Torrent info hash (ID)
        load_relations: If True, load languages, announce_urls, episode_files
    """
    if load_relations:
        query = (
            select(TorrentStream)
            .where(TorrentStream.id == info_hash.lower())
            .options(
                selectinload(TorrentStream.languages),
                selectinload(TorrentStream.announce_urls),
                selectinload(TorrentStream.episode_files),
            )
        )
    else:
        # Simple query - faster for existence checks and basic info
        query = select(TorrentStream).where(TorrentStream.id == info_hash.lower())
    
    result = await session.exec(query)
    return result.one_or_none()


async def is_torrent_stream_exists(session: AsyncSession, info_hash: str) -> bool:
    """Check if a torrent stream exists - optimized query"""
    # Use exists() subquery for fastest possible check
    query = select(sa_exists().where(TorrentStream.id == info_hash.lower()))
    result = await session.exec(query)
    return result.one()


async def get_cached_torrent_streams(
    session: AsyncSession,
    video_id: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    cache_key: Optional[str] = None,
) -> List[TorrentStream]:
    """Get torrent streams for a video with optional season/episode filtering and Redis caching"""
    # Generate cache key if not provided
    if cache_key is None:
        cache_key_parts = ["torrent_streams", video_id]
        if season is not None and episode is not None:
            cache_key_parts.extend([str(season), str(episode)])
        cache_key = ":".join(cache_key_parts)
    
    # Try Redis cache first
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_data is not None:
        try:
            # Return cached stream IDs and fetch fresh from DB
            cached_ids = json.loads(cached_data)
            if cached_ids:
                query = (
                    select(TorrentStream)
                    .where(TorrentStream.id.in_(cached_ids))
                    .options(
                        selectinload(TorrentStream.languages),
                        selectinload(TorrentStream.announce_urls),
                        selectinload(TorrentStream.episode_files),
                    )
                )
                result = await session.exec(query)
                return list(result.unique().all())
            return []
        except (json.JSONDecodeError, TypeError):
            pass  # Fall through to DB query
    
    # Query from database
    query = (
        select(TorrentStream)
        .where(TorrentStream.meta_id == video_id, TorrentStream.is_blocked == False)
        .options(
            selectinload(TorrentStream.languages),
            selectinload(TorrentStream.announce_urls),
            selectinload(TorrentStream.episode_files),
        )
    )

    if season is not None and episode is not None:
        # Filter by episode files
        query = query.join(EpisodeFile).where(
            EpisodeFile.season_number == season, EpisodeFile.episode_number == episode
        )

    result = await session.exec(query)
    streams = list(result.unique().all())
    
    # Cache stream IDs for 30 minutes
    if streams:
        stream_ids = [s.id for s in streams]
        await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(stream_ids), ex=1800)
    
    return streams


# =============================================================================
# Save/Create Operations
# =============================================================================


async def get_or_create_genre(session: AsyncSession, name: str) -> Genre:
    """Get or create a genre by name"""
    query = select(Genre).where(Genre.name == name)
    result = await session.exec(query)
    genre = result.one_or_none()

    if not genre:
        genre = Genre(name=name)
        session.add(genre)
        await session.flush()

    return genre


async def get_or_create_catalog(session: AsyncSession, name: str) -> Catalog:
    """Get or create a catalog by name with caching"""
    # Check cache first
    cache_key = f"catalog:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(Catalog).where(Catalog.id == int(cached_id))
        result = await session.exec(query)
        catalog = result.one_or_none()
        if catalog:
            return catalog
    
    query = select(Catalog).where(Catalog.name == name)
    result = await session.exec(query)
    catalog = result.one_or_none()

    if not catalog:
        catalog = Catalog(name=name)
        session.add(catalog)
        await session.flush()
    
    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(catalog.id), ex=86400)  # 24h

    return catalog


async def get_or_create_star(session: AsyncSession, name: str) -> Star:
    """Get or create a star by name"""
    query = select(Star).where(Star.name == name)
    result = await session.exec(query)
    star = result.one_or_none()

    if not star:
        star = Star(name=name)
        session.add(star)
        await session.flush()

    return star


async def get_or_create_parental_certificate(
    session: AsyncSession, name: str
) -> ParentalCertificate:
    """Get or create a parental certificate by name"""
    query = select(ParentalCertificate).where(ParentalCertificate.name == name)
    result = await session.exec(query)
    cert = result.one_or_none()

    if not cert:
        cert = ParentalCertificate(name=name)
        session.add(cert)
        await session.flush()

    return cert


async def get_or_create_language(session: AsyncSession, name: str) -> Language:
    """Get or create a language by name with caching"""
    # Check cache first
    cache_key = f"lang:{name}"
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(Language).where(Language.id == int(cached_id))
        result = await session.exec(query)
        lang = result.one_or_none()
        if lang:
            return lang
    
    query = select(Language).where(Language.name == name)
    result = await session.exec(query)
    lang = result.one_or_none()

    if not lang:
        lang = Language(name=name)
        session.add(lang)
        await session.flush()
    
    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(lang.id), ex=86400)  # 24h

    return lang


async def get_or_create_announce_url(session: AsyncSession, url: str) -> AnnounceURL:
    """Get or create an announce URL with caching"""
    # Check cache first
    cache_key = f"announce:{url[:50]}"  # Truncate long URLs for cache key
    cached_id = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached_id:
        query = select(AnnounceURL).where(AnnounceURL.id == int(cached_id))
        result = await session.exec(query)
        announce = result.one_or_none()
        if announce:
            return announce
    
    query = select(AnnounceURL).where(AnnounceURL.name == url)
    result = await session.exec(query)
    announce = result.one_or_none()

    if not announce:
        announce = AnnounceURL(name=url)
        session.add(announce)
        await session.flush()
    
    # Cache the ID
    await REDIS_ASYNC_CLIENT.set(cache_key, str(announce.id), ex=86400)  # 24h

    return announce


async def get_or_create_namespace(session: AsyncSession, name: str) -> Namespace:
    """Get or create a namespace by name"""
    query = select(Namespace).where(Namespace.name == name)
    result = await session.exec(query)
    ns = result.one_or_none()

    if not ns:
        ns = Namespace(name=name)
        session.add(ns)
        await session.flush()

    return ns


async def get_existing_metadata(
    session: AsyncSession, metadata: dict
) -> Optional[BaseMetadata]:
    """Get existing metadata by ID or title/year"""
    # Try by ID first
    if metadata.get("id"):
        query = select(BaseMetadata).where(BaseMetadata.id == metadata["id"])
        result = await session.exec(query)
        existing = result.one_or_none()
        if existing:
            return existing

    # Try by title/year
    if metadata.get("title") and metadata.get("year"):
        query = select(BaseMetadata).where(
            BaseMetadata.title == metadata["title"],
            BaseMetadata.year == metadata["year"],
        )
        result = await session.exec(query)
        return result.one_or_none()

    return None


async def save_base_metadata(
    session: AsyncSession,
    metadata: dict,
    media_type: MediaType,
) -> BaseMetadata:
    """Save or update base metadata"""
    base_data = BaseMetadata(
        id=metadata["id"],
        type=media_type,
        title=metadata["title"],
        year=metadata.get("year"),
        poster=metadata.get("poster"),
        is_poster_working=metadata.get("is_poster_working", True),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", False),
        background=metadata.get("background"),
        description=metadata.get("description"),
        runtime=metadata.get("runtime"),
        website=metadata.get("website"),
        last_stream_added=metadata.get(
            "last_stream_added", datetime.now(pytz.UTC)
        ),
        total_streams=metadata.get("total_streams", 0),
    )

    # Merge to handle upsert
    merged = await session.merge(base_data)
    await session.flush()

    # Handle genres
    if metadata.get("genres"):
        # Clear existing genre links
        await session.exec(
            sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == merged.id)
        )
        for genre_name in metadata["genres"]:
            genre = await get_or_create_genre(session, genre_name)
            link = MediaGenreLink(media_id=merged.id, genre_id=genre.id)
            session.add(link)

    # Handle catalogs
    if metadata.get("catalogs"):
        await session.exec(
            sa_delete(MediaCatalogLink).where(MediaCatalogLink.media_id == merged.id)
        )
        for catalog_name in metadata["catalogs"]:
            catalog = await get_or_create_catalog(session, catalog_name)
            link = MediaCatalogLink(media_id=merged.id, catalog_id=catalog.id)
            session.add(link)

    # Handle aka_titles
    if metadata.get("aka_titles"):
        await session.exec(
            sa_delete(AkaTitle).where(AkaTitle.media_id == merged.id)
        )
        for title in metadata["aka_titles"]:
            aka = AkaTitle(title=title, media_id=merged.id)
            session.add(aka)

    await session.flush()
    return merged


async def save_movie_metadata(
    session: AsyncSession,
    metadata: dict,
) -> MovieMetadata:
    """Save or update movie metadata"""
    # First save base metadata
    base = await save_base_metadata(session, metadata, MediaType.MOVIE)

    # Create movie-specific metadata
    movie = MovieMetadata(
        id=base.id,
        imdb_rating=metadata.get("imdb_rating"),
        tmdb_rating=metadata.get("tmdb_rating"),
        parent_guide_nudity_status=metadata.get("parent_guide_nudity_status", "UNKNOWN"),
    )
    merged = await session.merge(movie)
    await session.flush()

    # Handle stars
    if metadata.get("stars"):
        await session.exec(
            sa_delete(MediaStarLink).where(MediaStarLink.media_id == merged.id)
        )
        for star_name in metadata["stars"]:
            star = await get_or_create_star(session, star_name)
            link = MediaStarLink(media_id=merged.id, star_id=star.id)
            session.add(link)

    # Handle parental certificates
    if metadata.get("parent_guide_certificates"):
        await session.exec(
            sa_delete(MediaParentalCertificateLink).where(
                MediaParentalCertificateLink.media_id == merged.id
            )
        )
        for cert_name in metadata["parent_guide_certificates"]:
            cert = await get_or_create_parental_certificate(session, cert_name)
            link = MediaParentalCertificateLink(
                media_id=merged.id, certificate_id=cert.id
            )
            session.add(link)

    await session.commit()
    return merged


async def save_series_metadata(
    session: AsyncSession,
    metadata: dict,
) -> SeriesMetadata:
    """Save or update series metadata"""
    base = await save_base_metadata(session, metadata, MediaType.SERIES)

    series = SeriesMetadata(
        id=base.id,
        end_year=metadata.get("end_year"),
        imdb_rating=metadata.get("imdb_rating"),
        tmdb_rating=metadata.get("tmdb_rating"),
        parent_guide_nudity_status=metadata.get("parent_guide_nudity_status", "UNKNOWN"),
    )
    merged = await session.merge(series)
    await session.flush()

    # Handle stars
    if metadata.get("stars"):
        await session.exec(
            sa_delete(MediaStarLink).where(MediaStarLink.media_id == merged.id)
        )
        for star_name in metadata["stars"]:
            star = await get_or_create_star(session, star_name)
            link = MediaStarLink(media_id=merged.id, star_id=star.id)
            session.add(link)

    # Handle parental certificates
    if metadata.get("parent_guide_certificates"):
        await session.exec(
            sa_delete(MediaParentalCertificateLink).where(
                MediaParentalCertificateLink.media_id == merged.id
            )
        )
        for cert_name in metadata["parent_guide_certificates"]:
            cert = await get_or_create_parental_certificate(session, cert_name)
            link = MediaParentalCertificateLink(
                media_id=merged.id, certificate_id=cert.id
            )
            session.add(link)

    await session.commit()
    return merged


async def save_tv_channel_metadata(
    session: AsyncSession,
    tv_metadata: schemas.TVMetaData,
) -> str:
    """Save or update TV channel metadata"""
    metadata = tv_metadata.model_dump()
    base = await save_base_metadata(session, metadata, MediaType.TV)

    tv = TVMetadata(
        id=base.id,
        country=metadata.get("country"),
        tv_language=metadata.get("tv_language"),
        logo=metadata.get("logo"),
    )
    merged = await session.merge(tv)
    await session.commit()
    return merged.id


# =============================================================================
# Stream Operations
# =============================================================================


async def update_torrent_seeders(
    session: AsyncSession,
    info_hash: str,
    seeders: int,
) -> bool:
    """Update seeders count for a torrent stream"""
    result = await session.exec(
        select(TorrentStream).where(TorrentStream.id == info_hash.lower())
    )
    torrent = result.first()
    if torrent:
        torrent.seeders = seeders
        session.add(torrent)
        await session.commit()
        return True
    return False


async def store_new_torrent_streams(
    session: AsyncSession,
    streams: List[dict],
) -> List[TorrentStream]:
    """Store new torrent streams with their relationships"""
    stored_streams = []

    for stream_data in streams:
        # Handle both 'id' and '_id' keys (MongoDB uses _id internally)
        info_hash = (stream_data.get("id") or stream_data.get("_id") or "").lower()
        if not info_hash:
            logger.warning(f"Skipping stream with no id: {stream_data.get('torrent_name', 'unknown')}")
            continue

        # Check if stream exists
        if await is_torrent_stream_exists(session, info_hash):
            continue

        # Create torrent stream
        # Handle audio field - convert list to string if needed
        audio_value = stream_data.get("audio")
        if isinstance(audio_value, list):
            audio_value = ", ".join(audio_value) if audio_value else None
        
        torrent = TorrentStream(
            id=info_hash,
            meta_id=stream_data["meta_id"],
            torrent_name=stream_data["torrent_name"],
            size=stream_data["size"],
            source=stream_data["source"],
            resolution=stream_data.get("resolution"),
            codec=stream_data.get("codec"),
            quality=stream_data.get("quality"),
            audio=audio_value,
            seeders=stream_data.get("seeders"),
            is_blocked=stream_data.get("is_blocked", False),
            torrent_type=stream_data.get("torrent_type", TorrentType.PUBLIC),
            uploader=stream_data.get("uploader"),
            uploaded_at=stream_data.get("uploaded_at"),
            hdr=stream_data.get("hdr"),
            torrent_file=stream_data.get("torrent_file"),
            filename=stream_data.get("filename"),
            file_index=stream_data.get("file_index"),
        )
        session.add(torrent)

        # Add languages
        for lang_name in stream_data.get("languages", []):
            lang = await get_or_create_language(session, lang_name)
            link = TorrentLanguageLink(torrent_id=info_hash, language_id=lang.id)
            session.add(link)

        # Add announce URLs
        for url in stream_data.get("announce_urls", []):
            announce = await get_or_create_announce_url(session, url)
            link = TorrentAnnounceLink(torrent_id=info_hash, announce_id=announce.id)
            session.add(link)

        # Add episode files for series
        for ep in stream_data.get("episode_files", []):
            episode_file = EpisodeFile(
                torrent_stream_id=info_hash,
                season_number=ep["season_number"],
                episode_number=ep["episode_number"],
                file_index=ep.get("file_index"),
                filename=ep.get("filename"),
                size=ep.get("size"),
            )
            session.add(episode_file)

        stored_streams.append(torrent)

        # Update catalog stats
        await update_catalog_stats_on_stream_add(
            session,
            stream_data["meta_id"],
            stream_data.get("catalogs", []),
            torrent.created_at,
        )

    await session.commit()
    return stored_streams


async def get_movie_streams(
    session: AsyncSession,
    video_id: str,
    user_data: UserData,
    secret_str: str,
    user_ip: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> List[Stream]:
    """Get formatted streams for a movie
    
    This is the complete implementation that:
    1. Handles streaming provider deletion
    2. Fetches movie metadata
    3. Validates content (nudity, parental)
    4. Gets cached torrent streams
    5. Integrates with live scrapers when enabled
    6. Parses and formats streams for Stremio
    """
    from utils.parser import (
        parse_stream_data,
        create_exception_stream,
        create_content_warning_message,
    )
    from utils.const import USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS
    from utils.lock import acquire_redis_lock, release_redis_lock
    from db.schemas import MetadataData
    
    # Handle special case for streaming provider deletion
    if video_id.startswith("dl"):
        if not user_data.streaming_provider or not video_id.endswith(user_data.streaming_provider.service):
            return []
        return [
            Stream(
                name=f"MediaFusion {user_data.streaming_provider.service.title()} 🗑️💩",
                description="🚨💀⚠️\nDelete all files in streaming provider",
                url=f"{settings.host_url}/streaming_provider/{secret_str}/delete_all",
            )
        ]
    
    # Get metadata
    metadata = await get_movie_data_by_id(session, video_id, load_relations=True)
    
    if not metadata:
        return []
    
    # Validate content
    if not _validate_parent_guide_nudity(metadata, user_data):
        return [
            create_exception_stream(
                settings.addon_name,
                create_content_warning_message(metadata),
                "inappropriate_content.mp4",
            )
        ]
    
    # Create user feeds for supported catalogs (for custom MF IDs)
    user_feeds = []
    if video_id.startswith("mf") and metadata.base_metadata:
        # Check if this metadata has streams in user-uploadable catalogs
        catalog_names = [c.name for c in metadata.base_metadata.catalogs] if metadata.base_metadata.catalogs else []
        if any(cat in USER_UPLOAD_SUPPORTED_MOVIE_CATALOG_IDS for cat in catalog_names):
            user_feeds = [
                Stream(
                    name=settings.addon_name,
                    description=f"🔄 Migrate {video_id} to IMDb ID",
                    externalUrl=f"{settings.host_url}/scraper/?action=migrate_id&mediafusion_id={video_id}&meta_type=movie",
                )
            ]
    
    # Handle stream caching and live search
    live_search_streams = user_data.live_search_streams and video_id.startswith("tt")
    cache_key = f"torrent_streams:{video_id}"
    lock_key = f"{cache_key}_lock" if live_search_streams else None
    redis_lock = None
    
    if lock_key:
        _, redis_lock = await acquire_redis_lock(lock_key, timeout=60, block=True)
    
    try:
        # Get cached torrent streams
        pg_streams = await get_cached_torrent_streams(session, video_id, cache_key=cache_key)
        
        # Convert to adapter format
        cached_streams = [TorrentStreamData.from_pg_model(s) for s in pg_streams]
        
        # Handle live search and stream updates
        if live_search_streams and background_tasks:
            from scrapers.scraper_tasks import run_scrapers
            
            # Convert metadata for scraper compatibility
            scraper_metadata = MetadataData.from_pg_movie(metadata)
            
            new_streams = await run_scrapers(
                user_data=user_data,
                metadata=scraper_metadata,
                catalog_type="movie",
            )
            
            # Merge cached and new streams (dedup by id)
            existing_ids = {s.id for s in cached_streams}
            all_streams = cached_streams + [s for s in new_streams if s.id not in existing_ids]
            
            if new_streams:
                # Invalidate cache so new streams are included next time
                await REDIS_ASYNC_CLIENT.delete(cache_key)
                # Store new streams in background
                background_tasks.add_task(
                    _store_new_streams_background,
                    session,
                    list(new_streams),
                    redis_lock,
                )
                redis_lock = None  # Don't release in finally, background task will
        else:
            all_streams = cached_streams
        
        if not all_streams:
            return user_feeds
        
        # Parse and return streams
        parsed_results = await parse_stream_data(
            all_streams,
            user_data,
            secret_str,
            user_ip=user_ip,
            is_series=False,
        )
        return parsed_results + user_feeds
    finally:
        if redis_lock:
            await release_redis_lock(redis_lock)


async def get_series_streams(
    session: AsyncSession,
    video_id: str,
    season: int,
    episode: int,
    user_data: UserData,
    secret_str: str,
    user_ip: Optional[str] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> List[Stream]:
    """Get formatted streams for a series episode
    
    This is the complete implementation that:
    1. Handles streaming provider deletion
    2. Fetches series metadata
    3. Validates content (nudity, parental)
    4. Gets cached torrent streams for the episode
    5. Integrates with live scrapers when enabled
    6. Parses and formats streams for Stremio
    """
    from utils.parser import (
        parse_stream_data,
        create_exception_stream,
        create_content_warning_message,
    )
    from utils.const import USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS
    from utils.lock import acquire_redis_lock, release_redis_lock
    from db.schemas import MetadataData
    
    # Handle special case for streaming provider deletion
    if video_id.startswith("dl"):
        if not user_data.streaming_provider or not video_id.endswith(user_data.streaming_provider.service):
            return []
        return [
            Stream(
                name=f"MediaFusion {user_data.streaming_provider.service.title()} 🗑️💩",
                description="🚨💀⚠️\nDelete all files in streaming provider",
                url=f"{settings.host_url}/streaming_provider/{secret_str}/delete_all",
            )
        ]
    
    # Get metadata
    metadata = await get_series_data_by_id(session, video_id, load_relations=True)
    
    if not metadata:
        return []
    
    # Validate content
    if not _validate_parent_guide_nudity(metadata, user_data):
        return [
            create_exception_stream(
                settings.addon_name,
                create_content_warning_message(metadata),
                "inappropriate_content.mp4",
            )
        ]
    
    # Create user feeds for supported catalogs (for custom MF IDs)
    user_feeds = []
    if video_id.startswith("mf") and metadata.base_metadata:
        # Check if this metadata has streams in user-uploadable catalogs
        catalog_names = [c.name for c in metadata.base_metadata.catalogs] if metadata.base_metadata.catalogs else []
        if any(cat in USER_UPLOAD_SUPPORTED_SERIES_CATALOG_IDS for cat in catalog_names):
            user_feeds = [
                Stream(
                    name=settings.addon_name,
                    description=f"🔄 Migrate {video_id} to IMDb ID",
                    externalUrl=f"{settings.host_url}/scraper/?action=migrate_id&mediafusion_id={video_id}&meta_type=series",
                )
            ]
    
    # Handle stream caching and live search
    live_search_streams = user_data.live_search_streams and video_id.startswith("tt")
    cache_key = f"torrent_streams:{video_id}:{season}:{episode}"
    lock_key = f"{cache_key}_lock" if live_search_streams else None
    redis_lock = None
    
    if lock_key:
        _, redis_lock = await acquire_redis_lock(lock_key, timeout=60, block=True)
    
    try:
        # Get cached torrent streams for this episode
        pg_streams = await get_cached_torrent_streams(session, video_id, season, episode, cache_key=cache_key)
        
        # Convert to adapter format
        cached_streams = [TorrentStreamData.from_pg_model(s) for s in pg_streams]
        
        # Handle live search and stream updates
        if live_search_streams and background_tasks:
            from scrapers.scraper_tasks import run_scrapers
            
            # Convert metadata for scraper compatibility
            scraper_metadata = MetadataData.from_pg_series(metadata)
            
            new_streams = await run_scrapers(
                user_data=user_data,
                metadata=scraper_metadata,
                catalog_type="series",
                season=season,
                episode=episode,
            )
            
            # Merge cached and new streams (dedup by id)
            existing_ids = {s.id for s in cached_streams}
            all_streams = cached_streams + [s for s in new_streams if s.id not in existing_ids]
            
            if new_streams:
                # Invalidate cache so new streams are included next time
                await REDIS_ASYNC_CLIENT.delete(cache_key)
                # Store new streams in background
                background_tasks.add_task(
                    _store_new_streams_background,
                    session,
                    list(new_streams),
                    redis_lock,
                )
                redis_lock = None  # Don't release in finally, background task will
        else:
            all_streams = cached_streams
        
        if not all_streams:
            return user_feeds
        
        # Parse and return streams
        parsed_results = await parse_stream_data(
            all_streams,
            user_data,
            secret_str,
            season=season,
            episode=episode,
            user_ip=user_ip,
            is_series=True,
        )
        return parsed_results + user_feeds
    finally:
        if redis_lock:
            await release_redis_lock(redis_lock)


async def _store_new_streams_background(
    session: AsyncSession,
    streams: List[TorrentStreamData],
    redis_lock = None,
) -> None:
    """Background task to store new streams and release Redis lock
    
    This function is called as a background task to:
    1. Convert TorrentStreamData to dicts for storage
    2. Store new streams in the database
    3. Release the Redis lock when done
    """
    from utils.lock import release_redis_lock
    from db.database import get_background_session
    
    try:
        if not streams:
            return
        
        # Need a fresh session for background task
        async with get_background_session() as bg_session:
            # Convert TorrentStreamData to dicts for store_new_torrent_streams
            stream_dicts = []
            for stream in streams:
                stream_dict = {
                    "id": stream.id,
                    "meta_id": stream.meta_id,
                    "torrent_name": stream.torrent_name,
                    "size": stream.size,
                    "source": stream.source,
                    "resolution": stream.resolution,
                    "codec": stream.codec,
                    "quality": stream.quality,
                    "audio": stream.audio,
                    "seeders": stream.seeders,
                    "uploader": stream.uploader,
                    "uploaded_at": stream.uploaded_at,
                    "hdr": stream.hdr,
                    "torrent_file": stream.torrent_file,
                    "torrent_type": stream.torrent_type,
                    "filename": stream.filename,
                    "file_index": stream.file_index,
                    "languages": stream.languages,
                    "announce_urls": stream.announce_list,
                    "episode_files": [
                        {
                            "season_number": ef.season_number,
                            "episode_number": ef.episode_number,
                            "filename": ef.filename,
                            "size": ef.size,
                            "file_index": ef.file_index,
                        }
                        for ef in stream.episode_files
                    ] if stream.episode_files else [],
                    "catalogs": stream.catalog,
                }
                stream_dicts.append(stream_dict)
            
            await store_new_torrent_streams(bg_session, stream_dicts)
            logger.info(f"Stored {len(stream_dicts)} new streams from live search")
    except Exception as e:
        logger.error(f"Error storing new streams in background: {e}")
    finally:
        if redis_lock:
            await release_redis_lock(redis_lock)


async def get_tv_streams_formatted(
    session: AsyncSession,
    video_id: str,
    namespace: str,
    user_data: UserData,
) -> List[Stream]:
    """Get formatted TV streams for a channel"""
    from utils.parser import parse_tv_stream_data
    
    query = (
        select(TVStream)
        .where(TVStream.meta_id == video_id, TVStream.is_working == True)
        .join(TVStreamNamespaceLink)
        .join(Namespace)
        .where(Namespace.name.in_([namespace, "mediafusion"]))
        .options(selectinload(TVStream.namespaces))
    )
    result = await session.exec(query)
    tv_streams = list(result.unique().all())
    
    # Convert to adapter format and parse
    return await parse_tv_stream_data(tv_streams, user_data)


def _validate_parent_guide_nudity(metadata, user_data: UserData) -> bool:
    """Validate content against user's nudity filter"""
    if "Disable" in user_data.nudity_filter:
        return True
    
    nudity_status = None
    if hasattr(metadata, 'parent_guide_nudity_status'):
        nudity_status = metadata.parent_guide_nudity_status
    elif hasattr(metadata, 'base_metadata') and metadata.base_metadata:
        # For SQL models, nudity is on the specific type (MovieMetadata/SeriesMetadata)
        nudity_status = getattr(metadata, 'parent_guide_nudity_status', None)
    
    if nudity_status and nudity_status in user_data.nudity_filter:
        return False
    
    return True


async def get_tv_streams(
    session: AsyncSession,
    video_id: str,
    namespace: str,
    user_data: UserData,
) -> List[TVStream]:
    """Get raw TV streams for a channel (without formatting)"""
    query = (
        select(TVStream)
        .where(TVStream.meta_id == video_id, TVStream.is_working == True)
        .join(TVStreamNamespaceLink)
        .join(Namespace)
        .where(Namespace.name.in_([namespace, "mediafusion"]))
        .options(selectinload(TVStream.namespaces))
    )
    result = await session.exec(query)
    return list(result.unique().all())


async def find_tv_channel_by_title(
    session: AsyncSession,
    title: str,
) -> Optional[TVMetadata]:
    """Find TV channel by title"""
    query = select(TVMetadata).where(TVMetadata.title == title)
    result = await session.exec(query)
    return result.first()


async def get_tv_streams_by_meta_id(
    session: AsyncSession,
    meta_id: str,
    namespace: str = "mediafusion",
    is_working: Optional[bool] = True,
) -> List[TVStream]:
    """Get TV streams for a specific meta ID and namespace"""
    conditions = [TVStream.meta_id == meta_id]
    if is_working is not None:
        conditions.append(TVStream.is_working == is_working)
    
    query = (
        select(TVStream)
        .where(*conditions)
        .join(TVStreamNamespaceLink)
        .join(Namespace)
        .where(Namespace.name == namespace)
        .options(selectinload(TVStream.namespaces))
    )
    result = await session.exec(query)
    return list(result.unique().all())


async def get_all_tv_streams_paginated(
    session: AsyncSession,
    offset: int = 0,
    limit: int = 25,
) -> List[TVStream]:
    """Get paginated list of all TV streams"""
    query = (
        select(TVStream)
        .offset(offset)
        .limit(limit)
        .options(selectinload(TVStream.namespaces))
    )
    result = await session.exec(query)
    return list(result.all())


async def update_tv_stream_status(
    session: AsyncSession,
    stream_id: str,
    is_working: bool,
    test_failure_count: int,
) -> None:
    """Update TV stream working status and failure count"""
    await session.exec(
        sa_update(TVStream)
        .where(TVStream.id == stream_id)
        .values(is_working=is_working, test_failure_count=test_failure_count)
    )
    await session.commit()


async def delete_tv_stream(
    session: AsyncSession,
    stream_id: str,
) -> None:
    """Delete a TV stream by ID"""
    # Delete namespace links first
    await session.exec(
        sa_delete(TVStreamNamespaceLink).where(TVStreamNamespaceLink.tv_stream_id == stream_id)
    )
    # Delete the stream
    await session.exec(
        sa_delete(TVStream).where(TVStream.id == stream_id)
    )
    await session.commit()


async def get_tv_metadata_not_working_posters(
    session: AsyncSession,
) -> List[TVMetadata]:
    """Get TV metadata entries with non-working posters"""
    query = select(TVMetadata).where(TVMetadata.is_poster_working == False)
    result = await session.exec(query)
    return list(result.all())


async def update_tv_metadata_poster(
    session: AsyncSession,
    meta_id: str,
    poster_url: str,
    is_working: bool = True,
) -> None:
    """Update TV metadata poster URL and status"""
    await session.exec(
        sa_update(TVMetadata)
        .where(TVMetadata.id == meta_id)
        .values(poster=poster_url, is_poster_working=is_working)
    )
    await session.commit()


async def update_poster_working_status(
    session: AsyncSession,
    meta_id: str,
    is_working: bool,
) -> None:
    """Update the is_poster_working status for a metadata entry"""
    await session.exec(
        sa_update(BaseMetadata)
        .where(BaseMetadata.id == meta_id)
        .values(is_poster_working=is_working)
    )
    await session.commit()


# =============================================================================
# Catalog Statistics
# =============================================================================


async def update_catalog_stats_on_stream_add(
    session: AsyncSession,
    meta_id: str,
    catalogs: List[str],
    created_at: datetime,
) -> None:
    """Update catalog statistics when a stream is added and ensure catalog links exist"""
    # Update base metadata total_streams and last_stream_added
    await session.exec(
        sa_update(BaseMetadata)
        .where(BaseMetadata.id == meta_id)
        .values(
            total_streams=BaseMetadata.total_streams + 1,
            last_stream_added=func.greatest(
                BaseMetadata.last_stream_added, created_at
            ),
        )
    )

    # Update per-catalog statistics and ensure MediaCatalogLink exists
    for catalog_name in catalogs:
        catalog = await get_or_create_catalog(session, catalog_name)

        # Ensure MediaCatalogLink exists (insert if not exists)
        # Check if link already exists first (faster than handling constraint violation)
        existing_link = await session.exec(
            select(MediaCatalogLink).where(
                MediaCatalogLink.media_id == meta_id,
                MediaCatalogLink.catalog_id == catalog.id
            )
        )
        if not existing_link.one_or_none():
            session.add(MediaCatalogLink(media_id=meta_id, catalog_id=catalog.id))

        # Use upsert for catalog stream stats
        stmt = pg_insert(CatalogStreamStats).values(
            media_id=meta_id,
            catalog_id=catalog.id,
            total_streams=1,
            last_stream_added=created_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["media_id", "catalog_id"],
            set_={
                "total_streams": CatalogStreamStats.total_streams + 1,
                "last_stream_added": func.greatest(
                    CatalogStreamStats.last_stream_added, created_at
                ),
            },
        )
        await session.exec(stmt)


async def update_catalog_stats_on_stream_delete(
    session: AsyncSession,
    meta_id: str,
    catalogs: List[str],
) -> None:
    """Update catalog statistics when a stream is deleted"""
    # Update base metadata total_streams
    await session.exec(
        sa_update(BaseMetadata)
        .where(BaseMetadata.id == meta_id)
        .values(total_streams=func.greatest(0, BaseMetadata.total_streams - 1))
    )

    # Update per-catalog statistics
    for catalog_name in catalogs:
        query = select(Catalog).where(Catalog.name == catalog_name)
        result = await session.exec(query)
        catalog = result.one_or_none()
        if catalog:
            await session.exec(
                sa_update(CatalogStreamStats)
                .where(
                    CatalogStreamStats.media_id == meta_id,
                    CatalogStreamStats.catalog_id == catalog.id,
                )
                .values(
                    total_streams=func.greatest(
                        0, CatalogStreamStats.total_streams - 1
                    )
                )
            )


async def recalculate_catalog_stats(
    session: AsyncSession,
    meta_id: str,
) -> None:
    """Full recalculation of catalog statistics for a media item"""
    # Count total streams
    count_query = select(func.count(TorrentStream.id)).where(
        TorrentStream.meta_id == meta_id, TorrentStream.is_blocked == False
    )
    result = await session.exec(count_query)
    total = result.one()

    # Get latest stream date
    latest_query = select(func.max(TorrentStream.created_at)).where(
        TorrentStream.meta_id == meta_id, TorrentStream.is_blocked == False
    )
    result = await session.exec(latest_query)
    latest_date = result.one()

    # Update base metadata
    await session.exec(
        sa_update(BaseMetadata)
        .where(BaseMetadata.id == meta_id)
        .values(
            total_streams=total or 0,
            last_stream_added=latest_date or datetime.now(pytz.UTC),
        )
    )

    await session.commit()


# =============================================================================
# Search Operations
# =============================================================================


async def process_search_query(
    session: AsyncSession,
    search_query: str,
    catalog_type: MediaType,
    user_data: UserData,
    limit: int = 50,
) -> public_schemas.Metas:
    """Search for metadata using PostgreSQL full-text search"""
    return await search_metadata(
        session, catalog_type, search_query, user_data, limit=limit
    )


async def process_tv_search_query(
    session: AsyncSession,
    search_query: str,
    namespace: str,
    limit: int = 50,
) -> public_schemas.Metas:
    """Search for TV channels using PostgreSQL full-text search"""
    user_data = UserData()  # Default user data for TV search
    return await search_metadata(
        session,
        MediaType.TV,
        search_query,
        user_data,
        namespace=namespace,
        limit=limit,
    )


# =============================================================================
# Update Operations
# =============================================================================


async def update_metadata(
    session: AsyncSession,
    media_id: str,
    updates: dict,
) -> Optional[BaseMetadata]:
    """Update metadata fields"""
    query = select(BaseMetadata).where(BaseMetadata.id == media_id)
    result = await session.exec(query)
    metadata = result.one_or_none()

    if not metadata:
        return None

    for key, value in updates.items():
        if hasattr(metadata, key):
            setattr(metadata, key, value)

    metadata.updated_at = datetime.now(pytz.UTC)
    await session.commit()
    
    # Invalidate cache based on media type
    media_type = metadata.type.value if metadata.type else "movie"
    await REDIS_ASYNC_CLIENT.delete(f"{media_type}_exists:{media_id}")
    
    return metadata


async def update_torrent_stream(
    session: AsyncSession,
    info_hash: str,
    updates: dict,
) -> Optional[TorrentStream]:
    """Update torrent stream fields"""
    query = select(TorrentStream).where(TorrentStream.id == info_hash.lower())
    result = await session.exec(query)
    stream = result.one_or_none()

    if not stream:
        return None

    for key, value in updates.items():
        if hasattr(stream, key):
            setattr(stream, key, value)

    stream.updated_at = datetime.now(pytz.UTC)
    await session.commit()
    return stream


async def block_torrent_stream(
    session: AsyncSession,
    info_hash: str,
) -> bool:
    """Block a torrent stream"""
    result = await session.exec(
        sa_update(TorrentStream)
        .where(TorrentStream.id == info_hash.lower())
        .values(is_blocked=True, updated_at=datetime.now(pytz.UTC))
    )
    await session.commit()
    return result.rowcount > 0


async def update_episode_files(
    session: AsyncSession,
    torrent_id: str,
    episode_files: List,
) -> None:
    """Update episode files for a torrent stream"""
    from db.schemas import EpisodeFileData
    
    torrent_id = torrent_id.lower()
    
    # Delete existing episode files for this torrent
    await session.exec(
        sa_delete(EpisodeFile).where(EpisodeFile.torrent_stream_id == torrent_id)
    )
    
    # Insert new episode files
    for ef in episode_files:
        if isinstance(ef, EpisodeFileData):
            episode_file = EpisodeFile(
                torrent_stream_id=torrent_id,
                season_number=ef.season_number,
                episode_number=ef.episode_number,
                filename=ef.filename,
                size=ef.size,
                file_index=ef.file_index,
            )
        else:
            # Handle dict or other formats
            episode_file = EpisodeFile(
                torrent_stream_id=torrent_id,
                season_number=ef.get("season_number") if isinstance(ef, dict) else getattr(ef, "season_number", None),
                episode_number=ef.get("episode_number") if isinstance(ef, dict) else getattr(ef, "episode_number", None),
                filename=ef.get("filename") if isinstance(ef, dict) else getattr(ef, "filename", None),
                size=ef.get("size") if isinstance(ef, dict) else getattr(ef, "size", None),
                file_index=ef.get("file_index") if isinstance(ef, dict) else getattr(ef, "file_index", None),
            )
        session.add(episode_file)
    
    await session.commit()


async def delete_torrent_stream(
    session: AsyncSession,
    info_hash: str,
) -> bool:
    """Delete a torrent stream and its related data"""
    # Delete related episode files
    await session.exec(
        sa_delete(EpisodeFile).where(EpisodeFile.torrent_stream_id == info_hash.lower())
    )
    
    # Delete related language links
    await session.exec(
        sa_delete(TorrentLanguageLink).where(TorrentLanguageLink.torrent_id == info_hash.lower())
    )
    
    # Delete related announce links
    await session.exec(
        sa_delete(TorrentAnnounceLink).where(TorrentAnnounceLink.torrent_id == info_hash.lower())
    )
    
    # Delete the torrent stream
    result = await session.exec(
        sa_delete(TorrentStream).where(TorrentStream.id == info_hash.lower())
    )
    await session.commit()
    return result.rowcount > 0


async def get_metadata_by_id(
    session: AsyncSession,
    meta_id: str,
) -> Optional[BaseMetadata]:
    """Get base metadata by ID"""
    query = select(BaseMetadata).where(BaseMetadata.id == meta_id)
    result = await session.exec(query)
    return result.one_or_none()


async def migrate_torrent_streams(
    session: AsyncSession,
    old_meta_id: str,
    new_meta_id: str,
) -> int:
    """Migrate torrent streams from one meta ID to another"""
    result = await session.exec(
        sa_update(TorrentStream)
        .where(TorrentStream.meta_id == old_meta_id)
        .values(meta_id=new_meta_id, updated_at=datetime.now(pytz.UTC))
    )
    await session.commit()
    return result.rowcount


async def delete_metadata(
    session: AsyncSession,
    meta_id: str,
) -> bool:
    """Delete metadata and all related data"""
    # Delete from type-specific tables first
    await session.exec(sa_delete(MovieMetadata).where(MovieMetadata.id == meta_id))
    await session.exec(sa_delete(SeriesMetadata).where(SeriesMetadata.id == meta_id))
    await session.exec(sa_delete(TVMetadata).where(TVMetadata.id == meta_id))
    
    # Delete from link tables (handled by cascade, but explicit for clarity)
    await session.exec(sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == meta_id))
    await session.exec(sa_delete(MediaCatalogLink).where(MediaCatalogLink.media_id == meta_id))
    await session.exec(sa_delete(MediaStarLink).where(MediaStarLink.media_id == meta_id))
    await session.exec(sa_delete(MediaParentalCertificateLink).where(MediaParentalCertificateLink.media_id == meta_id))
    await session.exec(sa_delete(AkaTitle).where(AkaTitle.media_id == meta_id))
    
    # Delete base metadata
    result = await session.exec(sa_delete(BaseMetadata).where(BaseMetadata.id == meta_id))
    await session.commit()
    
    # Invalidate all cache keys for this metadata
    await REDIS_ASYNC_CLIENT.delete(
        f"movie_exists:{meta_id}",
        f"series_exists:{meta_id}",
        f"tv_exists:{meta_id}",
    )
    
    return result.rowcount > 0


# =============================================================================
# RSSFeed CRUD Operations
# =============================================================================


async def get_rss_feed(
    session: AsyncSession,
    feed_id: int,
) -> Optional[RSSFeed]:
    """Get RSS feed by ID"""
    query = (
        select(RSSFeed)
        .where(RSSFeed.id == feed_id)
        .options(selectinload(RSSFeed.catalog_patterns))
    )
    result = await session.exec(query)
    return result.one_or_none()


async def get_rss_feed_by_url(
    session: AsyncSession,
    url: str,
) -> Optional[RSSFeed]:
    """Get RSS feed by URL"""
    query = (
        select(RSSFeed)
        .where(RSSFeed.url == url)
        .options(selectinload(RSSFeed.catalog_patterns))
    )
    result = await session.exec(query)
    return result.one_or_none()


async def create_rss_feed(
    session: AsyncSession,
    feed_data: dict,
) -> RSSFeed:
    """Create a new RSS feed"""
    feed = RSSFeed(
        name=feed_data["name"],
        url=feed_data["url"],
        active=feed_data.get("active", True),
        source=feed_data.get("source"),
        torrent_type=feed_data.get("torrent_type", "public"),
        auto_detect_catalog=feed_data.get("auto_detect_catalog", False),
        parsing_patterns=feed_data.get("parsing_patterns"),
        filters=feed_data.get("filters"),
        metrics=feed_data.get("metrics"),
    )
    session.add(feed)
    await session.flush()

    # Add catalog patterns
    for pattern_data in feed_data.get("catalog_patterns", []):
        pattern = RSSFeedCatalogPattern(
            rss_feed_id=feed.id,
            name=pattern_data.get("name"),
            regex=pattern_data["regex"],
            enabled=pattern_data.get("enabled", True),
            case_sensitive=pattern_data.get("case_sensitive", False),
            target_catalogs=pattern_data.get("target_catalogs", []),
        )
        session.add(pattern)

    await session.commit()
    return feed


async def update_rss_feed(
    session: AsyncSession,
    feed_id: int,
    updates: dict,
) -> Optional[RSSFeed]:
    """Update an RSS feed"""
    feed = await get_rss_feed(session, feed_id)
    if not feed:
        return None

    for key, value in updates.items():
        if key == "catalog_patterns":
            # Clear existing patterns and add new ones
            await session.exec(
                sa_delete(RSSFeedCatalogPattern).where(
                    RSSFeedCatalogPattern.rss_feed_id == feed_id
                )
            )
            for pattern_data in value:
                pattern = RSSFeedCatalogPattern(
                    rss_feed_id=feed_id,
                    name=pattern_data.get("name"),
                    regex=pattern_data["regex"],
                    enabled=pattern_data.get("enabled", True),
                    case_sensitive=pattern_data.get("case_sensitive", False),
                    target_catalogs=pattern_data.get("target_catalogs", []),
                )
                session.add(pattern)
        elif hasattr(feed, key):
            setattr(feed, key, value)

    feed.updated_at = datetime.now(pytz.UTC)
    await session.commit()
    return feed


async def delete_rss_feed(
    session: AsyncSession,
    feed_id: int,
) -> bool:
    """Delete an RSS feed"""
    result = await session.exec(
        sa_delete(RSSFeed).where(RSSFeed.id == feed_id)
    )
    await session.commit()
    return result.rowcount > 0


async def list_rss_feeds(
    session: AsyncSession,
    active_only: bool = False,
) -> Sequence[RSSFeed]:
    """List all RSS feeds"""
    query = select(RSSFeed).options(selectinload(RSSFeed.catalog_patterns))
    if active_only:
        query = query.where(RSSFeed.active == True)
    query = query.order_by(RSSFeed.name)

    result = await session.exec(query)
    return result.all()


async def update_rss_feed_metrics(
    session: AsyncSession,
    feed_id: int,
    metrics: dict,
) -> None:
    """Update RSS feed scraping metrics"""
    await session.exec(
        sa_update(RSSFeed)
        .where(RSSFeed.id == feed_id)
        .values(
            metrics=metrics,
            last_scraped=datetime.now(pytz.UTC),
            updated_at=datetime.now(pytz.UTC),
        )
    )
    await session.commit()


# =============================================================================
# Scraper Operations
# =============================================================================


async def get_or_create_metadata(
    session: AsyncSession,
    metadata: dict,
    media_type: str,
    is_search_imdb_title: bool = True,
    is_imdb_only: bool = False,
) -> Optional[dict]:
    """Get existing metadata or create new one.
    
    Used by scrapers to find or create metadata entries.
    """
    from scrapers.scraper_tasks import meta_fetcher
    from uuid import uuid4
    
    # Determine media type enum
    media_type_enum = MediaType.MOVIE if media_type == "movie" else MediaType.SERIES
    
    # Check for existing metadata by title/year
    title = metadata.get("title", "")
    year = metadata.get("year")
    
    # Search by exact title match first
    query = (
        select(BaseMetadata.id)
        .where(
            BaseMetadata.type == media_type_enum,
            func.lower(BaseMetadata.title) == func.lower(title),
        )
    )
    if year:
        query = query.where(BaseMetadata.year == year)
    
    result = await session.exec(query)
    existing_id = result.first()
    
    if existing_id:
        metadata["id"] = existing_id
        return metadata
    
    # Search by AKA titles
    aka_query = (
        select(AkaTitle.media_id)
        .where(func.lower(AkaTitle.title) == func.lower(title))
    )
    result = await session.exec(aka_query)
    aka_match = result.first()
    
    if aka_match:
        metadata["id"] = aka_match
        return metadata
    
    # No existing metadata found - fetch from IMDb/TMDB if requested
    imdb_data = {}
    if is_search_imdb_title:
        imdb_data = await meta_fetcher.search_metadata(
            title,
            year,
            media_type,
            metadata.get("created_at"),
        )
    
    if not imdb_data and is_imdb_only:
        return None
    
    imdb_data = imdb_data or {}
    metadata["id"] = (
        imdb_data.get("imdb_id") or metadata.get("id") or f"mf{uuid4().fields[-1]}"
    )
    
    # Check if this ID already exists
    existing = await session.exec(
        select(BaseMetadata.id).where(BaseMetadata.id == metadata["id"])
    )
    if existing.first():
        return metadata
    
    # Create new metadata
    combined_metadata = {**metadata, **imdb_data}
    await save_base_metadata(session, combined_metadata, media_type_enum)
    
    # Create type-specific metadata
    if media_type == "movie":
        movie = MovieMetadata(
            id=metadata["id"],
            imdb_rating=combined_metadata.get("imdb_rating"),
            tmdb_rating=combined_metadata.get("tmdb_rating"),
            parent_guide_nudity_status=combined_metadata.get("parent_guide_nudity_status"),
        )
        await session.merge(movie)
    else:
        series = SeriesMetadata(
            id=metadata["id"],
            end_year=combined_metadata.get("end_year"),
            imdb_rating=combined_metadata.get("imdb_rating"),
            tmdb_rating=combined_metadata.get("tmdb_rating"),
            parent_guide_nudity_status=combined_metadata.get("parent_guide_nudity_status"),
        )
        await session.merge(series)
    
    await session.commit()
    logger.info(f"Created new {media_type} metadata: {metadata['id']} - {title}")
    
    return metadata


async def update_meta_stream(
    session: AsyncSession,
    meta_id: str,
    meta_type: str,
    is_update_data_only: bool = False,
) -> dict:
    """Update stream-related metadata for a given meta_id.
    
    Calculates and updates:
    - total_streams: Total number of non-blocked streams
    - last_stream_added: Most recent stream creation date
    - catalog_stats: Per-catalog stream counts
    """
    # Get torrent stream counts per catalog
    catalog_stats_query = (
        select(
            Catalog.name,
            func.count(TorrentStream.id).label("total_streams"),
            func.max(TorrentStream.created_at).label("last_stream_added"),
        )
        .join(CatalogStreamStats, CatalogStreamStats.catalog_id == Catalog.id)
        .join(TorrentStream, TorrentStream.meta_id == CatalogStreamStats.media_id)
        .where(
            TorrentStream.meta_id == meta_id,
            TorrentStream.is_blocked == False,
        )
        .group_by(Catalog.name)
    )
    
    result = await session.exec(catalog_stats_query)
    catalog_stats = result.all()
    
    # Calculate overall stats
    total_streams = sum(stat.total_streams for stat in catalog_stats)
    last_stream_added = (
        max(stat.last_stream_added for stat in catalog_stats)
        if catalog_stats
        else None
    )
    
    update_data = {
        "total_streams": total_streams,
        "last_stream_added": last_stream_added,
        "last_updated_at": datetime.now(pytz.UTC),
        "catalog_stats": [
            {
                "catalog": stat[0],  # catalog name
                "total_streams": stat[1],
                "last_stream_added": stat[2],
            }
            for stat in catalog_stats
        ],
    }
    
    if is_update_data_only:
        return update_data
    
    # Update base metadata
    await session.exec(
        sa_update(BaseMetadata)
        .where(BaseMetadata.id == meta_id)
        .values(
            total_streams=total_streams,
            last_stream_added=last_stream_added,
            updated_at=datetime.now(pytz.UTC),
        )
    )
    await session.commit()
    
    return update_data


async def get_existing_metadata_by_title(
    session: AsyncSession,
    title: str,
    year: Optional[int],
    media_type: MediaType,
) -> Optional[str]:
    """Find existing metadata by title and year"""
    # Exact title match
    query = (
        select(BaseMetadata.id)
        .where(
            BaseMetadata.type == media_type,
            func.lower(BaseMetadata.title) == func.lower(title),
        )
    )
    if year:
        query = query.where(BaseMetadata.year == year)
    
    result = await session.exec(query)
    existing = result.first()
    if existing:
        return existing
    
    # AKA title match
    aka_query = (
        select(AkaTitle.media_id)
        .join(BaseMetadata)
        .where(
            BaseMetadata.type == media_type,
            func.lower(AkaTitle.title) == func.lower(title),
        )
    )
    result = await session.exec(aka_query)
    return result.first()


async def scraper_save_movie_metadata(
    session: AsyncSession,
    metadata: dict,
    is_search_imdb_title: bool = True,
) -> Optional[dict]:
    """Save movie metadata from scrapers with torrent stream handling"""
    return await scraper_save_metadata_with_stream(
        session, metadata, "movie", is_search_imdb_title
    )


async def scraper_save_series_metadata(
    session: AsyncSession,
    metadata: dict,
    is_search_imdb_title: bool = True,
) -> Optional[dict]:
    """Save series metadata from scrapers with torrent stream handling"""
    return await scraper_save_metadata_with_stream(
        session, metadata, "series", is_search_imdb_title
    )


async def scraper_save_metadata_with_stream(
    session: AsyncSession,
    metadata: dict,
    media_type: str,
    is_search_imdb_title: bool = True,
) -> Optional[dict]:
    """Save metadata and its associated torrent stream"""
    info_hash = metadata.get("info_hash")
    
    # Check if torrent stream already exists
    if info_hash:
        existing_stream = await get_stream_by_info_hash(session, info_hash)
        if existing_stream:
            if (
                metadata.get("expected_sources")
                and existing_stream.source not in metadata["expected_sources"]
            ):
                logger.info(
                    "Source mismatch for %s %s: %s != %s. Trying to re-create the data",
                    media_type,
                    metadata["title"],
                    metadata.get("source"),
                    existing_stream.source,
                )
                await delete_torrent_stream(session, info_hash)
            else:
                logger.info(
                    "Stream already exists for %s %s", media_type, metadata["title"]
                )
                return metadata
    
    # Get or create metadata
    result = await get_or_create_metadata(
        session, metadata, media_type, is_search_imdb_title
    )
    
    if not result:
        return None
    
    meta_id = result["id"]
    
    # Prepare stream data
    stream_data = {
        "id": info_hash,
        "meta_id": meta_id,
        "torrent_name": metadata.get("torrent_name"),
        "announce_urls": metadata.get("announce_list", []),
        "size": metadata.get("total_size") or metadata.get("size", 0),
        "source": metadata.get("source"),
        "resolution": metadata.get("resolution"),
        "codec": metadata.get("codec"),
        "quality": metadata.get("quality"),
        "audio": metadata.get("audio"),
        "hdr": metadata.get("hdr"),
        "seeders": metadata.get("seeders"),
        "uploader": metadata.get("uploader"),
        "languages": metadata.get("languages", []),
        "catalogs": metadata.get("catalog", []),
        "created_at": metadata.get("created_at"),
        "filename": metadata.get("filename"),
        "file_index": metadata.get("file_index"),
    }
    
    # Handle series episode files
    if media_type == "series":
        episode_files = []
        file_data = metadata.get("file_data", [])
        
        for file_info in file_data:
            if not file_info.get("filename"):
                continue
            
            episode_number = None
            season_number = 1
            
            if file_info.get("episodes"):
                episode_number = file_info["episodes"][0]
            elif meta_id.startswith("mf"):
                episode_number = len(episode_files) + 1
            else:
                continue
            
            if file_info.get("seasons"):
                season_number = file_info["seasons"][0]
            
            episode_files.append({
                "season_number": season_number,
                "episode_number": episode_number,
                "filename": file_info.get("filename"),
                "size": file_info.get("size"),
                "file_index": file_info.get("index"),
            })
        
        if not episode_files and metadata.get("episodes"):
            # Use existing episodes if file_data is empty
            for ep in metadata["episodes"]:
                if hasattr(ep, "model_dump"):
                    episode_files.append(ep.model_dump())
                else:
                    episode_files.append(ep)
        
        if not episode_files:
            logger.warning("No episodes found for series %s", metadata["title"])
            return result
        
        stream_data["episode_files"] = episode_files
    
    # Store the stream
    if info_hash:
        await store_new_torrent_streams(session, [stream_data])
        logger.info(
            "Added stream for %s %s (%s), info_hash: %s",
            media_type,
            metadata["title"],
            metadata.get("year"),
            info_hash,
        )
    
    # Organize episodes for series with custom IDs
    if media_type == "series" and meta_id.startswith("mf"):
        await organize_episodes(session, meta_id)
    
    return result


async def organize_episodes(
    session: AsyncSession,
    series_id: str,
) -> None:
    """Organize episodes by release date and assign sequential numbers"""
    from db.sql_models import SeriesEpisode
    
    # Fetch all episode files for this series
    query = (
        select(EpisodeFile)
        .join(TorrentStream)
        .where(TorrentStream.meta_id == series_id)
        .order_by(EpisodeFile.season_number, EpisodeFile.episode_number)
    )
    result = await session.exec(query)
    episode_files = list(result.all())
    
    if not episode_files:
        return
    
    # Group by season and create unique episodes
    season_episode_map = {}
    
    for ef in episode_files:
        season = ef.season_number
        if season not in season_episode_map:
            season_episode_map[season] = {}
        
        title_key = ef.filename or str(ef.episode_number)
        if title_key not in season_episode_map[season]:
            episode_number = len(season_episode_map[season]) + 1
            season_episode_map[season][title_key] = episode_number
    
    # Update or create SeriesEpisode entries
    for season, episodes in season_episode_map.items():
        for title_key, episode_number in episodes.items():
            # Check if episode exists
            existing = await session.exec(
                select(SeriesEpisode)
                .where(
                    SeriesEpisode.series_id == series_id,
                    SeriesEpisode.season_number == season,
                    SeriesEpisode.episode_number == episode_number,
                )
            )
            
            if not existing.first():
                # Create new episode
                episode = SeriesEpisode(
                    series_id=series_id,
                    season_number=season,
                    episode_number=episode_number,
                    title=title_key if title_key != str(episode_number) else None,
                )
                session.add(episode)
    
    await session.commit()


async def save_events_data(
    session: AsyncSession,
    metadata: dict,
) -> str:
    """Save events data to Redis (events are stored in Redis, not PostgreSQL)"""
    from utils import crypto
    from db.schemas import MediaFusionEventsMetaData, TVStreams
    
    # Generate a unique event key
    meta_id = "mf" + crypto.get_text_hash(metadata["title"])
    event_key = f"event:{meta_id}"
    
    # Attempt to fetch existing event data
    existing_event_json = await REDIS_ASYNC_CLIENT.get(event_key)
    
    if existing_event_json:
        # Deserialize the existing event data
        existing_event_data = MediaFusionEventsMetaData.model_validate_json(
            existing_event_json
        )
        existing_streams = set(existing_event_data.streams)
    else:
        existing_streams = set()
    
    # Update or add streams based on the uniqueness of 'url'
    for stream in metadata.get("streams", []):
        # Create a TVStreams instance for each stream
        stream_instance = TVStreams(meta_id=meta_id, **stream)
        existing_streams.add(stream_instance)
    
    streams = list(existing_streams)
    
    event_start_timestamp = metadata.get("event_start_timestamp", 0)
    
    # Create or update the event data
    events_data = MediaFusionEventsMetaData(
        id=meta_id,
        streams=streams,
        title=metadata["title"],
        year=metadata.get("year"),
        poster=metadata.get("poster"),
        background=metadata.get("background"),
        logo=metadata.get("logo"),
        country=metadata.get("country"),
        genres=metadata.get("genres", []),
        is_add_title_to_poster=metadata.get("is_add_title_to_poster", True),
        event_start_timestamp=event_start_timestamp,
        website=metadata.get("website"),
    )
    
    # Store the event data in Redis
    await REDIS_ASYNC_CLIENT.set(
        event_key,
        events_data.model_dump_json(),
        ex=86400,  # 24 hours TTL
    )
    
    return meta_id


async def get_events_meta_list(session: AsyncSession, genre: str = None, skip: int = 0, limit: int = 50) -> List:
    """Get list of events metadata"""
    from scrapers.dlhd import dlhd_schedule_service
    
    return await dlhd_schedule_service.get_scheduled_events(
        session, genre=genre, skip=skip, limit=limit
    )


async def get_event_meta(meta_id: str) -> dict:
    """Get event metadata by ID from Redis"""
    from scrapers.dlhd import dlhd_schedule_service
    from db.schemas import MediaFusionEventsMetaData
    
    events_key = f"event:{meta_id}"
    events_json = await REDIS_ASYNC_CLIENT.get(events_key)
    if not events_json:
        return {}
    
    event_data = MediaFusionEventsMetaData.model_validate_json(events_json)
    if event_data.event_start_timestamp:
        # Update description with localized time
        event_data.description = f"🎬 {event_data.title} - ⏰ {dlhd_schedule_service.format_event_time(event_data.event_start_timestamp)}"
    
    return {
        "meta": {
            "_id": meta_id,
            **event_data.model_dump(),
        }
    }


async def get_event_data_by_id(meta_id: str):
    """Get event data by ID from Redis"""
    from db.schemas import MediaFusionEventsMetaData
    
    event_key = f"event:{meta_id}"
    events_json = await REDIS_ASYNC_CLIENT.get(event_key)
    if not events_json:
        return None
    
    return MediaFusionEventsMetaData.model_validate_json(events_json)


async def get_event_streams(meta_id: str, user_data) -> List:
    """Get event streams from Redis"""
    from db.schemas import MediaFusionEventsMetaData
    from utils.parser import parse_tv_stream_data
    
    event_key = f"event:{meta_id}"
    event_json = await REDIS_ASYNC_CLIENT.get(event_key)
    if not event_json:
        return await parse_tv_stream_data([], user_data)
    
    event_data = MediaFusionEventsMetaData.model_validate_json(event_json)
    return await parse_tv_stream_data(event_data.streams, user_data)


# =============================================================================
# Metrics Operations
# =============================================================================


async def get_torrent_count(session: AsyncSession) -> int:
    """Get total torrent stream count"""
    result = await session.exec(select(func.count(TorrentStream.id)))
    return result.one() or 0


async def get_torrents_by_source(session: AsyncSession, limit: int = 20) -> List[dict]:
    """Get torrent counts grouped by source"""
    query = (
        select(TorrentStream.source, func.count(TorrentStream.id).label("count"))
        .group_by(TorrentStream.source)
        .order_by(func.count(TorrentStream.id).desc())
        .limit(limit)
    )
    result = await session.exec(query)
    return [{"name": row[0], "count": row[1]} for row in result.all()]


async def get_torrents_by_uploader(session: AsyncSession, limit: int = 20) -> List[dict]:
    """Get torrent counts grouped by uploader"""
    query = (
        select(TorrentStream.uploader, func.count(TorrentStream.id).label("count"))
        .where(TorrentStream.uploader.isnot(None))
        .group_by(TorrentStream.uploader)
        .order_by(func.count(TorrentStream.id).desc())
        .limit(limit)
    )
    result = await session.exec(query)
    return [
        {"name": row[0] if row[0] else "Unknown", "count": row[1]}
        for row in result.all()
    ]


async def get_weekly_top_uploaders(
    session: AsyncSession,
    start_of_week: datetime,
    end_of_week: datetime,
) -> List[dict]:
    """Get top uploaders for a specific week"""
    query = (
        select(
            TorrentStream.uploader,
            func.count(TorrentStream.id).label("count"),
            func.max(TorrentStream.created_at).label("latest_upload"),
        )
        .where(
            TorrentStream.source == "Contribution Stream",
            TorrentStream.uploaded_at >= start_of_week,
            TorrentStream.uploaded_at < end_of_week,
            TorrentStream.is_blocked == False,
        )
        .group_by(TorrentStream.uploader)
    )
    result = await session.exec(query)
    return [
        {
            "_id": row[0],
            "count": row[1],
            "latest_upload": row[2],
        }
        for row in result.all()
    ]


async def get_metadata_counts(session: AsyncSession) -> dict:
    """Get metadata counts by type"""
    movie_count = await session.exec(
        select(func.count(BaseMetadata.id)).where(BaseMetadata.type == MediaType.MOVIE)
    )
    series_count = await session.exec(
        select(func.count(BaseMetadata.id)).where(BaseMetadata.type == MediaType.SERIES)
    )
    tv_count = await session.exec(
        select(func.count(BaseMetadata.id)).where(BaseMetadata.type == MediaType.TV)
    )
    
    return {
        "movies": movie_count.one() or 0,
        "series": series_count.one() or 0,
        "tv_channels": tv_count.one() or 0,
    }


async def fetch_last_run(spider_id: str, spider_name: str) -> dict:
    """Fetch last run information for a spider (from Redis)"""
    import humanize
    from apscheduler.triggers.cron import CronTrigger
    from db.config import settings
    
    task_key = f"background_tasks:run_spider:spider_name={spider_id}"
    state_key = f"scrapy_stats:{spider_id}"
    last_run_timestamp = await REDIS_ASYNC_CLIENT.get(task_key)
    last_run_state = await REDIS_ASYNC_CLIENT.get(state_key)

    if settings.disable_all_scheduler:
        next_schedule_in = None
        is_scheduler_disabled = True
    else:
        crontab_expression = getattr(settings, f"{spider_id}_scheduler_crontab")
        is_scheduler_disabled = getattr(settings, f"disable_{spider_id}_scheduler")
        cron_trigger = CronTrigger.from_crontab(crontab_expression)
        next_time = cron_trigger.get_next_fire_time(
            None, datetime.now(tz=cron_trigger.timezone)
        )
        next_schedule_in = humanize.naturaldelta(
            next_time - datetime.now(tz=cron_trigger.timezone)
        )

    response = {
        "name": spider_name,
        "last_run": "Never",
        "time_since_last_run": "Never run",
        "time_since_last_run_seconds": -1,
        "next_schedule_in": next_schedule_in,
        "is_scheduler_disabled": is_scheduler_disabled,
        "last_run_state": json.loads(last_run_state or "null") if last_run_state else None,
    }

    if last_run_timestamp:
        last_run = datetime.fromtimestamp(float(last_run_timestamp))
        delta = datetime.now() - last_run
        response.update(
            {
                "last_run": last_run.isoformat(),
                "time_since_last_run": humanize.precisedelta(
                    delta, minimum_unit="minutes"
                ),
                "time_since_last_run_seconds": delta.total_seconds(),
            }
        )

    return response


# =============================================================================
# Genre Operations
# =============================================================================


async def get_genres(
    session: AsyncSession,
    catalog_type: MediaType,
    cache_ttl: int = 3600,  # Cache for 1 hour
) -> List[str]:
    """Get all genres for a catalog type with Redis caching
    
    This query requires scanning large tables (media_genre_link + base_metadata),
    so results are cached in Redis for better performance.
    """
    # Handle both string and enum types for catalog_type
    if isinstance(catalog_type, str):
        type_value = catalog_type.lower()
        media_type = MediaType(type_value)
    else:
        type_value = catalog_type.value
        media_type = catalog_type
    cache_key = f"genres:{type_value}"
    
    # Try to get from cache first
    cached = await REDIS_ASYNC_CLIENT.get(cache_key)
    if cached:
        return json.loads(cached)
    
    # Query database
    query = (
        select(Genre.name)
        .distinct()
        .join(MediaGenreLink)
        .join(BaseMetadata)
        .where(BaseMetadata.type == media_type)
        .order_by(Genre.name)
    )
    result = await session.exec(query)
    genres = list(result.all())
    
    # Cache the result
    await REDIS_ASYNC_CLIENT.set(cache_key, json.dumps(genres), ex=cache_ttl)
    
    return genres


async def bulk_update_rss_feed_status(
    session: AsyncSession, 
    feed_ids: list[int], 
    active: bool
) -> int:
    """Bulk update active status for multiple RSS feeds"""
    stmt = (
        sa_update(RSSFeed)
        .where(RSSFeed.id.in_(feed_ids))
        .values(active=active)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount


# =============================================================================
# Batch Metadata Operations
# =============================================================================


async def update_metadata_batch(
    imdb_ids: List[str],
    metadata_type: str,
) -> None:
    """Batch update metadata from IMDB/TMDB for multiple IDs using circuit breaker pattern
    
    This function:
    1. Uses circuit breaker pattern for resilient API calls
    2. Fetches updated metadata from IMDB/TMDB
    3. Merges new data with existing data, preserving existing values when new ones are None/empty
    4. Updates episodes for series
    5. Invalidates cache for updated metadata
    
    Args:
        imdb_ids: List of IMDB IDs to update
        metadata_type: 'movie' or 'series'
    """
    from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
    from scrapers.scraper_tasks import meta_fetcher
    from db.database import get_background_session
    from db.enums import NudityStatus
    
    now = datetime.now(pytz.UTC)
    
    def merge_lists(existing_items: List, new_items: List, key_field: str = "name") -> List[str]:
        """Merge existing and new list items, removing duplicates"""
        existing_set = set()
        for item in existing_items or []:
            if hasattr(item, key_field):
                existing_set.add(getattr(item, key_field))
            elif isinstance(item, dict) and key_field in item:
                existing_set.add(item[key_field])
            elif isinstance(item, str):
                existing_set.add(item)
        
        new_set = set(new_items or [])
        return list(existing_set | new_set)
    
    # Initialize circuit breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=10, half_open_attempts=2
    )
    
    specific_model = MovieMetadata if metadata_type == "movie" else SeriesMetadata
    
    async for result in batch_process_with_circuit_breaker(
        meta_fetcher.get_metadata,
        imdb_ids,
        5,  # batch size
        rate_limit_delay=3,
        cb=circuit_breaker,
        media_type=metadata_type,
    ):
        if not result:
            continue
        
        meta_id = result.get("imdb_id")
        if not meta_id:
            continue
        
        async with get_background_session() as session:
            try:
                # Get existing metadata
                existing = await session.exec(
                    select(specific_model)
                    .where(specific_model.id == meta_id)
                    .options(
                        selectinload(specific_model.base_metadata).options(
                            selectinload(BaseMetadata.genres),
                            selectinload(BaseMetadata.aka_titles),
                        ),
                        selectinload(specific_model.parental_certificates),
                        selectinload(specific_model.stars),
                    )
                )
                existing_metadata = existing.one_or_none()
                
                if not existing_metadata:
                    logger.warning(
                        f"Metadata not found for {metadata_type} {meta_id}. Skipping update."
                    )
                    continue
                
                base = existing_metadata.base_metadata
                
                # Update base metadata
                base.title = result.get("title") or base.title
                base.poster = result.get("poster") or base.poster
                base.background = result.get("background") or base.background
                base.description = result.get("description") or base.description
                base.runtime = result.get("runtime") or base.runtime
                base.updated_at = now
                
                # Update type-specific metadata
                if result.get("imdb_rating") is not None:
                    existing_metadata.imdb_rating = result["imdb_rating"]
                if result.get("tmdb_rating") is not None:
                    existing_metadata.tmdb_rating = result["tmdb_rating"]
                
                nudity_status = result.get("parent_guide_nudity_status")
                if nudity_status and nudity_status != NudityStatus.UNKNOWN:
                    existing_metadata.parent_guide_nudity_status = nudity_status
                
                # Handle genres
                new_genres = merge_lists(base.genres, result.get("genres", []))
                await session.exec(
                    sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == meta_id)
                )
                for genre_name in new_genres:
                    genre = await get_or_create_genre(session, genre_name)
                    session.add(MediaGenreLink(media_id=meta_id, genre_id=genre.id))
                
                # Handle aka_titles
                new_aka_titles = merge_lists(base.aka_titles, result.get("aka_titles", []), "title")
                await session.exec(
                    sa_delete(AkaTitle).where(AkaTitle.media_id == meta_id)
                )
                for title in new_aka_titles:
                    session.add(AkaTitle(title=title, media_id=meta_id))
                
                # Handle stars
                new_stars = merge_lists(existing_metadata.stars, result.get("stars", []))
                await session.exec(
                    sa_delete(MediaStarLink).where(MediaStarLink.media_id == meta_id)
                )
                for star_name in new_stars:
                    star = await get_or_create_star(session, star_name)
                    session.add(MediaStarLink(media_id=meta_id, star_id=star.id))
                
                # Handle parental certificates
                new_certs = merge_lists(
                    existing_metadata.parental_certificates,
                    result.get("parent_guide_certificates", [])
                )
                await session.exec(
                    sa_delete(MediaParentalCertificateLink).where(
                        MediaParentalCertificateLink.media_id == meta_id
                    )
                )
                for cert_name in new_certs:
                    cert = await get_or_create_parental_certificate(session, cert_name)
                    session.add(MediaParentalCertificateLink(
                        media_id=meta_id, certificate_id=cert.id
                    ))
                
                # Handle series episodes
                if metadata_type == "series" and result.get("episodes"):
                    await _update_series_episodes(session, meta_id, existing_metadata, result["episodes"])
                
                await session.commit()
                logger.info(f"Updated metadata for {metadata_type} {meta_id}")
                
                # Invalidate cache
                await REDIS_ASYNC_CLIENT.delete(
                    f"{metadata_type}_exists:{meta_id}",
                    f"{metadata_type}_data:{meta_id}",
                )
                
            except Exception as e:
                logger.error(f"Error updating metadata for {meta_id}: {e}")
                await session.rollback()


async def _update_series_episodes(
    session: AsyncSession,
    series_id: str,
    existing_series: SeriesMetadata,
    new_episodes: List[dict],
) -> None:
    """Helper to update series episodes during batch metadata update"""
    from db.sql_models import SeriesEpisode
    
    # Get existing episodes
    existing_seasons = (
        await session.exec(
            select(SeriesSeason)
            .where(SeriesSeason.series_id == series_id)
            .options(selectinload(SeriesSeason.episodes))
        )
    ).all()
    
    # Create map of existing episodes: (season_number, episode_number) -> episode
    existing_episodes = {}
    for season in existing_seasons:
        for ep in season.episodes:
            # Season number is on the season, not the episode
            key = (season.season_number, ep.episode_number)
            existing_episodes[key] = ep
    
    # Process new episodes
    for new_ep in new_episodes:
        season_num = new_ep.get("season_number")
        ep_num = new_ep.get("episode_number")
        if season_num is None or ep_num is None:
            continue
        
        key = (season_num, ep_num)
        
        if key in existing_episodes:
            # Update existing episode
            existing_ep = existing_episodes[key]
            existing_ep.title = new_ep.get("title") or existing_ep.title
            existing_ep.overview = new_ep.get("overview") or existing_ep.overview
            existing_ep.thumbnail = new_ep.get("thumbnail") or existing_ep.thumbnail
            if new_ep.get("imdb_rating"):
                existing_ep.imdb_rating = new_ep["imdb_rating"]
            if new_ep.get("released"):
                existing_ep.released = new_ep["released"]
            # Mark as no longer a stub if we have real metadata
            if existing_ep.is_stub and new_ep.get("title"):
                existing_ep.is_stub = False
        else:
            # Check if season exists
            season = await session.exec(
                select(SeriesSeason).where(
                    SeriesSeason.series_id == series_id,
                    SeriesSeason.season_number == season_num
                )
            )
            season_record = season.one_or_none()
            
            if not season_record:
                season_record = SeriesSeason(
                    series_id=series_id,
                    season_number=season_num
                )
                session.add(season_record)
                await session.flush()
            
            # Create new episode (season_number is not on SeriesEpisode model)
            new_episode = SeriesEpisode(
                season_id=season_record.id,
                episode_number=ep_num,
                title=new_ep.get("title") or f"Episode {ep_num}",
                overview=new_ep.get("overview"),
                thumbnail=new_ep.get("thumbnail"),
                imdb_rating=new_ep.get("imdb_rating"),
                released=new_ep.get("released"),
            )
            session.add(new_episode)


async def fetch_metadata_batch(
    imdb_ids: List[str],
    metadata_type: str,
) -> None:
    """Background task to fetch metadata for missing IMDB IDs
    
    This function uses circuit breaker pattern to:
    1. Fetch metadata from IMDB/TMDB for each ID
    2. Store the metadata in PostgreSQL
    3. Handle failures gracefully with retries
    
    Args:
        imdb_ids: List of IMDB IDs to fetch
        metadata_type: 'movie' or 'series'
    """
    from utils.network import CircuitBreaker, batch_process_with_circuit_breaker
    from db.database import get_background_session
    
    circuit_breaker = CircuitBreaker(
        failure_threshold=2, recovery_timeout=5, half_open_attempts=2
    )
    
    # Use the appropriate fetch function
    async def fetch_and_store(imdb_id: str):
        async with get_background_session() as session:
            if metadata_type == "movie":
                return await get_movie_data_by_id(session, imdb_id, load_relations=True)
            else:
                return await get_series_data_by_id(session, imdb_id, load_relations=True)
    
    async for result in batch_process_with_circuit_breaker(
        fetch_and_store,
        imdb_ids,
        5,  # batch size
        rate_limit_delay=1,
        cb=circuit_breaker,
    ):
        if not result:
            continue
        
        logger.info(f"Stored metadata for {metadata_type} {result.id}")


async def update_single_imdb_metadata(
    session: AsyncSession,
    imdb_id: str,
    media_type: str,
) -> bool:
    """Update metadata for a single IMDB ID by fetching fresh data from IMDB/TMDB
    
    Args:
        session: Database session
        imdb_id: IMDB ID to update (e.g., "tt1234567")
        media_type: 'movie' or 'series'
        
    Returns:
        True if update was successful, False otherwise
    """
    from scrapers.scraper_tasks import meta_fetcher
    from db.enums import NudityStatus
    
    now = datetime.now(pytz.UTC)
    
    try:
        # Fetch fresh metadata from IMDB/TMDB
        result = await meta_fetcher.get_metadata(imdb_id, media_type=media_type)
        if not result:
            logger.warning(f"No metadata found from IMDB/TMDB for {imdb_id}")
            return False
        
        specific_model = MovieMetadata if media_type == "movie" else SeriesMetadata
        
        # Get existing metadata with all relationships
        existing = await session.exec(
            select(specific_model)
            .where(specific_model.id == imdb_id)
            .options(
                selectinload(specific_model.base_metadata).options(
                    selectinload(BaseMetadata.genres),
                    selectinload(BaseMetadata.aka_titles),
                ),
                selectinload(specific_model.parental_certificates),
                selectinload(specific_model.stars),
            )
        )
        existing_metadata = existing.one_or_none()
        
        if not existing_metadata:
            logger.warning(f"Metadata not found in database for {media_type} {imdb_id}")
            return False
        
        base = existing_metadata.base_metadata
        
        # Update base metadata
        base.title = result.get("title") or base.title
        base.poster = result.get("poster") or base.poster
        base.background = result.get("background") or base.background
        base.description = result.get("description") or base.description
        base.runtime = result.get("runtime") or base.runtime
        base.updated_at = now
        
        # Update type-specific metadata
        if result.get("imdb_rating") is not None:
            existing_metadata.imdb_rating = result["imdb_rating"]
        if result.get("tmdb_rating") is not None:
            existing_metadata.tmdb_rating = result["tmdb_rating"]
        
        nudity_status = result.get("parent_guide_nudity_status")
        if nudity_status and nudity_status != NudityStatus.UNKNOWN:
            existing_metadata.parent_guide_nudity_status = nudity_status
        
        # Helper to merge lists
        def merge_lists(existing_items, new_items, key_field="name"):
            existing_set = set()
            for item in existing_items or []:
                if hasattr(item, key_field):
                    existing_set.add(getattr(item, key_field))
                elif isinstance(item, dict) and key_field in item:
                    existing_set.add(item[key_field])
                elif isinstance(item, str):
                    existing_set.add(item)
            new_set = set(new_items or [])
            return list(existing_set | new_set)
        
        # Handle genres
        new_genres = merge_lists(base.genres, result.get("genres", []))
        await session.exec(
            sa_delete(MediaGenreLink).where(MediaGenreLink.media_id == imdb_id)
        )
        for genre_name in new_genres:
            genre = await get_or_create_genre(session, genre_name)
            session.add(MediaGenreLink(media_id=imdb_id, genre_id=genre.id))
        
        # Handle aka_titles
        new_aka_titles = merge_lists(base.aka_titles, result.get("aka_titles", []), "title")
        await session.exec(
            sa_delete(AkaTitle).where(AkaTitle.media_id == imdb_id)
        )
        for title in new_aka_titles:
            session.add(AkaTitle(title=title, media_id=imdb_id))
        
        # Handle stars
        new_stars = merge_lists(existing_metadata.stars, result.get("stars", []))
        await session.exec(
            sa_delete(MediaStarLink).where(MediaStarLink.media_id == imdb_id)
        )
        for star_name in new_stars:
            star = await get_or_create_star(session, star_name)
            session.add(MediaStarLink(media_id=imdb_id, star_id=star.id))
        
        # Handle parental certificates
        new_certs = merge_lists(
            existing_metadata.parental_certificates,
            result.get("parent_guide_certificates", [])
        )
        await session.exec(
            sa_delete(MediaParentalCertificateLink).where(
                MediaParentalCertificateLink.media_id == imdb_id
            )
        )
        for cert_name in new_certs:
            cert = await get_or_create_parental_certificate(session, cert_name)
            session.add(MediaParentalCertificateLink(
                media_id=imdb_id, certificate_id=cert.id
            ))
        
        # Handle series episodes
        if media_type == "series" and result.get("episodes"):
            await _update_series_episodes(session, imdb_id, existing_metadata, result["episodes"])
        
        await session.commit()
        logger.info(f"Successfully updated metadata for {media_type} {imdb_id}")
        
        # Invalidate cache
        await REDIS_ASYNC_CLIENT.delete(
            f"{media_type}_exists:{imdb_id}",
            f"{media_type}_data:{imdb_id}",
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Error updating metadata for {imdb_id}: {e}")
        await session.rollback()
        return False
