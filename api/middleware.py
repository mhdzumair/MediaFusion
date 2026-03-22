import hashlib
import logging
import time
from collections.abc import Callable
from threading import Lock

from fastapi.requests import Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Match

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils import const
from utils.crypto import UserFacingSecretError, crypto_utils
from utils.network import get_client_ip
from utils.parser import create_exception_stream
from utils.request_tracker import record_request

ROUTE_LOOKUP_CACHE_MAX_ENTRIES = 4096
_route_lookup_cache: dict[str, object] = {}
_route_lookup_cache_mu = Lock()


def _route_cache_get(cache_key: str):
    with _route_lookup_cache_mu:
        return _route_lookup_cache.get(cache_key)


def _route_cache_set(cache_key: str, route) -> None:
    with _route_lookup_cache_mu:
        if cache_key in _route_lookup_cache:
            return
        if len(_route_lookup_cache) >= ROUTE_LOOKUP_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(_route_lookup_cache), None)
            if oldest_key is not None:
                _route_lookup_cache.pop(oldest_key, None)
        _route_lookup_cache[cache_key] = route


async def find_route_handler(app, request: Request) -> Callable | None:
    if endpoint := request.scope.get("endpoint"):
        return endpoint

    path_value = request.scope.get("path", "")
    path_digest = hashlib.sha256(path_value.encode("utf-8")).hexdigest()
    cache_key = f"{request.method}:{path_digest}"
    cached_route = _route_cache_get(cache_key)
    if cached_route is not None:
        match, matched_scope = cached_route.matches(request.scope)
        if match == Match.FULL:
            request.scope["path_params"] = matched_scope.get("path_params", {})
            endpoint = getattr(cached_route, "endpoint", None)
            request.scope["endpoint"] = endpoint
            return endpoint

    routes = getattr(getattr(app, "router", app), "routes", [])
    for route in routes:
        route_methods = getattr(route, "methods", None)
        if route_methods and request.method not in route_methods:
            continue

        match, matched_scope = route.matches(request.scope)
        if match == Match.FULL:
            request.scope["path_params"] = matched_scope.get("path_params", {})
            endpoint = getattr(route, "endpoint", None)
            request.scope["endpoint"] = endpoint
            _route_cache_set(cache_key, route)
            return endpoint
    return None


class SecureLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        await self.custom_log(request, response)
        return response

    @staticmethod
    async def custom_log(request: Request, response: Response):
        ip = get_client_ip(request)
        url_path = request.url.path
        process_time = response.headers.get("X-Process-Time", "")
        if request.path_params.get("secret_str"):
            url_path = url_path.replace(request.path_params.get("secret_str"), "*MASKED*")
        if request.path_params.get("existing_secret_str"):
            url_path = url_path.replace(request.path_params.get("existing_secret_str"), "*MASKED*")
        logging.info(
            '%s - "%s %s HTTP/1.1" %s %s',
            ip,
            request.method,
            url_path,
            response.status_code,
            process_time,
        )


class UserDataMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        endpoint = await find_route_handler(request.app, request)

        # Decrypt and parse the UserData from secret_str or Decode from encoded_user_data header
        try:
            encoded_user_data = request.headers.get("encoded_user_data")
            if encoded_user_data:
                user_data = crypto_utils.decode_user_data(encoded_user_data)
                secret_str = await crypto_utils.process_user_data(user_data)
            else:
                secret_str = request.path_params.get("secret_str") or request.query_params.get("secret_str")
                user_data = await crypto_utils.decrypt_user_data(secret_str)
        except (ValueError, ValidationError) as e:
            user_hint = str(e) if isinstance(e, UserFacingSecretError) else ""
            base_invalid = (
                "Invalid MediaFusion configuration.\nDelete the Invalid MediaFusion installed addon and reconfigure it."
            )
            streams_msg = f"{base_invalid}\n\n{user_hint}" if user_hint else base_invalid
            # check if the endpoint is for /streams
            if endpoint and endpoint.__name__ == "get_streams":
                return JSONResponse(
                    {
                        "streams": [
                            create_exception_stream(
                                settings.addon_name,
                                streams_msg,
                                "invalid_config.mp4",
                            ).model_dump(exclude_none=True, by_alias=True)
                        ]
                    },
                    headers=const.CORS_HEADERS,
                )

            return JSONResponse(
                {
                    "status": "error",
                    "message": user_hint or "Invalid user data",
                }
            )

        # validate api password if set
        if settings.is_public_instance is False:
            is_auth_required = getattr(endpoint, "auth_required", False)
            if is_auth_required and user_data.api_password != settings.api_password:
                # check if the endpoint is for /streams
                if endpoint and endpoint.__name__ == "get_streams":
                    return JSONResponse(
                        {
                            "streams": [
                                create_exception_stream(
                                    settings.addon_name,
                                    "Unauthorized.\nInvalid MediaFusion configuration.\nDelete the Invalid MediaFusion installed addon and reconfigure it.",
                                    "invalid_config.mp4",
                                ).model_dump(exclude_none=True, by_alias=True)
                            ]
                        },
                        headers=const.CORS_HEADERS,
                    )
                if request.url.path.startswith("/api/v1/"):
                    return JSONResponse(
                        {
                            "error": True,
                            "detail": "Unauthorized",
                            "status_code": 401,
                        },
                        headers=const.NO_CACHE_HEADERS,
                    )
                return JSONResponse(
                    {
                        "status": "error",
                        "message": "Unauthorized",
                    },
                    status_code=401,
                    headers=const.NO_CACHE_HEADERS,
                )

        # Attach UserData to request state for access in endpoints
        request.scope["user"] = user_data
        request.scope["secret_str"] = secret_str

        return await call_next(request)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate API key for private instances.

    On private instances (is_public_instance=false), this middleware validates
    the X-API-Key header against the configured api_password for non-exempt paths.
    Stremio endpoints have their own api_password validation in UserData.
    """

    # Paths that don't require API key validation
    EXEMPT_PATH_PREFIXES = (
        "/api/v1/instance/",  # Must be accessible to know if key is needed
        "/api/v1/integrations/simkl/callback",  # OAuth callback must be browser-accessible without API key header
        "/api/v1/telegram/webhook",  # Telegram webhook - uses secret token authentication instead
        "/api/v1/telegram/login",  # Telegram login - uses login token authentication instead
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/static/",  # Static resources
        "/app/",  # React SPA
        "/app",
        # Stremio endpoints use secret_str with embedded api_password
        "/manifest.json",
        "/{secret_str}/",
        "/streaming_provider/",
        "/poster/",  # Poster endpoints
        "/torznab",  # Torznab API handles its own authentication
    )

    async def dispatch(self, request: Request, call_next: Callable):
        # Skip for public instances - no API key required
        if settings.is_public_instance:
            return await call_next(request)

        path = request.url.path

        # Skip exempt paths
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        # Skip for home page
        if path == "/":
            return await call_next(request)

        # Check for secret_str in path - Stremio addon URLs contain encrypted user data
        path_params = request.scope.get("path_params", {})
        if path_params.get("secret_str"):
            return await call_next(request)

        # Validate API key from header
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != settings.api_password:
            if path.startswith("/api/v1/"):
                return JSONResponse(
                    content={
                        "error": True,
                        "detail": "Invalid or missing API key",
                        "status_code": 401,
                    },
                    headers=const.NO_CACHE_HEADERS,
                )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
                headers=const.NO_CACHE_HEADERS,
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        # Skip rate limiting if disabled
        if not settings.enable_rate_limit:
            return await call_next(request)

        # Skip rate limiting entirely for private instances
        # Private instance users have authenticated via API key
        if not settings.is_public_instance:
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
            if request.url.path.startswith("/api/v1/"):
                return JSONResponse(
                    content={
                        "error": True,
                        "detail": "Rate limit exceeded",
                        "status_code": 429,
                    },
                    headers=const.NO_CACHE_HEADERS,
                )
            return Response(
                content="Rate limit exceeded",
                status_code=429,
                headers=const.NO_CACHE_HEADERS,
            )

        return await call_next(request)

    @staticmethod
    def generate_identifier(ip: str, user_data: UserData) -> str:
        raw_identifier = f"{ip}"
        primary_provider = user_data.get_primary_provider()
        if primary_provider:
            provider_profile = primary_provider.token or primary_provider.email
            raw_identifier += f"-{provider_profile}"
        return hashlib.md5(raw_identifier.encode()).hexdigest()

    @staticmethod
    async def check_rate_limit_with_redis(key: str, limit: int, window: int) -> bool:
        try:
            results = await REDIS_ASYNC_CLIENT.pipeline(transaction=True).incr(key).expire(key, window).execute()
            current_count = results[0]
            if current_count > limit:
                return False  # Rate limit exceeded
            return True
        except Exception as e:
            # Log error but allow the request to proceed to avoid blocking legitimate requests
            logging.error(f"Rate limit error: {e}")
            return True


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        is_api_path = request.url.path.startswith("/api/v1/")
        try:
            response = await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "No response returned." and await request.is_disconnected():
                response = Response(status_code=204)
            else:
                logging.exception(f"Internal Server Error: {exc}")
                if is_api_path:
                    response = JSONResponse(
                        content={
                            "error": True,
                            "detail": "Internal Server Error. Check the server log & Create GitHub Issue",
                            "status_code": 500,
                        },
                        headers=const.NO_CACHE_HEADERS,
                    )
                else:
                    response = Response(
                        status_code=500,
                        content="Internal Server Error. Check the server log & Create GitHub Issue",
                        headers=const.NO_CACHE_HEADERS,
                    )
        except Exception as e:
            logging.exception(f"Internal Server Error: {e}")
            if is_api_path:
                response = JSONResponse(
                    content={
                        "error": True,
                        "detail": "Internal Server Error. Check the server log & Create GitHub Issue",
                        "status_code": 500,
                    },
                    headers=const.NO_CACHE_HEADERS,
                )
            else:
                response = Response(
                    content="Internal Server Error. Check the server log & Create GitHub Issue",
                    status_code=500,
                    headers=const.NO_CACHE_HEADERS,
                )
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{process_time:.4f} seconds"

        # Record request metrics (fire-and-forget, never disrupts response)
        try:
            if settings.enable_request_metrics:
                sanitized_path = request.url.path
                # Mask secret path params (same logic as SecureLoggingMiddleware)
                if request.path_params.get("secret_str"):
                    sanitized_path = sanitized_path.replace(request.path_params["secret_str"], "*MASKED*")
                if request.path_params.get("existing_secret_str"):
                    sanitized_path = sanitized_path.replace(request.path_params["existing_secret_str"], "*MASKED*")

                # Extract route template from the matched route
                route_template = None
                route = request.scope.get("route")
                if route and hasattr(route, "path"):
                    route_template = route.path

                await record_request(
                    method=request.method,
                    path=sanitized_path,
                    route_template=route_template,
                    status_code=response.status_code,
                    process_time=process_time,
                    client_ip=get_client_ip(request),
                )
        except Exception:
            pass

        return response
