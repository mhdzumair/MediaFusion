"""Stream moderation endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_role
from db.database import get_async_session
from db.enums import UserRole
from db.models import (
    AceStreamStream,
    ExternalLinkStream,
    HTTPStream,
    Media,
    Stream,
    StreamMediaLink,
    StreamType,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    User,
    YouTubeStream,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Streams"])

STREAM_TYPE_MODEL_MAP = {
    StreamType.TORRENT: TorrentStream,
    StreamType.HTTP: HTTPStream,
    StreamType.YOUTUBE: YouTubeStream,
    StreamType.USENET: UsenetStream,
    StreamType.TELEGRAM: TelegramStream,
    StreamType.EXTERNAL_LINK: ExternalLinkStream,
    StreamType.ACESTREAM: AceStreamStream,
}


async def delete_stream_by_base_id(session: AsyncSession, stream_id: int) -> str:
    """Delete any stream type using base Stream.id."""
    base_stream = await session.get(Stream, stream_id)
    if not base_stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    # Update linked media total_streams and remove stream-media links.
    links_query = select(StreamMediaLink).where(StreamMediaLink.stream_id == stream_id)
    links_result = await session.exec(links_query)
    for link in links_result.all():
        media = await session.get(Media, link.media_id)
        if media and media.total_streams > 0:
            media.total_streams -= 1
            session.add(media)
        await session.delete(link)

    # Delete type-specific row first to satisfy FK constraints.
    model_cls = STREAM_TYPE_MODEL_MAP.get(base_stream.stream_type)
    if model_cls:
        type_row_query = select(model_cls).where(model_cls.stream_id == stream_id)
        type_row_result = await session.exec(type_row_query)
        type_row = type_row_result.first()
        if type_row:
            await session.delete(type_row)

    # Delete base stream (cascades remove language/quality links, files, etc).
    await session.delete(base_stream)
    await session.commit()

    return base_stream.stream_type.value


@router.delete("/streams/{stream_id}")
async def delete_stream(
    stream_id: int,  # Base Stream.id
    _moderator: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a stream of any type using base Stream.id (Moderator+)."""
    stream_type = await delete_stream_by_base_id(session, stream_id)
    stream_type_label = stream_type.replace("_", " ")
    logger.info(f"Moderator deleted {stream_type} stream {stream_id}")
    return {"message": f"{stream_type_label.capitalize()} stream deleted successfully"}
