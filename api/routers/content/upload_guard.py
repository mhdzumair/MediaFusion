"""Upload abuse guardrails for user contribution endpoints."""

import logging
from datetime import datetime, timedelta

import pytz
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.config import settings
from db.enums import UserRole
from db.models import Contribution, User
from db.redis_database import REDIS_ASYNC_CLIENT
from utils.email.service import get_email_service

logger = logging.getLogger(__name__)

_UPLOAD_RATE_LIMIT_TYPES = ("torrent", "nzb", "http", "youtube", "acestream", "telegram")


async def _send_upload_warning_email_once(user: User, reason_code: str, reason_text: str) -> None:
    """Send a warning email with cooldown to prevent email spam."""
    if not user.email:
        return

    cooldown_seconds = max(60, settings.upload_warning_email_cooldown_minutes * 60)
    cooldown_key = f"upload-warning-email:{reason_code}:{user.id}"

    try:
        recently_sent = await REDIS_ASYNC_CLIENT.get(cooldown_key)
    except Exception:
        logger.exception("Failed reading upload warning cooldown key for user %s", user.id)
        recently_sent = None

    if recently_sent:
        return

    email_service = get_email_service()
    if email_service is None:
        return

    try:
        await email_service.send_upload_warning_email(
            to=user.email,
            username=user.username,
            reason=reason_text,
        )
        await REDIS_ASYNC_CLIENT.set(cooldown_key, b"1", ex=cooldown_seconds)
    except Exception:
        logger.exception("Failed sending upload warning email to user %s", user.id)


async def enforce_upload_permissions(user: User, session: AsyncSession) -> None:
    """Raise HTTPException when a user is not allowed to upload content."""
    if user.role in (UserRole.MODERATOR, UserRole.ADMIN):
        return

    if user.uploads_restricted:
        await _send_upload_warning_email_once(
            user=user,
            reason_code="restricted",
            reason_text="Your account is currently restricted from uploading content.",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is restricted from uploading content. Please contact support.",
        )

    uploads_last_hour: int | None = None
    attempts_key = f"upload-attempts:{user.id}"
    try:
        attempts_value = await REDIS_ASYNC_CLIENT.incr(attempts_key)
        if attempts_value is not None:
            uploads_last_hour = int(attempts_value)
            ttl_value = await REDIS_ASYNC_CLIENT.ttl(attempts_key)
            if ttl_value in (None, -1):
                await REDIS_ASYNC_CLIENT.expire(attempts_key, 3600)
    except Exception:
        logger.exception("Failed to increment upload attempt counter for user %s", user.id)

    if uploads_last_hour is None:
        one_hour_ago = datetime.now(pytz.UTC) - timedelta(hours=1)
        uploads_count_query = select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.contribution_type.in_(_UPLOAD_RATE_LIMIT_TYPES),
            Contribution.created_at >= one_hour_ago,
        )
        uploads_count_result = await session.exec(uploads_count_query)
        uploads_last_hour = uploads_count_result.one()
        if uploads_last_hour >= settings.max_upload_contributions_per_hour:
            # DB fallback count excludes the current request.
            uploads_last_hour += 1

    if uploads_last_hour > settings.max_upload_contributions_per_hour:
        await _send_upload_warning_email_once(
            user=user,
            reason_code="rate-limit",
            reason_text=(
                f"Too many uploads were attempted in a short period. "
                f"Limit: {settings.max_upload_contributions_per_hour} uploads per hour."
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Upload rate limit reached. "
                f"Please wait before submitting more than {settings.max_upload_contributions_per_hour} uploads/hour."
            ),
        )
