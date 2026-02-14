"""
Contributions CRUD operations.

Handles StreamVote, MetadataVote, Contribution, MetadataSuggestion, StreamSuggestion.
"""

import logging
from collections.abc import Sequence
from datetime import datetime

import pytz
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from db.enums import ContributionStatus
from db.models import (
    Contribution,
    ContributionSettings,
    MetadataSuggestion,
    MetadataVote,
    Stream,
    StreamSuggestion,
    StreamVote,
)

logger = logging.getLogger(__name__)


# =============================================================================
# STREAM VOTE CRUD (Works for all stream types)
# =============================================================================


async def vote_on_stream(
    session: AsyncSession,
    user_id: int,
    stream_id: int,
    vote: int,  # 1 for upvote, -1 for downvote
) -> StreamVote:
    """Vote on a stream (upvote or downvote)."""
    # Check for existing vote
    query = select(StreamVote).where(
        StreamVote.user_id == user_id,
        StreamVote.stream_id == stream_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if existing:
        # Update existing vote
        old_vote = existing.vote
        if old_vote != vote:
            await session.exec(
                sa_update(StreamVote)
                .where(StreamVote.id == existing.id)
                .values(vote=vote, voted_at=datetime.now(pytz.UTC))
            )
            # Update stream vote count
            vote_diff = vote - old_vote
            await session.exec(
                sa_update(Stream).where(Stream.id == stream_id).values(vote_score=Stream.vote_score + vote_diff)
            )
        await session.flush()
        return existing

    # Create new vote
    stream_vote = StreamVote(
        user_id=user_id,
        stream_id=stream_id,
        vote=vote,
    )
    session.add(stream_vote)

    # Update stream vote count
    await session.exec(sa_update(Stream).where(Stream.id == stream_id).values(vote_score=Stream.vote_score + vote))

    await session.flush()
    return stream_vote


async def remove_stream_vote(
    session: AsyncSession,
    user_id: int,
    stream_id: int,
) -> bool:
    """Remove a vote on a stream."""
    # Get existing vote to adjust count
    query = select(StreamVote).where(
        StreamVote.user_id == user_id,
        StreamVote.stream_id == stream_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if not existing:
        return False

    # Remove vote and adjust count
    await session.exec(sa_delete(StreamVote).where(StreamVote.id == existing.id))
    await session.exec(
        sa_update(Stream).where(Stream.id == stream_id).values(vote_score=Stream.vote_score - existing.vote)
    )

    await session.flush()
    return True


async def get_user_stream_vote(
    session: AsyncSession,
    user_id: int,
    stream_id: int,
) -> StreamVote | None:
    """Get user's vote on a stream."""
    query = select(StreamVote).where(
        StreamVote.user_id == user_id,
        StreamVote.stream_id == stream_id,
    )
    result = await session.exec(query)
    return result.first()


async def get_stream_vote_count(
    session: AsyncSession,
    stream_id: int,
) -> dict:
    """Get vote statistics for a stream."""
    query = select(
        func.count(StreamVote.id).filter(StreamVote.vote_type == "up").label("upvotes"),
        func.count(StreamVote.id).filter(StreamVote.vote_type == "down").label("downvotes"),
    ).where(StreamVote.stream_id == stream_id)

    result = await session.exec(query)
    row = result.first()

    upvotes = row.upvotes if row else 0
    downvotes = row.downvotes if row else 0

    return {
        "upvotes": upvotes,
        "downvotes": downvotes,
        "score": upvotes - downvotes,
    }


# =============================================================================
# METADATA VOTE CRUD
# =============================================================================


async def vote_on_metadata(
    session: AsyncSession,
    user_id: int,
    media_id: int,
    vote: int,  # 1 for upvote, -1 for downvote
) -> MetadataVote:
    """Vote on metadata quality."""
    # Check for existing vote
    query = select(MetadataVote).where(
        MetadataVote.user_id == user_id,
        MetadataVote.media_id == media_id,
    )
    result = await session.exec(query)
    existing = result.first()

    if existing:
        # Update existing vote
        await session.exec(
            sa_update(MetadataVote)
            .where(MetadataVote.id == existing.id)
            .values(vote=vote, voted_at=datetime.now(pytz.UTC))
        )
        await session.flush()
        return existing

    # Create new vote
    metadata_vote = MetadataVote(
        user_id=user_id,
        media_id=media_id,
        vote=vote,
    )
    session.add(metadata_vote)
    await session.flush()
    return metadata_vote


# =============================================================================
# CONTRIBUTION CRUD
# =============================================================================


async def create_contribution(
    session: AsyncSession,
    user_id: int,
    contribution_type: str,
    *,
    media_id: int | None = None,
    stream_id: int | None = None,
    data: dict | None = None,
    status: ContributionStatus = ContributionStatus.PENDING,
) -> Contribution:
    """Create a new contribution record."""
    contribution = Contribution(
        user_id=user_id,
        contribution_type=contribution_type,
        media_id=media_id,
        stream_id=stream_id,
        data=data or {},
        status=status,
    )
    session.add(contribution)
    await session.flush()
    return contribution


async def get_contribution(
    session: AsyncSession,
    contribution_id: int,
) -> Contribution | None:
    """Get a contribution by ID."""
    query = select(Contribution).where(Contribution.id == contribution_id)
    result = await session.exec(query)
    return result.first()


async def get_pending_contributions(
    session: AsyncSession,
    *,
    contribution_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Contribution]:
    """Get pending contributions for review."""
    query = select(Contribution).where(Contribution.status == ContributionStatus.PENDING)

    if contribution_type:
        query = query.where(Contribution.contribution_type == contribution_type)

    query = query.order_by(Contribution.created_at)
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


async def update_contribution_status(
    session: AsyncSession,
    contribution_id: int,
    status: ContributionStatus,
    *,
    reviewed_by: int | None = None,
    review_notes: str | None = None,
) -> Contribution | None:
    """Update contribution status (approve/reject)."""
    updates = {
        "status": status,
        "updated_at": datetime.now(pytz.UTC),
    }

    if reviewed_by:
        updates["reviewed_by"] = reviewed_by
        updates["reviewed_at"] = datetime.now(pytz.UTC)

    if review_notes:
        updates["review_notes"] = review_notes

    await session.exec(sa_update(Contribution).where(Contribution.id == contribution_id).values(**updates))
    await session.flush()

    return await get_contribution(session, contribution_id)


async def get_user_contributions(
    session: AsyncSession,
    user_id: int,
    *,
    status: ContributionStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Contribution]:
    """Get contributions by a user."""
    query = select(Contribution).where(Contribution.user_id == user_id)

    if status:
        query = query.where(Contribution.status == status)

    query = query.order_by(Contribution.created_at.desc())
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


# =============================================================================
# METADATA SUGGESTION CRUD
# =============================================================================


async def create_metadata_suggestion(
    session: AsyncSession,
    user_id: int,
    media_id: int,
    field_name: str,
    suggested_value: str,
    *,
    reason: str | None = None,
) -> MetadataSuggestion:
    """Create a metadata edit suggestion."""
    suggestion = MetadataSuggestion(
        user_id=user_id,
        media_id=media_id,
        field_name=field_name,
        suggested_value=suggested_value,
        reason=reason,
    )
    session.add(suggestion)
    await session.flush()
    return suggestion


async def get_pending_metadata_suggestions(
    session: AsyncSession,
    media_id: int | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[MetadataSuggestion]:
    """Get pending metadata suggestions."""
    query = select(MetadataSuggestion).where(MetadataSuggestion.status == ContributionStatus.PENDING)

    if media_id:
        query = query.where(MetadataSuggestion.media_id == media_id)

    query = query.order_by(MetadataSuggestion.created_at)
    query = query.offset(offset).limit(limit)

    result = await session.exec(query)
    return result.all()


# =============================================================================
# STREAM SUGGESTION CRUD
# =============================================================================


async def create_stream_suggestion(
    session: AsyncSession,
    user_id: int,
    stream_id: int,
    field_name: str,
    suggested_value: str,
    *,
    reason: str | None = None,
) -> StreamSuggestion:
    """Create a stream edit suggestion."""
    suggestion = StreamSuggestion(
        user_id=user_id,
        stream_id=stream_id,
        field_name=field_name,
        suggested_value=suggested_value,
        reason=reason,
    )
    session.add(suggestion)
    await session.flush()
    return suggestion


# =============================================================================
# CONTRIBUTION SETTINGS CRUD
# =============================================================================


async def get_contribution_settings(
    session: AsyncSession,
) -> ContributionSettings | None:
    """Get global contribution settings."""
    query = select(ContributionSettings)
    result = await session.exec(query)
    return result.first()


async def update_contribution_settings(
    session: AsyncSession,
    **updates,
) -> ContributionSettings:
    """Update or create contribution settings."""
    settings = await get_contribution_settings(session)

    if settings:
        await session.exec(
            sa_update(ContributionSettings).where(ContributionSettings.id == settings.id).values(**updates)
        )
        await session.flush()
        return await get_contribution_settings(session)

    # Create new settings
    settings = ContributionSettings(**updates)
    session.add(settings)
    await session.flush()
    return settings
