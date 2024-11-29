import logging
from datetime import datetime, timezone
from typing import Dict, Any

import humanize
from db.redis_database import REDIS_ASYNC_CLIENT, REDIS_SYNC_CLIENT


async def get_redis_metrics() -> Dict[str, Any]:
    """
    Get comprehensive Redis metrics including both
    application connection pool stats and Redis server metrics.
    """
    # Get pool stats
    async_pool = REDIS_ASYNC_CLIENT.connection_pool
    sync_pool = REDIS_SYNC_CLIENT.connection_pool

    pool_stats = {
        "app_connections": {
            "async": {
                "in_use": len(async_pool._in_use_connections),
                "available": len(async_pool._available_connections),
                "max": async_pool.max_connections,
            },
            "sync": {
                "in_use": len(sync_pool._in_use_connections),
                "available": len(sync_pool._available_connections),
                "max": sync_pool.max_connections,
            },
        }
    }

    try:
        # Get Redis INFO stats
        info_stats = await REDIS_ASYNC_CLIENT.info()

        # Memory metrics
        memory_metrics = {
            "used_memory_human": info_stats.get("used_memory_human"),
            "used_memory_peak_human": info_stats.get("used_memory_peak_human"),
            "used_memory_lua_human": info_stats.get("used_memory_lua_human"),
            "maxmemory_human": info_stats.get("maxmemory_human"),
            "mem_fragmentation_ratio": info_stats.get("mem_fragmentation_ratio"),
        }

        # Connection metrics
        connection_metrics = {
            "connected_clients": info_stats.get("connected_clients"),
            "blocked_clients": info_stats.get("blocked_clients"),
            "connected_slaves": info_stats.get("connected_slaves"),
            "maxclients": info_stats.get("maxclients"),
        }

        # Performance metrics
        performance_metrics = {
            "instantaneous_ops_per_sec": info_stats.get("instantaneous_ops_per_sec"),
            "instantaneous_input_kbps": info_stats.get("instantaneous_input_kbps"),
            "instantaneous_output_kbps": info_stats.get("instantaneous_output_kbps"),
            "total_commands_processed": info_stats.get("total_commands_processed"),
            "total_connections_received": info_stats.get("total_connections_received"),
            "total_net_input_bytes": info_stats.get("total_net_input_bytes"),
            "total_net_output_bytes": info_stats.get("total_net_output_bytes"),
            "rejected_connections": info_stats.get("rejected_connections"),
        }

        # Cache metrics
        cache_metrics = {
            "keyspace_hits": info_stats.get("keyspace_hits"),
            "keyspace_misses": info_stats.get("keyspace_misses"),
            "hit_rate": (
                info_stats.get("keyspace_hits", 0)
                / (
                    info_stats.get("keyspace_hits", 0)
                    + info_stats.get("keyspace_misses", 1)
                )
                * 100
                if info_stats.get("keyspace_hits") is not None
                and info_stats.get("keyspace_misses") is not None
                else 0
            ),
        }

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "app_pool_stats": pool_stats,
            "memory": memory_metrics,
            "connections": connection_metrics,
            "performance": performance_metrics,
            "cache": cache_metrics,
            "replication": {
                "role": info_stats.get("role"),
                "connected_slaves": info_stats.get("connected_slaves"),
                "master_link_status": info_stats.get("master_link_status"),
            },
            "persistence": {
                "rdb_last_save_time": info_stats.get("rdb_last_save_time"),
                "rdb_changes_since_last_save": info_stats.get(
                    "rdb_changes_since_last_save"
                ),
            },
        }
    except Exception as e:
        logging.error(f"Error getting Redis metrics: {e}")
        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "error": str(e),
            "app_pool_stats": pool_stats,
        }


async def get_debrid_cache_metrics() -> Dict[str, Any]:
    """
    Get detailed metrics about debrid cache usage.
    """
    metrics = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "total_memory_usage": 0,
        "total_cached_torrents": 0,
        "services": {},
    }
    debrid_services = [
        "alldebrid",
        "debridlink",
        "offcloud",
        "pikpak",
        "premiumize",
        "qbittorrent",
        "realdebrid",
        "seedr",
        "torbox",
    ]

    try:

        for service in debrid_services:
            cache_key = f"debrid_cache:{service}"

            # Get various metrics for each service
            cache_size = await REDIS_ASYNC_CLIENT.hlen(cache_key)
            memory_usage = await REDIS_ASYNC_CLIENT.memory_usage(cache_key) or 0

            metrics["services"][service] = {
                "cached_torrents": cache_size,
                "memory_usage": memory_usage,
            }

            # Update totals
            metrics["total_cached_torrents"] += cache_size
            metrics["total_memory_usage"] += memory_usage

        # Add human readable total memory
        metrics["total_memory_usage_human"] = humanize.naturalsize(
            metrics["total_memory_usage"]
        )

        # Calculate which service has most cached torrents
        if metrics["services"]:
            most_cached = max(
                metrics["services"].items(), key=lambda x: x[1]["cached_torrents"]
            )
            metrics["most_used_service"] = {
                "name": most_cached[0],
                "cached_count": most_cached[1]["cached_torrents"],
            }

        return metrics
    except Exception as e:
        logging.error(f"Error getting debrid cache metrics: {e}")
        return {"timestamp": datetime.now(tz=timezone.utc).isoformat(), "error": str(e)}
