"""
Contributions API endpoints for user-submitted metadata corrections and stream additions.
"""

from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from db.database import get_async_session, get_read_session
from db.enums import ContributionStatus, UserRole
from db.models import Contribution, StreamSuggestion, User

router = APIRouter(prefix="/api/v1/contributions", tags=["Contributions"])

CONTRIBUTION_TYPES = ["metadata", "stream", "torrent", "telegram", "youtube", "nzb", "http", "acestream"]
_CONTRIBUTION_TYPE_PATTERN = "^(" + "|".join(CONTRIBUTION_TYPES) + ")$"


# ============================================
# Pydantic Schemas
# ============================================


class ContributionCreate(BaseModel):
    """Request schema for creating a contribution."""

    contribution_type: str = Field(..., pattern=_CONTRIBUTION_TYPE_PATTERN)
    target_id: str | None = None  # meta_id or stream_id
    data: dict[str, Any]  # The contribution data


class ContributionReview(BaseModel):
    """Request schema for reviewing a contribution."""

    status: ContributionStatus = Field(..., description="APPROVED or REJECTED")
    review_notes: str | None = None


class ContributionResponse(BaseModel):
    """Response schema for a contribution."""

    id: str  # UUID string
    user_id: int
    contribution_type: str
    target_id: str | None  # External ID (IMDb, TMDB)
    data: dict[str, Any]
    status: str
    reviewed_by: str | None = None  # User ID string
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class ContributionListResponse(BaseModel):
    """Response schema for paginated contribution list."""

    items: list[ContributionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ContributionStats(BaseModel):
    """Contribution statistics response."""

    total_contributions: int
    pending: int
    approved: int
    rejected: int
    by_type: dict[str, int]


# ============================================
# Helper Functions
# ============================================


def contribution_to_response(contribution: Contribution) -> ContributionResponse:
    """Convert a Contribution model to response schema."""
    return ContributionResponse(
        id=contribution.id,
        user_id=contribution.user_id,
        contribution_type=contribution.contribution_type,
        target_id=contribution.target_id,
        data=contribution.data,
        status=contribution.status.value if hasattr(contribution.status, "value") else str(contribution.status),
        reviewed_by=contribution.reviewed_by,
        reviewed_at=contribution.reviewed_at,
        review_notes=contribution.review_notes,
        created_at=contribution.created_at,
        updated_at=contribution.updated_at,
    )


# ============================================
# API Endpoints - User
# ============================================


@router.get("", response_model=ContributionListResponse)
async def list_contributions(
    contribution_type: str | None = Query(None, pattern=_CONTRIBUTION_TYPE_PATTERN),
    contribution_status: ContributionStatus | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    List user's contributions with pagination.
    Regular users see only their own contributions.
    Moderators and admins can see all contributions.
    """
    # Build base query - moderators+ see all, others see only their own
    is_mod_or_admin = user.role in [UserRole.MODERATOR, UserRole.ADMIN]

    query = select(Contribution)
    count_query = select(func.count(Contribution.id))

    if not is_mod_or_admin:
        query = query.where(Contribution.user_id == user.id)
        count_query = count_query.where(Contribution.user_id == user.id)

    if contribution_type:
        query = query.where(Contribution.contribution_type == contribution_type)
        count_query = count_query.where(Contribution.contribution_type == contribution_type)

    if contribution_status:
        query = query.where(Contribution.status == contribution_status)
        count_query = count_query.where(Contribution.status == contribution_status)

    # Get total count
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(col(Contribution.created_at).desc()).offset(offset).limit(page_size)
    result = await session.exec(query)
    items = result.all()

    return ContributionListResponse(
        items=[contribution_to_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.get("/stats", response_model=ContributionStats)
async def get_contribution_stats(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get contribution statistics for the current user (including stream suggestions)."""

    # ====== Contribution model counts ======
    contrib_total_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.user_id == user.id)
    )
    contrib_total = contrib_total_result.one()

    contrib_pending_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.PENDING,
        )
    )
    contrib_pending = contrib_pending_result.one()

    contrib_approved_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.APPROVED,
        )
    )
    contrib_approved = contrib_approved_result.one()

    contrib_rejected_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.REJECTED,
        )
    )
    contrib_rejected = contrib_rejected_result.one()

    # ====== StreamSuggestion model counts ======
    stream_total_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.user_id == user.id)
    )
    stream_total = stream_total_result.one()

    stream_pending_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status == "pending",
        )
    )
    stream_pending = stream_pending_result.one()

    stream_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status.in_(["approved", "auto_approved"]),
        )
    )
    stream_approved = stream_approved_result.one()

    stream_rejected_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status == "rejected",
        )
    )
    stream_rejected = stream_rejected_result.one()

    # ====== By type ======
    by_type = {}
    for ctype in CONTRIBUTION_TYPES:
        type_result = await session.exec(
            select(func.count(Contribution.id)).where(
                Contribution.user_id == user.id,
                Contribution.contribution_type == ctype,
            )
        )
        by_type[ctype] = type_result.one()

    by_type["stream_suggestions"] = stream_total

    # ====== Combined totals ======
    return ContributionStats(
        total_contributions=contrib_total + stream_total,
        pending=contrib_pending + stream_pending,
        approved=contrib_approved + stream_approved,
        rejected=contrib_rejected + stream_rejected,
        by_type=by_type,
    )


@router.get("/{contribution_id}", response_model=ContributionResponse)
async def get_contribution(
    contribution_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get a specific contribution by ID."""
    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    # Only owner or moderators+ can view
    is_mod_or_admin = user.role in [UserRole.MODERATOR, UserRole.ADMIN]
    if contribution.user_id != user.id and not is_mod_or_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this contribution",
        )

    return contribution_to_response(contribution)


@router.post("", response_model=ContributionResponse, status_code=status.HTTP_201_CREATED)
async def create_contribution(
    data: ContributionCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Submit a new contribution.

    Torrent and stream imports are auto-approved if the user is active.
    Metadata contributions require manual review.
    """
    # Auto-approve torrent/stream imports for active users
    # Metadata contributions still require review
    should_auto_approve = user.is_active and data.contribution_type in (
        "torrent",
        "stream",
    )

    initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

    contribution = Contribution(
        user_id=user.id,
        contribution_type=data.contribution_type,
        target_id=data.target_id,
        data=data.data,
        status=initial_status,
        # Mark auto-approved contributions
        reviewed_by="auto" if should_auto_approve else None,
        reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
        review_notes="Auto-approved: Active user content import" if should_auto_approve else None,
    )

    session.add(contribution)
    await session.commit()
    await session.refresh(contribution)

    return contribution_to_response(contribution)


@router.delete("/{contribution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contribution(
    contribution_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a contribution.
    Users can only delete their own pending contributions.
    Admins can delete any contribution.
    """
    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    is_admin = user.role == UserRole.ADMIN
    is_owner = contribution.user_id == user.id
    is_pending = contribution.status == ContributionStatus.PENDING

    if not is_admin and not (is_owner and is_pending):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete this contribution. Only pending contributions can be deleted by their owner.",
        )

    await session.delete(contribution)
    await session.commit()


# ============================================
# API Endpoints - Moderator/Admin
# ============================================


@router.get("/review/pending", response_model=ContributionListResponse)
async def list_pending_contributions(
    contribution_type: str | None = Query(None, pattern=_CONTRIBUTION_TYPE_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_read_session),
):
    """List all pending contributions for review. Moderator+ only."""
    query = select(Contribution).where(Contribution.status == ContributionStatus.PENDING)
    count_query = select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.PENDING)

    if contribution_type:
        query = query.where(Contribution.contribution_type == contribution_type)
        count_query = count_query.where(Contribution.contribution_type == contribution_type)

    # Get total count
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(col(Contribution.created_at).asc()).offset(offset).limit(page_size)
    result = await session.exec(query)
    items = result.all()

    return ContributionListResponse(
        items=[contribution_to_response(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.patch("/{contribution_id}/review", response_model=ContributionResponse)
async def review_contribution(
    contribution_id: str,
    review: ContributionReview,
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Review a contribution (approve or reject). Moderator+ only.

    If approved, torrent contributions are processed and imported into the database.
    """
    import logging

    logger = logging.getLogger(__name__)

    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    if contribution.status != ContributionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Contribution already reviewed with status: {contribution.status}",
        )

    contribution.status = review.status
    contribution.reviewed_by = str(user.id)
    contribution.reviewed_at = datetime.now(pytz.UTC)
    contribution.review_notes = review.review_notes

    # If approved and it's a torrent contribution, process the import
    if review.status == ContributionStatus.APPROVED and contribution.contribution_type == "torrent":
        try:
            from api.routers.content.torrent_import import process_torrent_import

            # Get the original contributor user
            contributor = await session.get(User, contribution.user_id)
            if contributor:
                import_result = await process_torrent_import(session, contribution.data, contributor)

                if import_result.get("status") == "success":
                    contribution.review_notes = (
                        f"{review.review_notes or ''}\nImport successful: stream_id={import_result.get('stream_id')}"
                    ).strip()
                elif import_result.get("status") == "exists":
                    contribution.review_notes = (
                        f"{review.review_notes or ''}\nTorrent already exists in database"
                    ).strip()

        except Exception as e:
            logger.exception(f"Failed to process torrent import on approval: {e}")
            contribution.review_notes = (f"{review.review_notes or ''}\nImport processing failed: {str(e)}").strip()

    session.add(contribution)
    await session.commit()
    await session.refresh(contribution)

    return contribution_to_response(contribution)


@router.get("/review/stats", response_model=ContributionStats)
async def get_all_contribution_stats(
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get overall contribution statistics (including stream suggestions). Moderator+ only."""

    # ====== Contribution model counts ======
    contrib_total_result = await session.exec(select(func.count(Contribution.id)))
    contrib_total = contrib_total_result.one()

    contrib_pending_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.PENDING)
    )
    contrib_pending = contrib_pending_result.one()

    contrib_approved_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.APPROVED)
    )
    contrib_approved = contrib_approved_result.one()

    contrib_rejected_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.REJECTED)
    )
    contrib_rejected = contrib_rejected_result.one()

    # ====== StreamSuggestion model counts ======
    stream_total_result = await session.exec(select(func.count(StreamSuggestion.id)))
    stream_total = stream_total_result.one()

    stream_pending_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == "pending")
    )
    stream_pending = stream_pending_result.one()

    stream_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status.in_(["approved", "auto_approved"]))
    )
    stream_approved = stream_approved_result.one()

    stream_rejected_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == "rejected")
    )
    stream_rejected = stream_rejected_result.one()

    # ====== By type ======
    by_type = {}
    for ctype in CONTRIBUTION_TYPES:
        type_result = await session.exec(
            select(func.count(Contribution.id)).where(Contribution.contribution_type == ctype)
        )
        by_type[ctype] = type_result.one()

    by_type["stream_suggestions"] = stream_total

    # ====== Combined totals ======
    return ContributionStats(
        total_contributions=contrib_total + stream_total,
        pending=contrib_pending + stream_pending,
        approved=contrib_approved + stream_approved,
        rejected=contrib_rejected + stream_rejected,
        by_type=by_type,
    )
