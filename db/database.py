import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings

logger = logging.getLogger(__name__)

# PostgreSQL connection engines
# Primary read-write engine
ASYNC_ENGINE: AsyncEngine = create_async_engine(
    settings.postgres_uri,
    echo=False,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Read replica engine (uses primary if no replica configured)
ASYNC_READ_ENGINE: AsyncEngine = (
    create_async_engine(
        settings.postgres_read_uri,
        echo=False,
        pool_size=30,  # More connections for read-heavy workloads
        max_overflow=40,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    if settings.postgres_read_uri
    else ASYNC_ENGINE
)


def _create_fresh_engine() -> AsyncEngine:
    """Create a fresh async engine for use in background tasks (like Dramatiq).
    This avoids event loop conflicts when running in a different context.
    """
    return create_async_engine(
        settings.postgres_uri,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
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
        await engine.dispose()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-write session for write operations"""
    session = AsyncSession(ASYNC_ENGINE, expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            # Log but don't raise - session cleanup errors shouldn't crash requests
            logger.warning(f"Error closing write session: {e}")


@asynccontextmanager
async def get_async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-write session as a context manager for background tasks.
    Use this when you need a session outside of FastAPI dependency injection.
    """
    session = AsyncSession(ASYNC_ENGINE, expire_on_commit=False)
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
    session = AsyncSession(ASYNC_READ_ENGINE, expire_on_commit=False)
    try:
        yield session
    finally:
        try:
            await session.close()
        except Exception as e:
            # Log but don't raise - session cleanup errors shouldn't crash requests
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
    """Initialize PostgreSQL connection and verify connectivity"""
    retries = 5
    last_exception: BaseException | None = None
    for i in range(retries):
        try:
            async with ASYNC_ENGINE.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("PostgreSQL primary connection initialized successfully.")

            if settings.postgres_read_uri:
                async with ASYNC_READ_ENGINE.begin() as conn:
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
    await ASYNC_ENGINE.dispose()
    if settings.postgres_read_uri:
        await ASYNC_READ_ENGINE.dispose()
    logger.info("PostgreSQL connections closed.")
