"""Instance information endpoints.

Provides endpoints to get current instance configuration,
allowing the frontend to determine if API key is required,
and the initial admin setup flow for first deployments.
"""

from datetime import datetime

import pytz
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import (
    TokenResponse,
    UserResponse,
    create_access_token,
    create_refresh_token,
    hash_password,
)
from db.config import settings
from db.database import get_async_session
from db.enums import UserRole
from db.models import User, UserProfile
from utils import const
from utils.bootstrap import check_setup_required, mark_setup_complete

router = APIRouter(prefix="/api/v1/instance", tags=["Instance"])


class InstanceInfo(BaseModel):
    """Instance configuration information."""

    is_public: bool
    requires_api_key: bool
    setup_required: bool
    addon_name: str
    version: str
    logo_url: str
    branding_svg: str | None = None  # Optional partner/host SVG logo URL


class SetupCompleteRequest(BaseModel):
    """Request body for creating the first admin account during initial setup.

    Requires the instance API_PASSWORD for authentication since no user
    accounts exist yet.
    """

    api_password: str
    email: EmailStr
    username: str | None = Field(None, min_length=3, max_length=100)
    password: str = Field(..., min_length=8)


class TelegramFeatureConfig(BaseModel):
    """Telegram feature configuration for the frontend."""

    enabled: bool  # Whether Telegram streaming is enabled on this instance
    bot_configured: bool  # Whether the Telegram bot is configured
    bot_username: str | None = None  # Bot @username for deep links (without @)
    scraping_enabled: bool  # Whether Telegram scraping is enabled


class AppConfig(BaseModel):
    """Full application configuration for the frontend."""

    addon_name: str
    logo_url: str
    branding_svg: str | None = None  # Optional partner/host SVG logo URL
    host_url: str
    poster_host_url: str | None
    version: str
    description: str
    branding_description: str
    is_public_instance: bool
    disabled_providers: list[str]
    disabled_content_imports: list[str]
    authentication_required: bool
    torznab_enabled: bool
    telegram: TelegramFeatureConfig


@router.get("/info", response_model=InstanceInfo)
async def get_instance_info(
    session: AsyncSession = Depends(get_async_session),
):
    """Get current instance configuration (public vs private).

    This endpoint is always accessible (no auth required) so the frontend
    can determine whether to show the API key input field and whether
    the initial admin setup wizard should be displayed.

    Returns:
        InstanceInfo: Instance configuration including whether API key is required
            and whether initial setup is needed.
    """
    setup_needed = await check_setup_required(session)

    return InstanceInfo(
        is_public=settings.is_public_instance,
        requires_api_key=not settings.is_public_instance,
        setup_required=setup_needed,
        addon_name=settings.addon_name,
        version=settings.version,
        logo_url=settings.logo_url,
        branding_svg=settings.branding_svg,
    )


@router.get("/app-config", response_model=AppConfig)
async def get_app_config():
    """Get full application configuration for the frontend.

    This endpoint provides all configuration needed by the frontend UI,
    including branding, disabled providers, and authentication requirements.
    """
    return AppConfig(
        addon_name=settings.addon_name,
        logo_url=settings.logo_url,
        branding_svg=settings.branding_svg,
        host_url=settings.host_url,
        poster_host_url=settings.poster_host_url,
        version=settings.version,
        description=settings.description,
        branding_description=settings.branding_description,
        is_public_instance=settings.is_public_instance,
        disabled_providers=settings.disabled_providers,
        disabled_content_imports=settings.disabled_content_imports,
        authentication_required=settings.api_password is not None and not settings.is_public_instance,
        torznab_enabled=settings.enable_torznab_api,
        telegram=TelegramFeatureConfig(
            enabled=settings.is_scrap_from_telegram,
            bot_configured=bool(settings.telegram_bot_token),
            bot_username=settings.telegram_bot_username,
            scraping_enabled=settings.is_scrap_from_telegram,
        ),
    )


@router.get("/constants")
async def get_system_constants():
    """Get all system constants needed by the frontend.

    Returns catalog data, resolutions, sorting options, languages, and quality groups.
    """
    return {
        "CATALOG_DATA": const.CATALOG_DATA,
        "RESOLUTIONS": const.RESOLUTIONS,
        "TORRENT_SORTING_PRIORITY_OPTIONS": const.TORRENT_SORTING_PRIORITY_OPTIONS,
        "SUPPORTED_LANGUAGES": const.SUPPORTED_LANGUAGES,
        "QUALITY_GROUPS": const.QUALITY_GROUPS,
    }


@router.post("/setup/create-admin", response_model=TokenResponse)
async def create_initial_admin(
    request: SetupCompleteRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Create the first admin account during initial setup.

    This endpoint is unauthenticated (no user accounts exist yet).
    It is protected by requiring the instance API_PASSWORD.

    SECURITY: Only available when the user table is completely empty.
    Once any user exists (regardless of role or status), this endpoint
    is permanently locked and returns 400.
    """
    # Validate API_PASSWORD
    if request.api_password != settings.api_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API password.",
        )

    # Verify setup is actually required
    if not await check_setup_required(session):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup has already been completed.",
        )

    # Check if the email is already taken
    result = await session.exec(select(User).where(User.email == request.email))
    if result.first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered.",
        )

    # Check if username is already taken
    if request.username:
        result = await session.exec(select(User).where(User.username == request.username))
        if result.first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken.",
            )

    # Create the admin user
    new_admin = User(
        email=request.email,
        username=request.username,
        password_hash=hash_password(request.password),
        role=UserRole.ADMIN,
        is_verified=True,
        is_active=True,
        last_login=datetime.now(pytz.UTC),
    )
    session.add(new_admin)
    await session.flush()

    # Create default profile
    profile = UserProfile(
        user_id=new_admin.id,
        name="Default",
        config={},
        is_default=True,
    )
    session.add(profile)

    await session.commit()
    await session.refresh(new_admin)

    # Mark setup as complete in-memory
    mark_setup_complete()

    # Generate tokens for the new admin
    access_token = create_access_token(new_admin.id, new_admin.role.value)
    refresh_token = create_refresh_token(new_admin.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(
            id=new_admin.id,
            uuid=new_admin.uuid,
            email=new_admin.email,
            username=new_admin.username,
            role=new_admin.role.value,
            is_verified=new_admin.is_verified,
            is_active=new_admin.is_active,
            created_at=new_admin.created_at,
            last_login=new_admin.last_login,
            contribution_points=new_admin.contribution_points,
            contribution_level=new_admin.contribution_level,
            contribute_anonymously=new_admin.contribute_anonymously,
        ),
    )
