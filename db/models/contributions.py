"""Contribution, voting, and suggestion models."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, DateTime, Index, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from db.enums import ContributionStatus
from db.models.base import TimestampMixin

if TYPE_CHECKING:
    from db.models.media import Media
    from db.models.users import User


class Contribution(TimestampMixin, table=True):
    """User contributions for metadata and stream corrections."""

    __tablename__ = "contributions"
    __table_args__ = (
        Index("idx_contribution_user", "user_id"),
        Index("idx_contribution_status", "status"),
        Index("idx_contribution_type", "contribution_type"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int | None = Field(default=None, foreign_key="users.id", index=True, ondelete="CASCADE")
    contribution_type: str = Field(index=True)  # 'metadata', 'stream', 'torrent'
    target_id: str | None = Field(default=None, index=True)  # Reference to media/stream
    data: dict = Field(default_factory=dict, sa_type=JSON)
    status: ContributionStatus = Field(default=ContributionStatus.PENDING, index=True)
    reviewed_by: str | None = Field(default=None)  # User ID of reviewer
    reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    review_notes: str | None = Field(default=None)

    # Relationships
    user: Optional["User"] = Relationship(back_populates="contributions")


class StreamVote(TimestampMixin, table=True):
    """User votes on stream quality - thumbs up/down with quality status."""

    __tablename__ = "stream_votes"
    __table_args__ = (
        UniqueConstraint("user_id", "stream_id", name="uq_user_stream_vote"),
        Index("idx_stream_vote_stream", "stream_id"),
        Index("idx_stream_vote_user", "user_id"),
        Index("idx_stream_vote_type", "vote_type"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    stream_id: int = Field(foreign_key="stream.id", index=True, ondelete="CASCADE")
    vote_type: str = Field(index=True)  # 'up' or 'down'
    quality_status: str | None = Field(default=None)  # 'working', 'broken', 'good_quality', 'poor_quality'
    comment: str | None = Field(default=None)

    # Relationships
    user: "User" = Relationship(back_populates="stream_votes")


class MetadataVote(TimestampMixin, table=True):
    """User votes on metadata accuracy - likes and ratings."""

    __tablename__ = "metadata_votes"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id", name="uq_user_metadata_vote"),
        Index("idx_metadata_vote_media", "media_id"),
        Index("idx_metadata_vote_user", "user_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    vote_type: str = Field(default="like")  # 'like' or rating value
    vote: int | None = Field(default=None)  # Rating value 1-10 for ratings

    # Relationships
    user: "User" = Relationship(back_populates="metadata_votes")
    media: "Media" = Relationship(back_populates="metadata_votes")


class MetadataSuggestion(TimestampMixin, table=True):
    """User suggestions for metadata corrections."""

    __tablename__ = "metadata_suggestions"
    __table_args__ = (
        Index("idx_suggestion_media", "media_id"),
        Index("idx_suggestion_user", "user_id"),
        Index("idx_suggestion_status", "status"),
        Index("idx_suggestion_field", "field_name"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    field_name: str  # 'title', 'description', 'year', 'poster', 'runtime', 'genre', etc.
    current_value: str | None = Field(default=None)
    suggested_value: str
    reason: str | None = Field(default=None)
    status: str = Field(default="pending", index=True)  # 'pending', 'approved', 'rejected'
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    review_notes: str | None = Field(default=None)

    # Relationships
    user: "User" = Relationship(back_populates="metadata_suggestions")
    media: "Media" = Relationship(back_populates="metadata_suggestions")


class StreamSuggestion(TimestampMixin, table=True):
    """User suggestions for stream corrections."""

    __tablename__ = "stream_suggestions"
    __table_args__ = (
        Index("idx_stream_suggestion_stream", "stream_id"),
        Index("idx_stream_suggestion_user", "user_id"),
        Index("idx_stream_suggestion_status", "status"),
        Index("idx_stream_suggestion_type", "suggestion_type"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    stream_id: int = Field(foreign_key="stream.id", index=True, ondelete="CASCADE")
    suggestion_type: str  # 'report_broken', 'quality_correction', 'language_correction', 'other'
    current_value: str | None = Field(default=None)
    suggested_value: str | None = Field(default=None)
    reason: str | None = Field(default=None)
    status: str = Field(default="pending", index=True)  # 'pending', 'approved', 'rejected'
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    review_notes: str | None = Field(default=None)

    # Relationships
    user: "User" = Relationship(back_populates="stream_suggestions")


class EpisodeSuggestion(TimestampMixin, table=True):
    """User suggestions for episode corrections."""

    __tablename__ = "episode_suggestions"
    __table_args__ = (
        Index("idx_ep_suggestion_episode", "episode_id"),
        Index("idx_ep_suggestion_user", "user_id"),
        Index("idx_ep_suggestion_status", "status"),
        Index("idx_ep_suggestion_field", "field_name"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
    )
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    episode_id: int = Field(foreign_key="episode.id", index=True, ondelete="CASCADE")
    field_name: str  # 'title', 'overview', 'air_date', 'runtime_minutes'
    current_value: str | None = Field(default=None)
    suggested_value: str
    reason: str | None = Field(default=None)
    status: str = Field(default="pending", index=True)  # 'pending', 'approved', 'rejected', 'auto_approved'
    reviewed_by: str | None = Field(default=None)
    reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    review_notes: str | None = Field(default=None)

    # Relationships
    user: "User" = Relationship(back_populates="episode_suggestions")


class ContributionSettings(SQLModel, table=True):
    """Admin-configurable settings for the contribution system."""

    __tablename__ = "contribution_settings"

    id: str = Field(primary_key=True, default="default")

    # Auto-approval thresholds
    auto_approval_threshold: int = Field(default=25)

    # Points awarded for contributions
    points_per_metadata_edit: int = Field(default=5)
    points_per_stream_edit: int = Field(default=3)
    points_for_rejection_penalty: int = Field(default=-2)

    # Contribution level thresholds
    contributor_threshold: int = Field(default=10)
    trusted_threshold: int = Field(default=50)
    expert_threshold: int = Field(default=200)

    # Feature flags
    allow_auto_approval: bool = Field(default=True)
    require_reason_for_edits: bool = Field(default=False)
    max_pending_suggestions_per_user: int = Field(default=20)

    # Broken stream report settings
    # Number of unique users required to report a stream as broken before it's blocked
    broken_report_threshold: int = Field(default=3)
