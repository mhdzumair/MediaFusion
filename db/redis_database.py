import redis

from db.config import settings

pool_settings = {
    "max_connections": settings.redis_max_connections,  # Maximum number of connections per pod
    "socket_timeout": 5.0,  # Socket timeout in seconds
    "socket_connect_timeout": 2.0,  # Connection timeout
    "socket_keepalive": True,  # Keep connections alive
    "health_check_interval": 30,  # Health check every 30 seconds
    "retry_on_timeout": True,
    "decode_responses": False,  # Automatically decode responses to Python strings
}

# Create sync client with connection pooling
REDIS_SYNC_CLIENT: redis.Redis = redis.Redis(
    connection_pool=redis.ConnectionPool.from_url(settings.redis_url, **pool_settings)
)

# Create async client with connection pooling
REDIS_ASYNC_CLIENT: redis.asyncio.Redis = redis.asyncio.Redis(
    connection_pool=redis.asyncio.ConnectionPool.from_url(
        settings.redis_url, **pool_settings
    )
)
