"""Global exception tracker that stores server tracebacks in Redis.

Captures exceptions via a custom ``logging.Handler`` that intercepts all
ERROR/CRITICAL log records containing exception info.  This covers:

- Unhandled request exceptions (caught by ``TimingMiddleware``)
- Handled exceptions logged via ``logging.exception()`` / ``logger.exception()``
- Background-task exceptions (Dramatiq workers, scrapers, schedulers)

Exceptions are fingerprinted by type + file + line so duplicate occurrences
are deduplicated with an incrementing counter.  A sorted-set index enables
efficient paginated listing for the admin API.

Redis keys used:
    exc:{fingerprint}  -- hash with exception detail (TTL = settings.exception_tracking_ttl)
    exc:index          -- sorted set of fingerprints scored by last_seen timestamp
"""

import hashlib
import logging
import traceback as tb_module
from datetime import datetime, timezone

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT, REDIS_SYNC_CLIENT

_logger = logging.getLogger(__name__)

# Redis key constants
_INDEX_KEY = "exc:index"
_KEY_PREFIX = "exc:"


# ============================================
# Fingerprinting
# ============================================


def _generate_fingerprint_from_exc(exc: BaseException) -> str:
    """Generate a stable fingerprint from the innermost traceback frame."""
    tb = tb_module.extract_tb(exc.__traceback__)
    if tb:
        last_frame = tb[-1]
        raw = f"{type(exc).__name__}:{last_frame.filename}:{last_frame.lineno}"
    else:
        raw = f"{type(exc).__name__}:{str(exc)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _generate_fingerprint_from_record(record: logging.LogRecord) -> str:
    """Generate a stable fingerprint from a log record with exc_info.

    If exc_info is available, uses the innermost traceback frame.
    Otherwise falls back to the log record's own pathname:lineno.
    """
    if record.exc_info and record.exc_info[1]:
        return _generate_fingerprint_from_exc(record.exc_info[1])

    # Fallback: use the location where logging.exception() was called
    raw = f"{record.pathname}:{record.lineno}:{record.getMessage()[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()


# ============================================
# Logging Handler (sync — uses REDIS_SYNC_CLIENT)
# ============================================


class RedisExceptionHandler(logging.Handler):
    """A logging handler that stores ERROR/CRITICAL records with exc_info in Redis.

    Uses the synchronous Redis client so it works from any context
    (request handlers, background tasks, sync code).  All Redis
    operations are wrapped in try/except so a Redis failure never
    disrupts application logging.
    """

    def __init__(self, level: int = logging.ERROR):
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        if not settings.enable_exception_tracking:
            return

        # Only track records that have exception info
        if not record.exc_info or not record.exc_info[1]:
            return

        try:
            self._store(record)
        except Exception:
            # Never disrupt the logging pipeline
            pass

    def _store(self, record: logging.LogRecord) -> None:
        exc = record.exc_info[1]
        fingerprint = _generate_fingerprint_from_record(record)
        key = f"{_KEY_PREFIX}{fingerprint}"
        now = datetime.now(timezone.utc).isoformat()
        now_ts = datetime.now(timezone.utc).timestamp()
        tb_str = "".join(tb_module.format_exception(*record.exc_info))
        source = f"{record.pathname}:{record.lineno}"

        existing = REDIS_SYNC_CLIENT.hgetall(key)

        if existing:
            count = int(existing.get(b"count", b"1")) + 1
            REDIS_SYNC_CLIENT.hset(
                key,
                mapping={
                    "count": str(count),
                    "last_seen": now,
                    "source": source,
                    "traceback": tb_str,
                    "message": str(exc),
                },
            )
        else:
            # Enforce max-entries cap
            total = REDIS_SYNC_CLIENT.zcard(_INDEX_KEY)
            if total and total >= settings.exception_tracking_max_entries:
                oldest = REDIS_SYNC_CLIENT.zrange(_INDEX_KEY, 0, 0)
                if oldest:
                    old_fp = oldest[0] if isinstance(oldest[0], str) else oldest[0].decode()
                    REDIS_SYNC_CLIENT.delete(f"{_KEY_PREFIX}{old_fp}")
                    REDIS_SYNC_CLIENT.zrem(_INDEX_KEY, old_fp)

            REDIS_SYNC_CLIENT.hset(
                key,
                mapping={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": tb_str,
                    "count": "1",
                    "first_seen": now,
                    "last_seen": now,
                    "source": source,
                },
            )

        REDIS_SYNC_CLIENT.expire(key, settings.exception_tracking_ttl)
        REDIS_SYNC_CLIENT.zadd(_INDEX_KEY, {fingerprint: now_ts})
        REDIS_SYNC_CLIENT.expire(_INDEX_KEY, settings.exception_tracking_ttl)


def install_exception_handler() -> None:
    """Install the Redis exception handler on the root logger.

    Call this once during application startup (after ``logging.basicConfig``).
    No-op if exception tracking is disabled or the handler is already installed.
    """
    if not settings.enable_exception_tracking:
        return

    root = logging.getLogger()
    if any(isinstance(h, RedisExceptionHandler) for h in root.handlers):
        return

    handler = RedisExceptionHandler(level=logging.ERROR)
    root.addHandler(handler)


# ============================================
# Async query functions (used by admin API)
# ============================================


async def list_exceptions(
    page: int = 1,
    per_page: int = 20,
    exception_type: str | None = None,
) -> dict:
    """Return a paginated list of tracked exceptions, most recent first."""
    all_fps = await REDIS_ASYNC_CLIENT.zrevrangebyscore(_INDEX_KEY, "+inf", "-inf", withscores=True)

    if not all_fps:
        return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}

    items = []
    for fp_raw, score in all_fps:
        fp = fp_raw if isinstance(fp_raw, str) else fp_raw.decode()
        data = await REDIS_ASYNC_CLIENT.hgetall(f"{_KEY_PREFIX}{fp}")
        if not data:
            await REDIS_ASYNC_CLIENT.zrem(_INDEX_KEY, fp)
            continue

        decoded = {
            (k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode())
            for k, v in data.items()
        }

        if exception_type and decoded.get("type") != exception_type:
            continue

        items.append(
            {
                "fingerprint": fp,
                "type": decoded.get("type", ""),
                "message": decoded.get("message", ""),
                "count": int(decoded.get("count", "1")),
                "first_seen": decoded.get("first_seen", ""),
                "last_seen": decoded.get("last_seen", ""),
                "source": decoded.get("source", ""),
            }
        )

    total = len(items)
    pages = (total + per_page - 1) // per_page if total > 0 else 0
    start = (page - 1) * per_page
    end = start + per_page

    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


async def get_exception_detail(fingerprint: str) -> dict | None:
    """Get full detail for a single tracked exception including the traceback."""
    data = await REDIS_ASYNC_CLIENT.hgetall(f"{_KEY_PREFIX}{fingerprint}")
    if not data:
        return None

    decoded = {
        (k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode()) for k, v in data.items()
    }
    decoded["fingerprint"] = fingerprint
    decoded["count"] = int(decoded.get("count", "1"))
    return decoded


async def clear_exceptions(fingerprint: str | None = None) -> int:
    """Clear tracked exceptions from Redis."""
    if fingerprint:
        existed = await REDIS_ASYNC_CLIENT.delete(f"{_KEY_PREFIX}{fingerprint}")
        await REDIS_ASYNC_CLIENT.zrem(_INDEX_KEY, fingerprint)
        return 1 if existed else 0

    all_fps = await REDIS_ASYNC_CLIENT.zrange(_INDEX_KEY, 0, -1)
    count = 0
    if all_fps:
        for fp_raw in all_fps:
            fp = fp_raw if isinstance(fp_raw, str) else fp_raw.decode()
            await REDIS_ASYNC_CLIENT.delete(f"{_KEY_PREFIX}{fp}")
            count += 1
    await REDIS_ASYNC_CLIENT.delete(_INDEX_KEY)
    return count
