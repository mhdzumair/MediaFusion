import hashlib
import logging
from typing import Callable, Optional

from fastapi.requests import Request
from fastapi.responses import Response
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from db.config import settings
from db.schemas import UserData
from utils import crypto, const


def get_client_ip(request: Request) -> str | None:
    """
    Extract the client's real IP address from the request headers or fallback to the client host.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # In some cases, this header can contain multiple IPs
        # separated by commas.
        # The first one is the original client's IP.
        return x_forwarded_for.split(",")[0].strip()
    # Fallback to X-Real-IP if X-Forwarded-For is not available
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip
    return request.client.host if request.client else "Unknown"


async def find_route_handler(app, request: Request) -> Optional[Callable]:
    for route in app.routes:
        match, scope = route.matches(request.scope)
        if match == Match.FULL:
            request.scope["path_params"] = scope["path_params"]
            request.scope["endpoint"] = route.endpoint
            return route.endpoint
    return None


class SecureLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        await self.custom_log(request, response)
        return response

    @staticmethod
    async def custom_log(request: Request, response: Response):
        ip = get_client_ip(request)
        url_path = str(request.url)
        if request.path_params.get("secret_str"):
            url_path = url_path.replace(
                request.path_params.get("secret_str"), "***MASKED***"
            )
        logging.info(
            f'{ip} - "{request.method} {url_path} HTTP/1.1" {response.status_code}'
        )


class UserDataMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        endpoint = await find_route_handler(request.app, request)
        secret_str = request.path_params.get("secret_str")
        # Decrypt and parse the UserData from secret_str
        user_data = crypto.decrypt_user_data(secret_str)

        # validate api password if set
        if settings.api_password:
            is_auth_required = getattr(endpoint, "auth_required", False)
            if is_auth_required and user_data.api_password != settings.api_password:
                return Response(
                    content="Unauthorized",
                    status_code=401,
                    headers=const.NO_CACHE_HEADERS,
                )

        # Attach UserData to request state for access in endpoints
        request.scope["user"] = user_data

        try:
            return await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "No response returned." and await request.is_disconnected():
                return Response(status_code=204)
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_client: Redis):
        super().__init__(app)
        self.redis = redis_client

    async def dispatch(self, request: Request, call_next: Callable):
        # Skip rate limiting for exempt paths
        if not settings.enable_rate_limit:
            return await call_next(request)

        # Retrieve the endpoint function from the request
        endpoint = request.scope.get("endpoint")
        if not endpoint:
            return await call_next(request)

        is_exclude = getattr(endpoint, "exclude_rate_limit", False)
        if is_exclude:
            return await call_next(request)

        limit = getattr(endpoint, "limit", 50)  # Default rate limit
        window = getattr(endpoint, "window", 60)
        scope = getattr(endpoint, "scope", "default")  # Default scope

        ip = get_client_ip(request)

        # Generate a unique key for rate limiting
        identifier = self.generate_identifier(ip, request.user)
        key = f"rate_limit:{identifier}:{scope}"

        # Check and apply rate limit
        allowed = await self.check_rate_limit_with_redis(key, limit, window)
        if not allowed:
            return Response(
                content="Rate limit exceeded",
                status_code=429,
                headers=const.NO_CACHE_HEADERS,
            )

        return await call_next(request)

    @staticmethod
    def generate_identifier(ip: str, user_data: UserData) -> str:
        raw_identifier = f"{ip}"
        if user_data.streaming_provider:
            provider_profile = (
                user_data.streaming_provider.token
                or user_data.streaming_provider.username
            )
            raw_identifier += f"-{provider_profile}"
        return hashlib.md5(raw_identifier.encode()).hexdigest()

    async def check_rate_limit_with_redis(
        self, key: str, limit: int, window: int
    ) -> bool:
        try:
            results = await (
                self.redis.pipeline(transaction=True)
                .incr(key)
                .expire(key, window)
                .execute()
            )
            current_count = results[0]
            if current_count > limit:
                return False  # Rate limit exceeded
            return True
        except Exception as e:
            # Log error but allow the request to proceed to avoid blocking legitimate requests
            logging.error(f"Rate limit error: {e}")
            return True
