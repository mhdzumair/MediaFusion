"""
External Platform Integrations API endpoints.

Manages OAuth connections and sync operations for external platforms
like Trakt, Simkl, MAL, etc.

Integrations are stored per-profile in the ProfileIntegration table,
separate from the Stremio config. Only authenticated users can have
integrations.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from api.services.sync import (
    exchange_simkl_code,
    exchange_trakt_code,
    get_simkl_auth_url,
    get_trakt_auth_url,
)
from api.services.sync.tasks import build_sync_service
from db.config import settings
from db.database import get_async_session, get_async_session_context, get_read_session, get_read_session_context
from db.enums import IntegrationType, SyncDirection
from db.models import ProfileIntegration, User, UserProfile
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])


# ============================================
# Pydantic Schemas
# ============================================


class IntegrationStatus(BaseModel):
    """Status of a single integration."""

    platform: IntegrationType
    connected: bool
    is_enabled: bool = False
    sync_direction: str = "two_way"
    scrobble_enabled: bool = True
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    last_sync_stats: dict | None = None


class IntegrationListResponse(BaseModel):
    """Response listing all integrations for a profile."""

    profile_id: int
    integrations: list[IntegrationStatus]


class OAuthUrlResponse(BaseModel):
    """Response with OAuth authorization URL."""

    auth_url: str
    platform: IntegrationType


class TraktConnectRequest(BaseModel):
    """Request to connect Trakt with authorization code."""

    code: str
    # For custom Trakt apps - both required if using custom credentials
    client_id: str | None = None
    client_secret: str | None = None


class SimklConnectRequest(BaseModel):
    """Request to connect Simkl with authorization code."""

    code: str
    # For custom Simkl apps - both required if using custom credentials
    client_id: str | None = None
    client_secret: str | None = None


class IntegrationSettingsUpdate(BaseModel):
    """Update integration settings."""

    is_enabled: bool | None = None
    sync_direction: str | None = None
    scrobble_enabled: bool | None = None
    settings: dict | None = None  # Platform-specific settings


class SyncTriggerResponse(BaseModel):
    """Response after triggering sync."""

    message: str
    sync_started: bool


class SyncStatusResponse(BaseModel):
    """Response with sync status."""

    platform: IntegrationType
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    last_sync_stats: dict | None


# ============================================
# Helper Functions
# ============================================


async def get_profile_for_user(
    session: AsyncSession,
    user: User,
    profile_id: int,
) -> UserProfile:
    """Get profile and verify ownership."""
    profile = await session.get(UserProfile, profile_id)
    if not profile or profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    return profile


async def get_integration(
    session: AsyncSession,
    profile_id: int,
    platform: IntegrationType,
) -> ProfileIntegration | None:
    """Get integration for a profile and platform."""
    query = select(ProfileIntegration).where(
        ProfileIntegration.profile_id == profile_id,
        ProfileIntegration.platform == platform,
    )
    result = await session.exec(query)
    return result.first()


def encrypt_credentials(credentials: dict) -> str:
    """Encrypt integration credentials."""
    return profile_crypto.encrypt_secrets(credentials)


def decrypt_credentials(encrypted: str) -> dict:
    """Decrypt integration credentials."""
    try:
        return profile_crypto.decrypt_secrets(encrypted)
    except Exception as e:
        logger.warning(f"Failed to decrypt credentials: {e}")
        return {}


# ============================================
# API Endpoints - List & Status
# ============================================


@router.get("", response_model=IntegrationListResponse)
async def list_integrations(
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """List all integrations and their status for a profile."""
    await get_profile_for_user(session, user, profile_id)

    # Get all integrations for this profile
    query = select(ProfileIntegration).where(ProfileIntegration.profile_id == profile_id)
    result = await session.exec(query)
    integrations_db = {i.platform: i for i in result.all()}

    integrations = []
    for platform in IntegrationType:
        integration = integrations_db.get(platform)
        integrations.append(
            IntegrationStatus(
                platform=platform,
                connected=integration is not None,
                is_enabled=integration.is_enabled if integration else False,
                sync_direction=integration.sync_direction if integration else "two_way",
                scrobble_enabled=integration.scrobble_enabled if integration else True,
                last_sync_at=integration.last_sync_at if integration else None,
                last_sync_status=integration.last_sync_status if integration else None,
                last_sync_error=integration.last_sync_error if integration else None,
                last_sync_stats=integration.last_sync_stats if integration else None,
            )
        )

    return IntegrationListResponse(profile_id=profile_id, integrations=integrations)


@router.get("/{platform}/status", response_model=SyncStatusResponse)
async def get_sync_status(
    platform: IntegrationType,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get detailed sync status for a platform."""
    await get_profile_for_user(session, user, profile_id)

    integration = await get_integration(session, profile_id, platform)

    return SyncStatusResponse(
        platform=platform,
        last_sync_at=integration.last_sync_at if integration else None,
        last_sync_status=integration.last_sync_status if integration else None,
        last_sync_error=integration.last_sync_error if integration else None,
        last_sync_stats=integration.last_sync_stats if integration else None,
    )


# ============================================
# API Endpoints - OAuth Connection
# ============================================


@router.get("/oauth/{platform}/url", response_model=OAuthUrlResponse)
async def get_oauth_url(
    platform: IntegrationType,
    client_id: str | None = Query(None, description="Custom client ID for OAuth"),
):
    """Get OAuth authorization URL for a platform."""
    if platform == IntegrationType.TRAKT:
        return OAuthUrlResponse(
            auth_url=get_trakt_auth_url(client_id),
            platform=platform,
        )
    elif platform == IntegrationType.SIMKL:
        return OAuthUrlResponse(
            auth_url=get_simkl_auth_url(client_id),
            platform=platform,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth not supported for {platform}",
        )


@router.get("/simkl/callback", include_in_schema=False)
async def simkl_oauth_callback(
    code: str | None = Query(None, description="OAuth authorization code"),
    error: str | None = Query(None, description="OAuth error code"),
    error_description: str | None = Query(None, description="OAuth error description"),
    state: str | None = Query(None, description="OAuth state parameter"),
):
    """Handle Simkl OAuth callback and redirect to Integrations page."""
    query_params: dict[str, str] = {"simkl_oauth": "1"}

    if code:
        query_params["simkl_code"] = code
    if error:
        query_params["simkl_error"] = error
    if error_description:
        query_params["simkl_error_description"] = error_description
    if state:
        query_params["simkl_state"] = state

    if not code and not error:
        query_params["simkl_error"] = "missing_code"
        query_params["simkl_error_description"] = "Missing authorization code in callback."

    host_url = settings.host_url.rstrip("/")
    if host_url.endswith("/app"):
        frontend_integrations_url = f"{host_url}/dashboard/integrations"
    else:
        frontend_integrations_url = f"{host_url}/app/dashboard/integrations"

    redirect_url = f"{frontend_integrations_url}?{urlencode(query_params)}"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/trakt/connect")
async def connect_trakt(
    data: TraktConnectRequest,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
):
    """Connect Trakt account using authorization code."""
    async with get_read_session_context() as read_session:
        await get_profile_for_user(read_session, user, profile_id)

    # Exchange code for token
    config = await exchange_trakt_code(data.code, data.client_id, data.client_secret)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect Trakt. Invalid or expired code.",
        )

    # Prepare credentials
    credentials = {
        "access_token": config.access_token,
        "refresh_token": config.refresh_token,
        "expires_at": config.expires_at,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    async with get_async_session_context() as write_session:
        # Check if integration already exists
        existing = await get_integration(write_session, profile_id, IntegrationType.TRAKT)

        if existing:
            # Update existing
            existing.encrypted_credentials = encrypt_credentials(credentials)
            existing.is_enabled = True
            existing.settings = {"min_watch_percent": config.min_watch_percent}
            write_session.add(existing)
        else:
            # Create new
            integration = ProfileIntegration(
                profile_id=profile_id,
                platform=IntegrationType.TRAKT,
                encrypted_credentials=encrypt_credentials(credentials),
                is_enabled=True,
                sync_direction="two_way",
                scrobble_enabled=True,
                settings={"min_watch_percent": 80},
            )
            write_session.add(integration)

        await write_session.commit()

    return {"message": "Trakt connected successfully", "platform": "trakt"}


@router.post("/simkl/connect")
async def connect_simkl(
    data: SimklConnectRequest,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
):
    """Connect Simkl account using authorization code."""
    async with get_read_session_context() as read_session:
        await get_profile_for_user(read_session, user, profile_id)

    # Exchange code for token
    config = await exchange_simkl_code(data.code, data.client_id, data.client_secret)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect Simkl. Invalid or expired code.",
        )

    # Prepare credentials
    credentials = {
        "access_token": config.access_token,
        "refresh_token": config.refresh_token,
        "expires_at": config.expires_at,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    async with get_async_session_context() as write_session:
        # Check if integration already exists
        existing = await get_integration(write_session, profile_id, IntegrationType.SIMKL)

        if existing:
            # Update existing
            existing.encrypted_credentials = encrypt_credentials(credentials)
            existing.is_enabled = True
            write_session.add(existing)
        else:
            # Create new
            integration = ProfileIntegration(
                profile_id=profile_id,
                platform=IntegrationType.SIMKL,
                encrypted_credentials=encrypt_credentials(credentials),
                is_enabled=True,
                sync_direction="two_way",
                scrobble_enabled=False,  # Simkl doesn't support real-time scrobbling
                settings={},
            )
            write_session.add(integration)

        await write_session.commit()

    return {"message": "Simkl connected successfully", "platform": "simkl"}


@router.delete("/{platform}/disconnect")
async def disconnect_integration(
    platform: IntegrationType,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Disconnect an integration."""
    await get_profile_for_user(session, user, profile_id)

    integration = await get_integration(session, profile_id, platform)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not connected",
        )

    await session.delete(integration)
    await session.commit()

    return {"message": f"{platform} disconnected successfully"}


# ============================================
# API Endpoints - Settings
# ============================================


@router.patch("/{platform}/settings")
async def update_integration_settings(
    platform: IntegrationType,
    data: IntegrationSettingsUpdate,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Update integration settings."""
    await get_profile_for_user(session, user, profile_id)

    integration = await get_integration(session, profile_id, platform)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not connected",
        )

    # Update fields
    if data.is_enabled is not None:
        integration.is_enabled = data.is_enabled
    if data.sync_direction is not None:
        integration.sync_direction = data.sync_direction
    if data.scrobble_enabled is not None:
        integration.scrobble_enabled = data.scrobble_enabled
    if data.settings is not None:
        # Merge with existing settings
        integration.settings = {**integration.settings, **data.settings}

    session.add(integration)
    await session.commit()

    return {"message": "Settings updated successfully"}


# ============================================
# API Endpoints - Sync Operations
# ============================================


@router.post("/{platform}/sync", response_model=SyncTriggerResponse)
async def trigger_sync(
    platform: IntegrationType,
    profile_id: int = Query(..., description="Profile ID"),
    direction: str | None = Query(None, description="Override sync direction"),
    full_sync: bool = Query(False, description="Perform full sync ignoring last_sync_at"),
    user: User = Depends(require_auth),
):
    """Trigger a sync operation for a platform.

    Args:
        full_sync: If True, fetches all history from the platform, not just since last sync.
    """
    async with get_async_session_context() as session:
        await get_profile_for_user(session, user, profile_id)
        integration = await get_integration(session, profile_id, platform)
        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{platform} not connected",
            )
        integration.last_sync_status = "in_progress"
        integration.last_sync_error = None
        integration.last_sync_stats = None
        session.add(integration)
        await session.commit()
        integration_id = integration.id
        credentials = decrypt_credentials(integration.encrypted_credentials)
        sync_dir = direction or integration.sync_direction

    result = None
    error_msg = None

    # Run sync inline and wait for completion (manual user action).
    try:
        async with get_async_session_context() as read_session:
            current_integration = await read_session.get(ProfileIntegration, integration_id)

        service = build_sync_service(current_integration, credentials)
        if service is None:
            logger.error(f"Sync not implemented for {platform}")
            return SyncTriggerResponse(
                message=f"Sync failed for {platform}: not implemented",
                sync_started=False,
            )

        sync_direction = None
        if sync_dir == "mf_to_platform":
            sync_direction = SyncDirection.MF_TO_PLATFORM
        elif sync_dir == "platform_to_mf":
            sync_direction = SyncDirection.PLATFORM_TO_MF
        elif sync_dir == "two_way":
            sync_direction = SyncDirection.BIDIRECTIONAL

        result = await service.sync(sync_direction, full_sync=full_sync)
        logger.info(f"Sync completed for {platform}: {result.to_dict()}")

    except Exception as e:
        logger.exception(f"Sync failed for {platform}: {e}")
        error_msg = str(e)

    # Update sync state in a separate session to avoid transaction issues.
    try:
        async with get_async_session_context() as update_session:
            fresh_integration = await get_integration(update_session, profile_id, platform)
            if fresh_integration:
                fresh_integration.last_sync_at = datetime.now(timezone.utc)
                if error_msg:
                    fresh_integration.last_sync_status = "failed"
                    fresh_integration.last_sync_error = error_msg
                    fresh_integration.last_sync_stats = None
                else:
                    fresh_integration.last_sync_status = "success"
                    fresh_integration.last_sync_error = None
                    if result:
                        fresh_integration.last_sync_stats = result.to_dict()
                update_session.add(fresh_integration)
                await update_session.commit()
    except Exception as update_error:
        logger.warning(f"Failed to update sync state for {platform}: {update_error}")

    if error_msg:
        return SyncTriggerResponse(
            message=f"Sync failed for {platform}: {error_msg}",
            sync_started=False,
        )

    return SyncTriggerResponse(
        message=f"Sync completed for {platform}",
        sync_started=True,
    )


@router.post("/sync-all", response_model=SyncTriggerResponse)
async def trigger_sync_all(
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
):
    """Trigger sync for all connected and enabled integrations."""
    async with get_async_session_context() as session:
        await get_profile_for_user(session, user, profile_id)
        query = select(ProfileIntegration).where(
            ProfileIntegration.profile_id == profile_id,
            ProfileIntegration.is_enabled == True,
        )
        result = await session.exec(query)
        enabled_integrations = result.all()
        enabled_integration_ids = [integration.id for integration in enabled_integrations]

    if not enabled_integrations:
        return SyncTriggerResponse(
            message="No enabled integrations to sync",
            sync_started=False,
        )

    async with get_async_session_context() as session:
        for integration_id in enabled_integration_ids:
            integration = await session.get(ProfileIntegration, integration_id)
            if not integration:
                continue
            integration.last_sync_status = "in_progress"
            integration.last_sync_error = None
            integration.last_sync_stats = None
            session.add(integration)
        await session.commit()

    success_count = 0
    failed_count = 0

    for integration_id in enabled_integration_ids:
        service = None
        platform_name = None
        sync_direction = None
        try:
            async with get_async_session_context() as sync_session:
                integration = await sync_session.get(ProfileIntegration, integration_id)
                if not integration or not integration.is_enabled:
                    continue
                credentials = decrypt_credentials(integration.encrypted_credentials)
                platform_name = integration.platform
                if integration.sync_direction == "mf_to_platform":
                    sync_direction = SyncDirection.MF_TO_PLATFORM
                elif integration.sync_direction == "platform_to_mf":
                    sync_direction = SyncDirection.PLATFORM_TO_MF
                elif integration.sync_direction == "two_way":
                    sync_direction = SyncDirection.BIDIRECTIONAL
                service = build_sync_service(integration, credentials)

            if service is None:
                logger.error(f"Sync not implemented for {platform_name}")
                failed_count += 1
                continue

            result = await service.sync(sync_direction)
            logger.info(f"Sync completed for {platform_name}: {result.to_dict()}")
            async with get_async_session_context() as update_session:
                integration = await update_session.get(ProfileIntegration, integration_id)
                if integration:
                    integration.last_sync_at = datetime.now(timezone.utc)
                    integration.last_sync_status = "success"
                    integration.last_sync_error = None
                    integration.last_sync_stats = result.to_dict()
                    update_session.add(integration)
                    await update_session.commit()
            success_count += 1
        except Exception as e:
            logger.exception(f"Sync failed for integration_id={integration_id}: {e}")
            async with get_async_session_context() as update_session:
                integration = await update_session.get(ProfileIntegration, integration_id)
                if integration:
                    integration.last_sync_at = datetime.now(timezone.utc)
                    integration.last_sync_status = "failed"
                    integration.last_sync_error = str(e)
                    integration.last_sync_stats = None
                    update_session.add(integration)
                    await update_session.commit()
            failed_count += 1

    return SyncTriggerResponse(
        message=f"Sync completed for {len(enabled_integrations)} platforms ({success_count} succeeded, {failed_count} failed)",
        sync_started=success_count > 0,
    )
