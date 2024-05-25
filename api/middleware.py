import hashlib
import logging
import os
import signal
import time
from datetime import timedelta, datetime
from threading import Lock
from typing import Callable, Optional

import dramatiq
import redis
from apscheduler.triggers.cron import CronTrigger
from dramatiq.middleware import Retries as OriginalRetries, Shutdown, SkipMessage
from fastapi.requests import Request
from fastapi.responses import Response
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from db.config import settings
from db.schemas import UserData
from utils import crypto, const
from utils.network import get_client_ip


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
        if settings.api_password and settings.is_public_instance is False:
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
            try:
                return await call_next(request)
            except RuntimeError:
                return Response(status_code=204)

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


class TaskManager(dramatiq.Middleware):
    def __init__(self):
        self.redis: redis.Redis = redis.Redis.from_url(settings.redis_url)

    @staticmethod
    def calculate_interval_from_crontab(crontab_expression: str) -> timedelta:
        """
        Calculate the minimum interval between two consecutive runs
        specified by a crontab expression.
        """
        cron_trigger = CronTrigger.from_crontab(crontab_expression)
        next_time = cron_trigger.get_next_fire_time(None, datetime.now())
        second_next_time = cron_trigger.get_next_fire_time(next_time, next_time)
        return second_next_time - next_time

    def get_task_data(self, broker, message):
        task_name = message.actor_name
        args = message.args
        kwargs = message.kwargs.copy()
        actor = broker.get_actor(task_name)
        min_interval = getattr(actor, "_minimum_run_interval", None)
        set_cache_expiry = False

        if kwargs.get("crontab_expression"):
            min_interval = self.calculate_interval_from_crontab(
                kwargs.get("crontab_expression")
            )
            del kwargs["crontab_expression"]
        elif min_interval:
            set_cache_expiry = True
        else:
            logging.info(
                f"No restriction set for task {task_name} with args {args} and kwargs {kwargs}"
            )
            return

        if spider_name := kwargs.get("spider_name"):
            task_key = f"background_tasks:run_spider:spider_name={spider_name}"
        elif video_id := kwargs.get("video_id"):
            task_key = f"background_tasks:{task_name}:video_id={video_id}"
        else:
            keys = "_".join([str(arg) for arg in args])
            keys += "_".join([f"{k}={v}" for k, v in kwargs.items()])
            task_key = f"background_tasks:{task_name}:{keys}"

        return task_name, min_interval, set_cache_expiry, task_key

    def before_process_message(self, broker, message):
        task_data = self.get_task_data(broker, message)
        if not task_data:
            return
        task_name, min_interval, set_cache_expiry, task_key = task_data

        # Subtract 10 seconds to account for processing time
        min_interval = min_interval - timedelta(seconds=10)

        last_run = self.redis.get(task_key)
        if last_run is not None:
            last_run = datetime.fromtimestamp(float(last_run))
            difference = datetime.now() - last_run
            if difference < min_interval:
                logging.warning(
                    f"Discarding task {task_name} with task_key {task_key} due to minimum run interval. Last run: {difference} ago. Minimum interval: {min_interval}"
                )
                raise SkipMessage()

        # Set the cache expiry for the task
        ex_time = int(min_interval.total_seconds()) if set_cache_expiry else None
        self.redis.set(
            task_key,
            datetime.now().timestamp(),
            ex=ex_time,
        )
        logging.info(f"Executing task {task_name} with task key {task_key}")

    def after_process_message(self, broker, message, *, result=None, exception=None):
        if exception:
            return

        task_data = self.get_task_data(broker, message)
        if not task_data:
            return
        task_name, min_interval, set_cache_expiry, task_key = task_data

        # Update the cache with the latest run time
        self.redis.set(
            task_key,
            datetime.now().timestamp(),
            ex=int(min_interval.total_seconds()) if set_cache_expiry else None,
        )
        logging.info(f"Task key {task_key} updated cache with latest run time.")


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{process_time:.4f} seconds"
        return response
