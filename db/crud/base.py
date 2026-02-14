"""Base query builders for catalog and search operations."""

from abc import ABC
from typing import Generic, Literal, TypeVar

from sqlalchemy import asc, desc, func, union
from sqlmodel import select
from sqlmodel.sql._expression_select_cls import Select

from db.enums import MediaType
from db.models import (
    AkaTitle,
    Catalog,
    # Reference
    Genre,
    # Core models
    Media,
    MediaCatalogLink,
    # Links
    MediaGenreLink,
    MediaParentalCertificateLink,
    # Ratings
    MediaRating,
    MovieMetadata,
    ParentalCertificate,
    RatingProvider,
    SeriesMetadata,
    # Streams
    Stream,
    StreamMediaLink,
    TorrentStream,
    TVMetadata,
)
from db.schemas import UserData

B = TypeVar("B", bound="CatalogBaseQueryBuilder")


class CatalogBaseQueryBuilder(ABC, Generic[B]):
    """Base class for query builders with common functionality."""

    def __init__(
        self,
        catalog_type: MediaType,
        user_data: UserData,
    ):
        self.catalog_type = catalog_type
        self.user_data = user_data
        self.base_query = select(Media.id, Media.title)

    def add_type_filter(self) -> B:
        """Add media type filter."""
        self.base_query = self.base_query.where(Media.type == self.catalog_type)
        return self

    def add_content_filters(self) -> B:
        """Add user preference based content filters."""
        if self.catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
            specific_model = MovieMetadata if self.catalog_type == MediaType.MOVIE else SeriesMetadata
            self.base_query = self.base_query.join(specific_model, specific_model.media_id == Media.id)

            if "Disable" not in self.user_data.nudity_filter:
                self.base_query = self.base_query.where(
                    specific_model.parent_guide_nudity_status.notin_(self.user_data.nudity_filter)
                )

            if "Disable" not in self.user_data.certification_filter:
                blocked_cert_exists = (
                    select(MediaParentalCertificateLink.media_id)
                    .join(ParentalCertificate)
                    .where(
                        MediaParentalCertificateLink.media_id == Media.id,
                        ParentalCertificate.name.in_(self.user_data.certification_filter),
                    )
                    .exists()
                )
                self.base_query = self.base_query.where(~blocked_cert_exists)
        return self

    def add_tv_filters(self, user_id: str | None = None) -> B:
        """Add TV-specific filters.

        v5 schema: Namespace concept is removed. TV streams are filtered
        by source or user ownership instead.
        """
        if self.catalog_type == MediaType.TV:
            # Join through the unified stream architecture
            self.base_query = (
                self.base_query.join(TVMetadata, TVMetadata.media_id == Media.id)
                .join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
                .join(Stream, Stream.id == StreamMediaLink.stream_id)
                .where(Stream.is_active.is_(True), Stream.is_blocked.is_(False))
            )

            # Filter by user if provided (for user-specific streams)
            if user_id:
                # Include public streams and user's own streams
                self.base_query = self.base_query.where(
                    (Stream.uploader_user_id == user_id) | (Stream.is_public.is_(True))
                )
        return self

    def add_pagination(self, skip: int = 0, limit: int = 25) -> B:
        """Add pagination."""
        self.base_query = self.base_query.offset(skip).limit(limit)
        return self

    def build(self) -> Select:
        """Build the final query."""
        return self.base_query


class CatalogQueryBuilder(CatalogBaseQueryBuilder["CatalogQueryBuilder"]):
    """Builder for constructing optimized catalog queries."""

    def __init__(self, catalog_type: MediaType, user_data: UserData, is_watchlist: bool = False):
        super().__init__(catalog_type, user_data)
        self.is_watchlist = is_watchlist

    def add_watchlist_filter(self, info_hashes: list[str] | None = None) -> "CatalogQueryBuilder":
        """Add watchlist-specific filters using torrent info_hashes.

        Args:
            info_hashes: List of torrent info hashes from streaming provider watchlist
        """
        if self.is_watchlist and info_hashes:
            self.base_query = (
                self.base_query.join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
                .join(Stream, Stream.id == StreamMediaLink.stream_id)
                .join(TorrentStream, TorrentStream.stream_id == Stream.id)
                .where(TorrentStream.info_hash.in_([h.lower() for h in info_hashes]))
            )
        return self

    def add_catalog_filter(self, catalog_id: str) -> "CatalogQueryBuilder":
        """Add catalog-specific filters."""
        if not self.is_watchlist and self.catalog_type != MediaType.TV:
            self.base_query = self.base_query.join(MediaCatalogLink).join(Catalog).where(Catalog.name == catalog_id)
        return self

    def add_genre_filter(self, genre: str | None) -> "CatalogQueryBuilder":
        """Add genre-specific filters."""
        if genre:
            self.base_query = self.base_query.join(MediaGenreLink).join(Genre).where(Genre.name == genre)
        return self

    def add_sorting(
        self,
        sort: Literal["latest", "popular", "rating", "year", "title", "release_date"] | None = None,
        sort_dir: Literal["asc", "desc"] | None = None,
    ) -> "CatalogQueryBuilder":
        """Add sorting based on user preferences.

        Args:
            sort: Sort field. Options: latest, popular, rating, year, title, release_date.
                  Defaults to "latest" if not specified.
            sort_dir: Sort direction. Options: asc, desc. Defaults to "desc".

        Returns:
            Self for method chaining.
        """
        # Default values
        sort = sort or "latest"
        sort_dir = sort_dir or "desc"

        order_func = asc if sort_dir == "asc" else desc
        nulls_position = "nulls_first" if sort_dir == "asc" else "nulls_last"

        if sort == "latest":
            order_expr = order_func(Media.last_stream_added)
            self.base_query = self.base_query.order_by(getattr(order_expr, nulls_position)())
        elif sort in ("popular", "rating"):
            # Sort by IMDb rating with fallback to total_streams
            # Uses a correlated subquery to get IMDb rating
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
            self.base_query = self.base_query.order_by(
                getattr(rating_order, nulls_position)(),
                getattr(streams_order, nulls_position)(),
            )
        elif sort == "year":
            order_expr = order_func(Media.year)
            self.base_query = self.base_query.order_by(getattr(order_expr, nulls_position)())
        elif sort == "release_date":
            order_expr = order_func(Media.release_date)
            self.base_query = self.base_query.order_by(getattr(order_expr, nulls_position)())
        elif sort == "title":
            # Title sort: asc = A-Z, desc = Z-A
            self.base_query = self.base_query.order_by(order_func(Media.title))
        else:
            # Fallback to latest
            order_expr = order_func(Media.last_stream_added)
            self.base_query = self.base_query.order_by(getattr(order_expr, nulls_position)())

        return self


class SearchQueryBuilder(CatalogBaseQueryBuilder["SearchQueryBuilder"]):
    """Builder for constructing optimized search queries."""

    def __init__(
        self,
        catalog_type: MediaType,
        user_data: UserData,
        search_query: str,
    ):
        super().__init__(catalog_type, user_data)
        self.search_query = search_query.strip()

    def add_text_search(self) -> "SearchQueryBuilder":
        """
        Optimized text search using indexed operations:
        1. Full-text search with @@ operator (uses GIN index on title_tsv)
        2. Trigram search with % operator (uses GIN index with gin_trgm_ops)
        """
        search_vector = func.plainto_tsquery("simple", self.search_query.lower())

        fts_base_matches = (
            select(Media.id)
            .where(
                Media.title_tsv.op("@@")(search_vector),
                Media.type == self.catalog_type,
            )
            .limit(200)
        )

        fts_aka_matches = select(AkaTitle.media_id).where(AkaTitle.title_tsv.op("@@")(search_vector)).limit(200)

        trgm_base_matches = (
            select(Media.id)
            .where(
                Media.title.op("%")(self.search_query),
                Media.type == self.catalog_type,
            )
            .limit(100)
        )

        all_matches = union(fts_base_matches, fts_aka_matches, trgm_base_matches).subquery()

        self.base_query = self.base_query.filter(Media.id.in_(select(all_matches.c.id))).order_by(
            func.ts_rank_cd(Media.title_tsv, search_vector).desc(), Media.title
        )
        return self

    def add_stream_filter(self) -> "SearchQueryBuilder":
        """Add stream specific filters using EXISTS for better performance."""
        if self.catalog_type in [MediaType.MOVIE, MediaType.SERIES]:
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
            self.base_query = self.base_query.where(stream_exists)
        return self
