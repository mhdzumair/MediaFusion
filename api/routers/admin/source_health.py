"""Admin API endpoints for public indexer source health and gate status."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.routers.user.auth import require_role
from db.config import settings
from db.enums import UserRole
from db.models import User
from scrapers.public_indexer_registry import PUBLIC_INDEXER_DEFINITIONS
from scrapers.source_health import get_source_health, get_source_health_scope

router = APIRouter(prefix="/api/v1/admin", tags=["Admin - Source Health"])


class SourceHealthGateConfig(BaseModel):
    enabled: bool
    scope_mode: str
    scope_key: str | None
    min_samples: int
    min_success_rate: float
    max_timeout_rate: float


class SourceHealthItem(BaseModel):
    source_key: str
    source_name: str
    supports_movie: bool
    supports_series: bool
    supports_anime: bool
    samples: int
    success: int
    timeout: int
    challenge_solved: int
    consecutive_success: int
    success_rate: float
    timeout_rate: float
    challenge_solve_rate: float
    gate_status: Literal["allowed", "blocked", "warming"]
    gate_enforced_now: bool
    recovery_admitted: bool


class SourceHealthResponse(BaseModel):
    gate: SourceHealthGateConfig
    total_sources: int
    allowed: int
    blocked: int
    warming: int
    sources: list[SourceHealthItem]


def _classify_gate_status(
    *,
    samples: int,
    success_rate: float,
    timeout_rate: float,
    min_samples: int,
    min_success_rate: float,
    max_timeout_rate: float,
) -> Literal["allowed", "blocked", "warming"]:
    if samples < max(1, min_samples):
        return "warming"
    if success_rate >= min_success_rate and timeout_rate <= max_timeout_rate:
        return "allowed"
    return "blocked"


@router.get("/public-indexers/source-health", response_model=SourceHealthResponse)
async def get_public_indexer_source_health(
    anime_only: bool = Query(False, description="Only return anime-capable indexers."),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    scope = get_source_health_scope()
    gate = SourceHealthGateConfig(
        enabled=settings.public_indexers_source_health_gates_enabled,
        scope_mode=scope.mode,
        scope_key=scope.scope_key,
        min_samples=settings.public_indexers_source_health_min_samples,
        min_success_rate=settings.public_indexers_source_min_success_rate,
        max_timeout_rate=settings.public_indexers_source_max_timeout_rate,
    )

    definitions = list(PUBLIC_INDEXER_DEFINITIONS.values())
    if anime_only:
        definitions = [definition for definition in definitions if definition.supports_anime]

    items: list[SourceHealthItem] = []
    for definition in definitions:
        snapshot = await get_source_health(definition.key)
        status = _classify_gate_status(
            samples=snapshot.total,
            success_rate=snapshot.success_rate,
            timeout_rate=snapshot.timeout_rate,
            min_samples=gate.min_samples,
            min_success_rate=gate.min_success_rate,
            max_timeout_rate=gate.max_timeout_rate,
        )
        recovery_admitted = (
            status == "blocked"
            and snapshot.consecutive_success >= max(0, settings.public_indexers_source_health_recovery_success_streak)
            and settings.public_indexers_source_health_recovery_success_streak > 0
        )
        items.append(
            SourceHealthItem(
                source_key=definition.key,
                source_name=definition.source_name,
                supports_movie=definition.supports_movie,
                supports_series=definition.supports_series,
                supports_anime=definition.supports_anime,
                samples=snapshot.total,
                success=snapshot.success,
                timeout=snapshot.timeout,
                challenge_solved=snapshot.challenge_solved,
                consecutive_success=snapshot.consecutive_success,
                success_rate=round(snapshot.success_rate, 4),
                timeout_rate=round(snapshot.timeout_rate, 4),
                challenge_solve_rate=round(snapshot.challenge_solve_rate, 4),
                gate_status=status,
                gate_enforced_now=bool(gate.enabled and status == "blocked" and not recovery_admitted),
                recovery_admitted=recovery_admitted,
            )
        )

    status_rank = {"allowed": 0, "warming": 1, "blocked": 2}
    items.sort(
        key=lambda item: (
            status_rank[item.gate_status],
            -item.success_rate,
            item.timeout_rate,
            -item.samples,
            item.source_key,
        )
    )

    allowed = sum(1 for item in items if item.gate_status == "allowed")
    blocked = sum(1 for item in items if item.gate_status == "blocked")
    warming = sum(1 for item in items if item.gate_status == "warming")

    return SourceHealthResponse(
        gate=gate,
        total_sources=len(items),
        allowed=allowed,
        blocked=blocked,
        warming=warming,
        sources=items,
    )
