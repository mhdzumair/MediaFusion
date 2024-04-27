from datetime import timedelta
from functools import wraps

from dramatiq.rate_limits.backends import RedisBackend

from db.config import settings

backend = RedisBackend(url=settings.redis_url)


def rate_limit(limit: int, window: int, scope: str = None):
    """
    Decorator to set a rate limit on an endpoint.
    Args:
        limit: The number of requests allowed in the time window.
        window: The time window in seconds.
        scope: The rate limit scope, e.g., "ip" or "user".
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        wrapper.limit = limit
        wrapper.window = window
        wrapper.scope = scope
        return wrapper

    return decorator


def exclude_rate_limit(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)

    wrapper.exclude_rate_limit = True
    return wrapper


def auth_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)

    wrapper.auth_required = True
    return wrapper


def minimum_run_interval(seconds: int):
    """
    Decorator to specify the minimum interval in seconds between task executions.
    """

    def decorator(func):
        func._minimum_run_interval = timedelta(seconds=seconds)
        return func

    return decorator
