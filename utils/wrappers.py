import logging
from functools import wraps

from dramatiq.rate_limits import ConcurrentRateLimiter, WindowRateLimiter
from dramatiq.rate_limits.backends import RedisBackend

backend = RedisBackend()


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


def worker_rate_limit(
    limiter_type="concurrent",
    limit=1,
    window=None,
    raise_on_failure=False,
    use_args_in_key=False,
):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Construct the limiter key including function arguments if specified
            if use_args_in_key:
                args_key = "_".join(str(arg) for arg in args)
                limiter_key = f"{func.__name__}_{args_key}_limiter"
            else:
                limiter_key = f"{func.__name__}_limiter"

            if limiter_type == "concurrent":
                limiter = ConcurrentRateLimiter(backend, limiter_key, limit=limit)
            elif limiter_type == "window":
                if window is None:
                    raise ValueError(
                        "Window parameter is required for window rate limiting"
                    )
                limiter = WindowRateLimiter(
                    backend, limiter_key, limit=limit, window=window
                )
            else:
                raise ValueError("Invalid limiter type specified")

            with limiter.acquire(raise_on_failure=raise_on_failure) as acquired:
                if acquired:
                    return func(*args, **kwargs)
                else:
                    logging.warning(f"Rate limit exceeded for {limiter_key}")

        return wrapper

    return decorator
