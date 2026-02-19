import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings

logger = logging.getLogger(__name__)

# Lazy engine initialization: engines are created per-process on first access.
# This prevents "Future attached to a different loop" errors when uvicorn forks
# workers (--workers N) or Dramatiq spawns child processes, because each forked
# process gets its own engine bound to its own event loop.
_engine_pid: int | None = None
_ASYNC_ENGINE: AsyncEngine | None = None
_ASYNC_READ_ENGINE: AsyncEngine | None = None


def _create_primary_engine() -> AsyncEngine:
    return create_async_engine(
        settings.postgres_uri,
        echo=False,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def _create_read_engine() -> AsyncEngine:
    if settings.postgres_read_uri:
        return create_async_engine(
            settings.postgres_read_uri,
            echo=False,
            pool_size=30,
            max_overflow=40,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _get_engine()


def _get_engine() -> AsyncEngine:
    """Return the primary read-write engine, creating it lazily if needed."""
    global _ASYNC_ENGINE, _ASYNC_READ_ENGINE, _engine_pid
    pid = os.getpid()
    if _ASYNC_ENGINE is None or _engine_pid != pid:
        _ASYNC_ENGINE = _create_primary_engine()
        _ASYNC_READ_ENGINE = None  # force re-creation for read engine too
        _engine_pid = pid
    return _ASYNC_ENGINE


def _get_read_engine() -> AsyncEngine:
    """Return the read-replica engine, creating it lazily if needed."""
    global _ASYNC_READ_ENGINE
    _get_engine()  # ensure primary is initialized (sets _engine_pid)
    if _ASYNC_READ_ENGINE is None:
        _ASYNC_READ_ENGINE = _create_read_engine()
    return _ASYNC_READ_ENGINE


# Public aliases for direct imports (e.g. database_admin.py).
# These are property-like module-level accessors; callers that import
# ASYNC_ENGINE should migrate to _get_engine() over time, but for
# backwards compatibility we expose lazy-init objects.
class _EngineProxy:
    """Thin proxy so ``from db.database import ASYNC_ENGINE`` still works
    but the real engine is created lazily per-process."""

    def __init__(self, getter):
        self._getter = getter

    def __getattr__(self, name):
        return getattr(self._getter(), name)


ASYNC_ENGINE = _EngineProxy(_get_engine)  # type: ignore[assignment]
ASYNC_READ_ENGINE = _EngineProxy(_get_read_engine)  # type: ignore[assignment]


def _create_fresh_engine() -> AsyncEngine:
    """Create a fresh async engine for use in background tasks (like Dramatiq).

    Uses NullPool to avoid event loop conflicts: pooled connections hold references
    to the event loop they were created on, causing 'Future attached to a different
    loop' errors when the Dramatiq worker's asyncio.run() creates a new loop.
    With NullPool, each connection is opened and closed inline — no pool state
    bleeds across event loops.
    """
    return create_async_engine(
        settings.postgres_uri,
        echo=False,
        poolclass=NullPool,
    )


@asynccontextmanager
async def get_background_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a session for background tasks (Dramatiq, etc.) that may run in a different event loop.
    Creates a fresh engine to avoid event loop conflicts.
    """
    engine = _create_fresh_engine()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose(close=True)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-write session for write operations"""
    session = AsyncSession(_get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"Error closing write session: {e}")


@asynccontextmanager
async def get_async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-write session as a context manager for background tasks.
    Use this when you need a session outside of FastAPI dependency injection.
    """
    session = AsyncSession(_get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"Error closing session: {e}")


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-only session optimized for read operations.
    Uses read replica if configured, otherwise falls back to primary.
    """
    session = AsyncSession(_get_read_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"Error closing read session: {e}")


@asynccontextmanager
async def get_read_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-only session as a context manager.
    Uses read replica if configured, otherwise falls back to primary.
    Use this when you need a read session outside of FastAPI dependency injection.
    """
    session = AsyncSession(_get_read_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            logger.warning(f"Error closing read session: {e}")


def _friendly_db_error(exc: BaseException) -> RuntimeError | None:
    """Return a user-friendly RuntimeError for known connection failures, or None."""
    cause = exc
    while cause is not None:
        if isinstance(cause, ConnectionRefusedError):
            msg = (
                "Cannot connect to PostgreSQL: connection refused. "
                "Check that PostgreSQL is running and that the host/port in POSTGRES_URI are correct."
            )
            err = RuntimeError(msg)
            err.__cause__ = exc
            return err
        if isinstance(cause, OSError) and getattr(cause, "errno", None) == 61:
            msg = (
                "Cannot connect to PostgreSQL: connection refused (errno 61). "
                "Check that PostgreSQL is running and that the host/port in POSTGRES_URI are correct."
            )
            err = RuntimeError(msg)
            err.__cause__ = exc
            return err
        if isinstance(cause, asyncio.TimeoutError):
            msg = (
                "Cannot connect to PostgreSQL: connection timed out. "
                "Check that PostgreSQL is running, reachable, and that the host/port in POSTGRES_URI are correct."
            )
            err = RuntimeError(msg)
            err.__cause__ = exc
            return err
        cause = getattr(cause, "__cause__", None)
    return None


async def init():
    """Initialize PostgreSQL connection and verify connectivity.

    This also forces lazy engine creation for the current process, so any
    connection errors are caught early during startup rather than on the
    first request.
    """
    engine = _get_engine()
    retries = 5
    last_exception: BaseException | None = None
    for i in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("PostgreSQL primary connection initialized successfully.")

            if settings.postgres_read_uri:
                read_engine = _get_read_engine()
                async with read_engine.begin() as conn:
                    await conn.execute(text("SELECT 1"))
                logger.info("PostgreSQL read replica connection initialized successfully.")

            return
        except Exception as e:
            last_exception = e
            if i < retries - 1:
                wait_time = 2**i
                logger.warning(
                    "PostgreSQL connection failed (%s), retrying in %s seconds...",
                    e,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
            else:
                break

    logger.error("Failed to initialize PostgreSQL after %d attempts.", retries)
    friendly = _friendly_db_error(last_exception) if last_exception else None
    if friendly is not None:
        raise friendly
    raise last_exception


async def close():
    """Close PostgreSQL connection pools"""
    if _ASYNC_ENGINE is not None:
        await _ASYNC_ENGINE.dispose()
    if _ASYNC_READ_ENGINE is not None and _ASYNC_READ_ENGINE is not _ASYNC_ENGINE:
        await _ASYNC_READ_ENGINE.dispose()
    logger.info("PostgreSQL connections closed.")
