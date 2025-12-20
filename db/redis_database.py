import asyncio
import logging
import socket
import time
from enum import Enum
from typing import Any, Optional, Union
import inspect

import redis

from db.config import settings

logger = logging.getLogger(__name__)

# Build socket keepalive options safely
socket_keepalive_options = {}
if hasattr(socket, 'TCP_KEEPIDLE'):
    socket_keepalive_options[socket.TCP_KEEPIDLE] = 60
if hasattr(socket, 'TCP_KEEPINTVL'):
    socket_keepalive_options[socket.TCP_KEEPINTVL] = 30
if hasattr(socket, 'TCP_KEEPCNT'):
    socket_keepalive_options[socket.TCP_KEEPCNT] = 3

pool_settings = {
    "max_connections": settings.redis_max_connections,  # Maximum number of connections per pod
    "socket_timeout": 10.0,  # Increased socket timeout to 10 seconds
    "socket_connect_timeout": 5.0,  # Increased connection timeout to 5 seconds
    "socket_keepalive": True,  # Keep connections alive
    "health_check_interval": 30,  # Health check every 30 seconds
    "retry_on_timeout": True,
    "retry_on_error": [
        redis.exceptions.ConnectionError,
        redis.exceptions.TimeoutError,
        redis.exceptions.BusyLoadingError,
    ],  # Retry on more error types
    "decode_responses": False,  # Automatically decode responses to Python strings
}

# Only add socket_keepalive_options if we have any options available
if socket_keepalive_options:
    pool_settings["socket_keepalive_options"] = socket_keepalive_options


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class RedisCircuitBreaker:
    """
    Circuit breaker implementation for Redis operations.
    Prevents cascading failures by temporarily disabling Redis operations
    when failure rate exceeds threshold.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: tuple = (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            RuntimeError,
        ),
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED

    def call(self, func):
        """
        Decorator to wrap Redis operations with circuit breaker logic.
        Supports both sync and async functions.
        """
        if inspect.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                if self.state == CircuitBreakerState.OPEN:
                    if self._should_attempt_reset():
                        self.state = CircuitBreakerState.HALF_OPEN
                    else:
                        logger.warning("Circuit breaker is OPEN, skipping Redis operation")
                        return None

                try:
                    result = await func(*args, **kwargs)
                    self._on_success()
                    return result
                except self.expected_exception as e:
                    self._on_failure()
                    logger.warning(f"Redis operation failed: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Unexpected error in Redis operation: {e}")
                    return None

            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                if self.state == CircuitBreakerState.OPEN:
                    if self._should_attempt_reset():
                        self.state = CircuitBreakerState.HALF_OPEN
                    else:
                        logger.warning("Circuit breaker is OPEN, skipping Redis operation")
                        return None

                try:
                    result = func(*args, **kwargs)
                    self._on_success()
                    return result
                except self.expected_exception as e:
                    self._on_failure()
                    logger.warning(f"Redis operation failed: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Unexpected error in Redis operation: {e}")
                    return None

            return sync_wrapper

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        return (
            time.time() - self.last_failure_time > self.recovery_timeout
            if self.last_failure_time
            else True
        )

    def _on_success(self):
        """Reset circuit breaker on successful operation."""
        self.failure_count = 0
        self.state = CircuitBreakerState.CLOSED

    def _on_failure(self):
        """Handle failure and potentially open circuit breaker."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures"
            )


# Global circuit breaker instance
redis_circuit_breaker = RedisCircuitBreaker()


class RedisWrapper:
    """
    Unified wrapper class for Redis operations with retry logic, circuit breaker,
    and graceful degradation. Supports both sync and async Redis clients.
    """

    def __init__(self, client: Union[redis.asyncio.Redis, redis.Redis]):
        self.client = client
        self.is_async = isinstance(client, redis.asyncio.Redis)

    async def _execute_with_retry_async(self, operation, *args, **kwargs):
        """Execute async Redis operation with retry logic."""
        last_exception = None

        for attempt in range(settings.redis_retry_attempts):
            try:
                return await operation(*args, **kwargs)
            except (
                redis.exceptions.ConnectionError,
                redis.exceptions.TimeoutError,
                RuntimeError,
            ) as e:
                last_exception = e
                if attempt < settings.redis_retry_attempts - 1:
                    await asyncio.sleep(settings.redis_retry_delay * (2 ** attempt))
                    logger.warning(
                        f"Redis operation failed (attempt {attempt + 1}/{settings.redis_retry_attempts}): {e}"
                    )
                else:
                    logger.error(f"Redis operation failed after all retries: {e}")

        raise last_exception

    def _execute_with_retry_sync(self, operation, *args, **kwargs):
        """Execute sync Redis operation with retry logic."""
        last_exception = None

        for attempt in range(settings.redis_retry_attempts):
            try:
                return operation(*args, **kwargs)
            except (
                redis.exceptions.ConnectionError,
                redis.exceptions.TimeoutError,
                RuntimeError,
            ) as e:
                last_exception = e
                if attempt < settings.redis_retry_attempts - 1:
                    time.sleep(settings.redis_retry_delay * (2 ** attempt))
                    logger.warning(
                        f"Redis operation failed (attempt {attempt + 1}/{settings.redis_retry_attempts}): {e}"
                    )
                else:
                    logger.error(f"Redis operation failed after all retries: {e}")

        raise last_exception

    def _create_method(self, method_name: str, default_return=None):
        """Create a method that works for both sync and async clients."""
        if self.is_async:
            @redis_circuit_breaker.call
            async def async_method(*args, **kwargs):
                try:
                    operation = getattr(self.client, method_name)
                    result = await self._execute_with_retry_async(operation, *args, **kwargs)
                    return result if result is not None else default_return
                except Exception:
                    return default_return
            return async_method
        else:
            @redis_circuit_breaker.call
            def sync_method(*args, **kwargs):
                try:
                    operation = getattr(self.client, method_name)
                    result = self._execute_with_retry_sync(operation, *args, **kwargs)
                    return result if result is not None else default_return
                except Exception:
                    return default_return
            return sync_method

    async def aclose(self):
        """Async close operation."""
        await self.client.aclose()

    def close(self):
        """Sync close operation."""
        self.client.close()

    def get(self, key: str):
        """Redis GET operation."""
        return self._create_method('get', None)(key)

    def getex(self, key: str, ex: int):
        """Redis GETEX operation."""
        return self._create_method('getex', None)(key, ex)

    def set(self, key: str, value: Any, ex: Optional[int] = None, **kwargs):
        """Redis SET operation."""
        return self._create_method('set', False)(key, value, ex=ex, **kwargs)

    def setex(self, key: str, ex: int, value: Any, **kwargs):
        """Redis SETEX operation."""
        return self._create_method('setex', False)(key, ex, value, **kwargs)

    def delete(self, *keys):
        """Redis DELETE operation."""
        return self._create_method('delete', 0)(*keys)

    def exists(self, key: str):
        """Redis EXISTS operation."""
        return self._create_method('exists', False)(key)

    def hget(self, name: str, key: str):
        """Redis HGET operation."""
        return self._create_method('hget', None)(name, key)

    def hset(self, name: str, key: str = None, value: Any = None, mapping: dict = None):
        """Redis HSET operation."""
        if mapping:
            return self._create_method('hset', 0)(name, mapping=mapping)
        else:
            return self._create_method('hset', 0)(name, key, value)

    def hlen(self, name: str):
        """Redis HLEN operation."""
        return self._create_method('hlen', 0)(name)

    def hgetall(self, name: str):
        """Redis HGETALL operation."""
        return self._create_method('hgetall', {})(name)

    def hmget(self, name: str, keys):
        """Redis HMGET operation."""
        return self._create_method('hmget', [])(name, keys)

    def hdel(self, name: str, *keys):
        """Redis HDEL operation."""
        return self._create_method('hdel', 0)(name, *keys)

    def hscan(self, name: str, cursor: int = 0, match: str = None, count: int = None):
        """Redis HSCAN operation."""
        kwargs = {}
        if match:
            kwargs['match'] = match
        if count:
            kwargs['count'] = count
        return self._create_method('hscan', (0, {}))(name, cursor, **kwargs)

    def zadd(self, name: str, mapping: dict):
        """Redis ZADD operation."""
        return self._create_method('zadd', 0)(name, mapping)

    def zscore(self, name: str, value: Any):
        """Redis ZSCORE operation."""
        return self._create_method('zscore', None)(name, value)

    def zremrangebyscore(self, name: str, min_score: float, max_score: float):
        """Redis ZREMRANGEBYSCORE operation."""
        return self._create_method('zremrangebyscore', 0)(name, min_score, max_score)

    def zrevrangebyscore(self, name: str, max_score: float, min_score: float, start: int = None, num: int = None, withscores: bool = False):
        """Redis ZREVRANGEBYSCORE operation."""
        kwargs = {}
        if start is not None:
            kwargs['start'] = start
        if num is not None:
            kwargs['num'] = num
        if withscores:
            kwargs['withscores'] = withscores
        return self._create_method('zrevrangebyscore', [])(name, max_score, min_score, **kwargs)

    def zrem(self, name: str, *values):
        """Redis ZREM operation."""
        return self._create_method('zrem', 0)(name, *values)

    def sadd(self, name: str, *values):
        """Redis SADD operation."""
        return self._create_method('sadd', 0)(name, *values)

    def sismember(self, name: str, value: Any):
        """Redis SISMEMBER operation."""
        return self._create_method('sismember', False)(name, value)

    def srem(self, name: str, *values):
        """Redis SREM operation."""
        return self._create_method('srem', 0)(name, *values)

    def smembers(self, name: str):
        """Redis SMEMBERS operation."""
        return self._create_method('smembers', set())(name)

    def lrange(self, name: str, start: int, end: int):
        """Redis LRANGE operation."""
        return self._create_method('lrange', [])(name, start, end)

    def keys(self, pattern: str = "*"):
        """Redis KEYS operation."""
        return self._create_method('keys', [])(pattern)

    def expire(self, key: str, ex: int):
        """Redis EXPIRE operation."""
        return self._create_method('expire', False)(key, ex)

    def pipeline(self, transaction: bool = False):
        """Redis PIPELINE operation."""
        return self._create_method('pipeline', None)(transaction)

    def execute(self, *args, **kwargs):
        """Redis EXECUTE operation."""
        return self._create_method('execute', None)(*args, **kwargs)

    def incr(self, name: str):
        """Redis INCR operation."""
        return self._create_method('incr', 0)(name)

    def incrby(self, name: str, amount: int):
        """Redis INCRBY operation."""
        return self._create_method('incrby', 0)(name, amount)

    def watch(self, *names):
        """Redis WATCH operation."""
        return self._create_method('watch', None)(*names)

    def unwatch(self):
        """Redis UNWATCH operation."""
        return self._create_method('unwatch', None)()

    def lock(self, key: str, timeout: int = None, sleep: float = 0.1, blocking_timeout: float = None, lock_class = None, thread_local: bool = True):
        """Redis LOCK operation."""
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout
        if sleep is not None:
            kwargs['sleep'] = sleep
        if blocking_timeout is not None:
            kwargs['blocking_timeout'] = blocking_timeout
        if lock_class is not None:
            kwargs['lock_class'] = lock_class
        if thread_local is not None:
            kwargs['thread_local'] = thread_local
        # Return the lock object directly, not wrapped with _create_method
        return self.client.lock(key, **kwargs)

    def info(self, section: str = None):
        """Redis INFO operation."""
        if section:
            return self._create_method('info', {})(section)
        else:
            return self._create_method('info', {})()

    @property
    def connection_pool(self):
        """Access to the underlying connection pool."""
        return self.client.connection_pool

    def ping(self):
        """Redis PING operation."""
        if self.is_async:
            @redis_circuit_breaker.call
            async def async_ping():
                try:
                    result = await self._execute_with_retry_async(self.client.ping)
                    return result is True
                except Exception:
                    return False
            return async_ping()
        else:
            @redis_circuit_breaker.call
            def sync_ping():
                try:
                    result = self._execute_with_retry_sync(self.client.ping)
                    return result is True
                except Exception:
                    return False
            return sync_ping()

    def health_check(self):
        """Comprehensive health check for Redis connection."""
        if self.is_async:
            return self._async_health_check()
        else:
            return self._sync_health_check()

    async def _async_health_check(self) -> dict:
        """Async health check implementation."""
        start_time = time.time()

        try:
            ping_result = await self.ping()
            response_time = time.time() - start_time

            return {
                "status": "healthy" if ping_result else "unhealthy",
                "response_time_ms": round(response_time * 1000, 2),
                "circuit_breaker_state": redis_circuit_breaker.state.value,
                "failure_count": redis_circuit_breaker.failure_count,
            }
        except Exception as e:
            response_time = time.time() - start_time
            return {
                "status": "error",
                "error": str(e),
                "response_time_ms": round(response_time * 1000, 2),
                "circuit_breaker_state": redis_circuit_breaker.state.value,
                "failure_count": redis_circuit_breaker.failure_count,
            }

    def _sync_health_check(self) -> dict:
        """Sync health check implementation."""
        start_time = time.time()

        try:
            ping_result = self.ping()
            response_time = time.time() - start_time

            return {
                "status": "healthy" if ping_result else "unhealthy",
                "response_time_ms": round(response_time * 1000, 2),
                "circuit_breaker_state": redis_circuit_breaker.state.value,
                "failure_count": redis_circuit_breaker.failure_count,
            }
        except Exception as e:
            response_time = time.time() - start_time
            return {
                "status": "error",
                "error": str(e),
                "response_time_ms": round(response_time * 1000, 2),
                "circuit_breaker_state": redis_circuit_breaker.state.value,
                "failure_count": redis_circuit_breaker.failure_count,
            }


# Create sync client with connection pooling
REDIS_SYNC_CLIENT = RedisWrapper(redis.Redis(
    connection_pool=redis.ConnectionPool.from_url(settings.redis_url, **pool_settings)
))

# Create async client with connection pooling
REDIS_ASYNC_CLIENT = RedisWrapper(redis.asyncio.Redis(
    connection_pool=redis.asyncio.ConnectionPool.from_url(
        settings.redis_url, **pool_settings
    )
))

