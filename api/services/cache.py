"""Cache management service."""

import logging

from .base import BaseService


class CacheService(BaseService):
    """Service for cache management operations."""

    # Cache key prefixes
    CATALOG_PREFIX = "catalog:"
    SEARCH_PREFIX = "search:"
    META_PREFIX = "meta:"
    STREAM_PREFIX = "stream:"
    POSTER_PREFIX = "poster:"

    def __init__(self, logger: logging.Logger | None = None):
        """Initialize the cache service."""
        super().__init__(session=None, logger=logger)

    async def get_catalog_cache_key(
        self,
        catalog_type: str,
        catalog_id: str,
        skip: int = 0,
        genre: str | None = None,
        namespace: str = "",
        filters: list[str] | None = None,
    ) -> str:
        """Generate a cache key for catalog queries.

        Args:
            catalog_type: Type of catalog (movie, series, tv).
            catalog_id: Catalog identifier.
            skip: Pagination offset.
            genre: Optional genre filter.
            namespace: Optional namespace (for TV).
            filters: Additional filter values.

        Returns:
            Cache key string.
        """
        key_parts = [catalog_type, catalog_id, str(skip), genre or ""]
        if filters:
            key_parts.extend(filters)
        if namespace:
            key_parts.append(namespace)
        return f"{self.CATALOG_PREFIX}{':'.join(key_parts)}"

    async def get_search_cache_key(
        self,
        catalog_type: str,
        catalog_id: str,
        search_query: str,
        namespace: str = "",
        filters: list[str] | None = None,
    ) -> str:
        """Generate a cache key for search results.

        Args:
            catalog_type: Type of catalog.
            catalog_id: Catalog identifier.
            search_query: Search query string.
            namespace: Optional namespace.
            filters: Additional filter values.

        Returns:
            Cache key string.
        """
        key_parts = [catalog_type, catalog_id, search_query]
        if filters:
            key_parts.extend(filters)
        if namespace:
            key_parts.append(namespace)
        return f"{self.SEARCH_PREFIX}{':'.join(key_parts)}"

    async def invalidate_by_pattern(self, pattern: str) -> int:
        """Invalidate cache entries matching a pattern.

        Args:
            pattern: Redis key pattern (e.g., "catalog:movie:*").

        Returns:
            Number of keys deleted.
        """
        deleted = 0
        async for key in self._redis.scan_iter(match=pattern):
            await self._redis.delete(key)
            deleted += 1
        return deleted

    async def invalidate_catalog(
        self,
        catalog_type: str | None = None,
        catalog_id: str | None = None,
    ) -> int:
        """Invalidate catalog cache entries.

        Args:
            catalog_type: Optional catalog type to filter.
            catalog_id: Optional catalog ID to filter.

        Returns:
            Number of keys deleted.
        """
        if catalog_type and catalog_id:
            pattern = f"{self.CATALOG_PREFIX}{catalog_type}:{catalog_id}:*"
        elif catalog_type:
            pattern = f"{self.CATALOG_PREFIX}{catalog_type}:*"
        else:
            pattern = f"{self.CATALOG_PREFIX}*"
        return await self.invalidate_by_pattern(pattern)

    async def invalidate_search(
        self,
        catalog_type: str | None = None,
    ) -> int:
        """Invalidate search cache entries.

        Args:
            catalog_type: Optional catalog type to filter.

        Returns:
            Number of keys deleted.
        """
        if catalog_type:
            pattern = f"{self.SEARCH_PREFIX}{catalog_type}:*"
        else:
            pattern = f"{self.SEARCH_PREFIX}*"
        return await self.invalidate_by_pattern(pattern)

    async def get_cache_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache statistics.
        """
        info = await self._redis.info("memory")
        keys_info = await self._redis.info("keyspace")

        # Count keys by prefix
        prefix_counts = {}
        for prefix in [
            self.CATALOG_PREFIX,
            self.SEARCH_PREFIX,
            self.META_PREFIX,
            self.STREAM_PREFIX,
            self.POSTER_PREFIX,
        ]:
            count = 0
            async for _ in self._redis.scan_iter(match=f"{prefix}*", count=1000):
                count += 1
            prefix_counts[prefix.rstrip(":")] = count

        return {
            "memory_used": info.get("used_memory_human", "N/A"),
            "memory_peak": info.get("used_memory_peak_human", "N/A"),
            "keys_by_type": prefix_counts,
            "keyspace": keys_info,
        }
