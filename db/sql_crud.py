import logging

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select, or_
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql._expression_select_cls import Select

from db import data_models, public_schemas
from db.schemas import UserData
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
)
from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)

from abc import ABC
from typing import Optional, List, TypeVar, Generic, Type, Any

B = TypeVar("B", bound="BaseQueryBuilder")


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
                self.base_query = (
                    self.base_query.join(MediaParentalCertificateLink)
                    .join(ParentalCertificate)
                    .where(
                        ParentalCertificate.name.notin_(
                            self.user_data.certification_filter
                        )
                    )
                )
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
        if not self.is_watchlist:
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
        self.search_query = search_query.lower()

    def add_text_search(self) -> "SearchQueryBuilder":
        """Optimized text search for all languages"""
        search_vector = func.plainto_tsquery("simple", self.search_query)

        # Build efficient subqueries
        base_matches = (
            select(BaseMetadata.id)
            .where(
                or_(
                    BaseMetadata.title_tsv.op("@@")(search_vector),
                    func.similarity(func.lower(BaseMetadata.title), self.search_query)
                    > 0.3,
                )
            )
            .subquery()
        )

        aka_matches = (
            select(AkaTitle.media_id)
            .where(
                or_(
                    AkaTitle.title_tsv.op("@@")(search_vector),
                    func.similarity(func.lower(AkaTitle.title), self.search_query)
                    > 0.3,
                )
            )
            .subquery()
        )

        # Combine results efficiently
        self.base_query = self.base_query.filter(
            or_(BaseMetadata.id.in_(base_matches), BaseMetadata.id.in_(aka_matches))
        ).order_by(
            func.greatest(
                func.ts_rank_cd(BaseMetadata.title_tsv, search_vector),
                func.similarity(func.lower(BaseMetadata.title), self.search_query),
            ).desc()
        )
        return self

    def add_torrent_stream_filter(self) -> "SearchQueryBuilder":
        """Add torrent stream specific filters"""
        if self.catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
            self.base_query = self.base_query.join(TorrentStream).where(
                TorrentStream.is_blocked != True,
                TorrentStream.meta_id == BaseMetadata.id,
            )
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

    async def _fetch_metadata(
        self, session: AsyncSession, media_id: str
    ) -> Optional[M]:
        """Fetch type-specific metadata"""
        query = (
            select(self.sql_model)
            .where(self.sql_model.id == media_id)
            .options(
                joinedload(self.sql_model.base_metadata).options(
                    selectinload(BaseMetadata.genres),
                    selectinload(BaseMetadata.catalogs),
                    selectinload(BaseMetadata.aka_titles),
                )
            )
        )

        if self.media_type in [MediaType.MOVIE, MediaType.SERIES]:
            query = query.options(
                selectinload(self.sql_model.parental_certificates),
                selectinload(self.sql_model.stars),
            )

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

        metadata = await self._fetch_metadata(session, media_id)
        if not metadata:
            return None

        # Construct the full metadata object
        metadata = self.data_model.model_validate(metadata)

        await self._set_cache(media_id, metadata)
        return metadata


class SeriesMetadataRetriever(
    MetadataRetriever[data_models.SeriesData, SeriesMetadata]
):
    """Series-specific metadata retriever with episode information"""

    async def get_metadata(
        self, session: AsyncSession, media_id: str, bypass_cache: bool = False
    ) -> Optional[data_models.SeriesData]:
        """Fetch series metadata with episodes"""
        metadata = await super().get_metadata(session, media_id, bypass_cache)
        if not metadata:
            return None

        # Fetch episode data
        season_data = await self.get_season_data(session, media_id)
        metadata.seasons = season_data
        return metadata

    async def get_season_data(self, session: AsyncSession, series_id: str) -> list:
        """Fetch season data for series"""
        # TODO: Implement season data retrieval
        return []


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
) -> Optional[Any]:
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
