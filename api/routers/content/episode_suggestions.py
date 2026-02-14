"""
Episode Suggestions API endpoints for episode correction suggestions.
Includes auto-approval for trusted users and points-based reputation system.
"""

import logging
from datetime import date, datetime
from typing import Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth, require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import ContributionSettings, Episode, EpisodeSuggestion, Season, User

logger = logging.getLogger(__name__)

# Suggestion status constants (stored as VARCHAR in DB)
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_APPROVED = "auto_approved"


router = APIRouter(prefix="/api/v1", tags=["Episode Suggestions"])


# ============================================
# Constants
# ============================================

# Editable episode fields
EPISODE_EDITABLE_FIELDS = ["title", "overview", "air_date", "runtime_minutes"]

EpisodeEditableFieldLiteral = Literal["title", "overview", "air_date", "runtime_minutes"]


# ============================================
# Pydantic Schemas
# ============================================


class EpisodeSuggestionCreateRequest(BaseModel):
    """Request to create an episode suggestion"""

    field_name: EpisodeEditableFieldLiteral
    current_value: str | None = None
    suggested_value: str = Field(..., min_length=1, max_length=10000)
    reason: str | None = Field(None, max_length=1000)


class EpisodeSuggestionReviewRequest(BaseModel):
    """Request to review an episode suggestion (moderator only)"""

    action: Literal["approve", "reject"]
    review_notes: str | None = Field(None, max_length=1000)


EpisodeSuggestionStatusLiteral = Literal["pending", "approved", "rejected", "auto_approved"]


class EpisodeSuggestionResponse(BaseModel):
    """Response for a single episode suggestion"""

    id: str  # UUID string
    user_id: int
    username: str | None = None
    episode_id: int
    episode_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    series_title: str | None = None
    field_name: str
    current_value: str | None = None
    suggested_value: str
    reason: str | None = None
    status: str
    was_auto_approved: bool = False
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # User reputation info
    user_contribution_level: str | None = None
    user_contribution_points: int | None = None


class EpisodeSuggestionListResponse(BaseModel):
    """Paginated list of episode suggestions"""

    suggestions: list[EpisodeSuggestionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class EpisodeSuggestionStatsResponse(BaseModel):
    """Statistics about episode suggestions"""

    total: int
    pending: int
    approved: int
    auto_approved: int
    rejected: int
    # Today's stats (for moderators)
    approved_today: int = 0
    rejected_today: int = 0
    # User stats
    user_pending: int = 0
    user_approved: int = 0
    user_auto_approved: int = 0
    user_rejected: int = 0
    user_contribution_points: int = 0
    user_contribution_level: str = "new"


# ============================================
# Helper Functions
# ============================================


async def get_contribution_settings(session: AsyncSession) -> ContributionSettings:
    """Get or create contribution settings"""
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    settings = result.first()

    if not settings:
        settings = ContributionSettings(id="default")
        session.add(settings)
        await session.commit()
        await session.refresh(settings)

    return settings


async def get_episode_info(session: AsyncSession, episode_id: int) -> dict | None:
    """Get episode info including season number and series title"""
    from db.models import Media, SeriesMetadata

    query = (
        select(
            Episode.id,
            Episode.title,
            Episode.episode_number,
            Season.season_number,
            Media.title.label("series_title"),
        )
        .join(Season, Episode.season_id == Season.id)
        .join(SeriesMetadata, Season.series_id == SeriesMetadata.id)
        .join(Media, SeriesMetadata.media_id == Media.id)
        .where(Episode.id == episode_id)
    )
    result = await session.exec(query)
    row = result.first()

    if row:
        return {
            "episode_id": row.id,
            "episode_title": row.title,
            "episode_number": row.episode_number,
            "season_number": row.season_number,
            "series_title": row.series_title,
        }
    return None


async def get_username(session: AsyncSession, user_id: int) -> str | None:
    """Get username for a user"""
    query = select(User.username).where(User.id == user_id)
    result = await session.exec(query)
    return result.first()


async def should_auto_approve(user: User, session: AsyncSession) -> bool:
    """Check if user has enough reputation for auto-approval."""
    # Moderators and admins are always auto-approved
    if user.role in (UserRole.MODERATOR, UserRole.ADMIN):
        return True

    settings = await get_contribution_settings(session)

    if not settings.allow_auto_approval:
        return False

    return user.contribution_points >= settings.auto_approval_threshold


def calculate_contribution_level(points: int, settings: ContributionSettings) -> str:
    """Calculate user contribution level based on points"""
    if points >= settings.expert_threshold:
        return "expert"
    elif points >= settings.trusted_threshold:
        return "trusted"
    elif points >= settings.contributor_threshold:
        return "contributor"
    return "new"


async def award_points(
    user: User,
    points: int,
    edit_type: str,
    session: AsyncSession,
) -> None:
    """Award points to a user and update their contribution level"""
    settings = await get_contribution_settings(session)

    user.contribution_points = max(0, user.contribution_points + points)

    if edit_type == "metadata":
        user.metadata_edits_approved += 1
    elif edit_type == "stream":
        user.stream_edits_approved += 1

    # Update contribution level
    user.contribution_level = calculate_contribution_level(user.contribution_points, settings)

    session.add(user)
    logger.info(
        f"Awarded {points} points to user {user.id}. "
        f"Total: {user.contribution_points}, Level: {user.contribution_level}"
    )


async def apply_episode_changes(
    episode: Episode,
    field_name: str,
    value: str,
    session: AsyncSession,
) -> bool:
    """Apply suggested changes to episode. Returns True if successful."""
    try:
        if field_name == "title":
            episode.title = value
        elif field_name == "overview":
            episode.overview = value
        elif field_name == "air_date":
            # Parse date string (YYYY-MM-DD format)
            if value:
                episode.air_date = date.fromisoformat(value)
            else:
                episode.air_date = None
        elif field_name == "runtime_minutes":
            if value:
                episode.runtime_minutes = int(value)
            else:
                episode.runtime_minutes = None
        else:
            logger.warning(f"Unknown episode field: {field_name}")
            return False

        episode.updated_at = datetime.now(pytz.UTC)
        session.add(episode)
        return True
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to apply episode change: {e}")
        return False


async def check_pending_limit(
    user_id: int,
    session: AsyncSession,
) -> bool:
    """Check if user has reached pending episode suggestions limit"""
    settings = await get_contribution_settings(session)

    count_query = select(func.count(EpisodeSuggestion.id)).where(
        EpisodeSuggestion.user_id == user_id,
        EpisodeSuggestion.status == STATUS_PENDING,
    )
    count_result = await session.exec(count_query)
    pending_count = count_result.one()

    return pending_count >= settings.max_pending_suggestions_per_user


async def format_suggestion_response(
    suggestion: EpisodeSuggestion,
    session: AsyncSession,
    current_user: User | None = None,
) -> EpisodeSuggestionResponse:
    """Format a suggestion into a response"""
    episode_info = await get_episode_info(session, suggestion.episode_id)
    username = await get_username(session, suggestion.user_id)
    reviewer_name = await get_username(session, int(suggestion.reviewed_by)) if suggestion.reviewed_by else None

    # Get user contribution info if we have the suggestion author
    user_contribution_level = None
    user_contribution_points = None
    if current_user and current_user.id == suggestion.user_id:
        user_contribution_level = current_user.contribution_level
        user_contribution_points = current_user.contribution_points
    else:
        # Fetch user info
        user_query = select(User).where(User.id == suggestion.user_id)
        user_result = await session.exec(user_query)
        suggestion_user = user_result.first()
        if suggestion_user:
            user_contribution_level = suggestion_user.contribution_level
            user_contribution_points = suggestion_user.contribution_points

    return EpisodeSuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=username,
        episode_id=suggestion.episode_id,
        episode_title=episode_info.get("episode_title") if episode_info else None,
        season_number=episode_info.get("season_number") if episode_info else None,
        episode_number=episode_info.get("episode_number") if episode_info else None,
        series_title=episode_info.get("series_title") if episode_info else None,
        field_name=suggestion.field_name,
        current_value=suggestion.current_value,
        suggested_value=suggestion.suggested_value,
        reason=suggestion.reason,
        status=suggestion.status,
        was_auto_approved=suggestion.status == STATUS_AUTO_APPROVED,
        reviewed_by=reviewer_name,
        reviewed_at=suggestion.reviewed_at,
        review_notes=suggestion.review_notes,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        user_contribution_level=user_contribution_level,
        user_contribution_points=user_contribution_points,
    )


# ============================================
# User Suggestion Endpoints
# ============================================


@router.post("/episode/{episode_id}/suggest", response_model=EpisodeSuggestionResponse)
async def create_episode_suggestion(
    episode_id: int,
    request: EpisodeSuggestionCreateRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Submit an episode correction suggestion.
    Trusted users may have their suggestions auto-approved.
    """
    settings = await get_contribution_settings(session)

    # Check reason requirement
    if settings.require_reason_for_edits and not request.reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A reason is required for edit suggestions",
        )

    # Verify episode exists
    episode_query = select(Episode).where(Episode.id == episode_id)
    episode_result = await session.exec(episode_query)
    episode = episode_result.first()

    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Episode not found",
        )

    # Check pending suggestions limit
    if await check_pending_limit(current_user.id, session):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You have reached the maximum number of pending suggestions ({settings.max_pending_suggestions_per_user})",
        )

    # Check for duplicate pending suggestion from same user for same field
    existing_query = select(EpisodeSuggestion).where(
        EpisodeSuggestion.user_id == current_user.id,
        EpisodeSuggestion.episode_id == episode_id,
        EpisodeSuggestion.field_name == request.field_name,
        EpisodeSuggestion.status == STATUS_PENDING,
    )
    existing_result = await session.exec(existing_query)
    existing = existing_result.first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending suggestion for this field",
        )

    # Check if user qualifies for auto-approval
    can_auto_approve = await should_auto_approve(current_user, session)

    now = datetime.now(pytz.UTC)

    # Create suggestion
    suggestion = EpisodeSuggestion(
        user_id=current_user.id,
        episode_id=episode_id,
        field_name=request.field_name,
        current_value=request.current_value,
        suggested_value=request.suggested_value,
        reason=request.reason,
        status=STATUS_AUTO_APPROVED if can_auto_approve else STATUS_PENDING,
    )

    # If auto-approved, apply changes immediately
    if can_auto_approve:
        suggestion.reviewed_by = str(current_user.id)
        suggestion.reviewed_at = now
        suggestion.review_notes = "Auto-approved based on user reputation"

        # Apply the changes
        apply_success = await apply_episode_changes(episode, request.field_name, request.suggested_value, session)

        if apply_success:
            # Award points for auto-approved suggestion
            await award_points(
                current_user,
                settings.points_per_metadata_edit,
                "metadata",
                session,
            )
        else:
            # If application failed, set to pending for moderator review
            suggestion.status = STATUS_PENDING
            suggestion.reviewed_by = None
            suggestion.reviewed_at = None
            suggestion.review_notes = None

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    return await format_suggestion_response(suggestion, session, current_user)


@router.get("/episode-suggestions", response_model=EpisodeSuggestionListResponse)
async def list_my_episode_suggestions(
    suggestion_status: EpisodeSuggestionStatusLiteral | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List current user's episode suggestions.
    """
    offset = (page - 1) * page_size

    # Base query
    base_query = select(EpisodeSuggestion).where(EpisodeSuggestion.user_id == current_user.id)
    count_query = select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.user_id == current_user.id)

    # Filter by status
    if suggestion_status:
        base_query = base_query.where(EpisodeSuggestion.status == suggestion_status)
        count_query = count_query.where(EpisodeSuggestion.status == suggestion_status)

    # Order and paginate
    base_query = base_query.order_by(EpisodeSuggestion.created_at.desc())
    base_query = base_query.offset(offset).limit(page_size)

    # Execute
    result = await session.exec(base_query)
    suggestions = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Build responses
    responses = []
    for s in suggestions:
        responses.append(await format_suggestion_response(s, session, current_user))

    return EpisodeSuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(suggestions)) < total,
    )


@router.get("/episode-suggestions/stats", response_model=EpisodeSuggestionStatsResponse)
async def get_episode_suggestion_stats(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get episode suggestion statistics.
    For moderators: shows global stats including today's counts.
    For users: shows their own stats.
    """
    is_moderator = current_user.role in [UserRole.MODERATOR, UserRole.ADMIN]

    # Initialize today's stats
    approved_today = 0
    rejected_today = 0

    # Global stats (for moderators)
    if is_moderator:
        total_result = await session.exec(select(func.count(EpisodeSuggestion.id)))
        total = total_result.one()

        pending_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.status == STATUS_PENDING)
        )
        pending = pending_result.one()

        approved_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.status == STATUS_APPROVED)
        )
        approved = approved_result.one()

        auto_approved_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.status == STATUS_AUTO_APPROVED)
        )
        auto_approved = auto_approved_result.one()

        rejected_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.status == STATUS_REJECTED)
        )
        rejected = rejected_result.one()

        # Today's stats
        today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        approved_today_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(
                EpisodeSuggestion.status.in_([STATUS_APPROVED, STATUS_AUTO_APPROVED]),
                EpisodeSuggestion.reviewed_at >= today_start,
            )
        )
        approved_today = approved_today_result.one()

        rejected_today_result = await session.exec(
            select(func.count(EpisodeSuggestion.id)).where(
                EpisodeSuggestion.status == STATUS_REJECTED,
                EpisodeSuggestion.reviewed_at >= today_start,
            )
        )
        rejected_today = rejected_today_result.one()
    else:
        total = pending = approved = auto_approved = rejected = 0

    # User stats
    user_pending_result = await session.exec(
        select(func.count(EpisodeSuggestion.id)).where(
            EpisodeSuggestion.user_id == current_user.id,
            EpisodeSuggestion.status == STATUS_PENDING,
        )
    )
    user_pending = user_pending_result.one()

    user_approved_result = await session.exec(
        select(func.count(EpisodeSuggestion.id)).where(
            EpisodeSuggestion.user_id == current_user.id,
            EpisodeSuggestion.status == STATUS_APPROVED,
        )
    )
    user_approved = user_approved_result.one()

    user_auto_approved_result = await session.exec(
        select(func.count(EpisodeSuggestion.id)).where(
            EpisodeSuggestion.user_id == current_user.id,
            EpisodeSuggestion.status == STATUS_AUTO_APPROVED,
        )
    )
    user_auto_approved = user_auto_approved_result.one()

    user_rejected_result = await session.exec(
        select(func.count(EpisodeSuggestion.id)).where(
            EpisodeSuggestion.user_id == current_user.id,
            EpisodeSuggestion.status == STATUS_REJECTED,
        )
    )
    user_rejected = user_rejected_result.one()

    return EpisodeSuggestionStatsResponse(
        total=total,
        pending=pending,
        approved=approved,
        auto_approved=auto_approved,
        rejected=rejected,
        approved_today=approved_today,
        rejected_today=rejected_today,
        user_pending=user_pending,
        user_approved=user_approved,
        user_auto_approved=user_auto_approved,
        user_rejected=user_rejected,
        user_contribution_points=current_user.contribution_points,
        user_contribution_level=current_user.contribution_level,
    )


# ============================================
# Moderator Endpoints
# ============================================


@router.get("/episode-suggestions/pending", response_model=EpisodeSuggestionListResponse)
async def list_pending_episode_suggestions(
    field_name: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List all pending episode suggestions (moderator only).
    """
    offset = (page - 1) * page_size

    # Base query for pending suggestions
    base_query = select(EpisodeSuggestion).where(EpisodeSuggestion.status == STATUS_PENDING)
    count_query = select(func.count(EpisodeSuggestion.id)).where(EpisodeSuggestion.status == STATUS_PENDING)

    # Filter by field name
    if field_name:
        base_query = base_query.where(EpisodeSuggestion.field_name == field_name)
        count_query = count_query.where(EpisodeSuggestion.field_name == field_name)

    # Order and paginate
    base_query = base_query.order_by(EpisodeSuggestion.created_at.asc())
    base_query = base_query.offset(offset).limit(page_size)

    # Execute
    result = await session.exec(base_query)
    suggestions = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    # Build responses
    responses = []
    for s in suggestions:
        responses.append(await format_suggestion_response(s, session))

    return EpisodeSuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(suggestions)) < total,
    )


@router.post("/episode-suggestions/bulk-review")
async def bulk_review_episode_suggestions(
    suggestion_ids: list[str],
    action: Literal["approve", "reject"],
    review_notes: str | None = None,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Bulk review multiple episode suggestions (moderator only).
    """
    settings = await get_contribution_settings(session)
    now = datetime.now(pytz.UTC)

    results = {"approved": 0, "rejected": 0, "skipped": 0}

    for suggestion_id in suggestion_ids:
        query = select(EpisodeSuggestion).where(EpisodeSuggestion.id == suggestion_id)
        result = await session.exec(query)
        suggestion = result.first()

        if not suggestion or suggestion.status != STATUS_PENDING:
            results["skipped"] += 1
            continue

        # Get author
        author_query = select(User).where(User.id == suggestion.user_id)
        author_result = await session.exec(author_query)
        author = author_result.first()

        suggestion.status = STATUS_APPROVED if action == "approve" else STATUS_REJECTED
        suggestion.reviewed_by = str(current_user.id)
        suggestion.reviewed_at = now
        suggestion.review_notes = review_notes
        suggestion.updated_at = now

        if action == "approve":
            episode_query = select(Episode).where(Episode.id == suggestion.episode_id)
            episode_result = await session.exec(episode_query)
            episode = episode_result.first()

            if episode:
                await apply_episode_changes(episode, suggestion.field_name, suggestion.suggested_value, session)

            if author:
                await award_points(author, settings.points_per_metadata_edit, "metadata", session)

            results["approved"] += 1
        else:
            if author and settings.points_for_rejection_penalty < 0:
                await award_points(author, settings.points_for_rejection_penalty, "metadata", session)
            results["rejected"] += 1

        session.add(suggestion)

    await session.commit()

    return results


# ============================================
# Single Suggestion Endpoints
# ============================================


@router.get("/episode-suggestions/{suggestion_id}", response_model=EpisodeSuggestionResponse)
async def get_episode_suggestion(
    suggestion_id: str,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get a specific episode suggestion.
    Users can only view their own suggestions unless they are moderators.
    """
    query = select(EpisodeSuggestion).where(EpisodeSuggestion.id == suggestion_id)
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    # Check access
    is_moderator = current_user.role in [UserRole.MODERATOR, UserRole.ADMIN]
    if suggestion.user_id != current_user.id and not is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return await format_suggestion_response(suggestion, session, current_user)


@router.delete("/episode-suggestions/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode_suggestion(
    suggestion_id: str,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a pending episode suggestion. Users can only delete their own pending suggestions.
    """
    query = select(EpisodeSuggestion).where(
        EpisodeSuggestion.id == suggestion_id,
        EpisodeSuggestion.user_id == current_user.id,
    )
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete pending suggestions",
        )

    await session.delete(suggestion)
    await session.commit()


@router.put("/episode-suggestions/{suggestion_id}/review", response_model=EpisodeSuggestionResponse)
async def review_episode_suggestion(
    suggestion_id: str,
    request: EpisodeSuggestionReviewRequest,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Review and approve/reject an episode suggestion (moderator only).
    If approved, the episode will be updated and points awarded.
    If rejected, points may be deducted based on settings.
    """
    settings = await get_contribution_settings(session)

    query = select(EpisodeSuggestion).where(EpisodeSuggestion.id == suggestion_id)
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suggestion has already been reviewed",
        )

    # Get the suggestion author
    author_query = select(User).where(User.id == suggestion.user_id)
    author_result = await session.exec(author_query)
    author = author_result.first()

    # Update suggestion status
    now = datetime.now(pytz.UTC)
    suggestion.status = STATUS_APPROVED if request.action == "approve" else STATUS_REJECTED
    suggestion.reviewed_by = str(current_user.id)
    suggestion.reviewed_at = now
    suggestion.review_notes = request.review_notes
    suggestion.updated_at = now

    # If approved, update the episode and award points
    if request.action == "approve":
        episode_query = select(Episode).where(Episode.id == suggestion.episode_id)
        episode_result = await session.exec(episode_query)
        episode = episode_result.first()

        if episode:
            apply_success = await apply_episode_changes(
                episode, suggestion.field_name, suggestion.suggested_value, session
            )

            if apply_success and author:
                # Award points for approved suggestion
                await award_points(
                    author,
                    settings.points_per_metadata_edit,
                    "metadata",
                    session,
                )
    elif request.action == "reject" and author:
        # Apply rejection penalty if configured
        if settings.points_for_rejection_penalty < 0:
            await award_points(
                author,
                settings.points_for_rejection_penalty,
                "metadata",
                session,
            )

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    return await format_suggestion_response(suggestion, session)
