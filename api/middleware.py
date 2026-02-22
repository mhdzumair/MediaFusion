import hashlib
import json
import logging
import os
import resource
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from threading import Lock

import dramatiq
from apscheduler.triggers.cron import CronTrigger
from dramatiq.middleware import Retries as OriginalRetries
from dramatiq.middleware import Shutdown, SkipMessage
from fastapi.requests import Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Match

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT, REDIS_SYNC_CLIENT
from db.schemas import UserData
from utils import const
from utils.crypto import crypto_utils
from utils.network import get_client_ip
from utils.parser import create_exception_stream
from utils.request_tracker import record_request
from utils.worker_memory_metrics import (
    WORKER_MEMORY_METRICS_HISTORY_KEY,
    WORKER_MEMORY_METRICS_SUMMARY_KEY,
)


async def find_route_handler(app, request: Request) -> Callable | None:
    for route in app.routes:
        match, scope = route.matches(request.scope)
        if match == Match.FULL:
            request.scope["path_params"] = scope["path_params"]
            request.scope["endpoint"] = getattr(route, "endpoint", None)
            return getattr(route, "endpoint", None)
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
        logging.info(f'{ip} - "{request.method} {url_path} HTTP/1.1" {response.status_code} {process_time}')


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
        except (ValueError, ValidationError):
            # check if the endpoint is for /streams
            if endpoint and endpoint.__name__ == "get_streams":
                return JSONResponse(
                    {
                        "streams": [
                            create_exception_stream(
                                settings.addon_name,
                                "Invalid MediaFusion configuration.\nDelete the Invalid MediaFusion installed addon and reconfigure it.",
                                "invalid_config.mp4",
                            ).model_dump(exclude_none=True, by_alias=True)
                        ]
                    },
                    headers=const.CORS_HEADERS,
                )

            return JSONResponse(
                {
                    "status": "error",
                    "message": "Invalid user data",
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
        "/api/v1/telegram/webhook",  # Telegram webhook - uses secret token authentication instead
        "/api/v1/telegram/login",  # Telegram login - uses login token authentication instead
        "/api/v1/kodi/qr-code/",  # QR code image - secured by short-lived Redis code
        "/api/v1/kodi/get-manifest/",  # Kodi addon poll - secured by short-lived one-time Redis code
        "/api/v1/kodi/generate-setup-code",  # Kodi addon - validates api_password from secret_str body
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


class MaxTasksPerChild(dramatiq.Middleware):
    def __init__(self, max_tasks=100):
        self.counter_mu = Lock()
        self.counter = max_tasks
        self.signaled = False
        self.requeue_log_emitted = False
        self.logger = dramatiq.get_logger("api.middleware", MaxTasksPerChild)

    def before_process_message(self, broker, message):
        with self.counter_mu:
            if self.counter <= 0:
                if not self.requeue_log_emitted:
                    self.logger.warning("Counter reached zero. Schedule message to be run later.")
                    self.requeue_log_emitted = True
                broker.enqueue(message, delay=30000)
                raise SkipMessage()

    def after_process_message(self, broker, message, *, result=None, exception=None):
        with self.counter_mu:
            if self.signaled:
                return

            self.counter = max(self.counter - 1, 0)
            self.logger.info("Remaining tasks: %d.", self.counter)
            if self.counter <= 0 and not self.signaled:
                self.logger.warning("Counter reached zero. Signaling current process.")
                self.signaled = True
                os.kill(os.getpid(), getattr(signal, "SIGTERM", signal.SIGINT))


class WorkerMemoryTelemetry(dramatiq.Middleware):
    def __init__(self, history_size: int = 1000):
        self.history_size = max(history_size, 100)
        self._snapshots: dict[str, dict[str, int | float | None]] = {}
        self._snapshots_mu = Lock()
        self.logger = dramatiq.get_logger("api.middleware", WorkerMemoryTelemetry)

    @staticmethod
    def _read_rss_bytes() -> int | None:
        """Read current process RSS from /proc when available."""
        try:
            with open("/proc/self/status", encoding="utf-8") as status_file:
                for line in status_file:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1]) * 1024
        except Exception:
            return None
        return None

    @staticmethod
    def _read_peak_rss_bytes() -> int | None:
        """Read process peak RSS (ru_maxrss) with platform-aware units."""
        try:
            max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if max_rss <= 0:
                return None
            # macOS reports bytes, Linux reports KB.
            if sys.platform == "darwin":
                return int(max_rss)
            return int(max_rss) * 1024
        except Exception:
            return None

    def _create_snapshot(self) -> dict[str, int | float | None]:
        rss = self._read_rss_bytes()
        peak = self._read_peak_rss_bytes()
        return {
            "started_at": time.time(),
            "rss_bytes": rss if rss is not None else peak,
            "peak_rss_bytes": peak,
        }

    def before_process_message(self, broker, message):
        with self._snapshots_mu:
            self._snapshots[message.message_id] = self._create_snapshot()

    @staticmethod
    def _parse_int(value: bytes | str | None) -> int:
        if value is None:
            return 0
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _persist_entry(self, entry: dict):
        try:
            entry_json = json.dumps(entry, default=str)
            REDIS_SYNC_CLIENT.lpush(WORKER_MEMORY_METRICS_HISTORY_KEY, entry_json)
            REDIS_SYNC_CLIENT.ltrim(WORKER_MEMORY_METRICS_HISTORY_KEY, 0, self.history_size - 1)

            REDIS_SYNC_CLIENT.hset(
                WORKER_MEMORY_METRICS_SUMMARY_KEY,
                mapping={
                    "last_timestamp": entry.get("timestamp"),
                    "last_actor": entry.get("actor_name"),
                    "last_status": entry.get("status"),
                    "last_rss_bytes": entry.get("rss_after_bytes") or entry.get("peak_rss_bytes") or 0,
                },
            )
            REDIS_SYNC_CLIENT.hincrby(WORKER_MEMORY_METRICS_SUMMARY_KEY, "total_events", 1)
            REDIS_SYNC_CLIENT.hincrby(WORKER_MEMORY_METRICS_SUMMARY_KEY, f"status:{entry.get('status')}", 1)
            REDIS_SYNC_CLIENT.hincrby(
                WORKER_MEMORY_METRICS_SUMMARY_KEY,
                f"actor:{entry.get('actor_name', 'unknown')}",
                1,
            )

            if error_type := entry.get("error_type"):
                REDIS_SYNC_CLIENT.hincrby(WORKER_MEMORY_METRICS_SUMMARY_KEY, f"error:{error_type}", 1)

            existing_peak = self._parse_int(REDIS_SYNC_CLIENT.hget(WORKER_MEMORY_METRICS_SUMMARY_KEY, "peak_rss_bytes"))
            measured_peak = max(
                entry.get("peak_rss_bytes") or 0,
                entry.get("rss_after_bytes") or 0,
            )
            if measured_peak > existing_peak:
                REDIS_SYNC_CLIENT.hset(WORKER_MEMORY_METRICS_SUMMARY_KEY, "peak_rss_bytes", measured_peak)
        except Exception as exc:
            self.logger.error("Failed to persist worker memory telemetry: %s", exc)

    def _record_event(self, message, status: str, exception: Exception | None = None):
        with self._snapshots_mu:
            snapshot = self._snapshots.pop(message.message_id, None)

        completed_at = time.time()
        rss_after = self._read_rss_bytes()
        peak_after = self._read_peak_rss_bytes()
        if rss_after is None:
            rss_after = peak_after
        rss_before = snapshot.get("rss_bytes") if snapshot else None
        rss_delta = None if rss_before is None or rss_after is None else rss_after - rss_before
        duration_ms = None
        if snapshot and snapshot.get("started_at"):
            duration_ms = round((completed_at - float(snapshot["started_at"])) * 1000, 2)

        entry = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "message_id": message.message_id,
            "actor_name": message.actor_name,
            "queue_name": getattr(message, "queue_name", None),
            "status": status,
            "duration_ms": duration_ms,
            "pid": os.getpid(),
            "rss_before_bytes": rss_before,
            "rss_after_bytes": rss_after,
            "rss_delta_bytes": rss_delta,
            "peak_rss_bytes": peak_after,
            "error_type": exception.__class__.__name__ if exception else None,
        }
        self._persist_entry(entry)

    def after_process_message(self, broker, message, *, result=None, exception=None):
        self._record_event(message, status="error" if exception else "success", exception=exception)

    def after_skip_message(self, broker, message):
        self._record_event(message, status="skipped")


class Retries(OriginalRetries):
    def after_process_message(self, broker, message, *, result=None, exception=None):
        if exception and isinstance(exception, Shutdown):
            message.fail()
            return

        return super().after_process_message(broker, message, result=result, exception=exception)


@dataclass
class TaskInfo:
    task_name: str
    min_interval: timedelta
    set_cache_expiry: bool
    task_key: str


class TaskManager(dramatiq.Middleware):
    def __init__(self, processing_time_buffer: int = 10):
        self.processing_time_buffer = processing_time_buffer
        self._task_info_cache: dict[str, TaskInfo] = {}

    @staticmethod
    @lru_cache(maxsize=128)
    def calculate_interval_from_crontab(crontab_expression: str) -> timedelta:
        """
        Calculate and cache the minimum interval between two consecutive runs
        specified by a crontab expression.
        """
        cron_trigger = CronTrigger.from_crontab(crontab_expression)
        next_time = cron_trigger.get_next_fire_time(None, datetime.now(tz=cron_trigger.timezone))
        second_next_time = cron_trigger.get_next_fire_time(next_time, next_time)
        return second_next_time - next_time

    def _generate_task_key(self, task_name: str, args: tuple, kwargs: dict) -> str:
        """Generate a consistent task key."""
        if spider_name := kwargs.get("spider_name"):
            return f"background_tasks:run_spider:spider_name={spider_name}"

        args_str = "_".join(str(arg) for arg in args)
        kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"background_tasks:{task_name}:{args_str}_{kwargs_str}".rstrip("_")

    def get_task_info(self, broker, message) -> TaskInfo | None:
        """
        Get task information with caching for better performance.
        """
        # Try to get from cache first
        message_id = message.message_id
        if message_id in self._task_info_cache:
            return self._task_info_cache[message_id]

        task_name = message.actor_name
        args = message.args
        kwargs = message.kwargs.copy()
        if kwargs.pop("force_run", False):
            logging.info(f"Force run enabled for task {task_name}; skipping interval guard.")
            return None
        actor = broker.get_actor(task_name)
        min_interval = getattr(actor, "_minimum_run_interval", None)
        set_cache_expiry = False

        if crontab_expr := kwargs.get("crontab_expression"):
            min_interval = self.calculate_interval_from_crontab(crontab_expr)
            del kwargs["crontab_expression"]
        elif min_interval:
            set_cache_expiry = True
        else:
            logging.debug(f"No restriction set for task {task_name} with args {args} and kwargs {kwargs}")
            return None

        task_key = self._generate_task_key(task_name, args, kwargs)
        task_info = TaskInfo(task_name, min_interval, set_cache_expiry, task_key)

        # Cache the result
        self._task_info_cache[message_id] = task_info
        return task_info

    def _check_and_update_redis(self, task_info: TaskInfo, operation: str = "check") -> bool | None:
        """
        Handle Redis operations with error handling and logging.
        """
        try:
            if operation == "check":
                last_run = REDIS_SYNC_CLIENT.get(task_info.task_key)
                if last_run is not None:
                    last_run = datetime.fromtimestamp(float(last_run))
                    difference = datetime.now() - last_run
                    min_interval = task_info.min_interval - timedelta(seconds=self.processing_time_buffer)

                    if difference < min_interval:
                        logging.warning(
                            f"Discarding task {task_info.task_name} with task_key {task_info.task_key}. "
                            f"Last run: {difference} ago. Minimum interval: {min_interval}"
                        )
                        return True

            # Update Redis with new timestamp
            ex_time = int(task_info.min_interval.total_seconds()) if task_info.set_cache_expiry else None
            REDIS_SYNC_CLIENT.set(
                task_info.task_key,
                datetime.now().timestamp(),
                ex=ex_time,
            )
            logging.debug(f"Task key {task_info.task_key} updated in Redis")

        except Exception as e:
            logging.error(f"Redis operation failed for task {task_info.task_key}: {str(e)}")
            # Don't skip the message if Redis fails
            return False

        return None

    def before_process_message(self, broker, message):
        if task_info := self.get_task_info(broker, message):
            if self._check_and_update_redis(task_info, "check"):
                raise SkipMessage()

    def after_process_message(self, broker, message, *, result=None, exception=None):
        task_info = self.get_task_info(broker, message)
        try:
            if exception:
                # If task execution failed (including forced timeout/kill), clear
                # the run marker so operators can retrigger immediately.
                if task_info:
                    try:
                        REDIS_SYNC_CLIENT.delete(task_info.task_key)
                    except Exception as redis_error:
                        logging.error("Failed to clear task key %s: %s", task_info.task_key, redis_error)
                return

            if task_info:
                self._check_and_update_redis(task_info, "update")
        finally:
            # Always cleanup cache entry for this message.
            self._task_info_cache.pop(message.message_id, None)

    def after_skip_message(self, broker, message):
        # Cleanup cache for skipped messages
        self._task_info_cache.pop(message.message_id, None)


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
