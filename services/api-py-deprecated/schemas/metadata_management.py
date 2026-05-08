"""Shared schemas for metadata management workflows."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
