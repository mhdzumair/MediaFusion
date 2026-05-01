"""
Background tasks for periodic watch-history sync with external platforms.

Two actors:
  run_all_integration_syncs  — fan-out: enqueues one sync_one_integration job
                               per enabled ProfileIntegration row.
  sync_one_integration       — executes BaseSyncService.sync() for a single row.

The APScheduler cron job in api/scheduler.py enqueues run_all_integration_syncs
on a global crontab (settings.integration_sync_crontab).

build_sync_service is a shared factory also called by the manual /sync endpoint
in api/routers/user/integrations.py.
"""

import logging
from datetime import datetime, timezone

from sqlmodel import select

from api.services.sync.simkl import SimklSyncService
from api.services.sync.trakt import TraktSyncService
from api.task_queue import actor
from db.database import get_async_session_context
from db.enums import IntegrationType, SyncDirection
from db.models import ProfileIntegration
from db.schemas.config import SimklConfig, TraktConfig
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)


def build_sync_service(integration: ProfileIntegration, credentials: dict):
    """
    Build and return the correct BaseSyncService for the given integration.

    Returns None when the platform has no sync implementation.
    Callers must supply decrypted credentials (to avoid repeated DB crypto calls).
    """
    settings_dict = integration.settings or {}

    if integration.platform == IntegrationType.TRAKT:
        config = TraktConfig(
            access_token=credentials.get("access_token", ""),
            refresh_token=credentials.get("refresh_token"),
            expires_at=credentials.get("expires_at"),
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
            sync_enabled=integration.is_enabled,
            scrobble_enabled=integration.scrobble_enabled,
            min_watch_percent=settings_dict.get("min_watch_percent", 80),
        )
        return TraktSyncService(config, integration.profile_id)

    if integration.platform == IntegrationType.SIMKL:
        config = SimklConfig(
            access_token=credentials.get("access_token", ""),
            refresh_token=credentials.get("refresh_token"),
            expires_at=credentials.get("expires_at"),
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
            sync_enabled=integration.is_enabled,
        )
        return SimklSyncService(config, integration.profile_id)

    return None


def _direction_from_str(direction_str: str | None) -> SyncDirection | None:
    if direction_str == "mf_to_platform":
        return SyncDirection.MF_TO_PLATFORM
    if direction_str == "platform_to_mf":
        return SyncDirection.PLATFORM_TO_MF
    if direction_str == "two_way":
        return SyncDirection.BIDIRECTIONAL
    return None


@actor(priority=5, time_limit=30 * 60 * 1000, queue_name="default")
async def run_all_integration_syncs() -> None:
    """
    Fan-out actor: enqueues sync_one_integration for every enabled ProfileIntegration.
    Triggered by APScheduler on settings.integration_sync_crontab.
    """
    async with get_async_session_context() as session:
        query = select(ProfileIntegration.id).where(
            ProfileIntegration.is_enabled == True,
        )
        result = await session.exec(query)
        integration_ids = list(result.all())

    if not integration_ids:
        logger.debug("integration_sync: no enabled integrations found, skipping")
        return

    logger.info("integration_sync: enqueueing sync for %d integrations", len(integration_ids))
    for integration_id in integration_ids:
        await sync_one_integration.async_send(integration_id=integration_id)


@actor(priority=5, time_limit=20 * 60 * 1000, queue_name="default", max_retries=2)
async def sync_one_integration(integration_id: int) -> None:
    """
    Execute BaseSyncService.sync() for a single ProfileIntegration row.
    Updates last_sync_at / last_sync_status / last_sync_stats / last_sync_error.
    """
    # Load integration, capture all fields, and mark in_progress.
    async with get_async_session_context() as session:
        integration = await session.get(ProfileIntegration, integration_id)
        if not integration or not integration.is_enabled:
            return

        platform = integration.platform
        profile_id = integration.profile_id
        sync_direction = _direction_from_str(integration.sync_direction)
        scrobble_enabled = integration.scrobble_enabled
        settings_dict = dict(integration.settings or {})
        credentials = profile_crypto.decrypt_secrets(integration.encrypted_credentials)

        integration.last_sync_status = "in_progress"
        integration.last_sync_error = None
        integration.last_sync_stats = None
        session.add(integration)
        await session.commit()

    # Build a lightweight stand-in so build_sync_service doesn't need a live DB object.
    class _Proxy:
        pass

    proxy = _Proxy()
    proxy.platform = platform
    proxy.profile_id = profile_id
    proxy.is_enabled = True
    proxy.scrobble_enabled = scrobble_enabled
    proxy.settings = settings_dict

    service = build_sync_service(proxy, credentials)

    if service is None:
        logger.warning("integration_sync: no sync implementation for platform=%s", platform)
        async with get_async_session_context() as update_session:
            row = await update_session.get(ProfileIntegration, integration_id)
            if row:
                row.last_sync_status = "failed"
                row.last_sync_error = f"Sync not implemented for {platform}"
                update_session.add(row)
                await update_session.commit()
        return

    result = None
    error_msg = None
    try:
        result = await service.sync(sync_direction, full_sync=False)
        logger.info(
            "integration_sync: completed integration_id=%d platform=%s %s", integration_id, platform, result.to_dict()
        )
    except Exception as exc:
        logger.exception("integration_sync: failed integration_id=%d platform=%s: %s", integration_id, platform, exc)
        error_msg = str(exc)

    async with get_async_session_context() as update_session:
        row = await update_session.get(ProfileIntegration, integration_id)
        if row:
            row.last_sync_at = datetime.now(timezone.utc)
            if error_msg:
                row.last_sync_status = "failed"
                row.last_sync_error = error_msg
                row.last_sync_stats = None
            else:
                row.last_sync_status = "success"
                row.last_sync_error = None
                row.last_sync_stats = result.to_dict() if result else None
            update_session.add(row)
            await update_session.commit()
