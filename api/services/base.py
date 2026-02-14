"""Base service class for all services."""

import logging
from abc import ABC

from sqlmodel.ext.asyncio.session import AsyncSession

from db.redis_database import REDIS_ASYNC_CLIENT


class BaseService(ABC):
    """Base class for all services.

    Provides common functionality like logging and database access.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ):
        """Initialize the service.

        Args:
            session: Optional database session for DB operations.
            logger: Optional logger instance. If not provided, creates one.
        """
        self._session = session
        self._logger = logger or logging.getLogger(self.__class__.__name__)
        self._redis = REDIS_ASYNC_CLIENT

    @property
    def session(self) -> AsyncSession | None:
        """Get the database session."""
        return self._session

    @property
    def logger(self) -> logging.Logger:
        """Get the logger."""
        return self._logger

    @property
    def redis(self):
        """Get the Redis client."""
        return self._redis

    async def get_cached(self, key: str) -> str | None:
        """Get a value from Redis cache.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if not found.
        """
        return await self._redis.get(key)

    async def set_cached(self, key: str, value: str, ttl: int = 3600) -> None:
        """Set a value in Redis cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time to live in seconds (default: 1 hour).
        """
        await self._redis.set(key, value, ex=ttl)

    async def delete_cached(self, key: str) -> None:
        """Delete a value from Redis cache.

        Args:
            key: Cache key.
        """
        await self._redis.delete(key)
