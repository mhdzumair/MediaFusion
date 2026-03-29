import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy import event, exc, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings

_REPLICA_CONFLICT_MSG = "conflict with recovery"


logger = logging.getLogger(__name__)

# Lazy engine initialization: engines are created per-process on first access.
# This prevents "Future attached to a different loop" errors when uvicorn forks
# workers (--workers N) or Dramatiq spawns child processes, because each forked
# process gets its own engine bound to its own event loop.
_engine_pid: int | None = None
_engine_loop_id: int | None = None
_ASYNC_ENGINE: AsyncEngine | None = None
_ASYNC_READ_ENGINE: AsyncEngine | None = None
_read_engine_loop_id: int | None = None


def _get_running_loop_id() -> int | None:
    """Return current running loop id, or None when no loop is active."""
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


def _derive_pool_sizes() -> tuple[int, int]:
    """Derive per-process pool sizes from DB_MAX_CONNECTIONS.

    DB_MAX_CONNECTIONS is treated as the total connection budget for one app
    instance (all workers + engines combined). We divide that budget by the
    configured Gunicorn worker count and active engine count (primary + read
    replica when configured), then split each engine budget into:
    - pool_size: 75% steady-state connections
    - max_overflow: 25% burst capacity
    """
    total_budget = max(1, settings.db_max_connections)
    worker_count = max(1, settings.gunicorn_workers)
    engine_count = 2 if settings.postgres_read_uri else 1

    per_worker_budget = max(1, total_budget // worker_count)
    per_engine_budget = max(1, per_worker_budget // engine_count)

    pool_size = max(1, (per_engine_budget * 3) // 4)
    max_overflow = max(0, per_engine_budget - pool_size)
    return pool_size, max_overflow


def _install_asyncpg_guards(engine: AsyncEngine) -> None:
    """Register pool/engine events to harden asyncpg connections.

    1. Checkout guard: asyncpg's internal protocol state machine can get stuck
       mid-operation if a previous request was interrupted.  pool_pre_ping only
       validates the PostgreSQL session, not asyncpg's state.  We inspect the
       raw protocol on checkout and invalidate dirty connections.

    2. Replica conflict handler: on a streaming-replication read replica,
       long-running queries can be cancelled by the primary's VACUUM via
       "SerializationError: conflict with recovery".  We treat this as a
       disconnect so SQLAlchemy invalidates the connection and the caller
       gets a retryable error rather than a corrupt connection.
    """

    @event.listens_for(engine.sync_engine, "checkout")
    def _check_asyncpg_state(dbapi_connection, connection_record, connection_proxy):
        raw = dbapi_connection.driver_connection
        if raw is None or raw.is_closed():
            raise exc.DisconnectionError("asyncpg connection is closed")
        protocol = getattr(raw, "_protocol", None)
        if protocol is not None:
            state = getattr(protocol, "state", None)
            if state is not None and state != 0:
                logger.warning(
                    "Discarding asyncpg connection with dirty protocol state %s",
                    state,
                )
                raise exc.DisconnectionError(f"asyncpg protocol in state {state}, expected idle (0)")

    @event.listens_for(engine.sync_engine, "handle_error")
    def _handle_asyncpg_disconnect_signals(context):
        orig = context.original_exception
        if orig is None:
            return
        msg = str(orig)
        if _REPLICA_CONFLICT_MSG in msg:
            logger.warning("Read replica conflict detected, invalidating connection")
            context.invalidate_pool_on_disconnect = False
            context.is_disconnect = True
            return
        if isinstance(orig, asyncpg.exceptions.ConnectionDoesNotExistError):
            logger.warning("asyncpg connection closed mid-operation, invalidating connection")
            context.invalidate_pool_on_disconnect = False
            context.is_disconnect = True
            return
        lowered = msg.lower()
        if "connection was closed in the middle of operation" in lowered:
            logger.warning("DB connection closed mid-operation, invalidating connection")
            context.invalidate_pool_on_disconnect = False
            context.is_disconnect = True


def _create_primary_engine() -> AsyncEngine:
    pool_size, max_overflow = _derive_pool_sizes()
    engine = create_async_engine(
        settings.postgres_uri,
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=30,
    )
    _install_asyncpg_guards(engine)
    return engine


def _create_read_engine() -> AsyncEngine:
    if settings.postgres_read_uri:
        pool_size, max_overflow = _derive_pool_sizes()
        engine = create_async_engine(
            settings.postgres_read_uri,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_timeout=30,
        )
        _install_asyncpg_guards(engine)
        return engine
    return _get_engine()


def _get_engine() -> AsyncEngine:
    """Return the primary read-write engine, creating it lazily if needed."""
    global _ASYNC_ENGINE, _ASYNC_READ_ENGINE, _engine_pid, _engine_loop_id, _read_engine_loop_id
    pid = os.getpid()
    loop_id = _get_running_loop_id()
    if _ASYNC_ENGINE is None or _engine_pid != pid or _engine_loop_id != loop_id:
        _ASYNC_ENGINE = _create_primary_engine()
        _ASYNC_READ_ENGINE = None  # force re-creation for read engine too
        _engine_pid = pid
        _engine_loop_id = loop_id
        _read_engine_loop_id = None
    return _ASYNC_ENGINE


def _get_read_engine() -> AsyncEngine:
    """Return the read-replica engine, creating it lazily if needed."""
    global _ASYNC_READ_ENGINE, _read_engine_loop_id
    loop_id = _get_running_loop_id()
    _get_engine()  # ensure primary is initialized (sets _engine_pid)
    if _ASYNC_READ_ENGINE is None or _read_engine_loop_id != loop_id:
        _ASYNC_READ_ENGINE = _create_read_engine()
        _read_engine_loop_id = loop_id
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


async def _safe_close_session(session: AsyncSession) -> None:
    """Close a session, properly handling dead connections.

    When a TCP connection drops mid-request (load balancer timeout, PG restart,
    network blip), session.close() attempts ROLLBACK on a dead socket and raises
    InterfaceError.  The pool will already discard the dead connection on the
    next checkout (pool_pre_ping=True), so the close-time failure is safe to
    absorb.  We log at DEBUG since this is expected in cloud/LB environments.
    """
    try:
        await asyncio.shield(session.close())
    except asyncio.CancelledError:
        # Ensure close still runs even when request/task cancellation interrupts cleanup.
        try:
            await session.close()
        except Exception as close_err:
            logger.debug("Session close failed after cancellation: %s", close_err)
        raise
    except Exception as close_err:
        logger.debug("Session close failed (dead connection, will be discarded by pool): %s", close_err)


def _create_fresh_engine() -> AsyncEngine:
    """Create a fresh async engine for use in background tasks (like Dramatiq).

    Uses NullPool to avoid event loop conflicts: pooled connections hold references
    to the event loop they were created on, causing 'Future attached to a different
    loop' errors when the Dramatiq worker's asyncio.run() creates a new loop.
    With NullPool, each connection is opened and closed inline — no pool state
    bleeds across event loops.
    """
    engine = create_async_engine(
        settings.postgres_uri,
        echo=False,
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    _install_asyncpg_guards(engine)
    return engine


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
        await _safe_close_session(session)


@asynccontextmanager
async def get_async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-write session as a context manager for background tasks.
    Use this when you need a session outside of FastAPI dependency injection.

    Persists changes: call ``await session.commit()`` before exiting the block
    when you performed writes. On exit without commit, the session rolls back
    and no data is saved (CRUD helpers do not commit on their own).
    """
    session = AsyncSession(_get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        await _safe_close_session(session)


async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a read-only session optimized for read operations.
    Uses read replica if configured, otherwise falls back to primary.
    """
    session = AsyncSession(_get_read_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        await _safe_close_session(session)


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
        await _safe_close_session(session)


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
    global _engine_loop_id, _read_engine_loop_id
    if _ASYNC_ENGINE is not None:
        await _ASYNC_ENGINE.dispose()
    if _ASYNC_READ_ENGINE is not None and _ASYNC_READ_ENGINE is not _ASYNC_ENGINE:
        await _ASYNC_READ_ENGINE.dispose()
    _engine_loop_id = None
    _read_engine_loop_id = None
    logger.info("PostgreSQL connections closed.")
