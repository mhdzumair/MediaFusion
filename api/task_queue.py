import asyncio
import contextvars
import json
import logging
import math
import os
import resource
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable

from apscheduler.triggers.cron import CronTrigger
from taskiq import SimpleRetryMiddleware
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT, REDIS_SYNC_CLIENT
from utils.worker_memory_metrics import (
    WORKER_MEMORY_METRICS_HISTORY_KEY,
    WORKER_MEMORY_METRICS_SUMMARY_KEY,
)

logger = logging.getLogger(__name__)

TASKIQ_QUEUE_PREFIX = "mediafusion:taskiq"
TASK_MANAGER_PROCESSING_TIME_BUFFER_SECONDS = 10
DEFAULT_RESULT_EXPIRY_SECONDS = 60 * 60
TASK_CANCELLATION_KEY_PREFIX = "mediafusion:taskiq:cancelled"
TASK_CANCELLATION_TTL_SECONDS = 24 * 60 * 60
TASK_DETAILS_KEY_PREFIX = "mediafusion:taskiq:task"
TASK_RECENT_TASKS_KEY = "mediafusion:taskiq:tasks:recent"
TASK_RUNNING_TASKS_KEY = "mediafusion:taskiq:tasks:running"
TASK_DETAILS_TTL_SECONDS = 7 * 24 * 60 * 60
TASK_RECENT_TASKS_MAX = 2000
INTERNAL_TASK_ID_KWARG = "_taskiq_task_id"
_CURRENT_TASK_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "taskiq_current_task_id",
    default=None,
)


class TaskCancelledError(RuntimeError):
    """Raised when a task is cancelled via cancellation marker."""


class TaskRetryError(RuntimeError):
    """Raised when a task cannot be retried from stored payload."""


@dataclass(slots=True)
class ActorRegistration:
    name: str
    fn: Callable[..., Any]
    queue_name: str
    priority: int
    time_limit_ms: int | float | None
    max_retries: int | None
    taskiq_task: Any


class ActorHandle:
    def __init__(self, registration: ActorRegistration):
        self._registration = registration
        self.fn = registration.fn
        self.__name__ = registration.name
        self.__doc__ = registration.fn.__doc__

    @property
    def actor_name(self) -> str:
        return self._registration.name

    @property
    def queue_name(self) -> str:
        return self._registration.queue_name

    async def async_send(self, *args, **kwargs):
        return await _enqueue_actor_job(self._registration, args=args, kwargs=kwargs, delay_ms=None)

    async def async_send_with_options(
        self,
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        delay: int | None = None,
    ):
        return await _enqueue_actor_job(
            self._registration,
            args=args,
            kwargs=kwargs or {},
            delay_ms=delay,
        )

    def send(self, *args, **kwargs):
        return _dispatch_or_run(self.async_send(*args, **kwargs))

    def send_with_options(
        self,
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        delay: int | None = None,
    ):
        return _dispatch_or_run(
            self.async_send_with_options(args=args, kwargs=kwargs, delay=delay),
        )


_ACTOR_REGISTRY: dict[str, ActorRegistration] = {}
_QUEUE_BROKERS: dict[str, RedisStreamBroker] = {}
_BROKER_CLIENT_STARTED: set[int] = set()
_BROKER_START_LOCKS: dict[int, asyncio.Lock] = {}


def _resolve_runtime_queue_name(queue_name: str) -> str:
    if settings.taskiq_single_worker_mode:
        return "default"
    return queue_name


def actor(
    *,
    priority: int = 0,
    time_limit: int | float | None = None,
    queue_name: str = "default",
    max_retries: int | None = None,
    min_backoff: int | None = None,  # Accepted for compatibility.
    max_backoff: int | None = None,  # Accepted for compatibility.
):
    del min_backoff, max_backoff

    def decorator(fn: Callable[..., Any]) -> ActorHandle:
        registration = ActorRegistration(
            name=fn.__name__,
            fn=fn,
            queue_name=queue_name,
            priority=priority,
            time_limit_ms=time_limit,
            max_retries=max_retries,
            taskiq_task=None,
        )
        broker = _get_or_create_queue_broker(queue_name)
        task_labels = _build_taskiq_task_labels(time_limit_ms=time_limit, max_retries=max_retries)

        async def worker_function(*args, _registration=registration, **kwargs):
            return await _execute_actor(_registration, queue_name, None, args, kwargs)

        registration.taskiq_task = broker.task(task_name=registration.name, **task_labels)(worker_function)
        _ACTOR_REGISTRY[registration.name] = registration
        return ActorHandle(registration)

    return decorator


def get_taskiq_queue_name(queue_name: str) -> str:
    runtime_queue_name = _resolve_runtime_queue_name(queue_name)
    return f"{TASKIQ_QUEUE_PREFIX}:{runtime_queue_name}"


def get_task_cancellation_key(task_id: str) -> str:
    return f"{TASK_CANCELLATION_KEY_PREFIX}:{task_id}"


def get_task_details_key(task_id: str) -> str:
    return f"{TASK_DETAILS_KEY_PREFIX}:{task_id}"


def get_current_task_id() -> str | None:
    return _CURRENT_TASK_ID.get()


def is_task_cancel_requested(task_id: str) -> bool:
    try:
        return bool(REDIS_SYNC_CLIENT.exists(get_task_cancellation_key(task_id)))
    except Exception:
        return False


async def request_task_cancellation(task_id: str, reason: str = "manual") -> None:
    await REDIS_ASYNC_CLIENT.set(
        get_task_cancellation_key(task_id),
        reason,
        ex=TASK_CANCELLATION_TTL_SECONDS,
    )
    await _upsert_task_record(
        task_id,
        {
            "cancellation_requested": True,
            "cancellation_reason": reason,
            "cancellation_requested_at": datetime.now(tz=UTC).isoformat(),
        },
    )


def request_task_cancellation_sync(task_id: str, reason: str = "manual") -> None:
    REDIS_SYNC_CLIENT.set(
        get_task_cancellation_key(task_id),
        reason,
        ex=TASK_CANCELLATION_TTL_SECONDS,
    )


def get_registered_queue_names() -> list[str]:
    return sorted({registration.queue_name for registration in _ACTOR_REGISTRY.values()})


async def get_task_record(task_id: str) -> dict[str, Any] | None:
    raw = await REDIS_ASYNC_CLIENT.get(get_task_details_key(task_id))
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        return None
    return None


async def list_task_records(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    task_ids_raw = await REDIS_ASYNC_CLIENT.lrange(TASK_RECENT_TASKS_KEY, offset, offset + limit - 1)
    task_ids: list[str] = []
    for task_id in task_ids_raw:
        if isinstance(task_id, bytes):
            task_id = task_id.decode("utf-8", errors="ignore")
        task_ids.append(str(task_id))
    if not task_ids:
        return []

    raw_records = await REDIS_ASYNC_CLIENT.mget([get_task_details_key(task_id) for task_id in task_ids])
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        if not raw:
            continue
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                records.append(parsed)
        except (TypeError, ValueError):
            continue
    return records


async def list_running_task_ids() -> list[str]:
    running_raw = await REDIS_ASYNC_CLIENT.smembers(TASK_RUNNING_TASKS_KEY)
    running: list[str] = []
    for task_id in running_raw:
        if isinstance(task_id, bytes):
            task_id = task_id.decode("utf-8", errors="ignore")
        running.append(str(task_id))
    return sorted(running)


async def retry_task_by_id(task_id: str) -> str:
    record = await get_task_record(task_id)
    if not record:
        raise TaskRetryError(f"Task '{task_id}' not found.")

    actor_name = record.get("actor_name")
    if not actor_name:
        raise TaskRetryError(f"Task '{task_id}' has no actor information.")

    registration = _ACTOR_REGISTRY.get(str(actor_name))
    if registration is None:
        raise TaskRetryError(f"Actor '{actor_name}' is not registered.")

    args_payload = record.get("args_payload", [])
    kwargs_payload = record.get("kwargs_payload", {})
    if not isinstance(args_payload, list):
        raise TaskRetryError(f"Task '{task_id}' has invalid args payload.")
    if not isinstance(kwargs_payload, dict):
        raise TaskRetryError(f"Task '{task_id}' has invalid kwargs payload.")

    return await _send_taskiq_message(
        registration,
        args=tuple(args_payload),
        kwargs=dict(kwargs_payload),
    )


def get_worker_broker(queue_name: str) -> RedisStreamBroker:
    return _get_or_create_queue_broker(queue_name)


def _build_taskiq_task_labels(
    *,
    time_limit_ms: int | float | None,
    max_retries: int | None,
) -> dict[str, Any]:
    labels: dict[str, Any] = {}
    timeout_seconds = _time_limit_to_timeout_seconds(time_limit_ms)
    if timeout_seconds is not None:
        labels["timeout"] = timeout_seconds
    if max_retries is not None:
        labels["retry_on_error"] = True
        labels["max_retries"] = max(max_retries, 0)
    return labels


def _create_queue_broker(queue_name: str) -> RedisStreamBroker:
    runtime_queue_name = _resolve_runtime_queue_name(queue_name)
    broker = RedisStreamBroker(
        url=settings.redis_url,
        queue_name=get_taskiq_queue_name(runtime_queue_name),
    )
    broker = broker.with_result_backend(
        RedisAsyncResultBackend(
            redis_url=settings.redis_url,
            result_ex_time=DEFAULT_RESULT_EXPIRY_SECONDS,
        )
    )
    broker = broker.with_middlewares(SimpleRetryMiddleware(default_retry_count=3))
    return broker


def _get_or_create_queue_broker(queue_name: str) -> RedisStreamBroker:
    runtime_queue_name = _resolve_runtime_queue_name(queue_name)
    broker = _QUEUE_BROKERS.get(runtime_queue_name)
    if broker is not None:
        return broker
    broker = _create_queue_broker(runtime_queue_name)
    _QUEUE_BROKERS[runtime_queue_name] = broker
    return broker


def _get_or_create_start_lock(broker: RedisStreamBroker) -> asyncio.Lock:
    lock = _BROKER_START_LOCKS.get(id(broker))
    if lock is not None:
        return lock
    lock = asyncio.Lock()
    _BROKER_START_LOCKS[id(broker)] = lock
    return lock


async def _ensure_client_broker_started(broker: RedisStreamBroker) -> None:
    broker_key = id(broker)
    if broker_key in _BROKER_CLIENT_STARTED:
        return
    lock = _get_or_create_start_lock(broker)
    async with lock:
        if broker_key in _BROKER_CLIENT_STARTED:
            return
        await broker.startup()
        _BROKER_CLIENT_STARTED.add(broker_key)


def _time_limit_to_timeout_seconds(time_limit_ms: int | float | None) -> int | None:
    if time_limit_ms is None:
        return None
    if isinstance(time_limit_ms, float) and math.isinf(time_limit_ms):
        return None
    if time_limit_ms <= 0:
        return None
    return max(int(math.ceil(time_limit_ms / 1000)), 1)


def _dispatch_or_run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    task = loop.create_task(coro)
    task.add_done_callback(_log_task_error)
    return task


def _log_task_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception as exc:
        logger.error("Background enqueue failed: %s", exc)


async def _send_taskiq_message(
    registration: ActorRegistration,
    *,
    args: tuple,
    kwargs: dict[str, Any],
    task_id: str | None = None,
) -> str:
    broker = _get_or_create_queue_broker(registration.queue_name)
    await _ensure_client_broker_started(broker)
    if task_id is None:
        task_id = uuid.uuid4().hex
    sanitized_kwargs = dict(kwargs)
    sanitized_kwargs.pop(INTERNAL_TASK_ID_KWARG, None)
    await _upsert_task_record(
        task_id,
        {
            "task_id": task_id,
            "actor_name": registration.name,
            "queue_name": registration.queue_name,
            "priority": registration.priority,
            "status": "queued",
            "args_preview": _build_args_preview(args),
            "kwargs_preview": _build_kwargs_preview(sanitized_kwargs),
            "args_payload": _build_args_payload(args),
            "kwargs_payload": _build_kwargs_payload(sanitized_kwargs),
            "max_retries": registration.max_retries,
            "time_limit_ms": registration.time_limit_ms,
        },
    )
    send_kwargs = dict(kwargs)
    send_kwargs[INTERNAL_TASK_ID_KWARG] = task_id
    try:
        await (
            registration.taskiq_task.kicker()
            .with_broker(broker)
            .with_task_id(task_id)
            .with_labels(queue_name=get_taskiq_queue_name(registration.queue_name))
            .kiq(*args, **send_kwargs)
        )
    except Exception as exc:
        await _upsert_task_record(
            task_id,
            {
                "status": "enqueue_failed",
                "finished_at": datetime.now(tz=UTC).isoformat(),
                "error_type": exc.__class__.__name__,
                "error_message": str(exc)[:500],
            },
        )
        raise
    return task_id


async def _enqueue_actor_job(
    registration: ActorRegistration,
    *,
    args: tuple,
    kwargs: dict[str, Any],
    delay_ms: int | None,
) -> str:
    if delay_ms is not None and delay_ms > 0:
        task_id = uuid.uuid4().hex
        sanitized_kwargs = dict(kwargs)
        sanitized_kwargs.pop(INTERNAL_TASK_ID_KWARG, None)
        await _upsert_task_record(
            task_id,
            {
                "task_id": task_id,
                "actor_name": registration.name,
                "queue_name": registration.queue_name,
                "priority": registration.priority,
                "status": "scheduled",
                "created_at": datetime.now(tz=UTC).isoformat(),
                "scheduled_for_ms": delay_ms,
                "args_preview": _build_args_preview(args),
                "kwargs_preview": _build_kwargs_preview(sanitized_kwargs),
                "args_payload": _build_args_payload(args),
                "kwargs_payload": _build_kwargs_payload(sanitized_kwargs),
                "max_retries": registration.max_retries,
                "time_limit_ms": registration.time_limit_ms,
            },
        )
        task = asyncio.create_task(
            _delayed_send(
                registration,
                args=args,
                kwargs=kwargs,
                delay_ms=delay_ms,
                task_id=task_id,
            )
        )
        task.add_done_callback(_log_task_error)
        return task_id
    return await _send_taskiq_message(registration, args=args, kwargs=kwargs)


async def _delayed_send(
    registration: ActorRegistration,
    *,
    args: tuple,
    kwargs: dict[str, Any],
    delay_ms: int,
    task_id: str,
) -> None:
    await asyncio.sleep(delay_ms / 1000)
    await _send_taskiq_message(registration, args=args, kwargs=kwargs, task_id=task_id)


def _build_args_preview(args: tuple) -> list[Any]:
    return [_safe_preview(value) for value in args]


def _build_kwargs_preview(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_preview(value) for key, value in kwargs.items()}


def _safe_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        safe_dict: dict[str, Any] = {}
        for key, nested_value in value.items():
            safe_dict[str(key)] = _safe_json_value(nested_value, depth=depth + 1)
        return safe_dict
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(item, depth=depth + 1) for item in value]
    return repr(value)


def _build_args_payload(args: tuple) -> list[Any]:
    return [_safe_json_value(value) for value in args]


def _build_kwargs_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_json_value(value) for key, value in kwargs.items()}


def _safe_preview(value: Any, *, depth: int = 0) -> Any:
    if depth > 2:
        return "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 240 else f"{value[:237]}..."
    if isinstance(value, dict):
        return {str(key): _safe_preview(val, depth=depth + 1) for key, val in list(value.items())[:20]}
    if isinstance(value, (list, tuple, set)):
        return [_safe_preview(item, depth=depth + 1) for item in list(value)[:20]]
    preview = repr(value)
    return preview if len(preview) <= 240 else f"{preview[:237]}..."


async def _upsert_task_record(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = get_task_details_key(task_id)
    existing_raw = await REDIS_ASYNC_CLIENT.get(key)
    if existing_raw:
        if isinstance(existing_raw, bytes):
            existing_raw = existing_raw.decode("utf-8", errors="ignore")
        try:
            record = json.loads(existing_raw)
        except (TypeError, ValueError):
            record = {}
    else:
        record = {}

    if not isinstance(record, dict):
        record = {}

    is_new_record = not bool(record)
    record.setdefault("task_id", task_id)
    record.update(payload)
    record.setdefault("created_at", datetime.now(tz=UTC).isoformat())
    record["updated_at"] = datetime.now(tz=UTC).isoformat()

    await REDIS_ASYNC_CLIENT.set(
        key,
        json.dumps(record, default=str),
        ex=TASK_DETAILS_TTL_SECONDS,
    )

    if is_new_record:
        await REDIS_ASYNC_CLIENT.lpush(TASK_RECENT_TASKS_KEY, task_id)
        await REDIS_ASYNC_CLIENT.ltrim(TASK_RECENT_TASKS_KEY, 0, TASK_RECENT_TASKS_MAX - 1)

    return record


@dataclass(slots=True)
class _TaskExecutionContext:
    task_key: str
    min_interval: timedelta
    set_cache_expiry: bool


@lru_cache(maxsize=128)
def _calculate_interval_from_crontab(crontab_expression: str) -> timedelta:
    cron_trigger = CronTrigger.from_crontab(crontab_expression)
    next_time = cron_trigger.get_next_fire_time(None, datetime.now(tz=cron_trigger.timezone))
    second_next_time = cron_trigger.get_next_fire_time(next_time, next_time)
    return second_next_time - next_time


def _build_task_key(task_name: str, args: tuple, kwargs: dict[str, Any]) -> str:
    if spider_name := kwargs.get("spider_name"):
        return f"background_tasks:run_spider:spider_name={spider_name}"

    args_str = "_".join(str(arg) for arg in args)
    kwargs_str = "_".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return f"background_tasks:{task_name}:{args_str}_{kwargs_str}".rstrip("_")


async def _prepare_task_execution(
    registration: ActorRegistration,
    args: tuple,
    kwargs: dict[str, Any],
) -> tuple[bool, _TaskExecutionContext | None]:
    kwargs_for_key = dict(kwargs)
    force_run = kwargs_for_key.pop("force_run", False)
    if force_run:
        logger.info("Force run enabled for task %s; skipping interval guard.", registration.name)
        return False, None

    min_interval = getattr(registration.fn, "_minimum_run_interval", None)
    set_cache_expiry = False
    if crontab_expression := kwargs_for_key.get("crontab_expression"):
        min_interval = _calculate_interval_from_crontab(crontab_expression)
        kwargs_for_key.pop("crontab_expression", None)
    elif min_interval:
        set_cache_expiry = True

    if not min_interval:
        return False, None

    task_key = _build_task_key(registration.name, args, kwargs_for_key)
    context = _TaskExecutionContext(
        task_key=task_key,
        min_interval=min_interval,
        set_cache_expiry=set_cache_expiry,
    )

    now = datetime.now()
    try:
        last_run_raw = await REDIS_ASYNC_CLIENT.get(task_key)
        if last_run_raw is not None:
            last_run = datetime.fromtimestamp(float(last_run_raw))
            difference = now - last_run
            required_interval = min_interval - timedelta(seconds=TASK_MANAGER_PROCESSING_TIME_BUFFER_SECONDS)
            if difference < required_interval:
                logger.warning(
                    "Discarding task %s with task_key %s. Last run %s ago, minimum interval %s",
                    registration.name,
                    task_key,
                    difference,
                    required_interval,
                )
                return True, None

        expiry = int(min_interval.total_seconds()) if set_cache_expiry else None
        await REDIS_ASYNC_CLIENT.set(task_key, now.timestamp(), ex=expiry)
    except Exception as exc:
        # Preserve previous behavior: allow execution if Redis check fails.
        logger.error("Redis operation failed for task %s: %s", task_key, exc)
        return False, None

    return False, context


async def _finalize_task_execution(
    context: _TaskExecutionContext | None,
    *,
    exception: Exception | None,
) -> None:
    if context is None:
        return

    try:
        if exception:
            await REDIS_ASYNC_CLIENT.delete(context.task_key)
            return

        expiry = int(context.min_interval.total_seconds()) if context.set_cache_expiry else None
        await REDIS_ASYNC_CLIENT.set(context.task_key, datetime.now().timestamp(), ex=expiry)
    except Exception as exc:
        logger.error("Redis finalization failed for task %s: %s", context.task_key, exc)


def _read_rss_bytes() -> int | None:
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


def _read_peak_rss_bytes() -> int | None:
    try:
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if max_rss <= 0:
            return None
        if sys.platform == "darwin":
            return int(max_rss)
        return int(max_rss) * 1024
    except Exception:
        return None


def _parse_int(value: bytes | str | None) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _persist_worker_memory_entry(entry: dict[str, Any]) -> None:
    history_size = max(settings.worker_memory_metrics_history_size, 100)
    entry_json = json.dumps(entry, default=str)
    REDIS_SYNC_CLIENT.lpush(WORKER_MEMORY_METRICS_HISTORY_KEY, entry_json)
    REDIS_SYNC_CLIENT.ltrim(WORKER_MEMORY_METRICS_HISTORY_KEY, 0, history_size - 1)

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
    REDIS_SYNC_CLIENT.hincrby(
        WORKER_MEMORY_METRICS_SUMMARY_KEY,
        f"status:{entry.get('status')}",
        1,
    )
    REDIS_SYNC_CLIENT.hincrby(
        WORKER_MEMORY_METRICS_SUMMARY_KEY,
        f"actor:{entry.get('actor_name', 'unknown')}",
        1,
    )

    if error_type := entry.get("error_type"):
        REDIS_SYNC_CLIENT.hincrby(WORKER_MEMORY_METRICS_SUMMARY_KEY, f"error:{error_type}", 1)

    existing_peak = _parse_int(REDIS_SYNC_CLIENT.hget(WORKER_MEMORY_METRICS_SUMMARY_KEY, "peak_rss_bytes"))
    measured_peak = max(
        entry.get("peak_rss_bytes") or 0,
        entry.get("rss_after_bytes") or 0,
    )
    if measured_peak > existing_peak:
        REDIS_SYNC_CLIENT.hset(WORKER_MEMORY_METRICS_SUMMARY_KEY, "peak_rss_bytes", measured_peak)


async def _execute_actor(
    registration: ActorRegistration,
    queue_name: str,
    ctx: dict[str, Any] | None,
    args: tuple,
    kwargs: dict[str, Any],
):
    task_id = kwargs.pop(INTERNAL_TASK_ID_KWARG, None)
    if task_id and is_task_cancel_requested(task_id):
        await _upsert_task_record(
            task_id,
            {
                "status": "cancelled",
                "started_at": datetime.now(tz=UTC).isoformat(),
                "finished_at": datetime.now(tz=UTC).isoformat(),
                "error_type": TaskCancelledError.__name__,
                "error_message": f"Task {task_id} was cancelled before execution.",
            },
        )
        raise TaskCancelledError(f"Task {task_id} was cancelled before execution.")
    task_id_token = _CURRENT_TASK_ID.set(task_id)
    if task_id:
        await REDIS_ASYNC_CLIENT.sadd(TASK_RUNNING_TASKS_KEY, task_id)
        await _upsert_task_record(
            task_id,
            {
                "status": "running",
                "started_at": datetime.now(tz=UTC).isoformat(),
                "worker_pid": os.getpid(),
            },
        )

    skip, task_context = await _prepare_task_execution(registration, args, kwargs)
    if skip:
        if task_id:
            await _upsert_task_record(
                task_id,
                {
                    "status": "skipped",
                    "finished_at": datetime.now(tz=UTC).isoformat(),
                },
            )
            await REDIS_ASYNC_CLIENT.srem(TASK_RUNNING_TASKS_KEY, task_id)
        _CURRENT_TASK_ID.reset(task_id_token)
        return None

    message_id = str(task_id or uuid.uuid4().hex)
    if isinstance(ctx, dict):
        message_id = str(ctx.get("job_id") or message_id)

    started_at = time.time()
    rss_before = _read_rss_bytes() or _read_peak_rss_bytes()
    exception: Exception | None = None

    try:
        if asyncio.iscoroutinefunction(registration.fn):
            return await registration.fn(*args, **kwargs)
        return await asyncio.to_thread(registration.fn, *args, **kwargs)
    except Exception as exc:
        exception = exc
        raise
    finally:
        _CURRENT_TASK_ID.reset(task_id_token)
        await _finalize_task_execution(task_context, exception=exception)
        if task_id:
            status = "success"
            error_message = None
            error_type = None
            if exception:
                status = "cancelled" if isinstance(exception, TaskCancelledError) else "error"
                error_type = exception.__class__.__name__
                error_message = str(exception)[:500]
            await _upsert_task_record(
                task_id,
                {
                    "status": status,
                    "finished_at": datetime.now(tz=UTC).isoformat(),
                    "duration_ms": round((time.time() - started_at) * 1000, 2),
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            await REDIS_ASYNC_CLIENT.srem(TASK_RUNNING_TASKS_KEY, task_id)
            try:
                await REDIS_ASYNC_CLIENT.delete(get_task_cancellation_key(task_id))
            except Exception:
                logger.debug("Failed to clear cancellation marker for task %s", task_id)

        if settings.enable_worker_memory_metrics and settings.worker_memory_metrics_history_size > 0:
            completed_at = time.time()
            rss_after = _read_rss_bytes() or _read_peak_rss_bytes()
            peak_after = _read_peak_rss_bytes()
            rss_delta = None
            if rss_before is not None and rss_after is not None:
                rss_delta = rss_after - rss_before

            entry = {
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "message_id": message_id,
                "actor_name": registration.name,
                "queue_name": queue_name,
                "status": "error" if exception else "success",
                "duration_ms": round((completed_at - started_at) * 1000, 2),
                "pid": os.getpid(),
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": rss_delta,
                "peak_rss_bytes": peak_after,
                "error_type": exception.__class__.__name__ if exception else None,
            }
            try:
                _persist_worker_memory_entry(entry)
            except Exception as telemetry_exc:
                logger.error("Failed to persist worker memory telemetry: %s", telemetry_exc)
