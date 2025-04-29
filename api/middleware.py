import hashlib
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import timedelta, datetime
from functools import lru_cache
from threading import Lock
from typing import Callable, Optional, Dict

import dramatiq
from apscheduler.triggers.cron import CronTrigger
from dramatiq.middleware import Retries as OriginalRetries, Shutdown, SkipMessage
from fastapi.requests import Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Match

from db.config import settings
from db.redis_database import REDIS_SYNC_CLIENT, REDIS_ASYNC_CLIENT
from db.schemas import UserData
from utils import const
from utils.crypto import crypto_utils
from utils.network import get_client_ip
from utils.parser import create_exception_stream


async def find_route_handler(app, request: Request) -> Optional[Callable]:
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
            url_path = url_path.replace(
                request.path_params.get("secret_str"), "*MASKED*"
            )
        if request.path_params.get("existing_secret_str"):
            url_path = url_path.replace(
                request.path_params.get("existing_secret_str"), "*MASKED*"
            )
        logging.info(
            f'{ip} - "{request.method} {url_path} HTTP/1.1" {response.status_code} {process_time}'
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

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
                user_data.streaming_provider.token or user_data.streaming_provider.email
            )
            raw_identifier += f"-{provider_profile}"
        return hashlib.md5(raw_identifier.encode()).hexdigest()

    @staticmethod
    async def check_rate_limit_with_redis(key: str, limit: int, window: int) -> bool:
        try:
            results = await (
                REDIS_ASYNC_CLIENT.pipeline(transaction=True)
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


class MaxTasksPerChild(dramatiq.Middleware):
    def __init__(self, max_tasks=100):
        self.counter_mu = Lock()
        self.counter = max_tasks
        self.signaled = False
        self.logger = dramatiq.get_logger("api.middleware", MaxTasksPerChild)

    def before_process_message(self, broker, message):
        with self.counter_mu:
            if self.counter <= 0:
                self.logger.warning(
                    "Counter reached zero. Schedule message to be run later."
                )
                broker.enqueue(message, delay=30000)

    def after_process_message(self, broker, message, *, result=None, exception=None):
        with self.counter_mu:
            self.counter -= 1
            self.logger.info("Remaining tasks: %d.", self.counter)
            if self.counter <= 0 and not self.signaled:
                self.logger.warning("Counter reached zero. Signaling current process.")
                os.kill(os.getppid(), getattr(signal, "SIGHUP", signal.SIGTERM))
                self.signaled = True


class Retries(OriginalRetries):
    def after_process_message(self, broker, message, *, result=None, exception=None):
        if exception and isinstance(exception, Shutdown):
            message.fail()
            return

        return super().after_process_message(
            broker, message, result=result, exception=exception
        )


@dataclass
class TaskInfo:
    task_name: str
    min_interval: timedelta
    set_cache_expiry: bool
    task_key: str


class TaskManager(dramatiq.Middleware):
    def __init__(self, processing_time_buffer: int = 10):
        self.processing_time_buffer = processing_time_buffer
        self._task_info_cache: Dict[str, TaskInfo] = {}

    @staticmethod
    @lru_cache(maxsize=128)
    def calculate_interval_from_crontab(crontab_expression: str) -> timedelta:
        """
        Calculate and cache the minimum interval between two consecutive runs
        specified by a crontab expression.
        """
        cron_trigger = CronTrigger.from_crontab(crontab_expression)
        next_time = cron_trigger.get_next_fire_time(
            None, datetime.now(tz=cron_trigger.timezone)
        )
        second_next_time = cron_trigger.get_next_fire_time(next_time, next_time)
        return second_next_time - next_time

    def _generate_task_key(self, task_name: str, args: tuple, kwargs: dict) -> str:
        """Generate a consistent task key."""
        if spider_name := kwargs.get("spider_name"):
            return f"background_tasks:run_spider:spider_name={spider_name}"

        args_str = "_".join(str(arg) for arg in args)
        kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return f"background_tasks:{task_name}:{args_str}_{kwargs_str}".rstrip("_")

    def get_task_info(self, broker, message) -> Optional[TaskInfo]:
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
        actor = broker.get_actor(task_name)
        min_interval = getattr(actor, "_minimum_run_interval", None)
        set_cache_expiry = False

        if crontab_expr := kwargs.get("crontab_expression"):
            min_interval = self.calculate_interval_from_crontab(crontab_expr)
            del kwargs["crontab_expression"]
        elif min_interval:
            set_cache_expiry = True
        else:
            logging.debug(
                f"No restriction set for task {task_name} with args {args} and kwargs {kwargs}"
            )
            return None

        task_key = self._generate_task_key(task_name, args, kwargs)
        task_info = TaskInfo(task_name, min_interval, set_cache_expiry, task_key)

        # Cache the result
        self._task_info_cache[message_id] = task_info
        return task_info

    def _check_and_update_redis(
        self, task_info: TaskInfo, operation: str = "check"
    ) -> Optional[bool]:
        """
        Handle Redis operations with error handling and logging.
        """
        try:
            if operation == "check":
                last_run = REDIS_SYNC_CLIENT.get(task_info.task_key)
                if last_run is not None:
                    last_run = datetime.fromtimestamp(float(last_run))
                    difference = datetime.now() - last_run
                    min_interval = task_info.min_interval - timedelta(
                        seconds=self.processing_time_buffer
                    )

                    if difference < min_interval:
                        logging.warning(
                            f"Discarding task {task_info.task_name} with task_key {task_info.task_key}. "
                            f"Last run: {difference} ago. Minimum interval: {min_interval}"
                        )
                        return True

            # Update Redis with new timestamp
            ex_time = (
                int(task_info.min_interval.total_seconds())
                if task_info.set_cache_expiry
                else None
            )
            REDIS_SYNC_CLIENT.set(
                task_info.task_key,
                datetime.now().timestamp(),
                ex=ex_time,
            )
            logging.debug(f"Task key {task_info.task_key} updated in Redis")

        except Exception as e:
            logging.error(
                f"Redis operation failed for task {task_info.task_key}: {str(e)}"
            )
            # Don't skip the message if Redis fails
            return False

        return None

    def before_process_message(self, broker, message):
        if task_info := self.get_task_info(broker, message):
            if self._check_and_update_redis(task_info, "check"):
                raise SkipMessage()

    def after_process_message(self, broker, message, *, result=None, exception=None):
        if exception:
            return

        if task_info := self.get_task_info(broker, message):
            self._check_and_update_redis(task_info, "update")

        # Cleanup cache
        self._task_info_cache.pop(message.message_id, None)

    def after_skip_message(self, broker, message):
        # Cleanup cache for skipped messages
        self._task_info_cache.pop(message.message_id, None)


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        try:
            response = await call_next(request)
        except RuntimeError as exc:
            if str(exc) == "No response returned." and await request.is_disconnected():
                response = Response(status_code=204)
            else:
                logging.exception(f"Internal Server Error: {exc}")
                response = Response(
                    status_code=500,
                    content="Internal Server Error. Check the server log & Create GitHub Issue",
                    headers=const.NO_CACHE_HEADERS,
                )
        except Exception as e:
            logging.exception(f"Internal Server Error: {e}")
            response = Response(
                content="Internal Server Error. Check the server log & Create GitHub Issue",
                status_code=500,
                headers=const.NO_CACHE_HEADERS,
            )
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{process_time:.4f} seconds"
        return response
