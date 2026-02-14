"""Metadata service for fetching and processing metadata from multiple providers."""

import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from db import crud
from db.enums import MediaType
from db.models import Media, MetadataProvider

from .base import BaseService


class MetadataService(BaseService):
    """Service for metadata operations with multi-provider support.

    This service handles metadata fetching, caching, and processing
    from multiple providers (TMDB, TVDB, IMDb, MAL, Kitsu, Fanart, etc.).
    """

    # Cache TTL settings (in seconds)
    META_CACHE_TTL = 86400  # 24 hours
    SEARCH_CACHE_TTL = 300  # 5 minutes
    PROVIDER_CACHE_TTL = 3600  # 1 hour

    # Provider names
    PROVIDER_IMDB = "imdb"
    PROVIDER_TMDB = "tmdb"
    PROVIDER_TVDB = "tvdb"
    PROVIDER_MAL = "mal"
    PROVIDER_KITSU = "kitsu"
    PROVIDER_FANART = "fanart"
    PROVIDER_MEDIAFUSION = "mediafusion"

    def __init__(
        self,
        session: AsyncSession,
        logger: logging.Logger | None = None,
    ):
        """Initialize the metadata service."""
        super().__init__(session=session, logger=logger)
        self._providers_cache: dict[str, MetadataProvider] = {}

    async def get_provider(self, name: str) -> MetadataProvider:
        """Get or create a metadata provider by name (cached)."""
        if name not in self._providers_cache:
            self._providers_cache[name] = await crud.get_or_create_provider(self._session, name)
        return self._providers_cache[name]

    async def get_media(
        self,
        media_id: int,
        *,
        load_genres: bool = False,
        load_images: bool = False,
        load_ratings: bool = False,
    ) -> dict[str, Any] | None:
        """Get media by internal ID with optional related data."""
        media = await crud.get_media_by_id(self._session, media_id, load_genres=load_genres)
        if not media:
            return None

        result = media.model_dump() if hasattr(media, "model_dump") else dict(media)

        if load_images:
            images = await crud.get_images_for_media(self._session, media_id)
            result["images"] = [img.model_dump() for img in images]

        if load_ratings:
            ratings = await crud.get_ratings_for_media(self._session, media_id)
            mf_rating = await crud.providers_get_mediafusion_rating(self._session, media_id)
            result["ratings"] = {
                "providers": [r.model_dump() for r in ratings],
                "mediafusion": mf_rating,
            }

        return result

    async def get_media_by_external(
        self,
        external_id: str,
        media_type: MediaType | None = None,
    ) -> Media | None:
        """Get media by external ID (e.g., 'tt1234567' for IMDb)."""
        return await crud.get_media_by_external_id(self._session, external_id, media_type)

    async def resolve_id(
        self,
        external_id: str,
        provider_name: str,
    ) -> int | None:
        """Resolve an external provider ID to internal media ID."""
        return await crud.resolve_media_id(self._session, external_id, provider_name)

    async def search(
        self,
        query: str,
        media_type: MediaType | None = None,
        limit: int = 20,
    ) -> list[Media]:
        """Search media by title."""
        return list(await crud.search_media(self._session, query, media_type=media_type, limit=limit))

    async def create_or_update_media(
        self,
        external_id: str,
        media_type: MediaType,
        title: str,
        *,
        provider_name: str = PROVIDER_MEDIAFUSION,
        year: int | None = None,
        description: str | None = None,
        **kwargs,
    ) -> Media:
        """Create or update media from provider data.

        Note: Images are stored separately via add_image(), not inline.
        """
        # Check if media exists
        existing = await crud.get_media_by_external_id(self._session, external_id, media_type)

        if existing:
            # Update existing media
            return await crud.update_media(
                self._session,
                existing.id,
                title=title,
                year=year,
                description=description,
                **kwargs,
            )

        # Create new media
        return await crud.create_media(
            self._session,
            external_id=external_id,
            media_type=media_type,
            title=title,
            year=year,
            description=description,
            **kwargs,
        )

    async def add_provider_id(
        self,
        media_id: int,
        provider_name: str,
        external_id: str,
    ) -> None:
        """Add an external provider ID mapping to media."""
        provider = await self.get_provider(provider_name)
        await crud.add_id_mapping(self._session, media_id, provider.id, external_id)

    async def get_all_external_ids(
        self,
        media_id: int,
    ) -> dict[str, str]:
        """Get all external IDs for a media entry."""
        mappings = await crud.get_id_mappings(self._session, media_id)
        providers = await crud.get_all_providers(self._session)
        provider_map = {p.id: p.name for p in providers}

        return {provider_map.get(m.provider_id, "unknown"): m.external_id for m in mappings}

    async def add_image(
        self,
        media_id: int,
        url: str,
        image_type: str,
        *,
        provider_name: str | None = None,
        width: int | None = None,
        height: int | None = None,
        is_primary: bool = False,
    ) -> None:
        """Add an image to media."""
        provider_id = None
        if provider_name:
            provider = await self.get_provider(provider_name)
            provider_id = provider.id

        await crud.providers_add_media_image(
            self._session,
            media_id,
            url,
            image_type,
            provider_id=provider_id,
            width=width,
            height=height,
            is_primary=is_primary,
        )

    async def add_rating(
        self,
        media_id: int,
        provider_name: str,
        rating: float,
        vote_count: int | None = None,
    ) -> None:
        """Add or update a rating from a provider."""
        provider = await crud.get_or_create_rating_provider(self._session, provider_name)
        await crud.add_or_update_rating(
            self._session,
            media_id,
            provider.id,
            rating,
            vote_count=vote_count,
        )

    async def store_raw_provider_data(
        self,
        media_id: int,
        provider_name: str,
        raw_data: dict,
    ) -> None:
        """Store raw API response from a provider."""
        provider = await self.get_provider(provider_name)
        await crud.store_provider_metadata(self._session, media_id, provider.id, raw_data)

    async def get_raw_provider_data(
        self,
        media_id: int,
        provider_name: str,
    ) -> dict | None:
        """Get stored raw provider data."""
        provider = await self.get_provider(provider_name)
        result = await crud.get_provider_metadata(self._session, media_id, provider.id)
        return result.raw_data if result else None
