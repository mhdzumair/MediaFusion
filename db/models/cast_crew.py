"""Cast and crew models for media."""

from datetime import date, datetime

from sqlalchemy import DateTime, Index
from sqlmodel import Field, Relationship, SQLModel

from db.models.base import TimestampMixin


class Person(TimestampMixin, table=True):
    """
    Person (actor, director, writer, etc.) information.

    Stores people who are involved in media production.
    """

    __tablename__ = "person"
    __table_args__ = (
        Index("idx_person_name", "name"),
        Index("idx_person_tmdb", "tmdb_id"),
        Index("idx_person_imdb", "imdb_id"),
        # Trigram index for efficient ILIKE pattern matching on names
        Index(
            "idx_person_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: int = Field(default=None, primary_key=True)
    tmdb_id: int | None = Field(default=None, unique=True, index=True)
    imdb_id: str | None = Field(default=None, unique=True, index=True)
    name: str = Field(index=True)
    profile_url: str | None = None  # Photo URL
    known_for_department: str | None = None  # Acting, Directing, Writing, etc.
    birthday: date | None = None
    deathday: date | None = None
    biography: str | None = None
    place_of_birth: str | None = None
    popularity: float | None = None

    # Which provider scraped this person
    provider_id: int | None = Field(default=None, foreign_key="metadata_provider.id")


class MediaCast(SQLModel, table=True):
    """
    Cast member in a media item.

    Links a person to media with their character name and billing order.
    """

    __tablename__ = "media_cast"
    __table_args__ = (
        Index("idx_media_cast_media", "media_id"),
        Index("idx_media_cast_person", "person_id"),
        Index("idx_media_cast_order", "media_id", "display_order"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    person_id: int = Field(foreign_key="person.id", index=True, ondelete="CASCADE")
    character: str | None = None  # Character name played
    display_order: int = Field(default=0)  # Billing order

    # Relationships
    person: Person = Relationship()
    # media: "Media" = Relationship()


class MediaCrew(SQLModel, table=True):
    """
    Crew member in a media item.

    Links a person to media with their department and job title.
    """

    __tablename__ = "media_crew"
    __table_args__ = (
        Index("idx_media_crew_media", "media_id"),
        Index("idx_media_crew_person", "person_id"),
        Index("idx_media_crew_department", "department"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    person_id: int = Field(foreign_key="person.id", index=True, ondelete="CASCADE")
    department: str | None = None  # Directing, Writing, Production, etc.
    job: str | None = None  # Director, Screenplay, Producer, etc.

    # Relationships
    person: Person = Relationship()
    # media: "Media" = Relationship()


class MediaReview(TimestampMixin, table=True):
    """
    Review for a media item.

    Can be from external providers (TMDB, RT) or MediaFusion users.
    """

    __tablename__ = "media_review"
    __table_args__ = (
        Index("idx_media_review_media", "media_id"),
        Index("idx_media_review_provider", "provider_id"),
        Index("idx_media_review_user", "user_id"),
    )

    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")

    # Source: either external provider OR user (one must be set)
    provider_id: int | None = Field(default=None, foreign_key="rating_provider.id")
    user_id: int | None = Field(default=None, foreign_key="users.id")

    # Review content
    author: str | None = None
    author_avatar: str | None = None
    content: str
    rating: float | None = None  # Optional rating with review (0-10)
    url: str | None = None  # External review URL
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
