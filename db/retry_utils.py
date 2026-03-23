import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg
from sqlalchemy.exc import DBAPIError, PendingRollbackError, TimeoutError as SQLAlchemyTimeoutError

T = TypeVar("T")

RETRYABLE_DB_ERROR_MARKERS = (
    "broken pipe",
    "connection reset by peer",
    "connection does not exist",
    "connection is closed",
    "connection was closed",
    "underlying connection is closed",
    "closed in the middle of operation",
    "server closed the connection unexpectedly",
    "terminating connection due to administrator command",
    "another operation is in progress",
    "cannot call transaction.commit()",
    "cannot switch to state",
    "can't reconnect until invalid transaction is rolled back",
    "too many open files",
    "unexpected connection_lost() call",
    "conflict with recovery",
    "canceling statement due to conflict with recovery",
    "queuepool limit",
    "connection timed out",
    "connectiondoesnotexist",
)


def is_retryable_db_error(exc: BaseException) -> bool:
    """Return True when exception chain indicates a transient DB disconnect."""
    to_visit: list[BaseException] = [exc]
    visited: set[int] = set()

    while to_visit:
        current = to_visit.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))

        if isinstance(current, (BrokenPipeError, ConnectionError, TimeoutError, SQLAlchemyTimeoutError)):
            return True
        if isinstance(current, PendingRollbackError):
            return True
        if isinstance(current, DBAPIError) and current.connection_invalidated:
            return True
        if isinstance(current, asyncpg.exceptions.ConnectionDoesNotExistError):
            return True

        message = str(current).lower()
        if any(marker in message for marker in RETRYABLE_DB_ERROR_MARKERS):
            return True

        cause = getattr(current, "__cause__", None)
        if isinstance(cause, BaseException):
            to_visit.append(cause)

        context = getattr(current, "__context__", None)
        if isinstance(context, BaseException):
            to_visit.append(context)

        grouped_errors = getattr(current, "exceptions", None)
        if isinstance(grouped_errors, tuple):
            for grouped_error in grouped_errors:
                if isinstance(grouped_error, BaseException):
                    to_visit.append(grouped_error)

    return False


async def run_db_operation_with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    max_attempts: int = 3,
    initial_delay_seconds: float = 0.5,
    before_retry: Callable[[int, int, Exception], Awaitable[None] | None] | None = None,
    on_retry: Callable[[int, int, Exception], Awaitable[None] | None] | None = None,
) -> T:
    """Run async DB operation with retry on transient disconnect errors."""
    delay_seconds = initial_delay_seconds
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable_db_error(exc) or attempt >= max_attempts:
                raise
            if before_retry is not None:
                maybe_awaitable = before_retry(attempt, max_attempts, exc)
                if isinstance(maybe_awaitable, Awaitable):
                    await maybe_awaitable
            if on_retry is not None:
                maybe_awaitable = on_retry(attempt, max_attempts, exc)
                if isinstance(maybe_awaitable, Awaitable):
                    await maybe_awaitable
            await asyncio.sleep(delay_seconds)
            delay_seconds *= 2

    raise RuntimeError(f"DB retry loop exhausted for {operation_name}")


async def run_db_read_with_primary_fallback(
    read_operation: Callable[[], Awaitable[T]],
    primary_operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    read_max_attempts: int = 3,
    primary_max_attempts: int = 2,
    initial_delay_seconds: float = 0.5,
    on_fallback: Callable[[Exception], Awaitable[None] | None] | None = None,
) -> T:
    """Run read operation on replica with retry, then fallback to primary.

    Only retryable DB errors trigger primary fallback. Non-transient errors are
    raised immediately from the read operation.
    """
    try:
        return await run_db_operation_with_retry(
            read_operation,
            operation_name=f"{operation_name} [read replica]",
            max_attempts=read_max_attempts,
            initial_delay_seconds=initial_delay_seconds,
        )
    except Exception as exc:
        if not is_retryable_db_error(exc):
            raise
        if on_fallback is not None:
            maybe_awaitable = on_fallback(exc)
            if isinstance(maybe_awaitable, Awaitable):
                await maybe_awaitable

    return await run_db_operation_with_retry(
        primary_operation,
        operation_name=f"{operation_name} [primary fallback]",
        max_attempts=primary_max_attempts,
        initial_delay_seconds=initial_delay_seconds,
    )
