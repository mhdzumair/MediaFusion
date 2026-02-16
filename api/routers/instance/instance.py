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
    require_auth,
    verify_password,
)
from db.config import settings
from db.database import get_async_session
from db.enums import UserRole
from db.models import User, UserProfile
from utils import const
from utils.bootstrap import (
    BOOTSTRAP_EMAIL,
    check_setup_required,
    is_bootstrap_user,
    mark_setup_complete,
)

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


class SetupLoginRequest(BaseModel):
    """Request body for bootstrap admin login during setup.

    Uses plain str for email instead of EmailStr because the bootstrap
    email uses a .local domain which is rejected by email validators.
    Requires the instance API_PASSWORD as an additional security layer.
    """

    email: str
    password: str
    api_password: str


class SetupCompleteRequest(BaseModel):
    """Request body for completing the initial admin setup."""

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


@router.post("/setup/login", response_model=TokenResponse)
async def setup_login(
    credentials: SetupLoginRequest,
    session: AsyncSession = Depends(get_async_session),
):
    """Login as the bootstrap admin during initial setup.

    This is the ONLY way to authenticate as the bootstrap admin.
    The normal /auth/login endpoint blocks the bootstrap account.
    Only works when setup is still required.

    Requires the instance API_PASSWORD as an additional security layer
    to prevent unauthorized access to the setup wizard.
    """
    # Validate API_PASSWORD first
    if credentials.api_password != settings.api_password:
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

    # Only allow bootstrap email
    if credentials.email != BOOTSTRAP_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials for setup login.",
        )

    result = await session.exec(select(User).where(User.email == BOOTSTRAP_EMAIL))
    user = result.first()

    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bootstrap admin not found.",
        )

    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials for setup login.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bootstrap admin has been deactivated.",
        )

    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse(
            id=user.id,
            uuid=user.uuid,
            email=user.email,
            username=user.username,
            role=user.role.value,
            is_verified=user.is_verified,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login=user.last_login,
            contribution_points=user.contribution_points,
            contribution_level=user.contribution_level,
            contribute_anonymously=user.contribute_anonymously,
        ),
    )


@router.post("/setup/complete", response_model=TokenResponse)
async def complete_setup(
    request: SetupCompleteRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Complete the initial admin setup by creating the deployer's admin account.

    This endpoint:
    1. Requires authentication as the bootstrap admin
    2. Creates a new admin user with the provided credentials
    3. Deactivates the bootstrap admin account
    4. Returns auth tokens for the new admin

    Only available during initial setup (when setup_required is true).
    """
    # Verify setup is actually required
    if not await check_setup_required(session):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup has already been completed.",
        )

    # Only the bootstrap admin can complete setup
    if not is_bootstrap_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the bootstrap admin can complete initial setup.",
        )

    # Check if the new email is already taken (by someone other than bootstrap)
    result = await session.exec(select(User).where(User.email == request.email))
    existing = result.first()
    if existing:
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

    # Create the new admin user
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

    # Create default profile for the new admin
    profile = UserProfile(
        user_id=new_admin.id,
        name="Default",
        config={},
        is_default=True,
    )
    session.add(profile)

    # Deactivate the bootstrap admin
    current_user.is_active = False
    session.add(current_user)

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
