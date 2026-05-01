"""
Contributions API endpoints for user-submitted metadata corrections and stream additions.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Literal

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import aliased
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.anonymous_utils import resolve_uploader_identity
from api.routers.user.auth import require_auth, require_role
from db.crud.media import get_media_by_external_id
from db.database import get_async_session, get_async_session_context, get_read_session, get_read_session_context
from db.retry_utils import run_db_read_with_primary_fallback
from db.enums import ContributionStatus, UserRole
from db.models import (
    AceStreamStream,
    Contribution,
    ContributionSettings,
    HTTPStream,
    Stream,
    StreamMediaLink,
    StreamSuggestion,
    TelegramStream,
    TorrentStream,
    UsenetStream,
    User,
    YouTubeStream,
)
from utils.notification_registry import send_pending_contribution_notification

router = APIRouter(prefix="/api/v1/contributions", tags=["Contributions"])
logger = logging.getLogger(__name__)

CONTRIBUTION_TYPES = ["metadata", "stream", "torrent", "telegram", "youtube", "nzb", "http", "acestream"]
_CONTRIBUTION_TYPE_PATTERN = "^(" + "|".join(CONTRIBUTION_TYPES) + ")$"
POINT_ELIGIBLE_IMPORT_TYPES = {"stream", "torrent", "telegram", "youtube", "nzb", "http", "acestream"}
PROCESSABLE_IMPORT_TYPES = {"torrent", "telegram", "youtube", "nzb", "http", "acestream"}


# ============================================
# Pydantic Schemas
# ============================================


class ContributionCreate(BaseModel):
    """Request schema for creating a contribution."""

    contribution_type: str = Field(..., pattern=_CONTRIBUTION_TYPE_PATTERN)
    target_id: str | None = None  # meta_id or stream_id
    data: dict[str, Any]  # The contribution data


class ContributionReview(BaseModel):
    """Request schema for reviewing a contribution."""

    status: ContributionStatus = Field(..., description="APPROVED or REJECTED")
    review_notes: str | None = None


class ContributionAdminFlagRequest(BaseModel):
    """Request schema for flagging an approved contribution for admin review."""

    reason: str | None = Field(None, max_length=1000)


class ContributionAdminRejectRequest(BaseModel):
    """Request schema for admin rejection of an already-approved contribution."""

    review_notes: str | None = Field(None, max_length=2000)


class ContributionBulkReviewRequest(BaseModel):
    """Request schema for bulk reviewing pending contributions."""

    action: Literal["approve", "reject"]
    contribution_type: str | None = Field(None, pattern=_CONTRIBUTION_TYPE_PATTERN)
    contribution_ids: list[str] | None = Field(
        default=None,
        description="Optional list of pending contribution IDs to review",
    )
    review_notes: str | None = None


class BulkContributionReviewResponse(BaseModel):
    """Bulk contribution review result counters."""

    approved: int
    rejected: int
    skipped: int


class ContributionResponse(BaseModel):
    """Response schema for a contribution."""

    id: str  # UUID string
    user_id: int | None
    username: str | None = None
    contribution_type: str
    target_id: str | None  # External ID (IMDb, TMDB)
    media_id: int | None = None  # Internal MediaFusion media ID
    mediafusion_id: str | None = None  # Canonical internal ID: mf:<media_id>
    data: dict[str, Any]
    status: str
    reviewed_by: str | None = None  # User ID string
    reviewer_name: str | None = None  # Reviewer's username or auto label
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    admin_review_requested: bool = False
    admin_review_requested_by: str | None = None
    admin_review_requested_at: datetime | None = None
    admin_review_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class ContributionListResponse(BaseModel):
    """Response schema for paginated contribution list."""

    items: list[ContributionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class ContributionContributorOption(BaseModel):
    """Single contributor option used by moderator filter UI."""

    key: str
    label: str
    user_id: int | None = None
    anonymous_display_name: str | None = None
    total: int
    pending: int
    approved: int
    rejected: int


class ContributionContributorListResponse(BaseModel):
    """List of contributors with contribution counters."""

    items: list[ContributionContributorOption]


class ContributionStats(BaseModel):
    """Contribution statistics response."""

    total_contributions: int
    pending: int
    approved: int
    rejected: int
    by_type: dict[str, int]


# ============================================
# Helper Functions
# ============================================


def contribution_to_response(
    contribution: Contribution,
    username: str | None = None,
    media_id: int | None = None,
    reviewer_name: str | None = None,
) -> ContributionResponse:
    """Convert a Contribution model to response schema."""
    return ContributionResponse(
        id=contribution.id,
        user_id=contribution.user_id,
        username=username,
        contribution_type=contribution.contribution_type,
        target_id=contribution.target_id,
        media_id=media_id,
        mediafusion_id=f"mf:{media_id}" if media_id is not None else None,
        data=contribution.data,
        status=contribution.status.value if hasattr(contribution.status, "value") else str(contribution.status),
        reviewed_by=contribution.reviewed_by,
        reviewer_name=reviewer_name,
        reviewed_at=contribution.reviewed_at,
        review_notes=contribution.review_notes,
        admin_review_requested=contribution.admin_review_requested,
        admin_review_requested_by=contribution.admin_review_requested_by,
        admin_review_requested_at=contribution.admin_review_requested_at,
        admin_review_reason=contribution.admin_review_reason,
        created_at=contribution.created_at,
        updated_at=contribution.updated_at,
    )


async def get_username_map(session: AsyncSession, user_ids: set[int]) -> dict[int, str | None]:
    """Get a map of user_id -> username for contribution lists."""
    if not user_ids:
        return {}

    result = await session.exec(select(User.id, User.username).where(User.id.in_(user_ids)))
    rows = result.all()
    return {user_id: username for user_id, username in rows}


async def get_contribution_username(session: AsyncSession, user_id: int | None) -> str | None:
    """Get username for a single contribution's user_id."""
    if user_id is None:
        return None

    result = await session.exec(select(User.username).where(User.id == user_id))
    return result.first()


async def get_reviewer_name_map(session: AsyncSession, reviewed_by_values: set[str]) -> dict[str, str]:
    """Build reviewed_by value to reviewer display name map."""
    if not reviewed_by_values:
        return {}

    reviewer_map: dict[str, str] = {}
    reviewer_ids: set[int] = set()

    for reviewed_by in reviewed_by_values:
        if reviewed_by == "auto":
            reviewer_map[reviewed_by] = "Auto-approved"
            continue
        try:
            reviewer_ids.add(int(reviewed_by))
        except (TypeError, ValueError):
            reviewer_map[reviewed_by] = reviewed_by

    if reviewer_ids:
        result = await session.exec(select(User.id, User.username).where(User.id.in_(reviewer_ids)))
        for reviewer_id, username in result.all():
            reviewer_map[str(reviewer_id)] = username or f"User #{reviewer_id}"

    return reviewer_map


async def get_reviewer_name(session: AsyncSession, reviewed_by: str | None) -> str | None:
    """Resolve reviewer label from reviewed_by value."""
    if not reviewed_by:
        return None
    reviewer_map = await get_reviewer_name_map(session, {reviewed_by})
    return reviewer_map.get(reviewed_by)


def _normalize_filter_query(value: str | None) -> str | None:
    """Normalize optional query string filters."""
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def _parse_contributor_filter(value: str) -> tuple[int | None, str | None]:
    """Parse contributor filter key into user-id or anonymous display name."""
    normalized_value = value.strip()
    if not normalized_value:
        return None, None

    if normalized_value.startswith("user:"):
        raw_user_id = normalized_value.split(":", 1)[1].strip()
        if not raw_user_id.isdigit():
            raise ValueError("Invalid contributor filter: user id must be numeric.")
        return int(raw_user_id), None

    if normalized_value.startswith("anon:"):
        anonymous_name = normalized_value.split(":", 1)[1].strip()
        if not anonymous_name:
            raise ValueError("Invalid contributor filter: anonymous label is required.")
        return None, anonymous_name

    if normalized_value.isdigit():
        return int(normalized_value), None

    return None, normalized_value


def _extract_contribution_meta_candidates(contribution: Contribution) -> list[str]:
    """Collect potential media identifiers from contribution payload."""
    data = contribution.data if isinstance(contribution.data, dict) else {}
    candidates: list[str] = []
    for raw_value in (
        data.get("mediafusion_id"),
        data.get("meta_id"),
        contribution.target_id,
    ):
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value and value not in candidates:
                candidates.append(value)
    return candidates


async def resolve_contribution_media_id(session: AsyncSession, contribution: Contribution) -> int | None:
    """Resolve contribution identifiers to internal MediaFusion media ID."""
    for candidate in _extract_contribution_meta_candidates(contribution):
        media = await get_media_by_external_id(session, candidate)
        if media:
            return media.id

    # Fallback: resolve via the stream linked to this contribution.
    # This covers imports where no meta_id/target_id was provided (common for sports/events).
    stream = await _resolve_stream_for_contribution(session, contribution)
    if stream:
        result = await session.exec(
            select(StreamMediaLink.media_id)
            .where(StreamMediaLink.stream_id == stream.id)
            .order_by(col(StreamMediaLink.id))
        )
        linked_media_id = result.first()
        if linked_media_id is not None:
            return linked_media_id

    return None


async def process_telegram_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User | None,
) -> dict:
    """Publish an already-created Telegram stream when a contribution is approved."""
    del user  # Telegram stream promotion does not require user context.

    file_unique_id = contribution_data.get("file_unique_id")
    file_id = contribution_data.get("file_id")

    if not file_unique_id and not file_id:
        return {"status": "error", "message": "Missing Telegram file identifier in contribution data"}

    query = select(TelegramStream)
    if file_unique_id:
        query = query.where(TelegramStream.file_unique_id == file_unique_id)
    else:
        query = query.where(TelegramStream.file_id == file_id)

    result = await session.exec(query)
    telegram_stream = result.first()
    if not telegram_stream:
        return {"status": "error", "message": "Telegram stream not found for this contribution"}

    stream = await session.get(Stream, telegram_stream.stream_id)
    if not stream:
        return {"status": "error", "message": "Telegram stream record is missing"}

    if not stream.is_public:
        stream.is_public = True
        await session.flush()

    return {"status": "success", "stream_id": stream.id}


def _append_review_note(existing_notes: str | None, note: str) -> str:
    """Append a system-generated note preserving optional moderator notes."""
    return f"{existing_notes or ''}\n{note}".strip()


def _extract_stream_id_from_review_notes(review_notes: str | None) -> int | None:
    """Extract a stream_id marker from review notes if present."""
    if not review_notes:
        return None

    match = re.search(r"stream_id=(\d+)", review_notes)
    if not match:
        return None

    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


async def _resolve_stream_for_contribution(session: AsyncSession, contribution: Contribution) -> Stream | None:
    """Resolve the stream created/affected by a contribution."""
    stream_id = _extract_stream_id_from_review_notes(contribution.review_notes)
    if stream_id:
        stream = await session.get(Stream, stream_id)
        if stream:
            return stream

    data = contribution.data if isinstance(contribution.data, dict) else {}
    contribution_type = contribution.contribution_type
    stream_id = None

    if contribution_type == "torrent":
        info_hash = str(data.get("info_hash") or "").strip().lower()
        if info_hash:
            result = await session.exec(select(TorrentStream.stream_id).where(TorrentStream.info_hash == info_hash))
            stream_id = result.first()
    elif contribution_type == "nzb":
        nzb_guid = str(data.get("nzb_guid") or "").strip()
        if nzb_guid:
            result = await session.exec(select(UsenetStream.stream_id).where(UsenetStream.nzb_guid == nzb_guid))
            stream_id = result.first()
    elif contribution_type == "http":
        url = str(data.get("url") or "").strip()
        if url:
            media_id = await resolve_contribution_media_id(session, contribution)
            http_query = select(HTTPStream.stream_id).where(HTTPStream.url == url)
            if media_id is not None:
                http_query = http_query.join(StreamMediaLink, StreamMediaLink.stream_id == HTTPStream.stream_id).where(
                    StreamMediaLink.media_id == media_id
                )
            http_query = http_query.order_by(col(HTTPStream.stream_id).desc())
            result = await session.exec(http_query)
            stream_id = result.first()
    elif contribution_type == "youtube":
        video_id = str(data.get("video_id") or "").strip()
        if video_id:
            result = await session.exec(select(YouTubeStream.stream_id).where(YouTubeStream.video_id == video_id))
            stream_id = result.first()
    elif contribution_type == "acestream":
        content_id = str(data.get("content_id") or "").strip().lower()
        info_hash = str(data.get("info_hash") or "").strip().lower()
        if content_id:
            result = await session.exec(
                select(AceStreamStream.stream_id).where(AceStreamStream.content_id == content_id)
            )
            stream_id = result.first()
        if stream_id is None and info_hash:
            result = await session.exec(select(AceStreamStream.stream_id).where(AceStreamStream.info_hash == info_hash))
            stream_id = result.first()
    elif contribution_type == "telegram":
        file_unique_id = str(data.get("file_unique_id") or "").strip()
        file_id = str(data.get("file_id") or "").strip()
        query = select(TelegramStream.stream_id)
        if file_unique_id:
            query = query.where(TelegramStream.file_unique_id == file_unique_id)
        elif file_id:
            query = query.where(TelegramStream.file_id == file_id)
        else:
            query = None
        if query is not None:
            result = await session.exec(query)
            stream_id = result.first()

    if stream_id is None:
        return None

    return await session.get(Stream, stream_id)


async def get_contribution_settings(session: AsyncSession) -> ContributionSettings:
    """Get contribution settings row or create the default one."""
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    settings = result.first()
    if settings:
        return settings

    settings = ContributionSettings(id="default")
    session.add(settings)
    await session.flush()
    return settings


def calculate_contribution_level(points: int, settings: ContributionSettings) -> str:
    """Map contribution points to the configured level."""
    if points >= settings.expert_threshold:
        return "expert"
    if points >= settings.trusted_threshold:
        return "trusted"
    if points >= settings.contributor_threshold:
        return "contributor"
    return "new"


async def award_import_approval_points(
    session: AsyncSession,
    user_id: int | None,
    contribution_type: str,
    logger: logging.Logger | None = None,
) -> bool:
    """Award points for approved import contributions when eligible."""
    if user_id is None or contribution_type not in POINT_ELIGIBLE_IMPORT_TYPES:
        return False

    user = await session.get(User, user_id)
    if not user:
        if logger:
            logger.warning("Skipping points award: user_id=%s not found", user_id)
        return False

    settings = await get_contribution_settings(session)
    user.contribution_points = max(0, user.contribution_points + settings.points_per_stream_edit)
    user.stream_edits_approved += 1
    user.contribution_level = calculate_contribution_level(user.contribution_points, settings)
    session.add(user)

    if logger:
        logger.info(
            "Awarded %s points for %s contribution to user_id=%s",
            settings.points_per_stream_edit,
            contribution_type,
            user_id,
        )

    return True


def get_import_processor(contribution_type: str):
    """Return the import processor for a contribution type.

    Imports are local to avoid circular imports with dedicated import routers.
    """
    if contribution_type == "torrent":
        from api.routers.content.torrent_import import process_torrent_import  # noqa: PLC0415

        return process_torrent_import
    if contribution_type == "nzb":
        from api.routers.content.nzb_import import process_nzb_import  # noqa: PLC0415

        return process_nzb_import
    if contribution_type == "youtube":
        from api.routers.content.youtube_import import process_youtube_import  # noqa: PLC0415

        return process_youtube_import
    if contribution_type == "http":
        from api.routers.content.http_import import process_http_import  # noqa: PLC0415

        return process_http_import
    if contribution_type == "acestream":
        from api.routers.content.acestream_import import process_acestream_import  # noqa: PLC0415

        return process_acestream_import
    if contribution_type == "telegram":
        return process_telegram_import
    return None


async def _apply_contribution_review(
    session: AsyncSession,
    contribution: Contribution,
    review_status: ContributionStatus,
    reviewer: User,
    review_notes: str | None,
    logger: logging.Logger,
) -> None:
    """Apply review decision and optional import processing to a contribution.

    Raises:
        AdultContentNotAllowedError: when the import processor detects adult content.
            The contribution is left untouched so the caller can surface a 4xx error
            or skip it in bulk review without persisting a bogus approval.
    """
    from api.routers.content.torrent_import import AdultContentNotAllowedError  # noqa: PLC0415

    now = datetime.now(pytz.UTC)
    reviewer_id = str(reviewer.id)

    if review_status != ContributionStatus.APPROVED:
        contribution.status = review_status
        contribution.reviewed_by = reviewer_id
        contribution.reviewed_at = now
        contribution.review_notes = review_notes
        return

    # Approval path: run the import processor before mutating the contribution so that
    # a hard failure (e.g. adult-content policy) leaves it untouched and can propagate.
    final_review_notes = review_notes
    contribution_data_update: dict[str, Any] | None = None

    if contribution.contribution_type in PROCESSABLE_IMPORT_TYPES:
        process_fn = get_import_processor(contribution.contribution_type)
        if process_fn is None:
            raise ValueError(f"Unsupported contribution type: {contribution.contribution_type}")

        # Anonymous contributions have no contributor user_id by design.
        contributor = await session.get(User, contribution.user_id) if contribution.user_id is not None else None
        contribution_data_update = dict(contribution.data or {})
        contribution_data_update["is_public"] = True

        try:
            import_result = await process_fn(session, contribution_data_update, contributor)
        except AdultContentNotAllowedError:
            # Do not mutate contribution state — propagate so the caller can return 4xx
            # or skip this item in a bulk review.
            raise
        except Exception as e:
            logger.exception(f"Failed to process contribution import on approval: {e}")
            final_review_notes = _append_review_note(review_notes, f"Import processing failed: {str(e)}")
        else:
            if import_result.get("status") == "success":
                final_review_notes = _append_review_note(
                    review_notes,
                    f"Import successful: stream_id={import_result.get('stream_id')}",
                )
            elif import_result.get("status") == "exists":
                final_review_notes = _append_review_note(review_notes, "Content already exists in database")

    contribution.status = ContributionStatus.APPROVED
    contribution.reviewed_by = reviewer_id
    contribution.reviewed_at = now
    contribution.review_notes = final_review_notes
    if contribution_data_update is not None:
        contribution.data = contribution_data_update

    await award_import_approval_points(
        session,
        contribution.user_id,
        contribution.contribution_type,
        logger,
    )


# ============================================
# API Endpoints - User
# ============================================


@router.get("", response_model=ContributionListResponse)
async def list_contributions(
    contribution_type: str | None = Query(None, pattern=_CONTRIBUTION_TYPE_PATTERN),
    contribution_status: ContributionStatus | None = Query(None),
    contributor: str | None = Query(None, max_length=180),
    uploader_query: str | None = Query(None, max_length=120),
    reviewer_query: str | None = Query(None, max_length=120),
    me_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_auth),
):
    """
    List user's contributions with pagination.
    Regular users see only their own contributions.
    Moderators and admins can see all contributions.
    """
    # Build base query - moderators+ see all, others see only their own
    is_mod_or_admin = user.role in [UserRole.MODERATOR, UserRole.ADMIN]

    query = select(Contribution)
    count_query = select(func.count(Contribution.id))

    if not is_mod_or_admin or me_only:
        query = query.where(Contribution.user_id == user.id)
        count_query = count_query.where(Contribution.user_id == user.id)

    if contribution_type:
        query = query.where(Contribution.contribution_type == contribution_type)
        count_query = count_query.where(Contribution.contribution_type == contribution_type)

    if contribution_status:
        query = query.where(Contribution.status == contribution_status)
        count_query = count_query.where(Contribution.status == contribution_status)

    anonymous_display_name_expr = cast(Contribution.data["anonymous_display_name"], String)
    anonymous_uploader_label_expr = func.coalesce(func.nullif(func.trim(anonymous_display_name_expr), ""), "Anonymous")

    normalized_contributor_filter = _normalize_filter_query(contributor)
    if normalized_contributor_filter:
        try:
            contributor_user_id, contributor_anon_name = _parse_contributor_filter(normalized_contributor_filter)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

        if contributor_user_id is not None:
            contributor_condition = Contribution.user_id == contributor_user_id
        else:
            contributor_condition = and_(
                Contribution.user_id.is_(None),
                anonymous_uploader_label_expr.ilike(contributor_anon_name or "Anonymous"),
            )

        query = query.where(contributor_condition)
        count_query = count_query.where(contributor_condition)

    normalized_uploader_query = _normalize_filter_query(uploader_query)
    if normalized_uploader_query:
        uploader_alias = aliased(User)
        query = query.outerjoin(uploader_alias, Contribution.user_id == uploader_alias.id)
        count_query = count_query.outerjoin(uploader_alias, Contribution.user_id == uploader_alias.id)

        if normalized_uploader_query.isdigit():
            uploader_id = int(normalized_uploader_query)
            uploader_condition = or_(
                Contribution.user_id == uploader_id,
                uploader_alias.username.ilike(f"%{normalized_uploader_query}%"),
                anonymous_display_name_expr.ilike(f"%{normalized_uploader_query}%"),
            )
        else:
            uploader_condition = or_(
                uploader_alias.username.ilike(f"%{normalized_uploader_query}%"),
                anonymous_display_name_expr.ilike(f"%{normalized_uploader_query}%"),
            )

        query = query.where(uploader_condition)
        count_query = count_query.where(uploader_condition)

    normalized_reviewer_query = _normalize_filter_query(reviewer_query)
    if normalized_reviewer_query:
        if normalized_reviewer_query.lower() == "auto":
            query = query.where(Contribution.reviewed_by == "auto")
            count_query = count_query.where(Contribution.reviewed_by == "auto")
        else:
            reviewer_alias = aliased(User)
            query = query.join(reviewer_alias, cast(reviewer_alias.id, String) == Contribution.reviewed_by)
            count_query = count_query.join(reviewer_alias, cast(reviewer_alias.id, String) == Contribution.reviewed_by)

            if normalized_reviewer_query.isdigit():
                reviewer_id = int(normalized_reviewer_query)
                reviewer_condition = or_(
                    Contribution.reviewed_by == str(reviewer_id),
                    reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%"),
                )
            else:
                reviewer_condition = reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%")

            query = query.where(reviewer_condition)
            count_query = count_query.where(reviewer_condition)

    offset = (page - 1) * page_size
    paginated_query = query.order_by(col(Contribution.created_at).desc()).offset(offset).limit(page_size)

    async def _execute(session: AsyncSession) -> ContributionListResponse:
        total_result = await session.exec(count_query)
        total = total_result.one()

        result = await session.exec(paginated_query)
        items = result.all()

        username_map = await get_username_map(session, {item.user_id for item in items if item.user_id is not None})
        reviewer_name_map = await get_reviewer_name_map(
            session, {item.reviewed_by for item in items if item.reviewed_by}
        )
        resolved_media_ids = await asyncio.gather(*(resolve_contribution_media_id(session, item) for item in items))

        return ContributionListResponse(
            items=[
                contribution_to_response(
                    item,
                    username_map.get(item.user_id),
                    resolved_media_ids[idx],
                    reviewer_name_map.get(item.reviewed_by) if item.reviewed_by else None,
                )
                for idx, item in enumerate(items)
            ],
            total=total,
            page=page,
            page_size=page_size,
            has_more=(offset + len(items)) < total,
        )

    async def _read():
        async with get_read_session_context() as session:
            return await _execute(session)

    async def _primary():
        async with get_async_session_context() as session:
            return await _execute(session)

    return await run_db_read_with_primary_fallback(
        _read,
        _primary,
        operation_name="list_contributions",
    )


@router.get("/contributors", response_model=ContributionContributorListResponse)
async def list_contribution_contributors(
    contribution_type: str | None = Query(None, pattern=_CONTRIBUTION_TYPE_PATTERN),
    contribution_status: ContributionStatus | None = Query(None),
    query: str | None = Query(None, max_length=120),
    limit: int = Query(80, ge=1, le=200),
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_read_session),
):
    """List contributors so moderators can quickly filter contribution feeds."""
    del user  # Access control handled by dependency.

    base_filters = []
    if contribution_type:
        base_filters.append(Contribution.contribution_type == contribution_type)
    if contribution_status:
        base_filters.append(Contribution.status == contribution_status)

    normalized_query = _normalize_filter_query(query)
    anonymous_display_name_expr = cast(Contribution.data["anonymous_display_name"], String)
    anonymous_label_expr = func.coalesce(func.nullif(func.trim(anonymous_display_name_expr), ""), "Anonymous")
    user_total_count_expr = func.count(Contribution.id)
    anonymous_total_count_expr = func.count(Contribution.id)

    user_contributors_query = (
        select(
            Contribution.user_id.label("user_id"),
            User.username.label("username"),
            user_total_count_expr.label("total"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.PENDING).label("pending"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.APPROVED).label("approved"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.REJECTED).label("rejected"),
        )
        .join(User, User.id == Contribution.user_id)
        .where(Contribution.user_id.is_not(None), *base_filters)
        .group_by(Contribution.user_id, User.username)
        .order_by(user_total_count_expr.desc(), User.username.asc())
        .limit(limit)
    )
    if normalized_query:
        user_contributors_query = user_contributors_query.where(
            or_(
                User.username.ilike(f"%{normalized_query}%"),
                cast(User.id, String).ilike(f"%{normalized_query}%"),
            )
        )

    user_contributors_result = await session.exec(user_contributors_query)
    user_rows = user_contributors_result.all()

    anonymous_contributors_query = (
        select(
            anonymous_label_expr.label("anonymous_name"),
            anonymous_total_count_expr.label("total"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.PENDING).label("pending"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.APPROVED).label("approved"),
            func.count(Contribution.id).filter(Contribution.status == ContributionStatus.REJECTED).label("rejected"),
        )
        .where(Contribution.user_id.is_(None), *base_filters)
        .group_by(anonymous_label_expr)
        .order_by(anonymous_total_count_expr.desc(), anonymous_label_expr.asc())
        .limit(limit)
    )
    if normalized_query:
        anonymous_contributors_query = anonymous_contributors_query.where(
            anonymous_label_expr.ilike(f"%{normalized_query}%")
        )

    anonymous_contributors_result = await session.exec(anonymous_contributors_query)
    anonymous_rows = anonymous_contributors_result.all()

    contributors: list[ContributionContributorOption] = []
    for row in user_rows:
        label = row.username or f"User #{row.user_id}"
        contributors.append(
            ContributionContributorOption(
                key=f"user:{row.user_id}",
                label=label,
                user_id=row.user_id,
                anonymous_display_name=None,
                total=int(row.total or 0),
                pending=int(row.pending or 0),
                approved=int(row.approved or 0),
                rejected=int(row.rejected or 0),
            )
        )

    for row in anonymous_rows:
        anonymous_name = str(row.anonymous_name or "Anonymous")
        label = "Anonymous" if anonymous_name == "Anonymous" else f"Anonymous: {anonymous_name}"
        contributors.append(
            ContributionContributorOption(
                key=f"anon:{anonymous_name}",
                label=label,
                user_id=None,
                anonymous_display_name=anonymous_name,
                total=int(row.total or 0),
                pending=int(row.pending or 0),
                approved=int(row.approved or 0),
                rejected=int(row.rejected or 0),
            )
        )

    contributors.sort(key=lambda item: (-item.total, item.label.lower()))
    return ContributionContributorListResponse(items=contributors[:limit])


@router.get("/stats", response_model=ContributionStats)
async def get_contribution_stats(
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get contribution statistics for the current user (including stream suggestions)."""

    # ====== Contribution model counts ======
    contrib_total_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.user_id == user.id)
    )
    contrib_total = contrib_total_result.one()

    contrib_pending_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.PENDING,
        )
    )
    contrib_pending = contrib_pending_result.one()

    contrib_approved_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.APPROVED,
        )
    )
    contrib_approved = contrib_approved_result.one()

    contrib_rejected_result = await session.exec(
        select(func.count(Contribution.id)).where(
            Contribution.user_id == user.id,
            Contribution.status == ContributionStatus.REJECTED,
        )
    )
    contrib_rejected = contrib_rejected_result.one()

    # ====== StreamSuggestion model counts ======
    stream_total_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.user_id == user.id)
    )
    stream_total = stream_total_result.one()

    stream_pending_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status == "pending",
        )
    )
    stream_pending = stream_pending_result.one()

    stream_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status.in_(["approved", "auto_approved"]),
        )
    )
    stream_approved = stream_approved_result.one()

    stream_rejected_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(
            StreamSuggestion.user_id == user.id,
            StreamSuggestion.status == "rejected",
        )
    )
    stream_rejected = stream_rejected_result.one()

    # ====== By type ======
    by_type = {}
    for ctype in CONTRIBUTION_TYPES:
        type_result = await session.exec(
            select(func.count(Contribution.id)).where(
                Contribution.user_id == user.id,
                Contribution.contribution_type == ctype,
            )
        )
        by_type[ctype] = type_result.one()

    by_type["stream_suggestions"] = stream_total

    # ====== Combined totals ======
    return ContributionStats(
        total_contributions=contrib_total + stream_total,
        pending=contrib_pending + stream_pending,
        approved=contrib_approved + stream_approved,
        rejected=contrib_rejected + stream_rejected,
        by_type=by_type,
    )


@router.get("/{contribution_id}", response_model=ContributionResponse)
async def get_contribution(
    contribution_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """Get a specific contribution by ID."""
    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    # Only owner or moderators+ can view
    is_mod_or_admin = user.role in [UserRole.MODERATOR, UserRole.ADMIN]
    if contribution.user_id != user.id and not is_mod_or_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this contribution",
        )

    username = await get_contribution_username(session, contribution.user_id)
    reviewer_name = await get_reviewer_name(session, contribution.reviewed_by)
    media_id = await resolve_contribution_media_id(session, contribution)
    return contribution_to_response(contribution, username, media_id, reviewer_name)


@router.post("", response_model=ContributionResponse, status_code=status.HTTP_201_CREATED)
async def create_contribution(
    data: ContributionCreate,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """Submit a new contribution.

    Torrent and stream imports are auto-approved if the user is active.
    Metadata contributions require manual review.
    """
    is_anonymous = data.data.get("is_anonymous") is True

    # Moderators/admins auto-approve regardless of anonymity.
    # Other users auto-approve torrent/stream imports only when active and non-anonymous.
    is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
    should_auto_approve = is_privileged_reviewer or (
        (not is_anonymous)
        and user.is_active
        and data.contribution_type
        in (
            "torrent",
            "stream",
        )
    )

    initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING

    contribution = Contribution(
        user_id=None if is_anonymous else user.id,
        contribution_type=data.contribution_type,
        target_id=data.target_id,
        data=data.data,
        status=initial_status,
        admin_review_requested=False,
        # Mark auto-approved contributions
        reviewed_by="auto" if should_auto_approve else None,
        reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
        review_notes=(
            "Auto-approved: Privileged reviewer"
            if is_privileged_reviewer
            else ("Auto-approved: Active user content import" if should_auto_approve else None)
        ),
    )

    session.add(contribution)
    if should_auto_approve:
        await award_import_approval_points(
            session,
            contribution.user_id,
            contribution.contribution_type,
            logger,
        )
    await session.commit()
    await session.refresh(contribution)

    if contribution.status == ContributionStatus.PENDING:
        uploader_name, _ = resolve_uploader_identity(
            user,
            is_anonymous,
            data.data.get("anonymous_display_name"),
        )
        await send_pending_contribution_notification(
            {
                "contribution_id": contribution.id,
                "contribution_type": contribution.contribution_type,
                "target_id": contribution.target_id,
                "uploader_name": uploader_name,
                "data": contribution.data,
            }
        )

    media_id = await resolve_contribution_media_id(session, contribution)
    reviewer_name = await get_reviewer_name(session, contribution.reviewed_by)
    return contribution_to_response(
        contribution,
        user.username if contribution.user_id is not None else None,
        media_id,
        reviewer_name,
    )


@router.delete("/{contribution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contribution(
    contribution_id: str,
    user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a contribution.
    Users can only delete their own pending contributions.
    Admins can delete any contribution.
    """
    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    is_admin = user.role == UserRole.ADMIN
    is_owner = contribution.user_id == user.id
    is_pending = contribution.status == ContributionStatus.PENDING

    if not is_admin and not (is_owner and is_pending):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete this contribution. Only pending contributions can be deleted by their owner.",
        )

    await session.delete(contribution)
    await session.commit()


# ============================================
# API Endpoints - Moderator/Admin
# ============================================


@router.get("/review/pending", response_model=ContributionListResponse)
async def list_pending_contributions(
    contribution_type: str | None = Query(None, pattern=_CONTRIBUTION_TYPE_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_read_session),
):
    """List all pending contributions for review. Moderator+ only."""
    query = select(Contribution).where(Contribution.status == ContributionStatus.PENDING)
    count_query = select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.PENDING)

    if contribution_type:
        query = query.where(Contribution.contribution_type == contribution_type)
        count_query = count_query.where(Contribution.contribution_type == contribution_type)

    # Get total count
    total_result = await session.exec(count_query)
    total = total_result.one()

    # Get paginated results
    offset = (page - 1) * page_size
    query = query.order_by(col(Contribution.created_at).asc()).offset(offset).limit(page_size)
    result = await session.exec(query)
    items = result.all()

    username_map = await get_username_map(session, {item.user_id for item in items if item.user_id is not None})
    reviewer_name_map = await get_reviewer_name_map(session, {item.reviewed_by for item in items if item.reviewed_by})
    media_ids = await asyncio.gather(*(resolve_contribution_media_id(session, item) for item in items))

    return ContributionListResponse(
        items=[
            contribution_to_response(
                item,
                username_map.get(item.user_id),
                media_ids[idx],
                reviewer_name_map.get(item.reviewed_by) if item.reviewed_by else None,
            )
            for idx, item in enumerate(items)
        ],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(items)) < total,
    )


@router.patch("/{contribution_id}/review", response_model=ContributionResponse)
async def review_contribution(
    contribution_id: str,
    review: ContributionReview,
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Review a contribution (approve or reject). Moderator+ only.

    If approved, supported content-import contributions are processed and imported.
    """
    logger = logging.getLogger(__name__)

    contribution = await session.get(Contribution, contribution_id)

    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    if contribution.status != ContributionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Contribution already reviewed with status: {contribution.status}",
        )

    from api.routers.content.torrent_import import AdultContentNotAllowedError  # noqa: PLC0415

    try:
        await _apply_contribution_review(
            session,
            contribution,
            review.status,
            user,
            review.review_notes,
            logger,
        )
    except AdultContentNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    session.add(contribution)
    await session.commit()
    await session.refresh(contribution)

    username = await get_contribution_username(session, contribution.user_id)
    reviewer_name = await get_reviewer_name(session, contribution.reviewed_by)
    media_id = await resolve_contribution_media_id(session, contribution)
    return contribution_to_response(contribution, username, media_id, reviewer_name)


@router.patch("/{contribution_id}/flag-admin-review", response_model=ContributionResponse)
async def flag_contribution_for_admin_review(
    contribution_id: str,
    request: ContributionAdminFlagRequest,
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Flag an already-approved contribution for admin-only rejection review."""
    contribution = await session.get(Contribution, contribution_id)
    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    if contribution.status != ContributionStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved contributions can be flagged (current status: {contribution.status})",
        )

    contribution.admin_review_requested = True
    contribution.admin_review_requested_by = str(user.id)
    contribution.admin_review_requested_at = datetime.now(pytz.UTC)
    contribution.admin_review_reason = request.reason.strip() if request.reason else None
    session.add(contribution)
    await session.commit()
    await session.refresh(contribution)

    username = await get_contribution_username(session, contribution.user_id)
    reviewer_name = await get_reviewer_name(session, contribution.reviewed_by)
    media_id = await resolve_contribution_media_id(session, contribution)
    return contribution_to_response(contribution, username, media_id, reviewer_name)


@router.patch("/{contribution_id}/reject-approved", response_model=ContributionResponse)
async def reject_approved_contribution(
    contribution_id: str,
    request: ContributionAdminRejectRequest,
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Reject an approved contribution and make its imported stream non-public/inactive.

    Supports the new `/reject-approved` route and legacy `/admin-reject` alias.
    """
    contribution = await session.get(Contribution, contribution_id)
    if not contribution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contribution not found",
        )

    if contribution.status != ContributionStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved contributions can be rejected (current status: {contribution.status})",
        )

    stream = await _resolve_stream_for_contribution(session, contribution)
    rollback_note = "Moderation rejection rollback: no linked stream could be resolved."
    if stream:
        stream.is_public = False
        stream.is_active = False
        session.add(stream)
        rollback_note = (
            f"Moderation rejection rollback applied: stream_id={stream.id}, is_public=False, is_active=False."
        )

    contribution.status = ContributionStatus.REJECTED
    contribution.reviewed_by = str(user.id)
    contribution.reviewed_at = datetime.now(pytz.UTC)
    contribution.admin_review_requested = False
    contribution.review_notes = _append_review_note(contribution.review_notes, rollback_note)
    if request.review_notes:
        contribution.review_notes = _append_review_note(contribution.review_notes, request.review_notes.strip())
    session.add(contribution)

    await session.commit()
    await session.refresh(contribution)

    username = await get_contribution_username(session, contribution.user_id)
    reviewer_name = await get_reviewer_name(session, contribution.reviewed_by)
    media_id = await resolve_contribution_media_id(session, contribution)
    return contribution_to_response(contribution, username, media_id, reviewer_name)


@router.post("/review/bulk", response_model=BulkContributionReviewResponse)
async def bulk_review_contributions(
    request: ContributionBulkReviewRequest,
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk review pending contributions, optionally filtered by type or IDs."""
    from api.routers.content.torrent_import import AdultContentNotAllowedError  # noqa: PLC0415

    logger = logging.getLogger(__name__)
    review_status = ContributionStatus.APPROVED if request.action == "approve" else ContributionStatus.REJECTED

    query = select(Contribution).where(Contribution.status == ContributionStatus.PENDING)
    if request.contribution_type:
        query = query.where(Contribution.contribution_type == request.contribution_type)
    if request.contribution_ids:
        query = query.where(Contribution.id.in_(request.contribution_ids))

    result = await session.exec(query.order_by(col(Contribution.created_at).asc()))
    contributions = result.all()

    approved = 0
    rejected = 0
    skipped = 0

    for contribution in contributions:
        if contribution.status != ContributionStatus.PENDING:
            skipped += 1
            continue

        # Run each contribution inside a savepoint so that a hard failure
        # (e.g. adult content detected mid-import) rolls back any partial
        # rows added by the processor without aborting the rest of the batch.
        try:
            async with session.begin_nested():
                await _apply_contribution_review(
                    session,
                    contribution,
                    review_status,
                    user,
                    request.review_notes,
                    logger,
                )
                session.add(contribution)
        except AdultContentNotAllowedError as exc:
            logger.warning(
                "Skipping adult-content contribution %s during bulk review: %s",
                contribution.id,
                exc,
            )
            skipped += 1
            continue

        if review_status == ContributionStatus.APPROVED:
            approved += 1
        else:
            rejected += 1

    await session.commit()

    return BulkContributionReviewResponse(
        approved=approved,
        rejected=rejected,
        skipped=skipped,
    )


@router.get("/review/stats", response_model=ContributionStats)
async def get_all_contribution_stats(
    user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_read_session),
):
    """Get overall contribution statistics (including stream suggestions). Moderator+ only."""

    # ====== Contribution model counts ======
    contrib_total_result = await session.exec(select(func.count(Contribution.id)))
    contrib_total = contrib_total_result.one()

    contrib_pending_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.PENDING)
    )
    contrib_pending = contrib_pending_result.one()

    contrib_approved_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.APPROVED)
    )
    contrib_approved = contrib_approved_result.one()

    contrib_rejected_result = await session.exec(
        select(func.count(Contribution.id)).where(Contribution.status == ContributionStatus.REJECTED)
    )
    contrib_rejected = contrib_rejected_result.one()

    # ====== StreamSuggestion model counts ======
    stream_total_result = await session.exec(select(func.count(StreamSuggestion.id)))
    stream_total = stream_total_result.one()

    stream_pending_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == "pending")
    )
    stream_pending = stream_pending_result.one()

    stream_approved_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status.in_(["approved", "auto_approved"]))
    )
    stream_approved = stream_approved_result.one()

    stream_rejected_result = await session.exec(
        select(func.count(StreamSuggestion.id)).where(StreamSuggestion.status == "rejected")
    )
    stream_rejected = stream_rejected_result.one()

    # ====== By type ======
    by_type = {}
    for ctype in CONTRIBUTION_TYPES:
        type_result = await session.exec(
            select(func.count(Contribution.id)).where(Contribution.contribution_type == ctype)
        )
        by_type[ctype] = type_result.one()

    by_type["stream_suggestions"] = stream_total

    # ====== Combined totals ======
    return ContributionStats(
        total_contributions=contrib_total + stream_total,
        pending=contrib_pending + stream_pending,
        approved=contrib_approved + stream_approved,
        rejected=contrib_rejected + stream_rejected,
        by_type=by_type,
    )
