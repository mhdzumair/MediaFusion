"""Admin routes for Telegram content management and recovery.

Provides endpoints for:
- Bot migration: Update file_ids when switching to a new bot
- Recovery: Rescrape backup channel to recover content
- Stats: View Telegram content statistics
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import select

from api.routers.user.auth import require_role
from db import crud
from db.config import settings
from db.database import get_async_session_context
from db.enums import UserRole
from db.models import TelegramStream, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["admin-telegram"])


class TelegramMigrationStats(BaseModel):
    """Statistics about Telegram content and migration status."""

    total_streams: int = Field(description="Total Telegram streams in database")
    with_file_unique_id: int = Field(description="Streams with file_unique_id (can be migrated)")
    without_file_unique_id: int = Field(description="Streams missing file_unique_id (cannot be auto-migrated)")
    with_backup: int = Field(description="Streams with backup channel copies")
    backup_channel_configured: bool = Field(description="Whether backup channel is configured")
    backup_channel_id: str | None = Field(description="Configured backup channel ID")


class MigrationRequest(BaseModel):
    """Request to update file_ids from backup channel scrape."""

    file_unique_id: str = Field(description="Universal file identifier to match")
    new_file_id: str = Field(description="New file_id from new bot")


class MigrationResult(BaseModel):
    """Result of a migration operation."""

    success: bool
    matched_streams: int = Field(description="Number of streams matched and updated")
    message: str


class BulkMigrationResult(BaseModel):
    """Result of bulk migration operation."""

    total_processed: int
    successful: int
    failed: int
    not_found: int
    details: list[dict] = Field(default_factory=list)


@router.get("/stats", response_model=TelegramMigrationStats)
async def get_telegram_stats(
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get statistics about Telegram content for migration planning.

    Requires admin role.
    """
    async with get_async_session_context() as session:
        # Total streams
        total_query = select(TelegramStream)
        total_result = await session.exec(total_query)
        all_streams = total_result.all()

        total_streams = len(all_streams)
        with_file_unique_id = sum(1 for s in all_streams if s.file_unique_id)
        with_backup = sum(1 for s in all_streams if s.backup_chat_id and s.backup_message_id)

        return TelegramMigrationStats(
            total_streams=total_streams,
            with_file_unique_id=with_file_unique_id,
            without_file_unique_id=total_streams - with_file_unique_id,
            with_backup=with_backup,
            backup_channel_configured=bool(settings.telegram_backup_channel_id),
            backup_channel_id=settings.telegram_backup_channel_id,
        )


@router.post("/migrate", response_model=MigrationResult)
async def migrate_single_stream(
    request: MigrationRequest,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update file_id for a single stream by file_unique_id.

    Used during bot migration when a new bot scrapes the backup channel
    and needs to update the database with new file_ids.

    Requires admin role.
    """
    async with get_async_session_context() as session:
        # Find stream by file_unique_id
        stream = await crud.get_telegram_stream_by_file_unique_id(session, request.file_unique_id)

        if not stream:
            return MigrationResult(
                success=False,
                matched_streams=0,
                message=f"No stream found with file_unique_id: {request.file_unique_id}",
            )

        # Update file_id
        updated = await crud.update_telegram_stream_file_id(
            session,
            file_unique_id=request.file_unique_id,
            new_file_id=request.new_file_id,
        )

        if updated:
            logger.info(f"Migrated stream file_id: {request.file_unique_id} -> new file_id")
            return MigrationResult(
                success=True,
                matched_streams=1,
                message="Successfully updated file_id",
            )
        else:
            return MigrationResult(
                success=False,
                matched_streams=0,
                message="Failed to update file_id",
            )


@router.post("/migrate/bulk", response_model=BulkMigrationResult)
async def migrate_bulk_streams(
    migrations: list[MigrationRequest],
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Bulk update file_ids for multiple streams.

    Used for efficient migration when a new bot scrapes the entire backup channel.
    Each item maps a file_unique_id to its new file_id.

    Requires admin role.
    """
    results = BulkMigrationResult(
        total_processed=len(migrations),
        successful=0,
        failed=0,
        not_found=0,
    )

    async with get_async_session_context() as session:
        for migration in migrations:
            try:
                # Check if stream exists
                stream = await crud.get_telegram_stream_by_file_unique_id(session, migration.file_unique_id)

                if not stream:
                    results.not_found += 1
                    results.details.append(
                        {
                            "file_unique_id": migration.file_unique_id,
                            "status": "not_found",
                        }
                    )
                    continue

                # Update file_id
                updated = await crud.update_telegram_stream_file_id(
                    session,
                    file_unique_id=migration.file_unique_id,
                    new_file_id=migration.new_file_id,
                )

                if updated:
                    results.successful += 1
                    results.details.append(
                        {
                            "file_unique_id": migration.file_unique_id,
                            "status": "success",
                        }
                    )
                else:
                    results.failed += 1
                    results.details.append(
                        {
                            "file_unique_id": migration.file_unique_id,
                            "status": "failed",
                        }
                    )

            except Exception as e:
                logger.exception(f"Error migrating {migration.file_unique_id}: {e}")
                results.failed += 1
                results.details.append(
                    {
                        "file_unique_id": migration.file_unique_id,
                        "status": "error",
                        "error": str(e),
                    }
                )

    logger.info(
        f"Bulk migration completed: {results.successful} successful, "
        f"{results.failed} failed, {results.not_found} not found"
    )

    return results


@router.get("/exportable")
async def get_exportable_streams(
    limit: int = 1000,
    offset: int = 0,
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Get Telegram streams with file_unique_id for export/migration.

    Returns streams that can be matched after bot migration.
    Export format is suitable for creating a recovery script.

    Requires admin role.
    """
    async with get_async_session_context() as session:
        query = select(TelegramStream).where(TelegramStream.file_unique_id.isnot(None)).offset(offset).limit(limit)
        result = await session.exec(query)
        streams = result.all()

        return {
            "count": len(streams),
            "offset": offset,
            "limit": limit,
            "streams": [
                {
                    "id": s.id,
                    "file_unique_id": s.file_unique_id,
                    "file_id": s.file_id,
                    "file_name": s.file_name,
                    "chat_id": s.chat_id,
                    "message_id": s.message_id,
                    "backup_chat_id": s.backup_chat_id,
                    "backup_message_id": s.backup_message_id,
                    "size": s.size,
                }
                for s in streams
            ],
        }
