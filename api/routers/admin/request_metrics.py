"""Admin API endpoints for request metrics tracking.

Provides read and management access to API request timing and usage
metrics stored in Redis. Requires the ENABLE_REQUEST_METRICS setting
to be True.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.routers.user.auth import require_role
from db.config import settings
from db.enums import UserRole
from db.models import User
from utils.request_tracker import (
    clear_metrics,
    get_endpoint_detail,
    get_endpoint_stats,
    get_recent_requests,
    get_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Request Metrics"])


# ============================================
# Pydantic Schemas
# ============================================


class EndpointStatsSummary(BaseModel):
    """Summary of aggregated stats for a single endpoint."""

    endpoint_key: str
    method: str
    route: str
    total_requests: int
    avg_time: float
    min_time: float
    max_time: float
    error_count: int
    status_2xx: int = 0
    status_3xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    unique_visitors: int = 0
    last_seen: str


class EndpointStatsDetail(EndpointStatsSummary):
    """Detailed stats for a single endpoint including percentiles."""

    p50: float
    p95: float
    p99: float


class EndpointStatsListResponse(BaseModel):
    """Paginated list of endpoint stats."""

    items: list[EndpointStatsSummary]
    total: int
    page: int
    per_page: int
    pages: int


class RecentRequestItem(BaseModel):
    """A single recent individual request."""

    request_id: str
    method: str
    path: str
    route_template: str
    status_code: int
    process_time: float
    timestamp: str


class RecentRequestsListResponse(BaseModel):
    """Paginated list of recent individual requests."""

    items: list[RecentRequestItem]
    total: int
    page: int
    per_page: int
    pages: int


class RequestMetricsStatusResponse(BaseModel):
    """Status of the request metrics tracking feature."""

    enabled: bool
    ttl_seconds: int
    recent_ttl_seconds: int
    max_recent: int
    total_endpoints: int
    total_requests: int
    total_recent: int
    unique_visitors: int = 0


class RequestMetricsClearResponse(BaseModel):
    """Response after clearing request metrics."""

    cleared: int
    message: str


# ============================================
# Helper
# ============================================


def _check_metrics_enabled() -> None:
    """Raise 404 if request metrics tracking is disabled."""
    if not settings.enable_request_metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request metrics tracking is not enabled. Set ENABLE_REQUEST_METRICS=true to enable.",
        )


# ============================================
# Endpoints
# ============================================


@router.get("/request-metrics/status", response_model=RequestMetricsStatusResponse)
async def get_request_metrics_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Check whether request metrics tracking is enabled and get current stats."""
    result = await get_status()
    return RequestMetricsStatusResponse(**result)


@router.get("/request-metrics/endpoints", response_model=EndpointStatsListResponse)
async def list_endpoint_stats(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort_by: str = Query("total_requests", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List aggregated stats for all tracked endpoints (Admin only).

    Returns a paginated list of per-endpoint statistics including
    request counts, average response time, and error rates.
    """
    _check_metrics_enabled()

    result = await get_endpoint_stats(
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return EndpointStatsListResponse(
        items=[EndpointStatsSummary(**item) for item in result["items"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        pages=result["pages"],
    )


@router.get(
    "/request-metrics/endpoints/{method}/{route:path}",
    response_model=EndpointStatsDetail,
)
async def get_endpoint_stats_detail(
    method: str,
    route: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get detailed stats for a specific endpoint including percentiles (Admin only)."""
    _check_metrics_enabled()

    # Ensure route starts with /
    if not route.startswith("/"):
        route = f"/{route}"

    detail = await get_endpoint_detail(method=method.upper(), route=route)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint metrics not found. It may have expired.",
        )

    return EndpointStatsDetail(**detail)


@router.get("/request-metrics/recent", response_model=RecentRequestsListResponse)
async def list_recent_requests(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    method: str | None = Query(None, description="Filter by HTTP method"),
    status_code: int | None = Query(None, description="Filter by status code"),
    route: str | None = Query(None, description="Filter by route pattern (substring match)"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List recent individual requests, most recent first (Admin only).

    Returns a paginated list of recently recorded individual API requests
    with timing details.
    """
    _check_metrics_enabled()

    result = await get_recent_requests(
        page=page,
        per_page=per_page,
        method_filter=method.upper() if method else None,
        status_filter=status_code,
        route_filter=route,
    )

    return RecentRequestsListResponse(
        items=[RecentRequestItem(**item) for item in result["items"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        pages=result["pages"],
    )


@router.delete("/request-metrics", response_model=RequestMetricsClearResponse)
async def clear_all_request_metrics(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Clear all request metrics (Admin only)."""
    _check_metrics_enabled()

    count = await clear_metrics()
    return RequestMetricsClearResponse(
        cleared=count,
        message=f"Cleared {count} request metrics key(s).",
    )
