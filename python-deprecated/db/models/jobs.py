"""
Job queue models for Postgres-backed background job system.

Replaces Python Dramatiq + taskiq. The Rust worker binary (mediafusion-worker)
owns these tables at runtime; Python only defines the schema here so Alembic
can generate migrations.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field, SQLModel


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    error = "error"
    dead = "dead"
    cancelled = "cancelled"


class Job(SQLModel, table=True):
    """Background job record. Claimed by workers via SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "jobs_claim_idx",
            "queue",
            "priority",
            "scheduled_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "jobs_running_idx",
            "worker_id",
            postgresql_where=text("status = 'running'"),
        ),
        Index("jobs_status_finished_idx", "status", "finished_at"),
        Index("jobs_created_at_idx", "created_at"),
        UniqueConstraint("dedupe_key", name="jobs_dedupe_key_uq"),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    queue: str = Field(sa_column=Column(Text, nullable=False, index=True))
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'")),
    )
    status: JobStatus = Field(
        default=JobStatus.pending,
        sa_column=Column(
            Text,
            nullable=False,
            server_default=text("'pending'"),
        ),
    )
    priority: int = Field(
        default=100,
        sa_column=Column(SmallInteger, nullable=False, server_default=text("100")),
    )
    attempts: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    max_attempts: int = Field(
        default=5,
        sa_column=Column(Integer, nullable=False, server_default=text("5")),
    )
    scheduled_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    worker_id: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    last_error: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    cancel_requested: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    dedupe_key: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )


class CronJob(SQLModel, table=True):
    """Cron schedule definition. One row per recurring job type."""

    __tablename__ = "cron_jobs"

    name: str = Field(sa_column=Column(Text, primary_key=True))
    schedule: str = Field(sa_column=Column(Text, nullable=False))
    queue: str = Field(sa_column=Column(Text, nullable=False))
    payload: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'")),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    last_enqueued_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class JobEvent(SQLModel, table=True):
    """Append-only audit log for job state transitions."""

    __tablename__ = "job_events"
    __table_args__ = (Index("job_events_job_id_idx", "job_id"),)

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    job_id: int = Field(
        sa_column=Column(
            BigInteger,
            nullable=False,
        )
    )
    event: str = Field(sa_column=Column(Text, nullable=False))
    detail: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )
