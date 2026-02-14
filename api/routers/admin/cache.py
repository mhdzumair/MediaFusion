"""
Cache management API endpoints for Redis cache operations.
Admin-only access.
"""

import base64
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.routers.user.auth import require_role
from db.enums import UserRole
from db.models import User
from db.redis_database import REDIS_ASYNC_CLIENT

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/admin/cache", tags=["Cache Management"])


# ============================================
# Cache Type Definitions
# ============================================

# Define all cache patterns used in MediaFusion
CACHE_PATTERNS = {
    "scrapers": {
        "description": "Scraper status tracking (sorted sets)",
        "patterns": [
            "rss_scraper",
            "zilean",
            "yts",
            "torrentio",
            "prowlarr",
            "mediafusion",
            "jackett",
            "bt4g",
        ],
        "type": "zset",
    },
    "metadata": {
        "description": "Metadata existence checks",
        "patterns": ["movie_exists:*", "series_exists:*", "tv_exists:*"],
        "type": "string",
    },
    "catalog": {
        "description": "Catalog metadata cache",
        "patterns": ["catalog:*", "mf:*"],
        "type": "string",
    },
    "streams": {
        "description": "Torrent stream data",
        "patterns": ["torrent_streams:*", "stream:*"],
        "type": "string",
    },
    "debrid": {
        "description": "Debrid availability cache",
        "patterns": ["debrid_cache:*"],
        "type": "hash",
    },
    "profiles": {
        "description": "User profile cache",
        "patterns": ["profile_enc:*"],
        "type": "string",
    },
    "events": {
        "description": "Live events cache",
        "patterns": ["events:*", "dlhd:*"],
        "type": "string",
    },
    "genres": {
        "description": "Genre lists cache",
        "patterns": ["genres:*"],
        "type": "string",
    },
    "lookup": {
        "description": "ID lookup cache (catalog, language, announce)",
        "patterns": ["lang:*", "announce:*"],
        "type": "string",
    },
    "scheduler": {
        "description": "Scheduler job states and history",
        "patterns": ["scheduler:*", "apscheduler*"],
        "type": "string",
    },
    "streaming": {
        "description": "Streaming provider caches",
        "patterns": ["streaming_provider_*", "pikpak:*", "setup_code:*", "manifest:*"],
        "type": "string",
    },
    "images": {
        "description": "Cached images/posters",
        "patterns": ["movie_*.jpg", "series_*.jpg", "tv_*.jpg"],
        "type": "string",
    },
    "rate_limit": {
        "description": "Rate limiting counters",
        "patterns": ["rate_limit:*", "ratelimit:*"],
        "type": "string",
    },
}


# ============================================
# Pydantic Schemas
# ============================================


class CacheTypeStats(BaseModel):
    name: str
    description: str
    keys_count: int
    memory_bytes: int | None = None


class RedisInfoStats(BaseModel):
    connected: bool
    version: str | None = None
    memory_used: str
    memory_peak: str | None = None
    total_keys: int
    connected_clients: int
    uptime_days: int | None = None
    hit_rate: float | None = None
    ops_per_sec: int | None = None


class CacheStatsResponse(BaseModel):
    redis: RedisInfoStats
    cache_types: list[CacheTypeStats]


class CacheKeyInfo(BaseModel):
    key: str
    type: str
    ttl: int  # -1 means no expiry, -2 means key doesn't exist
    size: int | None = None


class CacheKeysResponse(BaseModel):
    keys: list[CacheKeyInfo]
    total: int
    cursor: str
    has_more: bool


class CacheValueResponse(BaseModel):
    key: str
    type: str
    ttl: int
    value: Any
    size: int
    is_binary: bool = False


class ClearCacheRequest(BaseModel):
    type: Literal[
        "all",
        "scrapers",
        "metadata",
        "catalog",
        "streams",
        "debrid",
        "profiles",
        "events",
        "genres",
        "lookup",
        "scheduler",
        "streaming",
        "images",
        "rate_limit",
        "pattern",
    ]
    pattern: str | None = None  # Custom pattern when type is "pattern"


class ClearCacheResponse(BaseModel):
    success: bool
    message: str
    cleared_keys: int = 0
    admin_username: str


# ============================================
# Helper Functions
# ============================================


async def get_redis_info() -> RedisInfoStats:
    """Get comprehensive Redis server statistics."""
    try:
        if not REDIS_ASYNC_CLIENT:
            return RedisInfoStats(
                connected=False,
                memory_used="—",
                total_keys=0,
                connected_clients=0,
            )

        # Get various Redis info sections
        server_info = await REDIS_ASYNC_CLIENT.info("server")
        memory_info = await REDIS_ASYNC_CLIENT.info("memory")
        clients_info = await REDIS_ASYNC_CLIENT.info("clients")
        stats_info = await REDIS_ASYNC_CLIENT.info("stats")
        keyspace_info = await REDIS_ASYNC_CLIENT.info("keyspace")

        # Parse total keys from keyspace
        total_keys = 0
        for db_info in keyspace_info.values():
            if isinstance(db_info, dict):
                total_keys += db_info.get("keys", 0)

        # Calculate hit rate
        keyspace_hits = stats_info.get("keyspace_hits", 0)
        keyspace_misses = stats_info.get("keyspace_misses", 0)
        hit_rate = None
        if keyspace_hits + keyspace_misses > 0:
            hit_rate = round(keyspace_hits / (keyspace_hits + keyspace_misses) * 100, 2)

        return RedisInfoStats(
            connected=True,
            version=server_info.get("redis_version"),
            memory_used=memory_info.get("used_memory_human", "—"),
            memory_peak=memory_info.get("used_memory_peak_human"),
            total_keys=total_keys,
            connected_clients=clients_info.get("connected_clients", 0),
            uptime_days=server_info.get("uptime_in_days"),
            hit_rate=hit_rate,
            ops_per_sec=stats_info.get("instantaneous_ops_per_sec"),
        )
    except Exception as e:
        logger.error(f"Failed to get Redis info: {e}")
        return RedisInfoStats(
            connected=False,
            memory_used="—",
            total_keys=0,
            connected_clients=0,
        )


async def count_keys_by_pattern(pattern: str) -> int:
    """Count keys matching a pattern using KEYS command (faster for counting)."""
    if not REDIS_ASYNC_CLIENT:
        return 0

    try:
        # Use KEYS for patterns, it's faster for counting
        # Note: KEYS can be slow on very large databases but accurate for counting
        keys = await REDIS_ASYNC_CLIENT.keys(pattern)
        return len(keys) if keys else 0
    except Exception as e:
        logger.error(f"Failed to count keys for pattern {pattern}: {e}")
        return 0


async def get_cache_type_stats() -> list[CacheTypeStats]:
    """Get statistics for each cache type."""
    stats = []

    for cache_name, cache_info in CACHE_PATTERNS.items():
        total_keys = 0

        for pattern in cache_info["patterns"]:
            # For sorted sets (scrapers), count entries in the set
            if cache_info["type"] == "zset" and "*" not in pattern:
                try:
                    count = await REDIS_ASYNC_CLIENT.zcard(pattern)
                    total_keys += count if count else 0
                except Exception:
                    pass
            else:
                total_keys += await count_keys_by_pattern(pattern)

        stats.append(
            CacheTypeStats(
                name=cache_name,
                description=cache_info["description"],
                keys_count=total_keys,
            )
        )

    return stats


async def clear_cache_by_type(cache_type: str) -> int:
    """Clear cache keys by type."""
    if not REDIS_ASYNC_CLIENT:
        return 0

    if cache_type not in CACHE_PATTERNS:
        return 0

    cache_info = CACHE_PATTERNS[cache_type]
    deleted = 0

    for pattern in cache_info["patterns"]:
        # For sorted sets, delete the set itself
        if cache_info["type"] == "zset" and "*" not in pattern:
            try:
                result = await REDIS_ASYNC_CLIENT.delete(pattern)
                deleted += result if result else 0
            except Exception as e:
                logger.error(f"Failed to delete sorted set {pattern}: {e}")
        else:
            # For other types, use KEYS and delete
            try:
                keys = await REDIS_ASYNC_CLIENT.keys(pattern)
                if keys:
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        await REDIS_ASYNC_CLIENT.delete(key_str)
                        deleted += 1
            except Exception as e:
                logger.error(f"Failed to clear cache pattern {pattern}: {e}")

    return deleted


async def clear_cache_by_pattern(pattern: str) -> int:
    """Clear cache keys matching a custom pattern."""
    if not REDIS_ASYNC_CLIENT:
        return 0

    deleted = 0
    try:
        keys = await REDIS_ASYNC_CLIENT.keys(pattern)
        if keys:
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                await REDIS_ASYNC_CLIENT.delete(key_str)
                deleted += 1
    except Exception as e:
        logger.error(f"Failed to clear cache pattern {pattern}: {e}")

    return deleted


async def get_key_value_internal(key: str) -> tuple[Any, bool]:
    """
    Get the value of a Redis key based on its type.
    Returns (value, is_binary) tuple.
    """
    if not REDIS_ASYNC_CLIENT:
        return None, False

    try:
        key_type_raw = await REDIS_ASYNC_CLIENT.type(key)
        # Decode bytes to string if needed
        key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else key_type_raw

        if key_type == "string":
            # Use the underlying client directly to avoid wrapper's silent error handling
            # This is important for large binary data like images
            try:
                client = REDIS_ASYNC_CLIENT._get_client().client
                value = await client.get(key)
            except Exception as e:
                logger.warning(f"Direct client get failed for {key}, trying wrapper: {e}")
                value = await REDIS_ASYNC_CLIENT.get(key)

            if value is None:
                return {"_info": "Key exists but value is None or empty"}, False

            # Check if it's binary data (like images)
            if isinstance(value, bytes):
                try:
                    # Try to decode as UTF-8
                    decoded = value.decode("utf-8")
                    # Try to parse as JSON
                    try:
                        return json.loads(decoded), False
                    except json.JSONDecodeError:
                        return decoded, False
                except UnicodeDecodeError:
                    # Binary data - return base64 preview
                    size = len(value)
                    preview = base64.b64encode(value[:200]).decode() if size > 200 else base64.b64encode(value).decode()
                    return {
                        "_binary": True,
                        "size_bytes": size,
                        "preview_base64": preview,
                        "message": f"Binary data ({size:,} bytes) - showing base64 preview",
                    }, True
            return value, False

        elif key_type == "hash":
            value = await REDIS_ASYNC_CLIENT.hgetall(key)
            if not value:
                return {}, False
            # Decode bytes keys/values
            result = {}
            for k, v in value.items():
                key_str = k.decode() if isinstance(k, bytes) else k
                try:
                    val_str = v.decode() if isinstance(v, bytes) else v
                except (UnicodeDecodeError, AttributeError):
                    val_str = str(v)
                result[key_str] = val_str
            return result, False

        elif key_type == "list":
            values = await REDIS_ASYNC_CLIENT.lrange(key, 0, -1)
            return [v.decode() if isinstance(v, bytes) else v for v in (values or [])], False

        elif key_type == "set":
            members = await REDIS_ASYNC_CLIENT.smembers(key)
            return [m.decode() if isinstance(m, bytes) else m for m in (members or set())], False

        elif key_type == "zset":
            # Get all members with scores
            members = await REDIS_ASYNC_CLIENT.zrange(key, 0, -1, withscores=True)
            if not members:
                return [], False
            return [{"member": m.decode() if isinstance(m, bytes) else m, "score": s} for m, s in members], False

        return {"_info": f"Unknown type: {key_type}"}, False
    except Exception as e:
        logger.error(f"Failed to get value for key {key}: {e}")
        return {"error": str(e)}, False


# ============================================
# API Endpoints
# ============================================


@router.get("/stats", response_model=CacheStatsResponse)
async def get_cache_stats(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get comprehensive cache statistics including Redis info and per-type breakdown.
    Requires admin role.
    """
    redis_info = await get_redis_info()
    cache_types = await get_cache_type_stats()

    return CacheStatsResponse(
        redis=redis_info,
        cache_types=cache_types,
    )


@router.get("/keys", response_model=CacheKeysResponse)
async def browse_cache_keys(
    pattern: str = Query("*", description="Pattern to match keys (supports wildcards)"),
    cursor: str = Query("0", description="Cursor for pagination"),
    count: int = Query(50, ge=1, le=200, description="Number of keys to return per scan"),
    type_filter: str | None = Query(None, description="Filter by Redis type (string, hash, list, set, zset)"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Browse cache keys matching a pattern with pagination.
    Requires admin role.
    """
    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    try:
        keys_info = []
        current_cursor = int(cursor)
        scanned_count = 0
        max_scans = 10  # Limit scan iterations to prevent timeout

        # Keep scanning until we have enough keys or no more to scan
        while len(keys_info) < count and scanned_count < max_scans:
            result = await REDIS_ASYNC_CLIENT.scan(
                cursor=current_cursor,
                match=pattern,
                count=count * 2,  # Request more to account for filtering
            )

            if result is None:
                break

            new_cursor, keys = result
            scanned_count += 1

            for key in keys:
                if len(keys_info) >= count:
                    break

                key_str = key.decode() if isinstance(key, bytes) else key
                key_type_raw = await REDIS_ASYNC_CLIENT.type(key_str)
                key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else key_type_raw

                # Apply type filter if specified
                if type_filter and key_type != type_filter:
                    continue

                ttl = await REDIS_ASYNC_CLIENT.ttl(key_str)

                # Get approximate size
                try:
                    size = await REDIS_ASYNC_CLIENT.memory_usage(key_str)
                except Exception:
                    size = None

                keys_info.append(
                    CacheKeyInfo(
                        key=key_str,
                        type=key_type,
                        ttl=ttl,
                        size=size,
                    )
                )

            current_cursor = new_cursor
            if current_cursor == 0:
                break

        # Get total count for this pattern (use KEYS for accuracy)
        all_keys = await REDIS_ASYNC_CLIENT.keys(pattern)
        total = len(all_keys) if all_keys else 0

        # If type filter is applied, we need to count filtered keys
        if type_filter and all_keys:
            filtered_count = 0
            for key in all_keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                key_type_raw = await REDIS_ASYNC_CLIENT.type(key_str)
                key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else key_type_raw
                if key_type == type_filter:
                    filtered_count += 1
            total = filtered_count

        return CacheKeysResponse(
            keys=keys_info,
            total=total,
            cursor=str(current_cursor),
            has_more=current_cursor != 0,
        )
    except Exception as e:
        logger.error(f"Failed to browse cache keys: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to browse cache keys: {str(e)}",
        )


@router.get("/key/{key:path}", response_model=CacheValueResponse)
async def get_cache_value(
    key: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get the value of a specific cache key.
    Requires admin role.
    """
    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    try:
        exists = await REDIS_ASYNC_CLIENT.exists(key)
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Key '{key}' not found",
            )

        key_type_raw = await REDIS_ASYNC_CLIENT.type(key)
        key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else key_type_raw
        ttl = await REDIS_ASYNC_CLIENT.ttl(key)
        value, is_binary = await get_key_value_internal(key)

        # Get size
        try:
            size = await REDIS_ASYNC_CLIENT.memory_usage(key)
        except Exception:
            size = 0

        return CacheValueResponse(
            key=key,
            type=key_type,
            ttl=ttl,
            value=value,
            size=size or 0,
            is_binary=is_binary,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cache value for key {key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get cache value: {str(e)}",
        )


@router.get("/image/{key:path}")
async def get_cache_image(
    key: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Get a cached image as raw bytes for display.
    Requires admin role.
    """
    from fastapi.responses import Response

    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    try:
        # Use direct client for binary data
        client = REDIS_ASYNC_CLIENT._get_client().client
        value = await client.get(key)

        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Key '{key}' not found",
            )

        # Determine content type from key name
        content_type = "application/octet-stream"
        if key.endswith(".jpg") or key.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif key.endswith(".png"):
            content_type = "image/png"
        elif key.endswith(".gif"):
            content_type = "image/gif"
        elif key.endswith(".webp"):
            content_type = "image/webp"

        return Response(
            content=value,
            media_type=content_type,
            headers={"Cache-Control": "max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cache image {key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get cache image: {str(e)}",
        )


@router.delete("/key/{key:path}")
async def delete_cache_key(
    key: str,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Delete a specific cache key.
    Requires admin role.
    """
    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    try:
        deleted = await REDIS_ASYNC_CLIENT.delete(key)
        if deleted == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Key '{key}' not found",
            )

        return {
            "success": True,
            "message": f"Key '{key}' deleted",
            "admin_username": admin.username,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cache key {key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete cache key: {str(e)}",
        )


class DeleteItemRequest(BaseModel):
    """Request to delete a specific item from a complex data type."""

    field: str | None = None  # For hash: field name
    member: str | None = None  # For set/zset: member value
    value: str | None = None  # For list: value to remove
    index: int | None = None  # For list: index to remove (alternative)


@router.delete("/key/{key:path}/item")
async def delete_cache_item(
    key: str,
    request: DeleteItemRequest,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Delete a specific item from a complex data type (hash, list, set, zset).
    Requires admin role.

    - For hash: provide 'field' to delete a specific field
    - For set/zset: provide 'member' to delete a specific member
    - For list: provide 'value' to remove all occurrences, or 'index' to remove at position
    """
    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    try:
        # Get the key type
        client = REDIS_ASYNC_CLIENT._get_client().client
        key_type_raw = await client.type(key)
        key_type = key_type_raw.decode() if isinstance(key_type_raw, bytes) else str(key_type_raw)

        if key_type == "none":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Key '{key}' not found",
            )

        removed = 0

        if key_type == "hash":
            if not request.field:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Field name is required for hash type",
                )
            removed = await client.hdel(key, request.field)

        elif key_type == "set":
            if not request.member:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Member value is required for set type",
                )
            removed = await client.srem(key, request.member)

        elif key_type == "zset":
            if not request.member:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Member value is required for sorted set type",
                )
            removed = await client.zrem(key, request.member)

        elif key_type == "list":
            if request.index is not None:
                # Remove by index - Redis doesn't have direct index removal
                # Use LSET to mark, then LREM
                placeholder = "__DELETED_ITEM_PLACEHOLDER__"
                await client.lset(key, request.index, placeholder)
                removed = await client.lrem(key, 1, placeholder)
            elif request.value:
                # Remove all occurrences of value
                removed = await client.lrem(key, 0, request.value)
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Value or index is required for list type",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete items from type '{key_type}'. Use DELETE /key/{{key}} instead.",
            )

        if removed == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found in the collection",
            )

        return {
            "success": True,
            "message": f"Item deleted from '{key}'",
            "removed_count": removed,
            "admin_username": admin.username,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete item from {key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete item: {str(e)}",
        )


@router.post("/clear", response_model=ClearCacheResponse)
async def clear_cache(
    request: ClearCacheRequest,
    admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Clear cache by type or pattern. Requires admin role.

    Types:
    - all: Clear all known cache patterns
    - scrapers: Clear scraper tracking data
    - metadata: Clear metadata existence checks
    - catalog: Clear catalog cache
    - streams: Clear stream data cache
    - debrid: Clear debrid availability cache
    - profiles: Clear user profile cache
    - events: Clear live events cache
    - genres: Clear genre lists
    - lookup: Clear ID lookup caches
    - scheduler: Clear scheduler states
    - streaming: Clear streaming provider caches
    - images: Clear cached images
    - rate_limit: Clear rate limiting counters
    - pattern: Clear custom pattern (requires pattern parameter)
    """
    if not REDIS_ASYNC_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis connection not available",
        )

    total_cleared = 0

    try:
        if request.type == "all":
            # Clear all known cache types
            for cache_type in CACHE_PATTERNS.keys():
                total_cleared += await clear_cache_by_type(cache_type)
            logger.info(f"Admin {admin.username} cleared all caches, {total_cleared} keys deleted")

        elif request.type == "pattern":
            if not request.pattern:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Pattern is required when type is 'pattern'",
                )
            total_cleared = await clear_cache_by_pattern(request.pattern)
            logger.info(f"Admin {admin.username} cleared pattern '{request.pattern}', {total_cleared} keys deleted")

        else:
            total_cleared = await clear_cache_by_type(request.type)
            logger.info(f"Admin {admin.username} cleared {request.type} cache, {total_cleared} keys deleted")

        return ClearCacheResponse(
            success=True,
            message=f"Cache cleared successfully ({request.type})",
            cleared_keys=total_cleared,
            admin_username=admin.username,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear cache: {str(e)}",
        )
