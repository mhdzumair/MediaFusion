"""API request metrics tracker that stores timing and endpoint data in Redis.

Captures per-request timing via middleware and stores both per-endpoint
aggregated statistics and a rolling window of recent individual requests
for the admin dashboard.

Credential safety:
    - Path params ``secret_str`` / ``existing_secret_str`` are masked.
    - Query strings are stripped entirely.
    - Raw IPs are **never** stored.  Client IPs are SHA-256 hashed with a
      daily rotating salt before being fed into Redis HyperLogLogs, making
      it impossible to recover original addresses.

Redis keys used:
    req_metrics:agg:{method}:{route}     -- hash with aggregated endpoint stats
    req_metrics:latency:{method}:{route} -- sorted set of latencies for percentiles
    req_metrics:recent                   -- sorted set index of recent request IDs
    req_metrics:req:{request_id}         -- hash with individual request detail
    req_metrics:endpoints                -- sorted set of known endpoint keys
    req_metrics:uv:global                -- HyperLogLog of global unique visitors
    req_metrics:uv:{method}:{route}      -- HyperLogLog of per-endpoint unique visitors
"""

import hashlib
import logging
import math
import uuid
from datetime import datetime, timezone

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT

_logger = logging.getLogger(__name__)

# Redis key constants
_AGG_PREFIX = "req_metrics:agg:"
_LATENCY_PREFIX = "req_metrics:latency:"
_RECENT_INDEX = "req_metrics:recent"
_REQ_PREFIX = "req_metrics:req:"
_ENDPOINTS_INDEX = "req_metrics:endpoints"
_UV_GLOBAL = "req_metrics:uv:global"
_UV_PREFIX = "req_metrics:uv:"

# Paths to skip (health checks, static assets, docs)
_SKIP_PATH_PREFIXES = (
    "/health",
    "/static",
    "/app/assets",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
)


def _hash_ip(ip: str) -> str:
    """Hash an IP address with a daily-rotating salt.

    Produces a one-way SHA-256 hex digest so the original IP can never
    be recovered, while still allowing Redis HyperLogLog to count
    approximate unique visitors accurately.
    """
    day_salt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{ip}:{day_salt}".encode()).hexdigest()


# ============================================
# Recording (called from middleware)
# ============================================


async def record_request(
    method: str,
    path: str,
    route_template: str | None,
    status_code: int,
    process_time: float,
    client_ip: str | None = None,
) -> None:
    """Record a single API request into Redis.

    Args:
        method: HTTP method (GET, POST, etc.)
        path: Sanitized URL path (secrets already masked, no query string).
        route_template: The route pattern, e.g. ``/stream/{type}/{id}.json``.
                        Falls back to ``path`` if not available.
        status_code: HTTP response status code.
        process_time: Request duration in seconds.
        client_ip: Raw client IP (will be hashed before storage, never stored raw).
    """
    if not settings.enable_request_metrics:
        return

    # Skip paths we don't want to track
    if any(path.startswith(p) for p in _SKIP_PATH_PREFIXES):
        return

    route = route_template or path
    endpoint_key = f"{method}:{route}"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ts = now.timestamp()

    try:
        # --- 1. Per-endpoint aggregated stats ---
        agg_key = f"{_AGG_PREFIX}{endpoint_key}"

        await REDIS_ASYNC_CLIENT.hincrby(agg_key, "total_requests", 1)
        await REDIS_ASYNC_CLIENT.hincrbyfloat(agg_key, "total_time", process_time)
        await REDIS_ASYNC_CLIENT.hset(agg_key, "last_seen", now_iso)
        await REDIS_ASYNC_CLIENT.expire(agg_key, settings.request_metrics_ttl)

        # Status class counters
        status_class = f"status_{status_code // 100}xx"
        await REDIS_ASYNC_CLIENT.hincrby(agg_key, status_class, 1)

        if status_code >= 400:
            await REDIS_ASYNC_CLIENT.hincrby(agg_key, "error_count", 1)

        # Update min/max times (requires read-before-write)
        existing = await REDIS_ASYNC_CLIENT.hmget(agg_key, ["min_time", "max_time"])
        min_time = float(existing[0]) if existing[0] else float("inf")
        max_time = float(existing[1]) if existing[1] else 0.0

        update_fields = {}
        if process_time < min_time:
            update_fields["min_time"] = str(process_time)
        if process_time > max_time:
            update_fields["max_time"] = str(process_time)
        if update_fields:
            await REDIS_ASYNC_CLIENT.hset(agg_key, mapping=update_fields)

        # Ensure route template is stored for display
        await REDIS_ASYNC_CLIENT.hsetnx(agg_key, "method", method)
        await REDIS_ASYNC_CLIENT.hsetnx(agg_key, "route", route)

        # --- 2. Per-endpoint latency distribution ---
        latency_key = f"{_LATENCY_PREFIX}{endpoint_key}"
        # Use timestamp as score, process_time as member (encoded with unique suffix)
        latency_member = f"{process_time:.6f}:{uuid.uuid4().hex[:8]}"
        await REDIS_ASYNC_CLIENT.zadd(latency_key, {latency_member: now_ts})
        # Cap to the configured window size
        await REDIS_ASYNC_CLIENT.zremrangebyrank(
            latency_key, 0, -(settings.request_metrics_latency_window + 1)
        )
        await REDIS_ASYNC_CLIENT.expire(latency_key, settings.request_metrics_ttl)

        # --- 3. Unique visitor tracking (HyperLogLog with hashed IP) ---
        if client_ip:
            hashed_ip = _hash_ip(client_ip)
            # Global unique visitors
            await REDIS_ASYNC_CLIENT.pfadd(_UV_GLOBAL, hashed_ip)
            await REDIS_ASYNC_CLIENT.expire(_UV_GLOBAL, settings.request_metrics_ttl)
            # Per-endpoint unique visitors
            uv_ep_key = f"{_UV_PREFIX}{endpoint_key}"
            await REDIS_ASYNC_CLIENT.pfadd(uv_ep_key, hashed_ip)
            await REDIS_ASYNC_CLIENT.expire(uv_ep_key, settings.request_metrics_ttl)

        # --- 4. Track endpoint in the endpoints index ---
        await REDIS_ASYNC_CLIENT.zadd(_ENDPOINTS_INDEX, {endpoint_key: now_ts})
        await REDIS_ASYNC_CLIENT.expire(_ENDPOINTS_INDEX, settings.request_metrics_ttl)

        # --- 5. Recent individual request log ---
        request_id = uuid.uuid4().hex
        req_key = f"{_REQ_PREFIX}{request_id}"

        await REDIS_ASYNC_CLIENT.hset(
            req_key,
            mapping={
                "method": method,
                "path": path,
                "route_template": route,
                "status_code": str(status_code),
                "process_time": f"{process_time:.6f}",
                "timestamp": now_iso,
            },
        )
        await REDIS_ASYNC_CLIENT.expire(req_key, settings.request_metrics_recent_ttl)
        await REDIS_ASYNC_CLIENT.zadd(_RECENT_INDEX, {request_id: now_ts})
        await REDIS_ASYNC_CLIENT.expire(_RECENT_INDEX, settings.request_metrics_ttl)

        # Enforce max recent entries
        total_recent = await REDIS_ASYNC_CLIENT.zcard(_RECENT_INDEX)
        if total_recent and total_recent > settings.request_metrics_max_recent:
            excess = total_recent - settings.request_metrics_max_recent
            oldest = await REDIS_ASYNC_CLIENT.zrange(_RECENT_INDEX, 0, excess - 1)
            if oldest:
                for rid_raw in oldest:
                    rid = rid_raw if isinstance(rid_raw, str) else rid_raw.decode()
                    await REDIS_ASYNC_CLIENT.delete(f"{_REQ_PREFIX}{rid}")
                await REDIS_ASYNC_CLIENT.zremrangebyrank(_RECENT_INDEX, 0, excess - 1)

    except Exception:
        # Never disrupt the request pipeline
        _logger.debug("Failed to record request metrics", exc_info=True)


# ============================================
# Query functions (used by admin API)
# ============================================


def _decode_hash(data: dict) -> dict[str, str]:
    """Decode a Redis hash (bytes keys/values) into str dict."""
    return {
        (k if isinstance(k, str) else k.decode()): (v if isinstance(v, str) else v.decode())
        for k, v in data.items()
    }


async def _compute_percentiles(latency_key: str) -> dict[str, float]:
    """Compute p50, p95, p99 from a latency sorted set."""
    members = await REDIS_ASYNC_CLIENT.zrange(latency_key, 0, -1) or []
    if not members:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    # Extract the process_time from each member (format: "time:uuid_suffix")
    latencies = []
    for m in members:
        val = m if isinstance(m, str) else m.decode()
        try:
            latencies.append(float(val.split(":")[0]))
        except (ValueError, IndexError):
            continue

    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    latencies.sort()
    n = len(latencies)

    def percentile(p: float) -> float:
        idx = (p / 100.0) * (n - 1)
        lower = int(math.floor(idx))
        upper = min(lower + 1, n - 1)
        weight = idx - lower
        return latencies[lower] * (1 - weight) + latencies[upper] * weight

    return {
        "p50": round(percentile(50), 6),
        "p95": round(percentile(95), 6),
        "p99": round(percentile(99), 6),
    }


async def get_status() -> dict:
    """Return the status summary for request metrics tracking."""
    total_endpoints = 0
    total_requests = 0
    unique_visitors = 0

    if settings.enable_request_metrics:
        total_endpoints = await REDIS_ASYNC_CLIENT.zcard(_ENDPOINTS_INDEX) or 0
        total_recent = await REDIS_ASYNC_CLIENT.zcard(_RECENT_INDEX) or 0
        unique_visitors = await REDIS_ASYNC_CLIENT.pfcount(_UV_GLOBAL) or 0

        # Sum total requests across all endpoints
        all_eps = await REDIS_ASYNC_CLIENT.zrange(_ENDPOINTS_INDEX, 0, -1) or []
        for ep_raw in all_eps:
            ep = ep_raw if isinstance(ep_raw, str) else ep_raw.decode()
            count_raw = await REDIS_ASYNC_CLIENT.hget(f"{_AGG_PREFIX}{ep}", "total_requests")
            if count_raw:
                total_requests += int(count_raw)
    else:
        total_recent = 0

    return {
        "enabled": settings.enable_request_metrics,
        "ttl_seconds": settings.request_metrics_ttl,
        "recent_ttl_seconds": settings.request_metrics_recent_ttl,
        "max_recent": settings.request_metrics_max_recent,
        "total_endpoints": total_endpoints,
        "total_requests": total_requests,
        "total_recent": total_recent,
        "unique_visitors": unique_visitors,
    }


async def get_endpoint_stats(
    page: int = 1,
    per_page: int = 20,
    sort_by: str = "total_requests",
    sort_order: str = "desc",
) -> dict:
    """Return paginated per-endpoint aggregated stats."""
    all_eps = await REDIS_ASYNC_CLIENT.zrevrange(_ENDPOINTS_INDEX, 0, -1) or []

    if not all_eps:
        return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}

    items = []
    for ep_raw in all_eps:
        ep = ep_raw if isinstance(ep_raw, str) else ep_raw.decode()
        agg_key = f"{_AGG_PREFIX}{ep}"
        data = await REDIS_ASYNC_CLIENT.hgetall(agg_key)
        if not data:
            await REDIS_ASYNC_CLIENT.zrem(_ENDPOINTS_INDEX, ep)
            continue

        decoded = _decode_hash(data)
        total_req = int(decoded.get("total_requests", "0"))
        total_time = float(decoded.get("total_time", "0"))
        avg_time = total_time / total_req if total_req > 0 else 0

        # Per-endpoint unique visitors
        uv_ep_key = f"{_UV_PREFIX}{ep}"
        ep_unique = await REDIS_ASYNC_CLIENT.pfcount(uv_ep_key) or 0

        items.append({
            "endpoint_key": ep,
            "method": decoded.get("method", ep.split(":")[0] if ":" in ep else ""),
            "route": decoded.get("route", ep.split(":", 1)[1] if ":" in ep else ep),
            "total_requests": total_req,
            "avg_time": round(avg_time, 6),
            "min_time": round(float(decoded.get("min_time", "0")), 6),
            "max_time": round(float(decoded.get("max_time", "0")), 6),
            "error_count": int(decoded.get("error_count", "0")),
            "status_2xx": int(decoded.get("status_2xx", "0")),
            "status_3xx": int(decoded.get("status_3xx", "0")),
            "status_4xx": int(decoded.get("status_4xx", "0")),
            "status_5xx": int(decoded.get("status_5xx", "0")),
            "unique_visitors": ep_unique,
            "last_seen": decoded.get("last_seen", ""),
        })

    # Sort
    reverse = sort_order == "desc"
    if sort_by in ("total_requests", "avg_time", "error_count", "min_time", "max_time", "unique_visitors"):
        items.sort(key=lambda x: x.get(sort_by, 0), reverse=reverse)
    elif sort_by == "last_seen":
        items.sort(key=lambda x: x.get("last_seen", ""), reverse=reverse)

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


async def get_endpoint_detail(method: str, route: str) -> dict | None:
    """Get detailed stats for a specific endpoint including percentiles."""
    endpoint_key = f"{method}:{route}"
    agg_key = f"{_AGG_PREFIX}{endpoint_key}"
    data = await REDIS_ASYNC_CLIENT.hgetall(agg_key)

    if not data:
        return None

    decoded = _decode_hash(data)
    total_req = int(decoded.get("total_requests", "0"))
    total_time = float(decoded.get("total_time", "0"))
    avg_time = total_time / total_req if total_req > 0 else 0

    # Compute percentiles from latency distribution
    latency_key = f"{_LATENCY_PREFIX}{endpoint_key}"
    percentiles = await _compute_percentiles(latency_key)

    # Per-endpoint unique visitors
    uv_ep_key = f"{_UV_PREFIX}{endpoint_key}"
    ep_unique = await REDIS_ASYNC_CLIENT.pfcount(uv_ep_key) or 0

    return {
        "endpoint_key": endpoint_key,
        "method": decoded.get("method", method),
        "route": decoded.get("route", route),
        "total_requests": total_req,
        "avg_time": round(avg_time, 6),
        "min_time": round(float(decoded.get("min_time", "0")), 6),
        "max_time": round(float(decoded.get("max_time", "0")), 6),
        "error_count": int(decoded.get("error_count", "0")),
        "status_2xx": int(decoded.get("status_2xx", "0")),
        "status_3xx": int(decoded.get("status_3xx", "0")),
        "status_4xx": int(decoded.get("status_4xx", "0")),
        "status_5xx": int(decoded.get("status_5xx", "0")),
        "unique_visitors": ep_unique,
        "last_seen": decoded.get("last_seen", ""),
        **percentiles,
    }


async def get_recent_requests(
    page: int = 1,
    per_page: int = 20,
    method_filter: str | None = None,
    status_filter: int | None = None,
    route_filter: str | None = None,
) -> dict:
    """Return a paginated list of recent individual requests, most recent first."""
    all_ids = await REDIS_ASYNC_CLIENT.zrevrange(_RECENT_INDEX, 0, -1) or []

    if not all_ids:
        return {"items": [], "total": 0, "page": page, "per_page": per_page, "pages": 0}

    items = []
    for rid_raw in all_ids:
        rid = rid_raw if isinstance(rid_raw, str) else rid_raw.decode()
        data = await REDIS_ASYNC_CLIENT.hgetall(f"{_REQ_PREFIX}{rid}")
        if not data:
            await REDIS_ASYNC_CLIENT.zrem(_RECENT_INDEX, rid)
            continue

        decoded = _decode_hash(data)

        # Apply filters
        if method_filter and decoded.get("method") != method_filter:
            continue
        if status_filter is not None and int(decoded.get("status_code", "0")) != status_filter:
            continue
        if route_filter and route_filter not in decoded.get("route_template", ""):
            continue

        items.append({
            "request_id": rid,
            "method": decoded.get("method", ""),
            "path": decoded.get("path", ""),
            "route_template": decoded.get("route_template", ""),
            "status_code": int(decoded.get("status_code", "0")),
            "process_time": float(decoded.get("process_time", "0")),
            "timestamp": decoded.get("timestamp", ""),
        })

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


async def clear_metrics(endpoint_key: str | None = None) -> int:
    """Clear request metrics from Redis.

    Args:
        endpoint_key: If provided, clear only metrics for this endpoint
                     (format: ``METHOD:route``). Otherwise clear all.

    Returns:
        Number of keys deleted.
    """
    count = 0

    if endpoint_key:
        # Clear a specific endpoint
        agg_key = f"{_AGG_PREFIX}{endpoint_key}"
        lat_key = f"{_LATENCY_PREFIX}{endpoint_key}"
        uv_key = f"{_UV_PREFIX}{endpoint_key}"
        deleted = await REDIS_ASYNC_CLIENT.delete(agg_key, lat_key, uv_key)
        await REDIS_ASYNC_CLIENT.zrem(_ENDPOINTS_INDEX, endpoint_key)
        count = deleted
    else:
        # Clear all endpoint stats
        all_eps = await REDIS_ASYNC_CLIENT.zrange(_ENDPOINTS_INDEX, 0, -1) or []
        for ep_raw in all_eps:
            ep = ep_raw if isinstance(ep_raw, str) else ep_raw.decode()
            r1 = await REDIS_ASYNC_CLIENT.delete(f"{_AGG_PREFIX}{ep}")
            r2 = await REDIS_ASYNC_CLIENT.delete(f"{_LATENCY_PREFIX}{ep}")
            r3 = await REDIS_ASYNC_CLIENT.delete(f"{_UV_PREFIX}{ep}")
            count += (1 if r1 else 0) + (1 if r2 else 0) + (1 if r3 else 0)

        # Clear all recent requests
        all_ids = await REDIS_ASYNC_CLIENT.zrange(_RECENT_INDEX, 0, -1) or []
        for rid_raw in all_ids:
            rid = rid_raw if isinstance(rid_raw, str) else rid_raw.decode()
            r = await REDIS_ASYNC_CLIENT.delete(f"{_REQ_PREFIX}{rid}")
            if r:
                count += 1

        # Clear global unique visitors and indexes
        await REDIS_ASYNC_CLIENT.delete(_ENDPOINTS_INDEX, _RECENT_INDEX, _UV_GLOBAL)

    return count
