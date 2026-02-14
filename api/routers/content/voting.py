"""
Voting API endpoints for stream quality voting and content ratings.
Updated to use new unified stream architecture and integer PKs.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.crud import (
    get_stream_vote_count,
    get_user_stream_vote,
    remove_stream_vote,
    vote_on_metadata,
    vote_on_stream,
)
from db.database import get_async_session
from db.models import Media, MetadataVote, Stream, User

router = APIRouter(prefix="/api/v1", tags=["Voting"])


# ============================================
# Pydantic Schemas
# ============================================


VoteTypeLiteral = Literal["up", "down"]


class StreamVoteRequest(BaseModel):
    """Request to vote on a stream"""

    vote: int = Field(..., ge=-1, le=1, description="Vote value: 1 for upvote, -1 for downvote")
    comment: str | None = Field(None, max_length=500)


class StreamVoteResponse(BaseModel):
    """Response for a stream vote"""

    id: int
    stream_id: int
    user_id: int
    vote: int
    voted_at: datetime


class StreamVoteSummary(BaseModel):
    """Summary of votes for a stream"""

    stream_id: int
    upvotes: int = 0
    downvotes: int = 0
    score: int = 0
    user_vote: int | None = None  # User's current vote: 1, -1, or None


class ContentRatingRequest(BaseModel):
    """Request to rate content"""

    rating: float = Field(..., ge=1, le=10, description="Rating from 1 to 10")


class ContentRatingResponse(BaseModel):
    """Response for content rating"""

    media_id: int
    user_id: int
    rating: float
    voted_at: datetime


class ContentRatingSummary(BaseModel):
    """Summary of ratings for content"""

    media_id: int
    average_rating: float | None = None
    total_votes: int = 0
    user_rating: float | None = None


class BulkStreamVoteSummary(BaseModel):
    """Bulk stream vote summaries"""

    summaries: dict[int, StreamVoteSummary]


class BulkContentRatingSummary(BaseModel):
    """Bulk content rating summaries"""

    summaries: dict[int, ContentRatingSummary]


# Content Likes schemas
class ContentLikeResponse(BaseModel):
    """Response for content like"""

    id: str
    media_id: int
    liked: bool
    created_at: datetime


class ContentLikeSummary(BaseModel):
    """Summary of likes for content"""

    media_id: int
    likes_count: int = 0
    user_liked: bool = False


# ============================================
# Stream Voting Endpoints
# ============================================


@router.post("/streams/{stream_id}/vote", response_model=StreamVoteResponse)
async def vote_stream(
    stream_id: int,
    request: StreamVoteRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Vote on a stream's quality. Updates existing vote if one exists.
    Vote values: 1 for upvote, -1 for downvote.
    """
    # Verify stream exists
    stream_query = select(Stream.id).where(Stream.id == stream_id)
    stream_result = await session.exec(stream_query)
    stream = stream_result.first()

    if not stream:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )

    # Vote using CRUD function
    vote = await vote_on_stream(session, current_user.id, stream_id, request.vote)
    await session.commit()

    return StreamVoteResponse(
        id=vote.id,
        stream_id=vote.stream_id,
        user_id=vote.user_id,
        vote=vote.vote,
        voted_at=vote.voted_at,
    )


@router.delete("/streams/{stream_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream_vote(
    stream_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Remove user's vote on a stream.
    """
    success = await remove_stream_vote(session, current_user.id, stream_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vote not found",
        )

    await session.commit()


@router.get("/streams/{stream_id}/votes", response_model=StreamVoteSummary)
async def get_stream_votes(
    stream_id: int,
    current_user: User | None = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get vote summary for a stream.
    """
    # Get vote counts
    vote_stats = await get_stream_vote_count(session, stream_id)

    # Get user's vote if authenticated
    user_vote = None
    if current_user:
        user_vote_obj = await get_user_stream_vote(session, current_user.id, stream_id)
        if user_vote_obj:
            user_vote = user_vote_obj.vote

    return StreamVoteSummary(
        stream_id=stream_id,
        upvotes=vote_stats["upvotes"],
        downvotes=vote_stats["downvotes"],
        score=vote_stats["score"],
        user_vote=user_vote,
    )


@router.post("/streams/votes/bulk", response_model=BulkStreamVoteSummary)
async def get_bulk_stream_votes(
    stream_ids: list[int],
    current_user: User | None = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get vote summaries for multiple streams at once.
    """
    if len(stream_ids) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 50 streams per request",
        )

    summaries = {}
    for stream_id in stream_ids:
        vote_stats = await get_stream_vote_count(session, stream_id)

        user_vote = None
        if current_user:
            user_vote_obj = await get_user_stream_vote(session, current_user.id, stream_id)
            if user_vote_obj:
                user_vote = user_vote_obj.vote

        summaries[stream_id] = StreamVoteSummary(
            stream_id=stream_id,
            upvotes=vote_stats["upvotes"],
            downvotes=vote_stats["downvotes"],
            score=vote_stats["score"],
            user_vote=user_vote,
        )

    return BulkStreamVoteSummary(summaries=summaries)


# ============================================
# Content Rating Endpoints
# ============================================


@router.post("/content/{media_id}/rate", response_model=ContentRatingResponse)
async def rate_content(
    media_id: int,
    request: ContentRatingRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Rate a movie/series from 1-10. Updates existing rating if one exists.
    """
    # Verify media exists
    media_query = select(Media.id).where(Media.id == media_id)
    media_result = await session.exec(media_query)
    media = media_result.first()

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content not found",
        )

    # Rate using CRUD function (converts to vote)
    vote = await vote_on_metadata(session, current_user.id, media_id, int(request.rating))
    await session.commit()

    return ContentRatingResponse(
        media_id=media_id,
        user_id=current_user.id,
        rating=request.rating,
        voted_at=vote.voted_at,
    )


@router.get("/content/{media_id}/ratings", response_model=ContentRatingSummary)
async def get_content_ratings(
    media_id: int,
    current_user: User | None = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get rating summary for a movie/series.
    """
    from db.crud import get_mediafusion_rating

    # Get aggregate rating
    rating_stats = await get_mediafusion_rating(session, media_id)

    # Get user's rating if authenticated
    user_rating = None
    if current_user:
        user_query = select(MetadataVote).where(
            MetadataVote.user_id == current_user.id,
            MetadataVote.media_id == media_id,
        )
        result = await session.exec(user_query)
        user_vote = result.first()
        if user_vote:
            user_rating = float(user_vote.vote)

    return ContentRatingSummary(
        media_id=media_id,
        average_rating=rating_stats["average"],
        total_votes=rating_stats["count"],
        user_rating=user_rating,
    )


@router.post("/content/ratings/bulk", response_model=BulkContentRatingSummary)
async def get_bulk_content_ratings(
    media_ids: list[int],
    current_user: User | None = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get rating summaries for multiple content items at once.
    """
    if len(media_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 items per request",
        )

    from db.crud import get_mediafusion_rating

    summaries = {}
    for media_id in media_ids:
        rating_stats = await get_mediafusion_rating(session, media_id)

        user_rating = None
        if current_user:
            user_query = select(MetadataVote).where(
                MetadataVote.user_id == current_user.id,
                MetadataVote.media_id == media_id,
            )
            result = await session.exec(user_query)
            user_vote = result.first()
            if user_vote:
                user_rating = float(user_vote.vote)

        summaries[media_id] = ContentRatingSummary(
            media_id=media_id,
            average_rating=rating_stats["average"],
            total_votes=rating_stats["count"],
            user_rating=user_rating,
        )

    return BulkContentRatingSummary(summaries=summaries)


# ============================================
# Content Likes Endpoints
# ============================================


@router.post("/content/{media_id}/like", response_model=ContentLikeResponse)
async def like_content(
    media_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Like a movie/series. Toggle - if already liked, this will be a no-op.
    Uses media_id (integer).
    """
    import uuid

    # Verify media exists
    media_query = select(Media.id).where(Media.id == media_id)
    media_result = await session.exec(media_query)
    media = media_result.first()

    if not media:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content not found",
        )

    # Check existing like
    existing_query = select(MetadataVote).where(
        MetadataVote.user_id == current_user.id,
        MetadataVote.media_id == media_id,
    )
    result = await session.exec(existing_query)
    existing = result.first()

    if existing:
        # Already liked
        return ContentLikeResponse(
            id=existing.id,
            media_id=media_id,
            liked=True,
            created_at=existing.created_at,
        )

    # Create new like
    like = MetadataVote(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        media_id=media_id,
        vote_type="like",
    )
    session.add(like)
    await session.commit()
    await session.refresh(like)

    return ContentLikeResponse(
        id=like.id,
        media_id=media_id,
        liked=True,
        created_at=like.created_at,
    )


@router.delete("/content/{media_id}/like", status_code=status.HTTP_204_NO_CONTENT)
async def unlike_content(
    media_id: int,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Remove like from a movie/series.
    """
    # Find existing like
    query = select(MetadataVote).where(
        MetadataVote.user_id == current_user.id,
        MetadataVote.media_id == media_id,
    )
    result = await session.exec(query)
    like = result.first()

    if not like:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Like not found",
        )

    await session.delete(like)
    await session.commit()


@router.get("/content/{media_id}/likes", response_model=ContentLikeSummary)
async def get_content_likes(
    media_id: int,
    current_user: User | None = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get likes summary for a movie/series.
    """
    from sqlalchemy import func

    # Get total likes count
    count_query = select(func.count(MetadataVote.id)).where(
        MetadataVote.media_id == media_id,
        MetadataVote.vote_type == "like",
    )
    count_result = await session.exec(count_query)
    likes_count = count_result.one() or 0

    # Check if current user liked
    user_liked = False
    if current_user:
        user_query = select(MetadataVote.id).where(
            MetadataVote.user_id == current_user.id,
            MetadataVote.media_id == media_id,
        )
        result = await session.exec(user_query)
        user_liked = result.first() is not None

    return ContentLikeSummary(
        media_id=media_id,
        likes_count=likes_count,
        user_liked=user_liked,
    )
