"""
Suggestions API endpoints for metadata correction suggestions.
Includes auto-approval for trusted users and points-based reputation system.
"""

import logging
import re
from datetime import datetime
from typing import Literal, TypedDict

import pytz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import JSONResponse

from api.routers.user.auth import require_auth, require_role
from db.crud import add_external_id, get_canonical_external_id, invalidate_external_id_cache
from db.crud.providers import get_or_create_provider
from db.database import get_async_session
from db.enums import MediaType, NudityStatus, UserRole
from db.models import (
    AkaTitle,
    Catalog,
    ContributionSettings,
    Genre,
    Media,
    MediaCast,
    MediaCatalogLink,
    MediaCrew,
    MediaExternalID,
    MediaGenreLink,
    MediaImage,
    MediaParentalCertificateLink,
    MetadataSuggestion,
    ParentalCertificate,
    Person,
    TVMetadata,
    User,
)
from utils.const import CERTIFICATION_MAPPING
from utils.notification_registry import send_pending_metadata_suggestion_notification

logger = logging.getLogger(__name__)

# Suggestion status constants (stored as VARCHAR in DB)
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_AUTO_APPROVED = "auto_approved"  # New status for auto-approved suggestions


router = APIRouter(prefix="/api/v1", tags=["Suggestions"])


# ============================================
# Constants
# ============================================

# Expanded list of editable fields
EDITABLE_FIELDS = [
    "title",
    "description",
    "year",
    "poster",
    "background",
    "runtime",
    "genres",
    "country",
    "language",
    "aka_titles",
    "cast",
    "directors",
    "writers",
    "imdb_id",
    "tmdb_id",
    "tvdb_id",
    "mal_id",
    "kitsu_id",
    "catalogs",
    "parental_certificate",
    "nudity_status",
]

# Fields that require JSON handling (arrays)
JSON_FIELDS = ["genres", "aka_titles", "cast", "directors", "writers", "catalogs", "parental_certificate"]
EXTERNAL_ID_FIELD_TO_PROVIDER: dict[str, str] = {
    "imdb_id": "imdb",
    "tmdb_id": "tmdb",
    "tvdb_id": "tvdb",
    "mal_id": "mal",
    "kitsu_id": "kitsu",
}


def _api_error_response(detail: str, status_code: int) -> JSONResponse:
    """Return API-style error payload with HTTP 200."""
    return JSONResponse(
        status_code=200,
        content={
            "error": True,
            "detail": detail,
            "status_code": status_code,
        },
    )


# ============================================
# Pydantic Schemas
# ============================================


EditableFieldLiteral = Literal[
    "title",
    "description",
    "year",
    "poster",
    "background",
    "runtime",
    "genres",
    "country",
    "language",
    "aka_titles",
    "cast",
    "directors",
    "writers",
    "imdb_id",
    "tmdb_id",
    "tvdb_id",
    "mal_id",
    "kitsu_id",
    "catalogs",
    "parental_certificate",
    "nudity_status",
]


class SuggestionCreateRequest(BaseModel):
    """Request to create a metadata suggestion"""

    field_name: EditableFieldLiteral
    current_value: str | None = None
    suggested_value: str = Field(..., min_length=1, max_length=10000)
    reason: str | None = Field(None, max_length=1000)


class SuggestionReviewRequest(BaseModel):
    """Request to review a suggestion (moderator only)"""

    action: Literal["approve", "reject"]
    review_notes: str | None = Field(None, max_length=1000)


SuggestionStatusLiteral = Literal["pending", "approved", "rejected", "auto_approved"]


class SuggestionResponse(BaseModel):
    """Response for a single suggestion"""

    id: str  # UUID string
    user_id: int
    username: str | None = None
    media_id: int  # Internal media ID
    media_title: str | None = None
    media_type: str | None = None
    media_year: int | None = None
    media_poster_url: str | None = None
    media_background_url: str | None = None
    field_name: str
    current_value: str | None = None
    suggested_value: str
    reason: str | None = None
    status: str
    was_auto_approved: bool = False
    reviewed_by: str | None = None  # Also UUID string
    reviewed_at: datetime | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # User reputation info
    user_contribution_level: str | None = None
    user_contribution_points: int | None = None


class SuggestionListResponse(BaseModel):
    """Paginated list of suggestions"""

    suggestions: list[SuggestionResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class SuggestionStatsResponse(BaseModel):
    """Statistics about suggestions"""

    total: int
    pending: int
    approved: int
    auto_approved: int
    rejected: int
    # Today's stats (for moderators)
    approved_today: int = 0
    rejected_today: int = 0
    # User stats
    user_pending: int = 0
    user_approved: int = 0
    user_auto_approved: int = 0
    user_rejected: int = 0
    user_contribution_points: int = 0
    user_contribution_level: str = "new"


class UserContributionInfo(BaseModel):
    """Information about a user's contribution status"""

    contribution_points: int
    contribution_level: str
    metadata_edits_approved: int
    stream_edits_approved: int
    can_auto_approve: bool
    points_to_next_level: int
    next_level: str | None


# ============================================
# Helper Functions
# ============================================


async def get_contribution_settings(session: AsyncSession) -> ContributionSettings:
    """Get or create contribution settings"""
    result = await session.exec(select(ContributionSettings).where(ContributionSettings.id == "default"))
    settings = result.first()

    if not settings:
        settings = ContributionSettings(id="default")
        session.add(settings)
        await session.commit()
        await session.refresh(settings)

    return settings


class MediaContext(TypedDict):
    media_title: str | None
    media_type: str | None
    media_year: int | None
    media_poster_url: str | None
    media_background_url: str | None


def _empty_media_context() -> MediaContext:
    return {
        "media_title": None,
        "media_type": None,
        "media_year": None,
        "media_poster_url": None,
        "media_background_url": None,
    }


async def get_media_context_map(session: AsyncSession, media_ids: list[int]) -> dict[int, MediaContext]:
    """Get media context (title/type/year/primary images) for multiple media IDs."""
    if not media_ids:
        return {}

    unique_media_ids = list(dict.fromkeys(media_ids))
    context_by_media_id: dict[int, MediaContext] = {media_id: _empty_media_context() for media_id in unique_media_ids}

    media_query = select(Media).where(Media.id.in_(unique_media_ids))
    media_result = await session.exec(media_query)
    for media in media_result.all():
        context_by_media_id[media.id].update(
            {
                "media_title": media.title,
                "media_type": media.type.value if media.type else None,
                "media_year": media.year,
            }
        )

    images_query = (
        select(MediaImage)
        .where(
            MediaImage.media_id.in_(unique_media_ids),
            MediaImage.image_type.in_(["poster", "background", "backdrop"]),
        )
        .order_by(
            MediaImage.media_id,
            MediaImage.image_type,
            MediaImage.is_primary.desc(),
            MediaImage.display_order.asc(),
            MediaImage.id.asc(),
        )
    )
    images_result = await session.exec(images_query)
    for image in images_result.all():
        media_context = context_by_media_id.get(image.media_id)
        if not media_context:
            continue

        if image.image_type == "poster" and not media_context["media_poster_url"]:
            media_context["media_poster_url"] = image.url
        elif image.image_type in ("background", "backdrop") and not media_context["media_background_url"]:
            media_context["media_background_url"] = image.url

    return context_by_media_id


async def get_media_context(session: AsyncSession, media_id: int) -> MediaContext:
    """Get media context (title/type/year/primary images) for a single media ID."""
    context_map = await get_media_context_map(session, [media_id])
    return context_map.get(media_id, _empty_media_context())


async def get_username(session: AsyncSession, user_id: int) -> str | None:
    """Get username for a user"""
    query = select(User.username).where(User.id == user_id)
    result = await session.exec(query)
    return result.first()


def _normalize_filter_query(value: str | None) -> str | None:
    """Normalize optional query string filters."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


async def should_auto_approve(user: User, session: AsyncSession) -> bool:
    """Check if user has enough reputation for auto-approval.

    Moderators and admins are always auto-approved since they have
    the authority to approve suggestions anyway.
    """
    # Moderators and admins are always auto-approved
    if user.role in (UserRole.MODERATOR, UserRole.ADMIN):
        return True

    settings = await get_contribution_settings(session)

    if not settings.allow_auto_approval:
        return False

    return user.contribution_points >= settings.auto_approval_threshold


def calculate_contribution_level(points: int, settings: ContributionSettings) -> str:
    """Calculate user contribution level based on points"""
    if points >= settings.expert_threshold:
        return "expert"
    elif points >= settings.trusted_threshold:
        return "trusted"
    elif points >= settings.contributor_threshold:
        return "contributor"
    return "new"


async def award_points(
    user: User,
    points: int,
    edit_type: str,
    session: AsyncSession,
) -> None:
    """Award points to a user and update their contribution level"""
    settings = await get_contribution_settings(session)

    user.contribution_points = max(0, user.contribution_points + points)

    if edit_type == "metadata":
        user.metadata_edits_approved += 1
    elif edit_type == "stream":
        user.stream_edits_approved += 1

    # Update contribution level
    user.contribution_level = calculate_contribution_level(user.contribution_points, settings)

    session.add(user)
    logger.info(
        f"Awarded {points} points to user {user.id}. "
        f"Total: {user.contribution_points}, Level: {user.contribution_level}"
    )


RUNTIME_MINUTES_PATTERN = re.compile(
    r"^\s*(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*(?:(?P<minutes>\d+)\s*m(?:in(?:utes?)?)?)?\s*$",
    re.IGNORECASE,
)


def _parse_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _expand_parental_certificate_selection(value: str) -> list[str]:
    normalized = value.strip()
    if not normalized:
        return []

    mapped = CERTIFICATION_MAPPING.get(normalized)
    if mapped is None:
        # Backward compatibility for older suggestions that stored raw CSV values.
        if "," in normalized:
            return _parse_list(normalized)
        return [normalized]

    expanded: list[str] = []
    for certificate in mapped:
        if certificate is None:
            continue
        name = str(certificate).strip()
        if name and name not in expanded:
            expanded.append(name)
    return expanded


def _parse_runtime_minutes(value: str) -> int | None:
    normalized = value.strip().lower()
    if not normalized:
        return None

    if normalized.isdigit():
        return int(normalized)

    runtime_match = RUNTIME_MINUTES_PATTERN.match(normalized)
    if runtime_match:
        hours = int(runtime_match.group("hours") or 0)
        minutes = int(runtime_match.group("minutes") or 0)
        if hours == 0 and minutes == 0:
            return None
        return hours * 60 + minutes

    min_match = re.search(r"(\d+)\s*min", normalized)
    if min_match:
        return int(min_match.group(1))

    return None


async def _get_or_create_genre(session: AsyncSession, name: str) -> Genre:
    result = await session.exec(select(Genre).where(Genre.name == name))
    genre = result.first()
    if genre:
        return genre

    genre = Genre(name=name)
    session.add(genre)
    await session.flush()
    return genre


async def _get_or_create_catalog(session: AsyncSession, name: str) -> Catalog:
    result = await session.exec(select(Catalog).where(Catalog.name == name))
    catalog = result.first()
    if catalog:
        return catalog

    catalog = Catalog(name=name)
    session.add(catalog)
    await session.flush()
    return catalog


async def _get_or_create_person(session: AsyncSession, name: str) -> Person:
    result = await session.exec(select(Person).where(Person.name == name))
    person = result.first()
    if person:
        return person

    person = Person(name=name)
    session.add(person)
    await session.flush()
    return person


async def _get_or_create_parental_certificate(session: AsyncSession, name: str) -> ParentalCertificate:
    result = await session.exec(select(ParentalCertificate).where(ParentalCertificate.name == name))
    certificate = result.first()
    if certificate:
        return certificate

    certificate = ParentalCertificate(name=name)
    session.add(certificate)
    await session.flush()
    return certificate


async def _replace_genres(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (await session.exec(select(MediaGenreLink).where(MediaGenreLink.media_id == media_id))).all()
    for link in existing:
        await session.delete(link)

    for name in names:
        genre = await _get_or_create_genre(session, name)
        session.add(MediaGenreLink(media_id=media_id, genre_id=genre.id))


async def _replace_catalogs(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (await session.exec(select(MediaCatalogLink).where(MediaCatalogLink.media_id == media_id))).all()
    for link in existing:
        await session.delete(link)

    for name in names:
        catalog = await _get_or_create_catalog(session, name)
        session.add(MediaCatalogLink(media_id=media_id, catalog_id=catalog.id))


async def _replace_aka_titles(session: AsyncSession, media_id: int, titles: list[str]) -> None:
    existing = (await session.exec(select(AkaTitle).where(AkaTitle.media_id == media_id))).all()
    for aka in existing:
        await session.delete(aka)

    for title in titles:
        session.add(AkaTitle(media_id=media_id, title=title))


async def _replace_cast(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (await session.exec(select(MediaCast).where(MediaCast.media_id == media_id))).all()
    for cast_member in existing:
        await session.delete(cast_member)

    for index, name in enumerate(names):
        person = await _get_or_create_person(session, name)
        session.add(MediaCast(media_id=media_id, person_id=person.id, display_order=index))


async def _replace_directors(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (
        await session.exec(
            select(MediaCrew).where(
                MediaCrew.media_id == media_id,
                func.lower(cast(MediaCrew.job, String)) == "director",
            )
        )
    ).all()
    for crew in existing:
        await session.delete(crew)

    for name in names:
        person = await _get_or_create_person(session, name)
        session.add(MediaCrew(media_id=media_id, person_id=person.id, department="Directing", job="Director"))


async def _replace_writers(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (
        await session.exec(
            select(MediaCrew).where(
                MediaCrew.media_id == media_id,
                func.lower(cast(MediaCrew.job, String)).in_(["writer", "screenplay", "story"]),
            )
        )
    ).all()
    for crew in existing:
        await session.delete(crew)

    for name in names:
        person = await _get_or_create_person(session, name)
        session.add(MediaCrew(media_id=media_id, person_id=person.id, department="Writing", job="Writer"))


async def _replace_parental_certificates(session: AsyncSession, media_id: int, names: list[str]) -> None:
    existing = (
        await session.exec(
            select(MediaParentalCertificateLink).where(MediaParentalCertificateLink.media_id == media_id)
        )
    ).all()
    for link in existing:
        await session.delete(link)

    for name in names:
        certificate = await _get_or_create_parental_certificate(session, name)
        session.add(MediaParentalCertificateLink(media_id=media_id, certificate_id=certificate.id))


async def _set_external_id(session: AsyncSession, media_id: int, provider: str, value: str) -> bool:
    normalized = value.strip()
    if provider == "imdb" and normalized and not normalized.startswith("tt"):
        return False

    if provider in {"tmdb", "tvdb", "mal", "kitsu"} and normalized.startswith(f"{provider}:"):
        normalized = normalized.split(":", 1)[1]

    existing = (
        await session.exec(
            select(MediaExternalID).where(MediaExternalID.media_id == media_id, MediaExternalID.provider == provider)
        )
    ).all()

    if not normalized:
        for item in existing:
            await session.delete(item)
        if existing:
            await invalidate_external_id_cache(media_id)
        return True

    # Do not allow hijacking an external ID already attached to another media.
    global_existing = (
        await session.exec(
            select(MediaExternalID).where(
                MediaExternalID.provider == provider,
                MediaExternalID.external_id == normalized,
            )
        )
    ).first()
    if global_existing and global_existing.media_id != media_id:
        logger.warning(
            "Cannot assign %s:%s to media %s because it already belongs to media %s",
            provider,
            normalized,
            media_id,
            global_existing.media_id,
        )
        return False

    # Replacement semantics: keep at most one ID per provider for a media.
    changed_existing_ids = False
    for item in existing:
        if item.external_id != normalized:
            await session.delete(item)
            changed_existing_ids = True

    if changed_existing_ids:
        await invalidate_external_id_cache(media_id)

    if any(item.external_id == normalized for item in existing):
        return True

    await add_external_id(session, media_id, provider, normalized)
    return True


def _format_conflicting_media_label(conflicting_media: Media | None, conflict_media_id: int) -> str:
    """Build a friendly label for conflict messages."""
    if not conflicting_media:
        return f"media #{conflict_media_id}"

    title = conflicting_media.title or f"Media #{conflict_media_id}"
    year_suffix = f" ({conflicting_media.year})" if conflicting_media.year else ""
    media_type = conflicting_media.type.value if conflicting_media.type else "unknown"
    return f'"{title}{year_suffix}" [{media_type}, media_id={conflict_media_id}]'


async def _build_external_id_conflict_message(
    session: AsyncSession,
    provider: str,
    normalized_external_id: str,
    conflict_media_id: int,
) -> str:
    """Create a clear conflict error message with remediation guidance."""
    conflict_media = await session.get(Media, conflict_media_id)
    conflict_label = _format_conflicting_media_label(conflict_media, conflict_media_id)
    canonical_conflict_id = await get_canonical_external_id(session, conflict_media_id)
    provider_label = "IMDb" if provider == "imdb" else provider.upper()

    return (
        f'{provider_label} ID "{normalized_external_id}" is already attached to {conflict_label} '
        f"(canonical ID: {canonical_conflict_id}). If streams are linked to the wrong media, "
        "do not change media external IDs. Use Stream Link -> Replace Link to fix stream-media links."
    )


def _normalize_external_id_suggestion_value(provider: str, suggested_value: str) -> str | None:
    """Normalize user-entered external ID values to storage format."""
    normalized = suggested_value.strip()
    if not normalized:
        return ""

    if provider == "imdb":
        return normalized if normalized.startswith("tt") else None

    if provider in {"tmdb", "tvdb", "mal", "kitsu"} and normalized.startswith(f"{provider}:"):
        return normalized.split(":", 1)[1]

    return normalized


async def _get_external_id_conflict_message(
    session: AsyncSession,
    media_id: int,
    field_name: str,
    suggested_value: str,
) -> str | None:
    """Return a conflict message when external ID suggestion collides with another media."""
    provider = EXTERNAL_ID_FIELD_TO_PROVIDER.get(field_name)
    if not provider:
        return None

    normalized_external_id = _normalize_external_id_suggestion_value(provider, suggested_value)
    if normalized_external_id is None:
        if provider == "imdb":
            return (
                f'Invalid IMDb ID "{suggested_value.strip()}". Use IMDb format like "tt1234567". '
                "If streams are linked to the wrong media, use Stream Link -> Replace Link."
            )
        return None

    if not normalized_external_id:
        # Clearing the provider ID is allowed.
        return None

    existing = (
        await session.exec(
            select(MediaExternalID).where(
                MediaExternalID.provider == provider,
                MediaExternalID.external_id == normalized_external_id,
            )
        )
    ).first()
    if not existing or existing.media_id == media_id:
        return None

    return await _build_external_id_conflict_message(
        session,
        provider,
        normalized_external_id,
        existing.media_id,
    )


async def _set_media_image(session: AsyncSession, media_id: int, image_type: str, value: str) -> bool:
    normalized = value.strip()
    image_types = ["background", "backdrop"] if image_type == "background" else [image_type]

    existing = (
        await session.exec(
            select(MediaImage).where(MediaImage.media_id == media_id, MediaImage.image_type.in_(image_types))
        )
    ).all()
    for image in existing:
        await session.delete(image)

    if not normalized:
        return True

    user_provider = await get_or_create_provider(session, "user")
    session.add(
        MediaImage(
            media_id=media_id,
            provider_id=user_provider.id,
            image_type="backdrop" if image_type == "background" else image_type,
            url=normalized,
            is_primary=True,
        )
    )
    return True


async def apply_metadata_changes(
    meta: Media,
    field_name: str,
    value: str,
    session: AsyncSession,
) -> bool:
    """Apply suggested changes to metadata. Returns True if successful."""
    try:
        if field_name == "title":
            meta.title = value
        elif field_name == "description":
            meta.description = value
        elif field_name == "year":
            meta.year = int(value)
        elif field_name == "poster":
            return await _set_media_image(session, meta.id, "poster", value)
        elif field_name == "background":
            return await _set_media_image(session, meta.id, "background", value)
        elif field_name == "runtime":
            runtime_minutes = _parse_runtime_minutes(value)
            if runtime_minutes is None and value.strip():
                return False
            meta.runtime_minutes = runtime_minutes
        elif field_name == "country":
            if meta.type != MediaType.TV:
                return False
            tv_metadata_result = await session.exec(select(TVMetadata).where(TVMetadata.media_id == meta.id))
            tv_metadata = tv_metadata_result.first()
            if not tv_metadata:
                tv_metadata = TVMetadata(media_id=meta.id)
            tv_metadata.country = value.strip() or None
            session.add(tv_metadata)
        elif field_name == "language":
            if meta.type != MediaType.TV:
                return False
            tv_metadata_result = await session.exec(select(TVMetadata).where(TVMetadata.media_id == meta.id))
            tv_metadata = tv_metadata_result.first()
            if not tv_metadata:
                tv_metadata = TVMetadata(media_id=meta.id)
            tv_metadata.tv_language = value.strip() or None
            session.add(tv_metadata)
        elif field_name == "imdb_id":
            return await _set_external_id(session, meta.id, "imdb", value)
        elif field_name == "tmdb_id":
            return await _set_external_id(session, meta.id, "tmdb", value)
        elif field_name == "tvdb_id":
            return await _set_external_id(session, meta.id, "tvdb", value)
        elif field_name == "mal_id":
            return await _set_external_id(session, meta.id, "mal", value)
        elif field_name == "kitsu_id":
            return await _set_external_id(session, meta.id, "kitsu", value)
        elif field_name == "nudity_status":
            try:
                meta.nudity_status = NudityStatus(value.strip() or NudityStatus.UNKNOWN.value)
            except ValueError:
                return False
        elif field_name == "genres":
            await _replace_genres(session, meta.id, _parse_list(value))
        elif field_name == "catalogs":
            await _replace_catalogs(session, meta.id, _parse_list(value))
        elif field_name == "aka_titles":
            await _replace_aka_titles(session, meta.id, _parse_list(value))
        elif field_name == "cast":
            await _replace_cast(session, meta.id, _parse_list(value))
        elif field_name == "directors":
            await _replace_directors(session, meta.id, _parse_list(value))
        elif field_name == "writers":
            await _replace_writers(session, meta.id, _parse_list(value))
        elif field_name == "parental_certificate":
            await _replace_parental_certificates(session, meta.id, _expand_parental_certificate_selection(value))
        elif field_name in JSON_FIELDS:
            return False
        else:
            return False

        session.add(meta)
        return True
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to apply metadata change: {e}")
        return False


async def check_pending_limit(
    user_id: str,
    session: AsyncSession,
) -> bool:
    """Check if user has reached pending suggestions limit"""
    settings = await get_contribution_settings(session)

    count_query = select(func.count(MetadataSuggestion.id)).where(
        MetadataSuggestion.user_id == user_id,
        MetadataSuggestion.status == STATUS_PENDING,
    )
    count_result = await session.exec(count_query)
    pending_count = count_result.one()

    return pending_count >= settings.max_pending_suggestions_per_user


# ============================================
# User Suggestion Endpoints
# ============================================


@router.post("/metadata/{media_id}/suggest", response_model=SuggestionResponse)
async def create_suggestion(
    media_id: int,
    request: SuggestionCreateRequest,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Submit a metadata correction suggestion.
    Trusted users may have their suggestions auto-approved.
    """
    settings = await get_contribution_settings(session)

    # Check reason requirement
    if settings.require_reason_for_edits and not request.reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A reason is required for edit suggestions",
        )

    # Verify metadata exists by media_id
    meta_query = select(Media).where(Media.id == media_id)
    meta_result = await session.exec(meta_query)
    meta = meta_result.first()

    if not meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found",
        )

    # Validate external ID edits early so users get immediate feedback instead of
    # a suggestion that can never be safely applied.
    external_id_conflict_message = await _get_external_id_conflict_message(
        session,
        media_id,
        request.field_name,
        request.suggested_value,
    )
    if external_id_conflict_message:
        return _api_error_response(external_id_conflict_message, status.HTTP_400_BAD_REQUEST)

    # Check pending suggestions limit
    if await check_pending_limit(current_user.id, session):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You have reached the maximum number of pending suggestions ({settings.max_pending_suggestions_per_user})",
        )

    # Check for duplicate pending suggestion from same user for same field
    existing_query = select(MetadataSuggestion).where(
        MetadataSuggestion.user_id == current_user.id,
        MetadataSuggestion.media_id == media_id,
        MetadataSuggestion.field_name == request.field_name,
        MetadataSuggestion.status == STATUS_PENDING,
    )
    existing_result = await session.exec(existing_query)
    existing = existing_result.first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending suggestion for this field",
        )

    # Check if user qualifies for auto-approval
    can_auto_approve = await should_auto_approve(current_user, session)

    now = datetime.now(pytz.UTC)

    # Create suggestion using media_id
    suggestion = MetadataSuggestion(
        user_id=current_user.id,
        media_id=media_id,
        field_name=request.field_name,
        current_value=request.current_value,
        suggested_value=request.suggested_value,
        reason=request.reason,
        status=STATUS_AUTO_APPROVED if can_auto_approve else STATUS_PENDING,
    )

    # If auto-approved, apply changes immediately
    if can_auto_approve:
        suggestion.reviewed_by = str(current_user.id)  # Self-approved
        suggestion.reviewed_at = now
        suggestion.review_notes = "Auto-approved based on user reputation"

        # Apply the changes
        apply_success = await apply_metadata_changes(meta, request.field_name, request.suggested_value, session)

        if apply_success:
            # Award points for auto-approved suggestion
            await award_points(
                current_user,
                settings.points_per_metadata_edit,
                "metadata",
                session,
            )
        else:
            # If application failed, set to pending for moderator review
            suggestion.status = STATUS_PENDING
            suggestion.reviewed_by = None
            suggestion.reviewed_at = None
            suggestion.review_notes = None

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    if suggestion.status == STATUS_PENDING:
        await send_pending_metadata_suggestion_notification(
            {
                "suggestion_id": suggestion.id,
                "media_id": suggestion.media_id,
                "media_title": meta.title,
                "media_type": meta.type.value if meta.type else None,
                "field_name": suggestion.field_name,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "reason": suggestion.reason,
                "user_id": current_user.id,
                "username": current_user.username or f"User #{current_user.id}",
            }
        )

    media_context = await get_media_context(session, suggestion.media_id)

    return SuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=current_user.username,
        media_id=suggestion.media_id,
        media_title=media_context["media_title"] or meta.title,
        media_type=media_context["media_type"] or (meta.type.value if meta.type else None),
        media_year=media_context["media_year"] if media_context["media_year"] is not None else meta.year,
        media_poster_url=media_context["media_poster_url"],
        media_background_url=media_context["media_background_url"],
        field_name=suggestion.field_name,
        current_value=suggestion.current_value,
        suggested_value=suggestion.suggested_value,
        reason=suggestion.reason,
        status=suggestion.status,
        was_auto_approved=suggestion.status == STATUS_AUTO_APPROVED,
        reviewed_by=current_user.username if suggestion.reviewed_by else None,
        reviewed_at=suggestion.reviewed_at,
        review_notes=suggestion.review_notes,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        user_contribution_level=current_user.contribution_level,
        user_contribution_points=current_user.contribution_points,
    )


@router.get("/suggestions", response_model=SuggestionListResponse)
async def list_my_suggestions(
    suggestion_status: SuggestionStatusLiteral | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List current user's suggestions.
    """
    offset = (page - 1) * page_size

    # Base query
    base_query = select(MetadataSuggestion).where(MetadataSuggestion.user_id == current_user.id)
    count_query = select(func.count(MetadataSuggestion.id)).where(MetadataSuggestion.user_id == current_user.id)

    # Filter by status
    if suggestion_status:
        base_query = base_query.where(MetadataSuggestion.status == suggestion_status)
        count_query = count_query.where(MetadataSuggestion.status == suggestion_status)

    # Order and paginate
    base_query = base_query.order_by(MetadataSuggestion.created_at.desc())
    base_query = base_query.offset(offset).limit(page_size)

    # Execute
    result = await session.exec(base_query)
    suggestions = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    media_context_map = await get_media_context_map(session, [s.media_id for s in suggestions])

    # Build responses with media context
    responses = []
    for s in suggestions:
        media_context = media_context_map.get(s.media_id, _empty_media_context())
        reviewer_name = await get_username(session, int(s.reviewed_by)) if s.reviewed_by else None
        responses.append(
            SuggestionResponse(
                id=s.id,
                user_id=s.user_id,
                username=current_user.username,
                media_id=s.media_id,
                media_title=media_context["media_title"],
                media_type=media_context["media_type"],
                media_year=media_context["media_year"],
                media_poster_url=media_context["media_poster_url"],
                media_background_url=media_context["media_background_url"],
                field_name=s.field_name,
                current_value=s.current_value,
                suggested_value=s.suggested_value,
                reason=s.reason,
                status=s.status,
                was_auto_approved=s.status == STATUS_AUTO_APPROVED,
                reviewed_by=reviewer_name,
                reviewed_at=s.reviewed_at,
                review_notes=s.review_notes,
                created_at=s.created_at,
                updated_at=s.updated_at,
                user_contribution_level=current_user.contribution_level,
                user_contribution_points=current_user.contribution_points,
            )
        )

    return SuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(suggestions)) < total,
    )


@router.get("/contributions/me", response_model=UserContributionInfo)
async def get_my_contribution_info(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get current user's contribution information.
    """
    settings = await get_contribution_settings(session)
    can_auto = await should_auto_approve(current_user, session)

    # Calculate points to next level
    current_points = current_user.contribution_points
    current_level = current_user.contribution_level

    if current_level == "new":
        next_level = "contributor"
        points_to_next = max(0, settings.contributor_threshold - current_points)
    elif current_level == "contributor":
        next_level = "trusted"
        points_to_next = max(0, settings.trusted_threshold - current_points)
    elif current_level == "trusted":
        next_level = "expert"
        points_to_next = max(0, settings.expert_threshold - current_points)
    else:
        next_level = None
        points_to_next = 0

    return UserContributionInfo(
        contribution_points=current_user.contribution_points,
        contribution_level=current_user.contribution_level,
        metadata_edits_approved=current_user.metadata_edits_approved,
        stream_edits_approved=current_user.stream_edits_approved,
        can_auto_approve=can_auto,
        points_to_next_level=points_to_next,
        next_level=next_level,
    )


# ============================================
# Specific routes (must be before parameterized routes)
# ============================================


@router.get("/suggestions/pending", response_model=SuggestionListResponse)
async def list_pending_suggestions(
    field_name: str | None = Query(None),
    suggestion_status: Literal["pending", "approved", "rejected", "auto_approved", "all"] | None = Query(
        None, alias="status"
    ),
    uploader_query: str | None = Query(None, max_length=120),
    reviewer_query: str | None = Query(None, max_length=120),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    List moderator-visible metadata suggestions.
    Defaults to pending unless `status` is provided.
    """
    offset = (page - 1) * page_size

    # Base query. Default is pending-only for backwards compatibility.
    base_query = select(MetadataSuggestion)
    count_query = select(func.count(MetadataSuggestion.id))

    if suggestion_status and suggestion_status != "all":
        base_query = base_query.where(MetadataSuggestion.status == suggestion_status)
        count_query = count_query.where(MetadataSuggestion.status == suggestion_status)
    elif suggestion_status is None:
        base_query = base_query.where(MetadataSuggestion.status == STATUS_PENDING)
        count_query = count_query.where(MetadataSuggestion.status == STATUS_PENDING)

    # Filter by field name
    if field_name:
        base_query = base_query.where(MetadataSuggestion.field_name == field_name)
        count_query = count_query.where(MetadataSuggestion.field_name == field_name)

    normalized_uploader_query = _normalize_filter_query(uploader_query)
    if normalized_uploader_query:
        uploader_alias = aliased(User)
        base_query = base_query.join(uploader_alias, MetadataSuggestion.user_id == uploader_alias.id)
        count_query = count_query.join(uploader_alias, MetadataSuggestion.user_id == uploader_alias.id)

        if normalized_uploader_query.isdigit():
            uploader_id = int(normalized_uploader_query)
            uploader_condition = or_(
                MetadataSuggestion.user_id == uploader_id,
                uploader_alias.username.ilike(f"%{normalized_uploader_query}%"),
            )
        else:
            uploader_condition = uploader_alias.username.ilike(f"%{normalized_uploader_query}%")

        base_query = base_query.where(uploader_condition)
        count_query = count_query.where(uploader_condition)

    normalized_reviewer_query = _normalize_filter_query(reviewer_query)
    if normalized_reviewer_query:
        if normalized_reviewer_query.lower() == "auto":
            base_query = base_query.where(MetadataSuggestion.reviewed_by == "auto")
            count_query = count_query.where(MetadataSuggestion.reviewed_by == "auto")
        else:
            reviewer_alias = aliased(User)
            base_query = base_query.join(
                reviewer_alias,
                cast(reviewer_alias.id, String) == MetadataSuggestion.reviewed_by,
            )
            count_query = count_query.join(
                reviewer_alias,
                cast(reviewer_alias.id, String) == MetadataSuggestion.reviewed_by,
            )

            if normalized_reviewer_query.isdigit():
                reviewer_id = int(normalized_reviewer_query)
                reviewer_condition = or_(
                    MetadataSuggestion.reviewed_by == str(reviewer_id),
                    reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%"),
                )
            else:
                reviewer_condition = reviewer_alias.username.ilike(f"%{normalized_reviewer_query}%")

            base_query = base_query.where(reviewer_condition)
            count_query = count_query.where(reviewer_condition)

    # Order and paginate
    base_query = base_query.order_by(MetadataSuggestion.created_at.desc())
    base_query = base_query.offset(offset).limit(page_size)

    # Execute
    result = await session.exec(base_query)
    suggestions = result.all()

    count_result = await session.exec(count_query)
    total = count_result.one()

    media_context_map = await get_media_context_map(session, [s.media_id for s in suggestions])

    # Build responses with user contribution info
    responses = []
    for s in suggestions:
        media_context = media_context_map.get(s.media_id, _empty_media_context())
        username = await get_username(session, s.user_id)
        reviewer_name = await get_username(session, int(s.reviewed_by)) if s.reviewed_by else None

        # Get user contribution info
        user_query = select(User).where(User.id == s.user_id)
        user_result = await session.exec(user_query)
        suggestion_user = user_result.first()

        responses.append(
            SuggestionResponse(
                id=s.id,
                user_id=s.user_id,
                username=username,
                media_id=s.media_id,
                media_title=media_context["media_title"],
                media_type=media_context["media_type"],
                media_year=media_context["media_year"],
                media_poster_url=media_context["media_poster_url"],
                media_background_url=media_context["media_background_url"],
                field_name=s.field_name,
                current_value=s.current_value,
                suggested_value=s.suggested_value,
                reason=s.reason,
                status=s.status,
                was_auto_approved=s.status == STATUS_AUTO_APPROVED,
                reviewed_by=reviewer_name,
                reviewed_at=s.reviewed_at,
                review_notes=s.review_notes,
                created_at=s.created_at,
                updated_at=s.updated_at,
                user_contribution_level=suggestion_user.contribution_level if suggestion_user else None,
                user_contribution_points=suggestion_user.contribution_points if suggestion_user else None,
            )
        )

    return SuggestionListResponse(
        suggestions=responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(suggestions)) < total,
    )


@router.post("/suggestions/bulk-review")
async def bulk_review_suggestions(
    suggestion_ids: list[str],
    action: Literal["approve", "reject"],
    review_notes: str | None = None,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Bulk review multiple suggestions (moderator only).
    """
    settings = await get_contribution_settings(session)
    now = datetime.now(pytz.UTC)

    results = {"approved": 0, "rejected": 0, "skipped": 0}

    for suggestion_id in suggestion_ids:
        query = select(MetadataSuggestion).where(MetadataSuggestion.id == suggestion_id)
        result = await session.exec(query)
        suggestion = result.first()

        if not suggestion or suggestion.status != STATUS_PENDING:
            results["skipped"] += 1
            continue

        # Get author
        author_query = select(User).where(User.id == suggestion.user_id)
        author_result = await session.exec(author_query)
        author = author_result.first()

        if action == "approve":
            external_id_conflict_message = await _get_external_id_conflict_message(
                session,
                suggestion.media_id,
                suggestion.field_name,
                suggestion.suggested_value,
            )
            if external_id_conflict_message:
                logger.warning(
                    "Skipping suggestion %s due to external ID conflict: %s",
                    suggestion.id,
                    external_id_conflict_message,
                )
                results["skipped"] += 1
                continue

            meta_query = select(Media).where(Media.id == suggestion.media_id)
            meta_result = await session.exec(meta_query)
            meta = meta_result.first()
            if not meta:
                logger.warning(
                    "Skipping suggestion %s because metadata %s was not found", suggestion.id, suggestion.media_id
                )
                results["skipped"] += 1
                continue

            apply_success = await apply_metadata_changes(
                meta, suggestion.field_name, suggestion.suggested_value, session
            )

            if not apply_success:
                logger.warning("Skipping suggestion %s because apply_metadata_changes returned false", suggestion.id)
                results["skipped"] += 1
                continue

            suggestion.status = STATUS_APPROVED
            suggestion.reviewed_by = str(current_user.id)
            suggestion.reviewed_at = now
            suggestion.review_notes = review_notes
            suggestion.updated_at = now

            if author:
                await award_points(author, settings.points_per_metadata_edit, "metadata", session)

            results["approved"] += 1
        else:
            suggestion.status = STATUS_REJECTED
            suggestion.reviewed_by = str(current_user.id)
            suggestion.reviewed_at = now
            suggestion.review_notes = review_notes
            suggestion.updated_at = now

            if author and settings.points_for_rejection_penalty < 0:
                await award_points(author, settings.points_for_rejection_penalty, "metadata", session)
            results["rejected"] += 1

        session.add(suggestion)

    await session.commit()

    return results


@router.get("/suggestions/stats", response_model=SuggestionStatsResponse)
async def get_suggestion_stats(
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get suggestion statistics.
    For moderators: shows global stats including today's counts.
    For users: shows their own stats.
    """
    is_moderator = current_user.role in [UserRole.MODERATOR, UserRole.ADMIN]

    # Initialize today's stats
    approved_today = 0
    rejected_today = 0

    # Global stats (for moderators)
    if is_moderator:
        total_result = await session.exec(select(func.count(MetadataSuggestion.id)))
        total = total_result.one()

        pending_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(MetadataSuggestion.status == STATUS_PENDING)
        )
        pending = pending_result.one()

        approved_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(MetadataSuggestion.status == STATUS_APPROVED)
        )
        approved = approved_result.one()

        auto_approved_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(MetadataSuggestion.status == STATUS_AUTO_APPROVED)
        )
        auto_approved = auto_approved_result.one()

        rejected_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(MetadataSuggestion.status == STATUS_REJECTED)
        )
        rejected = rejected_result.one()

        # Today's stats - reviewed_at is set when a suggestion is approved/rejected
        today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        approved_today_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(
                MetadataSuggestion.status.in_([STATUS_APPROVED, STATUS_AUTO_APPROVED]),
                MetadataSuggestion.reviewed_at >= today_start,
            )
        )
        approved_today = approved_today_result.one()

        rejected_today_result = await session.exec(
            select(func.count(MetadataSuggestion.id)).where(
                MetadataSuggestion.status == STATUS_REJECTED,
                MetadataSuggestion.reviewed_at >= today_start,
            )
        )
        rejected_today = rejected_today_result.one()
    else:
        total = pending = approved = auto_approved = rejected = 0

    # User stats
    user_pending_result = await session.exec(
        select(func.count(MetadataSuggestion.id)).where(
            MetadataSuggestion.user_id == current_user.id,
            MetadataSuggestion.status == STATUS_PENDING,
        )
    )
    user_pending = user_pending_result.one()

    user_approved_result = await session.exec(
        select(func.count(MetadataSuggestion.id)).where(
            MetadataSuggestion.user_id == current_user.id,
            MetadataSuggestion.status == STATUS_APPROVED,
        )
    )
    user_approved = user_approved_result.one()

    user_auto_approved_result = await session.exec(
        select(func.count(MetadataSuggestion.id)).where(
            MetadataSuggestion.user_id == current_user.id,
            MetadataSuggestion.status == STATUS_AUTO_APPROVED,
        )
    )
    user_auto_approved = user_auto_approved_result.one()

    user_rejected_result = await session.exec(
        select(func.count(MetadataSuggestion.id)).where(
            MetadataSuggestion.user_id == current_user.id,
            MetadataSuggestion.status == STATUS_REJECTED,
        )
    )
    user_rejected = user_rejected_result.one()

    return SuggestionStatsResponse(
        total=total,
        pending=pending,
        approved=approved,
        auto_approved=auto_approved,
        rejected=rejected,
        approved_today=approved_today,
        rejected_today=rejected_today,
        user_pending=user_pending,
        user_approved=user_approved,
        user_auto_approved=user_auto_approved,
        user_rejected=user_rejected,
        user_contribution_points=current_user.contribution_points,
        user_contribution_level=current_user.contribution_level,
    )


# ============================================
# Parameterized routes (must be after specific routes)
# ============================================


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionResponse)
async def get_suggestion(
    suggestion_id: str,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get a specific suggestion.
    Users can only view their own suggestions unless they are moderators.
    """
    query = select(MetadataSuggestion).where(MetadataSuggestion.id == suggestion_id)
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    # Check access
    is_moderator = current_user.role in [UserRole.MODERATOR, UserRole.ADMIN]
    if suggestion.user_id != current_user.id and not is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    media_context = await get_media_context(session, suggestion.media_id)
    username = await get_username(session, suggestion.user_id)
    reviewer_name = await get_username(session, int(suggestion.reviewed_by)) if suggestion.reviewed_by else None

    # Get user contribution info
    user_query = select(User).where(User.id == suggestion.user_id)
    user_result = await session.exec(user_query)
    suggestion_user = user_result.first()

    return SuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=username,
        media_id=suggestion.media_id,
        media_title=media_context["media_title"],
        media_type=media_context["media_type"],
        media_year=media_context["media_year"],
        media_poster_url=media_context["media_poster_url"],
        media_background_url=media_context["media_background_url"],
        field_name=suggestion.field_name,
        current_value=suggestion.current_value,
        suggested_value=suggestion.suggested_value,
        reason=suggestion.reason,
        status=suggestion.status,
        was_auto_approved=suggestion.status == STATUS_AUTO_APPROVED,
        reviewed_by=reviewer_name,
        reviewed_at=suggestion.reviewed_at,
        review_notes=suggestion.review_notes,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        user_contribution_level=suggestion_user.contribution_level if suggestion_user else None,
        user_contribution_points=suggestion_user.contribution_points if suggestion_user else None,
    )


@router.delete("/suggestions/{suggestion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suggestion(
    suggestion_id: str,
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Delete a pending suggestion. Users can only delete their own pending suggestions.
    """
    query = select(MetadataSuggestion).where(
        MetadataSuggestion.id == suggestion_id,
        MetadataSuggestion.user_id == current_user.id,
    )
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete pending suggestions",
        )

    await session.delete(suggestion)
    await session.commit()


@router.put("/suggestions/{suggestion_id}/review", response_model=SuggestionResponse)
async def review_suggestion(
    suggestion_id: str,
    request: SuggestionReviewRequest,
    current_user: User = Depends(require_role(UserRole.MODERATOR)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Review and approve/reject a suggestion (moderator only).
    If approved, the metadata will be updated and points awarded.
    If rejected, points may be deducted based on settings.
    """
    settings = await get_contribution_settings(session)

    query = select(MetadataSuggestion).where(MetadataSuggestion.id == suggestion_id)
    result = await session.exec(query)
    suggestion = result.first()

    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found",
        )

    if suggestion.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suggestion has already been reviewed",
        )

    # Get the suggestion author
    author_query = select(User).where(User.id == suggestion.user_id)
    author_result = await session.exec(author_query)
    author = author_result.first()

    now = datetime.now(pytz.UTC)

    # If approved, update the metadata and award points
    if request.action == "approve":
        external_id_conflict_message = await _get_external_id_conflict_message(
            session,
            suggestion.media_id,
            suggestion.field_name,
            suggestion.suggested_value,
        )
        if external_id_conflict_message:
            return _api_error_response(external_id_conflict_message, status.HTTP_400_BAD_REQUEST)

        meta_query = select(Media).where(Media.id == suggestion.media_id)
        meta_result = await session.exec(meta_query)
        meta = meta_result.first()
        if not meta:
            return _api_error_response("Metadata not found for this suggestion", status.HTTP_404_NOT_FOUND)

        apply_success = await apply_metadata_changes(meta, suggestion.field_name, suggestion.suggested_value, session)

        if not apply_success:
            return _api_error_response(
                ("Failed to apply this metadata suggestion. Please verify the suggested value format and try again."),
                status.HTTP_400_BAD_REQUEST,
            )

        if author:
            # Award points for approved suggestion
            await award_points(
                author,
                settings.points_per_metadata_edit,
                "metadata",
                session,
            )
        suggestion.status = STATUS_APPROVED
    elif request.action == "reject" and author:
        # Apply rejection penalty if configured
        if settings.points_for_rejection_penalty < 0:
            await award_points(
                author,
                settings.points_for_rejection_penalty,
                "metadata",
                session,
            )
        suggestion.status = STATUS_REJECTED
    elif request.action == "reject":
        suggestion.status = STATUS_REJECTED

    suggestion.reviewed_by = str(current_user.id)
    suggestion.reviewed_at = now
    suggestion.review_notes = request.review_notes
    suggestion.updated_at = now

    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    # Refresh author to get updated points
    if author:
        await session.refresh(author)

    media_context = await get_media_context(session, suggestion.media_id)
    username = await get_username(session, suggestion.user_id)

    return SuggestionResponse(
        id=suggestion.id,
        user_id=suggestion.user_id,
        username=username,
        media_id=suggestion.media_id,
        media_title=media_context["media_title"],
        media_type=media_context["media_type"],
        media_year=media_context["media_year"],
        media_poster_url=media_context["media_poster_url"],
        media_background_url=media_context["media_background_url"],
        field_name=suggestion.field_name,
        current_value=suggestion.current_value,
        suggested_value=suggestion.suggested_value,
        reason=suggestion.reason,
        status=suggestion.status,
        was_auto_approved=False,
        reviewed_by=current_user.username,
        reviewed_at=suggestion.reviewed_at,
        review_notes=suggestion.review_notes,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
        user_contribution_level=author.contribution_level if author else None,
        user_contribution_points=author.contribution_points if author else None,
    )
