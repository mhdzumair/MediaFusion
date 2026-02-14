"""
External Platform Integrations API endpoints.

Manages OAuth connections and sync operations for external platforms
like Trakt, Simkl, MAL, etc.

Integrations are stored per-profile in the ProfileIntegration table,
separate from the Stremio config. Only authenticated users can have
integrations.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from api.services.sync import (
    SimklSyncService,
    TraktSyncService,
    exchange_simkl_code,
    exchange_trakt_code,
    get_simkl_auth_url,
    get_trakt_auth_url,
)
from db.database import get_async_session, get_read_session
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


@router.post("/trakt/connect")
async def connect_trakt(
    data: TraktConnectRequest,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Connect Trakt account using authorization code."""
    await get_profile_for_user(session, user, profile_id)

    # Exchange code for token
    config = await exchange_trakt_code(data.code, data.client_id, data.client_secret)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect Trakt. Invalid or expired code.",
        )

    # Check if integration already exists
    existing = await get_integration(session, profile_id, IntegrationType.TRAKT)

    # Prepare credentials
    credentials = {
        "access_token": config.access_token,
        "refresh_token": config.refresh_token,
        "expires_at": config.expires_at,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    if existing:
        # Update existing
        existing.encrypted_credentials = encrypt_credentials(credentials)
        existing.is_enabled = True
        existing.settings = {"min_watch_percent": config.min_watch_percent}
        session.add(existing)
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
        session.add(integration)

    await session.commit()

    return {"message": "Trakt connected successfully", "platform": "trakt"}


@router.post("/simkl/connect")
async def connect_simkl(
    data: SimklConnectRequest,
    profile_id: int = Query(..., description="Profile ID"),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Connect Simkl account using authorization code."""
    await get_profile_for_user(session, user, profile_id)

    # Exchange code for token
    config = await exchange_simkl_code(data.code, data.client_id, data.client_secret)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to connect Simkl. Invalid or expired code.",
        )

    # Check if integration already exists
    existing = await get_integration(session, profile_id, IntegrationType.SIMKL)

    # Prepare credentials
    credentials = {
        "access_token": config.access_token,
        "refresh_token": config.refresh_token,
        "expires_at": config.expires_at,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
    }

    if existing:
        # Update existing
        existing.encrypted_credentials = encrypt_credentials(credentials)
        existing.is_enabled = True
        session.add(existing)
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
        session.add(integration)

    await session.commit()

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
    background_tasks: BackgroundTasks = BackgroundTasks(),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Trigger a sync operation for a platform.

    Args:
        full_sync: If True, fetches all history from the platform, not just since last sync.
    """
    await get_profile_for_user(session, user, profile_id)

    integration = await get_integration(session, profile_id, platform)
    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{platform} not connected",
        )

    # Create sync service and run in background
    async def run_sync():
        from db.database import get_async_session_context

        result = None
        error_msg = None

        # Run sync in its own session
        try:
            async with get_async_session_context() as sync_session:
                # Get fresh integration data
                fresh_integration = await get_integration(sync_session, profile_id, platform)
                if not fresh_integration:
                    logger.error(f"Integration not found during sync: {platform}")
                    return

                credentials = decrypt_credentials(fresh_integration.encrypted_credentials)
                sync_dir = direction or fresh_integration.sync_direction

                if platform == IntegrationType.TRAKT:
                    from db.schemas.config import TraktConfig

                    config = TraktConfig(
                        access_token=credentials.get("access_token", ""),
                        refresh_token=credentials.get("refresh_token"),
                        expires_at=credentials.get("expires_at"),
                        client_id=credentials.get("client_id"),
                        client_secret=credentials.get("client_secret"),
                        sync_enabled=fresh_integration.is_enabled,
                        scrobble_enabled=fresh_integration.scrobble_enabled,
                        min_watch_percent=fresh_integration.settings.get("min_watch_percent", 80),
                    )
                    service = TraktSyncService(config, profile_id)
                elif platform == IntegrationType.SIMKL:
                    from db.schemas.config import SimklConfig

                    config = SimklConfig(
                        access_token=credentials.get("access_token", ""),
                        refresh_token=credentials.get("refresh_token"),
                        expires_at=credentials.get("expires_at"),
                        client_id=credentials.get("client_id"),
                        client_secret=credentials.get("client_secret"),
                        sync_enabled=fresh_integration.is_enabled,
                    )
                    service = SimklSyncService(config, profile_id)
                else:
                    logger.error(f"Sync not implemented for {platform}")
                    return

                # Map string direction to enum
                sync_direction = None
                if sync_dir == "mf_to_platform":
                    sync_direction = SyncDirection.MF_TO_PLATFORM
                elif sync_dir == "platform_to_mf":
                    sync_direction = SyncDirection.PLATFORM_TO_MF
                elif sync_dir == "two_way":
                    sync_direction = SyncDirection.BIDIRECTIONAL

                result = await service.sync(sync_session, sync_direction, full_sync=full_sync)
                logger.info(f"Sync completed for {platform}: {result.to_dict()}")

        except Exception as e:
            logger.exception(f"Sync failed for {platform}: {e}")
            error_msg = str(e)

        # Update sync state in a separate session to avoid transaction issues
        try:
            async with get_async_session_context() as update_session:
                fresh_integration = await get_integration(update_session, profile_id, platform)
                if fresh_integration:
                    fresh_integration.last_sync_at = datetime.utcnow()
                    if error_msg:
                        fresh_integration.last_sync_status = "failed"
                        fresh_integration.last_sync_error = error_msg
                    else:
                        fresh_integration.last_sync_status = "success"
                        fresh_integration.last_sync_error = None
                        if result:
                            fresh_integration.last_sync_stats = result.to_dict()
                    update_session.add(fresh_integration)
                    await update_session.commit()
        except Exception as update_error:
            logger.warning(f"Failed to update sync state for {platform}: {update_error}")

    background_tasks.add_task(run_sync)

    return SyncTriggerResponse(
        message=f"Sync started for {platform}",
        sync_started=True,
    )


@router.post("/sync-all", response_model=SyncTriggerResponse)
async def trigger_sync_all(
    profile_id: int = Query(..., description="Profile ID"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Trigger sync for all connected and enabled integrations."""
    await get_profile_for_user(session, user, profile_id)

    # Get all enabled integrations
    query = select(ProfileIntegration).where(
        ProfileIntegration.profile_id == profile_id,
        ProfileIntegration.is_enabled == True,
    )
    result = await session.exec(query)
    enabled_integrations = result.all()

    if not enabled_integrations:
        return SyncTriggerResponse(
            message="No enabled integrations to sync",
            sync_started=False,
        )

    # Run sync for each enabled platform
    async def run_all_syncs():
        from db.database import get_async_session_context

        async with get_async_session_context() as sync_session:
            for integration in enabled_integrations:
                try:
                    credentials = decrypt_credentials(integration.encrypted_credentials)

                    if integration.platform == IntegrationType.TRAKT:
                        from db.schemas.config import TraktConfig

                        config = TraktConfig(
                            access_token=credentials.get("access_token", ""),
                            refresh_token=credentials.get("refresh_token"),
                            expires_at=credentials.get("expires_at"),
                            client_id=credentials.get("client_id"),
                            client_secret=credentials.get("client_secret"),
                            sync_enabled=integration.is_enabled,
                            scrobble_enabled=integration.scrobble_enabled,
                            min_watch_percent=integration.settings.get("min_watch_percent", 80),
                        )
                        service = TraktSyncService(config, profile_id)
                    elif integration.platform == IntegrationType.SIMKL:
                        from db.schemas.config import SimklConfig

                        config = SimklConfig(
                            access_token=credentials.get("access_token", ""),
                            refresh_token=credentials.get("refresh_token"),
                            expires_at=credentials.get("expires_at"),
                            client_id=credentials.get("client_id"),
                            client_secret=credentials.get("client_secret"),
                            sync_enabled=integration.is_enabled,
                        )
                        service = SimklSyncService(config, profile_id)
                    else:
                        continue

                    result = await service.sync(sync_session)
                    logger.info(f"Sync completed for {integration.platform}: {result.to_dict()}")

                    # Update sync state
                    integration.last_sync_at = datetime.utcnow()
                    integration.last_sync_status = "success"
                    integration.last_sync_error = None
                    integration.last_sync_stats = result.to_dict()
                    sync_session.add(integration)

                except Exception as e:
                    logger.exception(f"Sync failed for {integration.platform}: {e}")
                    integration.last_sync_at = datetime.utcnow()
                    integration.last_sync_status = "failed"
                    integration.last_sync_error = str(e)
                    sync_session.add(integration)

            await sync_session.commit()

    background_tasks.add_task(run_all_syncs)

    return SyncTriggerResponse(
        message=f"Sync started for {len(enabled_integrations)} platforms",
        sync_started=True,
    )
