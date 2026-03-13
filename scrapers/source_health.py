from __future__ import annotations

from dataclasses import dataclass

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT

METRICS_KEY_PREFIX = "public_indexer_source_health:"
METRICS_TTL_SECONDS = settings.public_indexers_source_health_metrics_ttl_seconds


@dataclass(frozen=True)
class SourceHealthSnapshot:
    source_key: str
    total: int
    success: int
    timeout: int
    challenge_solved: int

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


def _metrics_key(source_key: str) -> str:
    return f"{METRICS_KEY_PREFIX}{(source_key or '').strip().lower()}"


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


async def record_source_outcome(
    source_key: str,
    *,
    success: bool,
    timed_out: bool = False,
    challenge_solved: bool = False,
) -> None:
    key = _metrics_key(source_key)
    await REDIS_ASYNC_CLIENT.hincrby(key, "total", 1)
    if success:
        await REDIS_ASYNC_CLIENT.hincrby(key, "success", 1)
    if timed_out:
        await REDIS_ASYNC_CLIENT.hincrby(key, "timeout", 1)
    if challenge_solved:
        await REDIS_ASYNC_CLIENT.hincrby(key, "challenge_solved", 1)
    await REDIS_ASYNC_CLIENT.expire(key, METRICS_TTL_SECONDS)


async def get_source_health(source_key: str) -> SourceHealthSnapshot:
    key = _metrics_key(source_key)
    raw = await REDIS_ASYNC_CLIENT.hgetall(key)
    if not raw:
        return SourceHealthSnapshot(
            source_key=source_key,
            total=0,
            success=0,
            timeout=0,
            challenge_solved=0,
        )
    return SourceHealthSnapshot(
        source_key=source_key,
        total=_get_counter(raw, "total"),
        success=_get_counter(raw, "success"),
        timeout=_get_counter(raw, "timeout"),
        challenge_solved=_get_counter(raw, "challenge_solved"),
    )


async def is_source_within_budget(
    source_key: str,
    *,
    min_samples: int,
    min_success_rate: float,
    max_timeout_rate: float,
) -> bool:
    snapshot = await get_source_health(source_key)
    if snapshot.total < max(1, min_samples):
        return True
    return snapshot.success_rate >= min_success_rate and snapshot.timeout_rate <= max_timeout_rate
