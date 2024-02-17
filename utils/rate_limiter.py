from functools import wraps


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


def exclude(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)

    wrapper.exclude = True
    return wrapper
