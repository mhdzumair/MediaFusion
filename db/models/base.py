"""Base models and mixins for all database models."""

from datetime import datetime

import pytz
from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


class TimestampMixin(SQLModel):
    """Mixin that adds created_at and updated_at timestamps."""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(pytz.UTC),
        nullable=False,
        sa_type=DateTime(timezone=True),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"onupdate": lambda: datetime.now(pytz.UTC)},
        index=True,
        sa_type=DateTime(timezone=True),
    )
