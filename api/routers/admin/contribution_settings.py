"""
Contribution Settings API endpoints for admin configuration.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import ContributionSettings, User

router = APIRouter(prefix="/api/v1/admin", tags=["Admin - Contribution Settings"])


# ============================================
# Pydantic Schemas
# ============================================


class ContributionSettingsResponse(BaseModel):
    """Response model for contribution settings"""

    id: str
    auto_approval_threshold: int
    points_per_metadata_edit: int
    points_per_stream_edit: int
    points_for_rejection_penalty: int
    contributor_threshold: int
    trusted_threshold: int
    expert_threshold: int
    allow_auto_approval: bool
    require_reason_for_edits: bool
    max_pending_suggestions_per_user: int


class ContributionSettingsUpdate(BaseModel):
    """Request model for updating contribution settings"""

    auto_approval_threshold: int | None = Field(None, ge=0, le=1000)
    points_per_metadata_edit: int | None = Field(None, ge=0, le=100)
    points_per_stream_edit: int | None = Field(None, ge=0, le=100)
    points_for_rejection_penalty: int | None = Field(None, ge=-50, le=0)
    contributor_threshold: int | None = Field(None, ge=0, le=500)
    trusted_threshold: int | None = Field(None, ge=0, le=1000)
    expert_threshold: int | None = Field(None, ge=0, le=5000)
    allow_auto_approval: bool | None = None
    require_reason_for_edits: bool | None = None
    max_pending_suggestions_per_user: int | None = Field(None, ge=1, le=100)


class ContributionLevelsInfo(BaseModel):
    """Information about contribution levels"""

    levels: list[dict]
    current_settings: ContributionSettingsResponse


# ============================================
# Helper Functions
# ============================================


async def get_or_create_settings(session: AsyncSession) -> ContributionSettings:
    """Get contribution settings, creating default if not exists"""
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    settings = result.first()

    if not settings:
        # Create default settings
        settings = ContributionSettings(id="default")
        session.add(settings)
        await session.commit()
        await session.refresh(settings)

    return settings


def settings_to_response(
    settings: ContributionSettings,
) -> ContributionSettingsResponse:
    """Convert settings model to response"""
    return ContributionSettingsResponse(
        id=settings.id,
        auto_approval_threshold=settings.auto_approval_threshold,
        points_per_metadata_edit=settings.points_per_metadata_edit,
        points_per_stream_edit=settings.points_per_stream_edit,
        points_for_rejection_penalty=settings.points_for_rejection_penalty,
        contributor_threshold=settings.contributor_threshold,
        trusted_threshold=settings.trusted_threshold,
        expert_threshold=settings.expert_threshold,
        allow_auto_approval=settings.allow_auto_approval,
        require_reason_for_edits=settings.require_reason_for_edits,
        max_pending_suggestions_per_user=settings.max_pending_suggestions_per_user,
    )


# ============================================
# API Endpoints
# ============================================


@router.get("/contribution-settings", response_model=ContributionSettingsResponse)
async def get_contribution_settings(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get current contribution settings (admin only).
    """
    settings = await get_or_create_settings(session)
    return settings_to_response(settings)


@router.put("/contribution-settings", response_model=ContributionSettingsResponse)
async def update_contribution_settings(
    update_data: ContributionSettingsUpdate,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Update contribution settings (admin only).
    """
    settings = await get_or_create_settings(session)

    # Update only provided fields
    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        if value is not None:
            setattr(settings, field, value)

    # Validate threshold ordering
    if settings.contributor_threshold >= settings.trusted_threshold:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contributor threshold must be less than trusted threshold",
        )
    if settings.trusted_threshold >= settings.expert_threshold:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trusted threshold must be less than expert threshold",
        )

    session.add(settings)
    await session.commit()
    await session.refresh(settings)

    return settings_to_response(settings)


@router.get("/contribution-levels", response_model=ContributionLevelsInfo)
async def get_contribution_levels(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get information about contribution levels (admin only).
    """
    settings = await get_or_create_settings(session)

    levels = [
        {
            "name": "new",
            "display_name": "New Contributor",
            "description": "New users who haven't earned contribution points yet",
            "min_points": 0,
            "max_points": settings.contributor_threshold - 1,
            "can_auto_approve": False,
        },
        {
            "name": "contributor",
            "display_name": "Contributor",
            "description": "Users who have started contributing",
            "min_points": settings.contributor_threshold,
            "max_points": settings.trusted_threshold - 1,
            "can_auto_approve": False,
        },
        {
            "name": "trusted",
            "display_name": "Trusted Contributor",
            "description": "Users with a track record of quality contributions",
            "min_points": settings.trusted_threshold,
            "max_points": settings.expert_threshold - 1,
            "can_auto_approve": settings.allow_auto_approval
            and settings.auto_approval_threshold <= settings.trusted_threshold,
        },
        {
            "name": "expert",
            "display_name": "Expert Contributor",
            "description": "Top contributors with extensive experience",
            "min_points": settings.expert_threshold,
            "max_points": None,
            "can_auto_approve": settings.allow_auto_approval,
        },
    ]

    return ContributionLevelsInfo(
        levels=levels,
        current_settings=settings_to_response(settings),
    )


@router.post("/contribution-settings/reset", response_model=ContributionSettingsResponse)
async def reset_contribution_settings(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Reset contribution settings to defaults (admin only).
    """
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    existing = result.first()

    if existing:
        await session.delete(existing)
        await session.commit()

    # Create fresh default settings
    settings = ContributionSettings(id="default")
    session.add(settings)
    await session.commit()
    await session.refresh(settings)

    return settings_to_response(settings)
