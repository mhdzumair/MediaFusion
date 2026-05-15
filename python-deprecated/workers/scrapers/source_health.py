from __future__ import annotations

import os
import re
from dataclasses import dataclass

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT

METRICS_KEY_PREFIX = "public_indexer_source_health:"
METRICS_TTL_SECONDS = settings.public_indexers_source_health_metrics_ttl_seconds
DEFAULT_HEALTH_BUCKET = "general"


@dataclass(frozen=True)
class SourceHealthScope:
    mode: str
    scope_key: str | None


@dataclass(frozen=True)
class SourceHealthSnapshot:
    source_key: str
    total: int
    success: int
    timeout: int
    challenge_solved: int
    consecutive_success: int

    @property
    def success_rate(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.success / self.total

    @property
    def timeout_rate(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.timeout / self.total

    @property
    def challenge_solve_rate(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.challenge_solved / self.total


def _metrics_key(source_key: str, health_bucket: str = DEFAULT_HEALTH_BUCKET) -> str:
    normalized_source_key = (source_key or "").strip().lower()
    normalized_bucket = _sanitize_scope_component(health_bucket) or DEFAULT_HEALTH_BUCKET
    scope = _resolve_scope()
    if not scope.scope_key:
        return f"{METRICS_KEY_PREFIX}{normalized_bucket}:{normalized_source_key}"
    return f"{METRICS_KEY_PREFIX}{scope.scope_key}:{normalized_bucket}:{normalized_source_key}"


def _sanitize_scope_component(raw_value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(raw_value or "").strip().lower()).strip("-")
    return normalized


def _resolve_scope() -> SourceHealthScope:
    mode = (settings.public_indexers_source_health_scope_mode or "global").strip().lower()
    if mode not in {"global", "pod", "custom"}:
        mode = "global"
    if mode == "global":
        return SourceHealthScope(mode=mode, scope_key=None)

    raw_scope = ""
    if mode == "pod":
        raw_scope = (
            os.getenv("PUBLIC_INDEXERS_SOURCE_HEALTH_SCOPE") or os.getenv("POD_NAME") or os.getenv("HOSTNAME") or ""
        )
    elif mode == "custom":
        raw_scope = settings.public_indexers_source_health_scope

    sanitized_scope = _sanitize_scope_component(raw_scope)
    if not sanitized_scope:
        return SourceHealthScope(mode=mode, scope_key="default")
    return SourceHealthScope(mode=mode, scope_key=sanitized_scope)


def get_source_health_scope() -> SourceHealthScope:
    return _resolve_scope()


def _parse_counter(raw_value) -> int:
    if raw_value is None:
        return 0
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8", errors="ignore")
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def _get_counter(raw: dict, field: str) -> int:
    if not raw:
        return 0
    return _parse_counter(raw.get(field) or raw.get(field.encode("utf-8")))


async def _decay_source_counters(key: str) -> None:
    raw = await REDIS_ASYNC_CLIENT.hgetall(key)
    if not raw:
        return

    current_total = _get_counter(raw, "total")
    if current_total <= 0:
        return

    decay_factor = settings.public_indexers_source_health_decay_factor
    decayed_total = max(1, int(current_total * decay_factor))
    decayed_success = min(decayed_total, max(0, int(_get_counter(raw, "success") * decay_factor)))
    decayed_timeout = min(decayed_total, max(0, int(_get_counter(raw, "timeout") * decay_factor)))
    decayed_challenge = min(decayed_total, max(0, int(_get_counter(raw, "challenge_solved") * decay_factor)))
    current_streak = _get_counter(raw, "consecutive_success")
    decayed_streak = min(max(0, current_streak), decayed_total)

    await REDIS_ASYNC_CLIENT.hset(
        key,
        mapping={
            "total": decayed_total,
            "success": decayed_success,
            "timeout": decayed_timeout,
            "challenge_solved": decayed_challenge,
            "consecutive_success": decayed_streak,
        },
    )


async def record_source_outcome(
    source_key: str,
    *,
    success: bool,
    timed_out: bool = False,
    challenge_solved: bool = False,
    health_bucket: str = DEFAULT_HEALTH_BUCKET,
) -> None:
    key = _metrics_key(source_key, health_bucket)
    total = await REDIS_ASYNC_CLIENT.hincrby(key, "total", 1)
    if success:
        await REDIS_ASYNC_CLIENT.hincrby(key, "success", 1)
    if timed_out:
        await REDIS_ASYNC_CLIENT.hincrby(key, "timeout", 1)
    if challenge_solved:
        await REDIS_ASYNC_CLIENT.hincrby(key, "challenge_solved", 1)

    clean_success = success and not timed_out
    if clean_success:
        await REDIS_ASYNC_CLIENT.hincrby(key, "consecutive_success", 1)
    else:
        await REDIS_ASYNC_CLIENT.hset(key, "consecutive_success", 0)

    if total >= settings.public_indexers_source_health_counter_soft_cap:
        await _decay_source_counters(key)

    await REDIS_ASYNC_CLIENT.expire(key, METRICS_TTL_SECONDS)


async def get_source_health(
    source_key: str,
    *,
    health_bucket: str = DEFAULT_HEALTH_BUCKET,
) -> SourceHealthSnapshot:
    key = _metrics_key(source_key, health_bucket)
    raw = await REDIS_ASYNC_CLIENT.hgetall(key)
    if not raw:
        return SourceHealthSnapshot(
            source_key=source_key,
            total=0,
            success=0,
            timeout=0,
            challenge_solved=0,
            consecutive_success=0,
        )
    return SourceHealthSnapshot(
        source_key=source_key,
        total=_get_counter(raw, "total"),
        success=_get_counter(raw, "success"),
        timeout=_get_counter(raw, "timeout"),
        challenge_solved=_get_counter(raw, "challenge_solved"),
        consecutive_success=_get_counter(raw, "consecutive_success"),
    )


async def is_source_within_budget(
    source_key: str,
    *,
    min_samples: int,
    min_success_rate: float,
    max_timeout_rate: float,
    health_bucket: str = DEFAULT_HEALTH_BUCKET,
) -> bool:
    snapshot = await get_source_health(source_key, health_bucket=health_bucket)
    if snapshot.total < max(1, min_samples):
        return True
    return snapshot.success_rate >= min_success_rate and snapshot.timeout_rate <= max_timeout_rate
