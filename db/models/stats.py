"""Statistics and caching models."""

from datetime import date, datetime

from sqlalchemy import JSON, DateTime
from sqlmodel import Field, SQLModel


class DailyStats(SQLModel, table=True):
    """Daily aggregated statistics."""

    __tablename__ = "daily_stats"

    id: int = Field(default=None, primary_key=True)
    stat_date: date = Field(unique=True, index=True)
    new_users: int = Field(default=0)
    active_users: int = Field(default=0)
    new_streams: int = Field(default=0)
    total_playbacks: int = Field(default=0)
    top_media: dict | None = Field(default=None, sa_type=JSON)  # Top 10 by playback
    top_streams: dict | None = Field(default=None, sa_type=JSON)  # Top 10 by playback
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_type=DateTime(timezone=True),
    )
