"""Admin API endpoints for exception tracking.

Provides read and management access to server exceptions stored in Redis.
Requires the ENABLE_EXCEPTION_TRACKING setting to be True.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.routers.user.auth import require_role
from db.config import settings
from db.enums import UserRole
from db.models import User
from utils.exception_tracker import (
    clear_exceptions,
    get_exception_detail,
    list_exceptions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Exception Tracking"])


# ============================================
# Pydantic Schemas
# ============================================


class ExceptionSummary(BaseModel):
    """Summary of a tracked exception (list view)."""

    fingerprint: str
    type: str
    message: str
    count: int
    first_seen: str
    last_seen: str
    source: str = ""


class ExceptionDetail(BaseModel):
    """Full detail of a tracked exception including traceback."""

    fingerprint: str
    type: str
    message: str
    traceback: str
    count: int
    first_seen: str
    last_seen: str
    source: str = ""


class ExceptionListResponse(BaseModel):
    """Paginated list of tracked exceptions."""

    items: list[ExceptionSummary]
    total: int
    page: int
    per_page: int
    pages: int


class ExceptionStatusResponse(BaseModel):
    """Status of the exception tracking feature."""

    enabled: bool
    ttl_seconds: int
    max_entries: int
    total_tracked: int


class ExceptionClearResponse(BaseModel):
    """Response after clearing exceptions."""

    cleared: int
    message: str


# ============================================
# Helper
# ============================================


def _check_tracking_enabled() -> None:
    """Raise 404 if exception tracking is disabled."""
    if not settings.enable_exception_tracking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exception tracking is not enabled. Set ENABLE_EXCEPTION_TRACKING=true to enable.",
        )


# ============================================
# Endpoints
# ============================================


@router.get("/exceptions/status", response_model=ExceptionStatusResponse)
async def get_exception_tracking_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Check whether exception tracking is enabled and get current stats."""
    total = 0
    if settings.enable_exception_tracking:
        result = await list_exceptions(page=1, per_page=1)
        total = result["total"]

    return ExceptionStatusResponse(
        enabled=settings.enable_exception_tracking,
        ttl_seconds=settings.exception_tracking_ttl,
        max_entries=settings.exception_tracking_max_entries,
        total_tracked=total,
    )


@router.get("/exceptions", response_model=ExceptionListResponse)
async def list_tracked_exceptions(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    exception_type: str | None = Query(None, description="Filter by exception type name"),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List tracked exceptions, most recent first (Admin only).

    Returns a paginated summary of all captured server exceptions.
    """
    _check_tracking_enabled()

    result = await list_exceptions(
        page=page,
        per_page=per_page,
        exception_type=exception_type,
    )

    return ExceptionListResponse(
        items=[ExceptionSummary(**item) for item in result["items"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        pages=result["pages"],
    )


@router.get("/exceptions/{fingerprint}", response_model=ExceptionDetail)
async def get_tracked_exception(
    fingerprint: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get full detail of a tracked exception including the traceback (Admin only)."""
    _check_tracking_enabled()

    detail = await get_exception_detail(fingerprint)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exception not found. It may have expired.",
        )

    return ExceptionDetail(**detail)


@router.delete("/exceptions", response_model=ExceptionClearResponse)
async def clear_all_exceptions(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Clear all tracked exceptions (Admin only)."""
    _check_tracking_enabled()

    count = await clear_exceptions()
    return ExceptionClearResponse(
        cleared=count,
        message=f"Cleared {count} tracked exception(s).",
    )


@router.delete("/exceptions/{fingerprint}", response_model=ExceptionClearResponse)
async def clear_single_exception(
    fingerprint: str,
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Clear a specific tracked exception by fingerprint (Admin only)."""
    _check_tracking_enabled()

    count = await clear_exceptions(fingerprint=fingerprint)
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exception not found. It may have already expired.",
        )

    return ExceptionClearResponse(
        cleared=count,
        message="Exception cleared successfully.",
    )
