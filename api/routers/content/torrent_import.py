"""
Torrent Import API endpoints for importing magnet links and torrent files.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

import pytz
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.anonymous_utils import normalize_anonymous_display_name, resolve_uploader_identity
from api.routers.content.contributions import award_import_approval_points
from api.routers.content.import_title_validation import is_import_metadata_adult, resolve_and_validate_import_title
from api.routers.content.upload_guard import enforce_upload_permissions
from api.routers.user.auth import require_auth
from db.config import settings
from db.crud.media import (
    get_canonical_external_id,
    get_primary_image,
    get_or_create_metadata_provider,
    get_or_create_episode,
    get_or_create_season,
    get_media_by_external_id,
    get_media_by_id,
    get_media_by_title_year,
)
from db.crud.reference import (
    get_or_create_audio_channel,
    get_or_create_audio_format,
    get_or_create_catalog,
    get_or_create_hdr_format,
    get_or_create_language,
)
from db.crud.scraper_helpers import get_or_create_metadata
from db.database import get_async_session_context
from db.enums import ContributionStatus, MediaType, TorrentType, UserRole
from db.models import (
    Contribution,
    Episode,
    Media,
    MediaCatalogLink,
    MediaImage,
    Season,
    SeriesMetadata,
    Stream,
    StreamFile,
    StreamMediaLink,
    User,
)
from db.models.streams import (
    FileMediaLink,
    StreamAudioLink,
    StreamChannelLink,
    StreamHDRLink,
    StreamLanguageLink,
    StreamType,
    TorrentStream,
)
from utils import torrent
from utils.notification_registry import send_pending_contribution_notification
from utils.parser import convert_bytes_to_readable
from utils.sports_parser import (
    build_sports_match_search_terms,
    clean_sports_context_title,
    derive_sports_episode_title,
    detect_sports_category,
    normalize_sports_match_text,
    parse_sports_title,
    pick_best_sports_source_title,
    tokenize_sports_match_text,
)

logger = logging.getLogger(__name__)
SPORTS_SERIES_CATEGORIES = {"formula_racing", "motogp_racing"}
ADULT_CONTENT_METADATA_ERROR_MESSAGE = "Adult content metadata is not allowed in user contributions."


def _resolve_sports_media_type(sports_category: str | None) -> MediaType:
    """Map sports categories to persisted media types."""
    if sports_category in SPORTS_SERIES_CATEGORIES:
        return MediaType.SERIES
    return MediaType.MOVIE


def _resolve_fetch_media_type(meta_type: str, sports_category: str | None = None) -> str:
    """Map import meta_type to fetch/create media_type expected by metadata helpers."""
    if meta_type == "series":
        return "series"
    if meta_type == "sports":
        return "series" if _resolve_sports_media_type(sports_category) == MediaType.SERIES else "movie"
    return "movie"


async def _upsert_import_media_images(
    session: AsyncSession,
    media_id: int,
    *,
    poster: str | None = None,
    background: str | None = None,
    logo: str | None = None,
) -> None:
    """Persist user-supplied media images for imports."""
    image_values = {
        "poster": poster,
        "background": background,
        "logo": logo,
    }
    normalized_images = {
        image_type: str(url).strip()
        for image_type, url in image_values.items()
        if isinstance(url, str) and str(url).strip()
    }
    if not normalized_images:
        return

    provider = await get_or_create_metadata_provider(session, "mediafusion", "MediaFusion")

    for image_type, image_url in normalized_images.items():
        existing_result = await session.exec(
            select(MediaImage).where(
                MediaImage.media_id == media_id,
                MediaImage.image_type == image_type,
                MediaImage.is_primary.is_(True),
            )
        )
        existing = existing_result.first()
        if existing:
            existing.url = image_url
            existing.provider_id = provider.id
        else:
            session.add(
                MediaImage(
                    media_id=media_id,
                    provider_id=provider.id,
                    image_type=image_type,
                    url=image_url,
                    is_primary=True,
                )
            )


async def _notify_pending_contribution(
    contribution: Contribution,
    user: User,
    is_anonymous: bool,
    anonymous_display_name: str | None,
) -> None:
    """Notify moderators when a contribution is pending review."""
    if contribution.status != ContributionStatus.PENDING:
        return

    uploader_name, _ = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)
    await send_pending_contribution_notification(
        {
            "contribution_id": contribution.id,
            "contribution_type": contribution.contribution_type,
            "target_id": contribution.target_id,
            "uploader_name": uploader_name,
            "data": contribution.data,
        }
    )


async def _get_stream_media_attachment_details(
    session: AsyncSession,
    stream_id: int,
    max_items: int = 2,
) -> dict[str, Any]:
    """Get a compact description of media entries linked to a stream."""
    link_result = await session.exec(
        select(StreamMediaLink)
        .where(StreamMediaLink.stream_id == stream_id)
        .order_by(StreamMediaLink.is_primary.desc(), StreamMediaLink.id.asc())
    )
    links = link_result.all()
    if not links:
        return {"count": 0, "items": []}

    items: list[dict[str, Any]] = []
    for link in links[:max_items]:
        media = await get_media_by_id(session, link.media_id)
        if not media:
            continue
        external_id = await get_canonical_external_id(session, media.id)
        items.append(
            {
                "media_id": media.id,
                "external_id": external_id,
                "title": media.title,
                "year": media.year,
                "type": media.type.value,
                "file_index": link.file_index,
                "is_primary": link.is_primary,
            }
        )

    return {"count": len(links), "items": items}


def _build_existing_torrent_warning_message(
    info_hash: str,
    attachment_details: dict[str, Any],
) -> str:
    """Build a user-facing warning for duplicate torrent uploads."""
    items = attachment_details.get("items") or []
    linked_count = attachment_details.get("count") or 0

    base_message = "⚠️ Upload skipped: this torrent already exists in MediaFusion."
    if items:
        first_item = items[0]
        title = first_item.get("title") or "Unknown title"
        year = first_item.get("year")
        media_type = first_item.get("type") or "media"
        external_id = first_item.get("external_id") or f"mf:{first_item.get('media_id')}"
        year_suffix = f" ({year})" if year else ""
        extra_suffix = f" and {linked_count - 1} more linked media item(s)" if linked_count > 1 else ""
        base_message += f" Already attached to {title}{year_suffix} [{media_type}, {external_id}]{extra_suffix}."
    else:
        base_message += " Existing stream linkage metadata could not be resolved."

    return (
        f"{base_message} Thank you for trying to contribute ✨. "
        f"If you cannot find it, contact support with this info hash ({info_hash})."
    )


def _parse_csv_form_values(raw_value: str | None) -> list[str]:
    """Parse comma-separated form values into a clean list."""
    if not raw_value:
        return []
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def _normalize_string_list(raw_value: Any) -> list[str]:
    """Normalize an arbitrary value to a list of non-empty strings."""
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        return _parse_csv_form_values(raw_value)
    if isinstance(raw_value, list):
        return [value.strip() for value in raw_value if isinstance(value, str) and value.strip()]
    return []


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    """De-duplicate values while preserving first-seen order."""
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _resolve_import_multi_value(form_value: str | None, torrent_data: dict[str, Any], key: str) -> list[str]:
    """Prefer form list values; fallback to parsed torrent values for a given key."""
    parsed_form_values = _parse_csv_form_values(form_value)
    if parsed_form_values:
        return _dedupe_preserve_order(parsed_form_values)
    return _dedupe_preserve_order(_normalize_string_list(torrent_data.get(key)))


def _collect_torrent_title_candidates(torrent_data: dict[str, Any]) -> list[str]:
    """Collect raw torrent source names for adult-keyword validation."""
    candidates: list[str] = []

    raw_torrent_name = torrent_data.get("torrent_name")
    if isinstance(raw_torrent_name, str):
        normalized = raw_torrent_name.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    for file_info in torrent_data.get("file_data", []) or []:
        if not isinstance(file_info, dict):
            continue
        filename = file_info.get("filename")
        if isinstance(filename, str):
            normalized = filename.strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)

    return candidates


def _resolve_import_languages(form_languages: str | None, torrent_data: dict[str, Any]) -> list[str]:
    """Prefer user-provided languages; fallback to parsed torrent languages."""
    return _resolve_import_multi_value(form_languages, torrent_data, "languages")


def _resolve_created_at_date(form_created_at: str | None, torrent_data: dict[str, Any]) -> str | None:
    """Resolve release date (YYYY-MM-DD) from form input or torrent metadata."""
    if form_created_at:
        return form_created_at
    created_at = torrent_data.get("created_at")
    if isinstance(created_at, datetime):
        return created_at.date().isoformat()
    return None


def _parse_iso_date(value: str | None):
    """Parse ISO date/datetime into a date object."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _select_sports_source_title(torrent_data: dict[str, Any]) -> str:
    """Pick the most informative title candidate for sports parsing."""
    parsed_title = (torrent_data.get("title") or "").strip()
    torrent_name = (torrent_data.get("torrent_name") or "").strip()
    return pick_best_sports_source_title(parsed_title, torrent_name)


def _enrich_sports_file_data(file_data: list[dict[str, Any]], context_title: str) -> list[dict[str, Any]]:
    """Enrich sports file entries with better episode titles and ordering."""
    enriched_files: list[dict[str, Any]] = []
    for idx, file_info in enumerate(file_data):
        file_entry = dict(file_info)
        filename = str(file_entry.get("filename") or "")
        file_entry["episode_title"] = derive_sports_episode_title(filename, context_title)
        if file_entry.get("episode_number") is None:
            file_entry["episode_number"] = idx + 1
        enriched_files.append(file_entry)
    return enriched_files


def _build_torrent_analysis_fields(meta_type: str, torrent_data: dict[str, Any]) -> dict[str, Any]:
    """Build analysis fields, using sports parser when importing sports content."""
    if meta_type != "sports":
        return {
            "parsed_title": torrent_data.get("title"),
            "year": torrent_data.get("year"),
            "resolution": torrent_data.get("resolution"),
            "quality": torrent_data.get("quality"),
            "codec": torrent_data.get("codec"),
            "audio": _normalize_string_list(torrent_data.get("audio")),
            "hdr": _normalize_string_list(torrent_data.get("hdr")),
            "languages": _normalize_string_list(torrent_data.get("languages")),
            "sports_category": None,
            "sports_event": None,
            "sports_league": None,
            "sports_event_date": None,
        }

    source_title = _select_sports_source_title(torrent_data)
    parsed = parse_sports_title(source_title)
    cleaned_context_title = clean_sports_context_title(source_title)
    file_data = torrent_data.get("file_data", []) or []
    if not parsed.category:
        for file_info in file_data:
            detected = detect_sports_category(str(file_info.get("filename") or ""))
            if detected:
                parsed.category = detected
                break

    enriched_file_data = _enrich_sports_file_data(file_data, source_title) if file_data else file_data
    torrent_data["file_data"] = enriched_file_data

    parsed_audio = [parsed.audio] if parsed.audio else []
    raw_audio = _normalize_string_list(torrent_data.get("audio"))
    merged_audio = parsed_audio + [item for item in raw_audio if item not in parsed_audio]
    languages = parsed.languages if parsed.languages else _normalize_string_list(torrent_data.get("languages"))

    return {
        "parsed_title": cleaned_context_title
        or parsed.title
        or torrent_data.get("title")
        or torrent_data.get("torrent_name"),
        "year": parsed.year or torrent_data.get("year"),
        "resolution": parsed.resolution or torrent_data.get("resolution"),
        "quality": parsed.quality or torrent_data.get("quality"),
        "codec": parsed.codec or torrent_data.get("codec"),
        "audio": merged_audio,
        "hdr": _normalize_string_list(torrent_data.get("hdr")),
        "languages": languages,
        "sports_category": parsed.category,
        "sports_event": clean_sports_context_title(parsed.event or "") or parsed.event,
        "sports_league": parsed.league,
        "sports_event_date": parsed.event_date.isoformat() if parsed.event_date else None,
    }


def _resolve_catalogs(meta_type: str, raw_catalogs: str | None, sports_category: str | None) -> list[str]:
    """Resolve final catalog list, ensuring sports content has a sports catalog."""
    catalogs = _parse_csv_form_values(raw_catalogs)
    if meta_type == "sports":
        resolved_sports_category = sports_category or "other_sports"
        if resolved_sports_category not in catalogs:
            catalogs.insert(0, resolved_sports_category)
    return catalogs


def _normalize_import_file_data(file_entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize incoming file annotations from UI/analyzer payloads."""
    normalized: list[dict[str, Any]] = []
    for raw_entry in file_entries or []:
        if not isinstance(raw_entry, dict):
            continue
        entry = dict(raw_entry)

        # UI sends per-file sports episode names as "title".
        raw_manual_title = entry.get("title")
        if raw_manual_title is not None:
            manual_title = str(raw_manual_title).strip()
            if manual_title and not entry.get("episode_title"):
                entry["episode_title"] = manual_title

        normalized.append(entry)
    return normalized


def _should_replace_episode_title(
    current_title: str | None,
    proposed_title: str,
    filename: str | None,
    episode_number: int,
) -> bool:
    """Replace auto/filename placeholders, but keep real editorial titles."""
    cleaned_current = str(current_title or "").strip()
    if not cleaned_current:
        return True
    if cleaned_current == proposed_title:
        return False

    cleaned_filename = str(filename or "").strip()
    if cleaned_filename and cleaned_current.casefold() == cleaned_filename.casefold():
        return True

    if re.fullmatch(rf"Episode\s+{episode_number}", cleaned_current, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"Episode\s+\d+", cleaned_current, flags=re.IGNORECASE):
        return True

    return False


def _normalize_sports_import_metadata(
    meta_type: str,
    torrent_data: dict[str, Any],
    sports_category: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Normalize parsed torrent metadata for sports imports."""
    if meta_type != "sports":
        return torrent_data, sports_category

    source_title = _select_sports_source_title(torrent_data)
    parsed = parse_sports_title(source_title)
    cleaned_context_title = clean_sports_context_title(source_title)
    normalized = dict(torrent_data)
    file_data = normalized.get("file_data", []) or []
    if file_data:
        normalized["file_data"] = _enrich_sports_file_data(file_data, source_title)

    if cleaned_context_title:
        normalized["title"] = cleaned_context_title
    elif parsed.title:
        normalized["title"] = parsed.title
    if parsed.year:
        normalized["year"] = parsed.year
    if parsed.resolution and not normalized.get("resolution"):
        normalized["resolution"] = parsed.resolution
    if parsed.quality and not normalized.get("quality"):
        normalized["quality"] = parsed.quality
    if parsed.codec and not normalized.get("codec"):
        normalized["codec"] = parsed.codec
    if parsed.audio and not normalized.get("audio"):
        normalized["audio"] = [parsed.audio]
    if parsed.languages and not normalized.get("languages"):
        normalized["languages"] = parsed.languages

    if not parsed.category:
        for file_info in file_data:
            detected = detect_sports_category(str(file_info.get("filename") or ""))
            if detected:
                parsed.category = detected
                break

    resolved_sports_category = sports_category or parsed.category or "other_sports"
    return normalized, resolved_sports_category


async def _search_existing_sports_matches(
    session: AsyncSession,
    parsed_title: str | None,
    event_name: str | None,
    year: int | None,
    sports_league: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search existing movie/series media as selectable sports matches."""
    search_terms = build_sports_match_search_terms(parsed_title, event_name, sports_league)
    if not search_terms:
        return []

    conditions = [Media.title.ilike(f"%{term}%") for term in search_terms]
    query = (
        select(Media)
        .where(Media.type.in_([MediaType.MOVIE, MediaType.SERIES]))
        .where(or_(*conditions))
        .order_by(Media.last_stream_added.desc().nullslast())
    )
    if year:
        query = query.where(or_(Media.year == year, Media.year.is_(None)))
    query = query.limit(limit)

    result = await session.exec(query)
    medias = result.all()

    # Fallback: token-overlap matching for cases where titles differ by
    # broadcaster noise/round formatting between 1080p and 4k uploads.
    if not medias:
        fallback_query = (
            select(Media)
            .where(Media.type.in_([MediaType.MOVIE, MediaType.SERIES]))
            .order_by(Media.last_stream_added.desc().nullslast())
        )
        if year:
            fallback_query = fallback_query.where(
                or_(Media.year == year, Media.year == year - 1, Media.year == year + 1, Media.year.is_(None))
            )
        fallback_query = fallback_query.limit(200)

        fallback_result = await session.exec(fallback_query)
        fallback_candidates = fallback_result.all()

        target_tokens: set[str] = set()
        for term in search_terms:
            target_tokens.update(tokenize_sports_match_text(term))

        if target_tokens:
            scored_candidates: list[tuple[int, Media]] = []
            for candidate in fallback_candidates:
                candidate_tokens = tokenize_sports_match_text(candidate.title or "")
                overlap = target_tokens & candidate_tokens
                if len(overlap) < 2:
                    continue

                score = len(overlap) * 10
                if year and candidate.year == year:
                    score += 5
                elif candidate.year is None:
                    score += 1

                candidate_norm = normalize_sports_match_text(candidate.title or "")
                if any(
                    term_norm and (term_norm in candidate_norm or candidate_norm in term_norm)
                    for term_norm in (normalize_sports_match_text(term) for term in search_terms)
                ):
                    score += 3

                scored_candidates.append((score, candidate))

            scored_candidates.sort(key=lambda item: item[0], reverse=True)
            medias = [media for _, media in scored_candidates[:limit]]

    matches: list[dict[str, Any]] = []
    for media in medias:
        external_id = await get_canonical_external_id(session, media.id)
        match_id = external_id or f"mf:{media.id}"
        poster_image = await get_primary_image(session, media.id, "poster")
        background_image = await get_primary_image(session, media.id, "background")
        logo_image = await get_primary_image(session, media.id, "logo")
        matches.append(
            {
                "id": match_id,
                "media_id": media.id,
                "title": media.title,
                "year": media.year,
                "type": "series" if media.type == MediaType.SERIES else "movie",
                "imdb_id": external_id if external_id and external_id.startswith("tt") else None,
                "description": media.description,
                "poster": poster_image.url if poster_image else None,
                "background": background_image.url if background_image else None,
                "logo": logo_image.url if logo_image else None,
                "release_date": media.release_date.isoformat() if media.release_date else None,
            }
        )
    return matches


async def _ensure_series_episode_metadata(
    session: AsyncSession,
    media: Media,
    file_entries: list[dict[str, Any]],
    fallback_title: str,
) -> None:
    """Ensure series metadata has seasons/episodes for file-linked imports."""
    if media.type != MediaType.SERIES:
        return

    result = await session.exec(select(SeriesMetadata).where(SeriesMetadata.media_id == media.id))
    series_meta = result.first()
    if not series_meta:
        series_meta = SeriesMetadata(media_id=media.id)
        session.add(series_meta)
        await session.flush()

    normalized_entries = file_entries or [
        {
            "season_number": 1,
            "episode_number": 1,
            "episode_title": fallback_title or media.title or "Episode 1",
        }
    ]

    touched_season_ids: set[int] = set()
    for idx, file_info in enumerate(normalized_entries):
        season_number = file_info.get("season_number")
        episode_number = file_info.get("episode_number")

        if season_number is None:
            season_number = 1
        if episode_number is None:
            episode_number = idx + 1

        season = await get_or_create_season(
            session,
            series_meta.id,
            season_number,
            name=f"Season {season_number}",
        )
        touched_season_ids.add(season.id)

        raw_episode_title = (
            file_info.get("episode_title")
            or file_info.get("title")
            or file_info.get("filename")
            or f"Episode {episode_number}"
        )
        episode_title = str(raw_episode_title).strip() or f"Episode {episode_number}"
        episode = await get_or_create_episode(session, season.id, episode_number, title=episode_title)
        if _should_replace_episode_title(
            episode.title,
            episode_title,
            str(file_info.get("filename") or ""),
            episode_number,
        ):
            episode.title = episode_title
        parsed_air_date = _parse_iso_date(file_info.get("release_date"))
        if parsed_air_date and (episode.air_date is None or episode.air_date != parsed_air_date):
            episode.air_date = parsed_air_date

    # Refresh aggregate counters to keep series UI fully functional.
    for season_id in touched_season_ids:
        episode_count_result = await session.exec(select(func.count(Episode.id)).where(Episode.season_id == season_id))
        episode_count = int(episode_count_result.one() or 0)
        season = await session.get(Season, season_id)
        if season:
            season.episode_count = episode_count

    total_seasons_result = await session.exec(select(func.count(Season.id)).where(Season.series_id == series_meta.id))
    total_episodes_result = await session.exec(
        select(func.count(Episode.id))
        .join(Season, Episode.season_id == Season.id)
        .where(Season.series_id == series_meta.id)
    )
    series_meta.total_seasons = int(total_seasons_result.one() or 0)
    series_meta.total_episodes = int(total_episodes_result.one() or 0)


async def fetch_external_metadata_payload(
    external_id: str,
    media_type: str,
    fallback_title: str | None = None,
) -> dict[str, Any]:
    """Fetch metadata from external providers without holding a DB session."""
    from scrapers.scraper_tasks import meta_fetcher

    provider = None
    provider_id = external_id

    if external_id.startswith("tt"):
        provider = "imdb"
    elif external_id.startswith("tmdb:"):
        provider = "tmdb"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("tvdb:"):
        provider = "tvdb"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("mal:"):
        provider = "mal"
        provider_id = external_id.split(":")[-1]
    elif external_id.startswith("kitsu:"):
        provider = "kitsu"
        provider_id = external_id.split(":")[-1]
    else:
        if external_id.isdigit():
            provider = "tmdb"
        else:
            return {"id": external_id, "title": fallback_title or "Unknown"}

    try:
        metadata = await meta_fetcher.get_metadata_from_provider(provider, provider_id, media_type)
        if metadata:
            return metadata
        logger.warning(f"Could not fetch metadata from {provider} for {external_id}, using fallback")
    except Exception as e:
        logger.warning(f"Error fetching metadata from {provider} for {external_id}: {e}")

    return {"id": external_id, "title": fallback_title or "Unknown"}


async def _is_import_target_adult(
    meta_id: str | None,
    meta_type: str,
    sports_category: str | None,
    fallback_title: str | None,
) -> bool:
    """Check external/internal metadata target for adult markers before import."""
    if not meta_id or meta_type == "sports":
        return False

    async with get_async_session_context() as session:
        existing_media = await get_media_by_external_id(session, meta_id)
        if is_import_metadata_adult(existing_media):
            return True

    media_type = _resolve_fetch_media_type(meta_type, sports_category)
    metadata_payload = await fetch_external_metadata_payload(
        external_id=meta_id,
        media_type=media_type,
        fallback_title=fallback_title,
    )
    return is_import_metadata_adult(metadata_payload)


async def fetch_and_create_media_from_external(
    session: AsyncSession,
    external_id: str,
    media_type: str,
    fallback_title: str | None = None,
) -> Media | None:
    """Backward-compatible helper to fetch metadata and persist it in DB."""
    metadata_payload = await fetch_external_metadata_payload(
        external_id=external_id,
        media_type=media_type,
        fallback_title=fallback_title,
    )
    return await get_or_create_metadata(session, metadata_payload, media_type)


router = APIRouter(prefix="/api/v1/import", tags=["Content Import"])


# ============================================
# Torrent Import Processing
# ============================================


async def process_torrent_import(
    session: AsyncSession,
    contribution_data: dict,
    user: User | None,
) -> dict:
    """
    Process a torrent import - creates the actual TorrentStream record in the database.

    Args:
        session: Database session
        contribution_data: The data stored in the contribution record
        user: The user who submitted the import

    Returns:
        Dict with stream_id and status info
    """

    info_hash = contribution_data.get("info_hash", "").lower()
    meta_type = contribution_data.get("meta_type", "movie")
    meta_id = contribution_data.get("meta_id")
    title = contribution_data.get("title", "Unknown")
    name = contribution_data.get("name", title)
    total_size = contribution_data.get("total_size", 0)
    sports_category = contribution_data.get("sports_category")
    sports_media_type = _resolve_sports_media_type(sports_category)
    catalogs: list[str] = [item for item in contribution_data.get("catalogs", []) if item]
    if meta_type == "sports":
        resolved_sports_category = sports_category or "other_sports"
        if resolved_sports_category not in catalogs:
            catalogs.insert(0, resolved_sports_category)

    is_anonymous = contribution_data.get("is_anonymous", False)
    anonymous_display_name = contribution_data.get("anonymous_display_name")
    is_public = bool(contribution_data.get("is_public", True))

    if not info_hash:
        raise ValueError("Missing info_hash in contribution data")

    # Fetch external metadata before opening heavy DB transaction work.
    prefetched_media_payloads: dict[tuple[str, str], dict[str, Any]] = {}
    primary_external_id = meta_id or f"user_{info_hash[:8]}"
    if meta_type != "sports":
        prefetched_payload = await fetch_external_metadata_payload(
            primary_external_id,
            meta_type,
            fallback_title=title,
        )
        if is_import_metadata_adult(prefetched_payload):
            raise ValueError(ADULT_CONTENT_METADATA_ERROR_MESSAGE)
        prefetched_media_payloads[(primary_external_id, meta_type)] = prefetched_payload

    for file_info in contribution_data.get("file_data", []):
        file_meta_id = file_info.get("meta_id")
        if not file_meta_id:
            continue
        file_meta_type = file_info.get("meta_type", meta_type)
        if file_meta_type == "sports":
            file_sports_category = file_info.get("sports_category") or sports_category
            file_meta_type = _resolve_fetch_media_type(file_meta_type, file_sports_category)
        fetch_key = (file_meta_id, file_meta_type)
        if fetch_key in prefetched_media_payloads:
            continue
        prefetched_payload = await fetch_external_metadata_payload(
            file_meta_id,
            file_meta_type,
            fallback_title=file_info.get("meta_title"),
        )
        if is_import_metadata_adult(prefetched_payload):
            raise ValueError(ADULT_CONTENT_METADATA_ERROR_MESSAGE)
        prefetched_media_payloads[fetch_key] = prefetched_payload

    # Check if torrent already exists
    existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
    existing_torrent = existing.first()
    if existing_torrent:
        if is_public:
            existing_stream = await session.get(Stream, existing_torrent.stream_id)
            if existing_stream and not existing_stream.is_public:
                existing_stream.is_public = True
                await session.flush()
                return {
                    "status": "success",
                    "stream_id": existing_stream.id,
                    "message": "Existing torrent stream published",
                }
        return {"status": "exists", "message": "Torrent already exists in database"}

    # Get or create media metadata
    media = None
    if meta_id:
        if meta_id.startswith("mf:"):
            try:
                media_id = int(meta_id.split(":", 1)[1])
                media = await get_media_by_id(session, media_id)
            except (TypeError, ValueError):
                media = None
        if not media:
            media = await get_media_by_external_id(session, meta_id)

    if not media:
        if meta_type == "sports":
            media = await get_media_by_title_year(session, title, contribution_data.get("year"), sports_media_type)
            if not media:
                media = Media(
                    title=title,
                    type=sports_media_type,
                    year=contribution_data.get("year"),
                )
                session.add(media)
                await session.flush()
        else:
            try:
                payload = prefetched_media_payloads.get((primary_external_id, meta_type)) or {
                    "id": primary_external_id,
                    "title": title,
                    "year": contribution_data.get("year"),
                }
                media = await get_or_create_metadata(session, payload, meta_type)
            except Exception as e:
                logger.warning(f"Failed to fetch/create media for {meta_id}: {e}")
                media_type_map = {
                    "movie": MediaType.MOVIE,
                    "series": MediaType.SERIES,
                    "sports": sports_media_type,
                }
                media_type_enum = media_type_map.get(meta_type, MediaType.MOVIE)
                media = await get_or_create_metadata(
                    session,
                    {
                        "id": meta_id or f"user_{info_hash[:8]}",
                        "title": title,
                        "year": contribution_data.get("year"),
                    },
                    "series" if media_type_enum == MediaType.SERIES else "movie",
                )

    if is_import_metadata_adult(media):
        raise ValueError(ADULT_CONTENT_METADATA_ERROR_MESSAGE)

    release_date = _parse_iso_date(contribution_data.get("created_at"))
    if release_date and (meta_type == "sports" or media.release_date is None):
        media.release_date = release_date
    await _upsert_import_media_images(
        session,
        media.id,
        poster=contribution_data.get("poster"),
        background=contribution_data.get("background"),
        logo=contribution_data.get("logo"),
    )

    uploader_name, uploader_user_id = resolve_uploader_identity(user, is_anonymous, anonymous_display_name)

    # Create Stream base record
    stream = Stream(
        stream_type=StreamType.TORRENT,
        name=name,
        source="Contribution Stream",
        resolution=contribution_data.get("resolution"),
        codec=contribution_data.get("codec"),
        quality=contribution_data.get("quality"),
        bit_depth=contribution_data.get("bit_depth"),
        uploader=uploader_name,
        uploader_user_id=uploader_user_id,
        release_group=contribution_data.get("release_group"),
        is_remastered=bool(contribution_data.get("is_remastered", False)),
        is_upscaled=bool(contribution_data.get("is_upscaled", False)),
        is_proper=bool(contribution_data.get("is_proper", False)),
        is_repack=bool(contribution_data.get("is_repack", False)),
        is_extended=bool(contribution_data.get("is_extended", False)),
        is_complete=bool(contribution_data.get("is_complete", False)),
        is_dubbed=bool(contribution_data.get("is_dubbed", False)),
        is_subbed=bool(contribution_data.get("is_subbed", False)),
        is_public=is_public,
    )
    session.add(stream)
    await session.flush()

    # Create TorrentStream record
    torrent_stream = TorrentStream(
        stream_id=stream.id,
        info_hash=info_hash,
        total_size=total_size,
        torrent_type=TorrentType.PUBLIC,
        file_count=contribution_data.get("file_count", 1),
        uploaded_at=datetime.now(pytz.UTC),
    )
    session.add(torrent_stream)

    # Link stream to media
    stream_media_link = StreamMediaLink(
        stream_id=stream.id,
        media_id=media.id,
    )
    session.add(stream_media_link)
    primary_stream_link_time = stream_media_link.created_at or datetime.now(pytz.UTC)

    # Add languages
    languages = _dedupe_preserve_order(_normalize_string_list(contribution_data.get("languages")))
    for lang_name in languages:
        if lang_name:
            try:
                lang = await get_or_create_language(session, lang_name)
                lang_link = StreamLanguageLink(stream_id=stream.id, language_id=lang.id)
                session.add(lang_link)
            except Exception as e:
                logger.warning(f"Failed to add language {lang_name}: {e}")

    # Add audio format links
    audio_formats = _dedupe_preserve_order(
        _normalize_string_list(contribution_data.get("audio_formats") or contribution_data.get("audio"))
    )
    for audio_name in audio_formats:
        if audio_name:
            try:
                audio_format = await get_or_create_audio_format(session, audio_name)
                audio_link = StreamAudioLink(stream_id=stream.id, audio_format_id=audio_format.id)
                session.add(audio_link)
            except Exception as e:
                logger.warning(f"Failed to add audio format {audio_name}: {e}")

    # Add audio channel links
    channels = _dedupe_preserve_order(_normalize_string_list(contribution_data.get("channels")))
    for channel_name in channels:
        if channel_name:
            try:
                channel = await get_or_create_audio_channel(session, channel_name)
                channel_link = StreamChannelLink(stream_id=stream.id, channel_id=channel.id)
                session.add(channel_link)
            except Exception as e:
                logger.warning(f"Failed to add audio channel {channel_name}: {e}")

    # Add HDR format links
    hdr_formats = _dedupe_preserve_order(
        _normalize_string_list(contribution_data.get("hdr_formats") or contribution_data.get("hdr"))
    )
    for hdr_name in hdr_formats:
        if hdr_name:
            try:
                hdr_format = await get_or_create_hdr_format(session, hdr_name)
                hdr_link = StreamHDRLink(stream_id=stream.id, hdr_format_id=hdr_format.id)
                session.add(hdr_link)
            except Exception as e:
                logger.warning(f"Failed to add HDR format {hdr_name}: {e}")

    # Add files with per-file metadata support
    file_data = contribution_data.get("file_data", [])

    # Track all media IDs we need to link the stream to
    linked_media_ids = {media.id}  # Start with the primary media

    primary_series_file_entries: list[dict[str, Any]] = []
    for idx, file_info in enumerate(file_data):
        stream_file = StreamFile(
            stream_id=stream.id,
            file_index=file_info.get("index", idx),
            filename=file_info.get("filename", ""),
            size=file_info.get("size", 0),
        )
        session.add(stream_file)
        await session.flush()

        # Determine which media to link this file to
        file_meta_id = file_info.get("meta_id")
        file_media = None

        if file_meta_id:
            # Per-file metadata: look up the specified media
            file_media = await get_media_by_external_id(session, file_meta_id)

            # If media doesn't exist, fetch from external provider and create
            if not file_media:
                file_meta_title = file_info.get("meta_title")
                file_meta_type = file_info.get("meta_type", meta_type)
                if file_meta_type == "sports":
                    file_sports_category = file_info.get("sports_category") or sports_category
                    file_meta_type = _resolve_fetch_media_type(file_meta_type, file_sports_category)

                try:
                    payload = prefetched_media_payloads.get((file_meta_id, file_meta_type))
                    if payload:
                        file_media = await get_or_create_metadata(session, payload, file_meta_type)
                    elif file_meta_title:
                        file_media = await get_or_create_metadata(
                            session,
                            {"id": file_meta_id, "title": file_meta_title},
                            file_meta_type,
                        )
                except Exception as e:
                    logger.warning(f"Failed to fetch/create media for file meta_id {file_meta_id}: {e}")

            if file_media:
                if is_import_metadata_adult(file_media):
                    raise ValueError(ADULT_CONTENT_METADATA_ERROR_MESSAGE)
                linked_media_ids.add(file_media.id)

        # Fall back to primary media if no per-file metadata
        target_media = file_media or media

        # Ensure series-linked files always have season/episode values.
        link_season_number = file_info.get("season_number")
        link_episode_number = file_info.get("episode_number")
        if target_media.type == MediaType.SERIES:
            if link_season_number is None:
                link_season_number = 1
            if link_episode_number is None:
                link_episode_number = idx + 1

        # Link file to media with season/episode info if present
        file_media_link = FileMediaLink(
            file_id=stream_file.id,
            media_id=target_media.id,
            season_number=link_season_number,
            episode_number=link_episode_number,
            episode_end=file_info.get("episode_end"),
        )
        session.add(file_media_link)

        if target_media.id == media.id and target_media.type == MediaType.SERIES:
            primary_series_file_entries.append(
                {
                    "season_number": link_season_number,
                    "episode_number": link_episode_number,
                    "episode_title": file_info.get("episode_title") or file_info.get("title"),
                    "release_date": file_info.get("release_date"),
                    "filename": file_info.get("filename"),
                }
            )

    # Create StreamMediaLink for each unique media (multi-content support)
    extra_media_link_times: dict[int, datetime] = {}
    for extra_media_id in linked_media_ids:
        if extra_media_id != media.id:  # Primary already linked above
            extra_link = StreamMediaLink(
                stream_id=stream.id,
                media_id=extra_media_id,
                is_primary=False,
            )
            session.add(extra_link)
            extra_media_link_times[extra_media_id] = extra_link.created_at or datetime.now(pytz.UTC)

    # Series detail page relies on season/episode metadata entries.
    await _ensure_series_episode_metadata(session, media, primary_series_file_entries, title)

    # Update media stream count for primary media
    media.total_streams = (media.total_streams or 0) + 1
    media.last_stream_added = primary_stream_link_time

    # Also update stream count for linked media
    for extra_media_id in linked_media_ids:
        if extra_media_id != media.id:
            extra_media = await session.get(Media, extra_media_id)
            if extra_media:
                extra_media.total_streams = (extra_media.total_streams or 0) + 1
                extra_media.last_stream_added = extra_media_link_times.get(extra_media_id, primary_stream_link_time)

    # Apply catalog links selected during import.
    for catalog_name in catalogs:
        if not catalog_name:
            continue
        try:
            catalog = await get_or_create_catalog(session, catalog_name)
            existing_link_query = select(MediaCatalogLink).where(
                MediaCatalogLink.media_id == media.id,
                MediaCatalogLink.catalog_id == catalog.id,
            )
            existing_link_result = await session.exec(existing_link_query)
            if not existing_link_result.first():
                session.add(MediaCatalogLink(media_id=media.id, catalog_id=catalog.id))
        except Exception as error:
            logger.warning("Failed to link catalog '%s' to media %s: %s", catalog_name, media.id, error)

    await session.flush()

    logger.info(
        f"Successfully imported torrent {info_hash} for media_id={media.id}, "
        f"linked to {len(linked_media_ids)} media entries"
    )

    return {
        "status": "success",
        "stream_id": stream.id,
        "media_id": media.id,
        "info_hash": info_hash,
        "linked_media_count": len(linked_media_ids),
        "linked_media_ids": list(linked_media_ids),
    }


# ============================================
# Pydantic Schemas
# ============================================


class MagnetImportRequest(BaseModel):
    """Request schema for importing a magnet link."""

    magnet_link: str
    meta_type: str = Field(..., pattern="^(movie|series|sports)$")
    meta_id: str | None = None  # IMDb ID if known
    title: str | None = None
    catalogs: list[str] | None = None
    languages: list[str] | None = None


class TorrentAnalyzeResponse(BaseModel):
    """Response from torrent analysis."""

    status: str
    info_hash: str | None = None
    torrent_name: str | None = None  # Original torrent name
    total_size: int | None = None
    total_size_readable: str | None = None
    created_at: str | None = None
    file_count: int | None = None
    files: list[dict[str, Any]] | None = None
    parsed_title: str | None = None
    year: int | None = None
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    audio: list[str] | None = None
    hdr: list[str] | None = None
    languages: list[str] | None = None
    sports_category: str | None = None
    sports_event: str | None = None
    sports_league: str | None = None
    sports_event_date: str | None = None
    matches: list[dict[str, Any]] | None = None
    error: str | None = None


class ImportResponse(BaseModel):
    """Generic import response."""

    status: str
    message: str
    import_id: str | None = None
    details: dict[str, Any] | None = None


# ============================================
# API Endpoints
# ============================================


@router.post("/magnet/analyze", response_model=TorrentAnalyzeResponse)
async def analyze_magnet(
    data: MagnetImportRequest,
    user: User = Depends(require_auth),
):
    """
    Analyze a magnet link and return torrent metadata.
    Also searches for matching content in IMDb/TMDB.
    """
    from scrapers.scraper_tasks import meta_fetcher

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fetching torrent metadata from P2P is disabled.",
        )

    # Parse magnet link
    info_hash, trackers = torrent.parse_magnet(data.magnet_link)
    if not info_hash:
        return TorrentAnalyzeResponse(
            status="error",
            error="Failed to parse magnet link. Invalid format.",
        )

    try:
        # Fetch torrent metadata
        torrent_data_list = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers, is_raise_error=True)

        if not torrent_data_list or not torrent_data_list[0]:
            return TorrentAnalyzeResponse(
                status="error",
                error="Failed to fetch torrent metadata from DHT network.",
            )

        torrent_data = torrent_data_list[0]

        # Convert size to readable format
        total_size = torrent_data.get("total_size", 0)
        size_readable = convert_bytes_to_readable(total_size) if total_size > 0 else "Unknown"

        # Search for matching content if title is available
        matches = []
        if data.meta_type != "sports" and torrent_data.get("title"):
            try:
                matches = await meta_fetcher.search_multiple_results(
                    title=torrent_data["title"],
                    year=torrent_data.get("year"),
                    media_type=data.meta_type,
                )
            except Exception:
                pass  # Ignore search errors

        analysis_fields = _build_torrent_analysis_fields(data.meta_type, torrent_data)
        if data.meta_type == "sports":
            async with get_async_session_context() as session:
                matches = await _search_existing_sports_matches(
                    session,
                    analysis_fields.get("parsed_title"),
                    analysis_fields.get("sports_event"),
                    analysis_fields.get("year"),
                    analysis_fields.get("sports_league"),
                )

        created_at = torrent_data.get("created_at")
        return TorrentAnalyzeResponse(
            status="success",
            info_hash=info_hash.lower(),
            torrent_name=torrent_data.get("torrent_name"),
            total_size=total_size,
            total_size_readable=size_readable,
            created_at=created_at.isoformat() if isinstance(created_at, datetime) else None,
            file_count=len(torrent_data.get("file_data", [])),
            files=torrent_data.get("file_data", []),
            matches=matches,
            **analysis_fields,
        )

    except ExceptionGroup as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=str(e.exceptions[0]) if e.exceptions else "Unknown error",
        )
    except Exception as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=f"Failed to analyze magnet: {str(e)}",
        )


@router.post("/torrent/analyze", response_model=TorrentAnalyzeResponse)
async def analyze_torrent_file(
    torrent_file: UploadFile = File(...),
    meta_type: str = Form(...),
    user: User = Depends(require_auth),
):
    """
    Analyze a torrent file and return metadata.
    Also searches for matching content in IMDb/TMDB.
    """
    from scrapers.scraper_tasks import meta_fetcher

    if not torrent_file.filename or not torrent_file.filename.endswith(".torrent"):
        return TorrentAnalyzeResponse(
            status="error",
            error="Invalid file. Please upload a .torrent file.",
        )

    try:
        content = await torrent_file.read()

        if len(content) > settings.max_torrent_file_size:
            return TorrentAnalyzeResponse(
                status="error",
                error=f"Torrent file too large. Maximum size is {settings.max_torrent_file_size // (1024 * 1024)} MB.",
            )

        torrent_data = torrent.extract_torrent_metadata(content, is_raise_error=True)

        if not torrent_data:
            return TorrentAnalyzeResponse(
                status="error",
                error="Failed to parse torrent file.",
            )

        # Convert size to readable format
        total_size = torrent_data.get("total_size", 0)
        size_readable = convert_bytes_to_readable(total_size) if total_size > 0 else "Unknown"

        # Search for matching content
        matches = []
        if meta_type != "sports" and torrent_data.get("title"):
            try:
                matches = await meta_fetcher.search_multiple_results(
                    title=torrent_data["title"],
                    year=torrent_data.get("year"),
                    media_type=meta_type,
                )
            except Exception:
                pass

        analysis_fields = _build_torrent_analysis_fields(meta_type, torrent_data)
        if meta_type == "sports":
            async with get_async_session_context() as session:
                matches = await _search_existing_sports_matches(
                    session,
                    analysis_fields.get("parsed_title"),
                    analysis_fields.get("sports_event"),
                    analysis_fields.get("year"),
                    analysis_fields.get("sports_league"),
                )

        created_at = torrent_data.get("created_at")
        return TorrentAnalyzeResponse(
            status="success",
            info_hash=torrent_data.get("info_hash", "").lower(),
            torrent_name=torrent_data.get("torrent_name"),
            total_size=total_size,
            total_size_readable=size_readable,
            created_at=created_at.isoformat() if isinstance(created_at, datetime) else None,
            file_count=len(torrent_data.get("file_data", [])),
            files=torrent_data.get("file_data", []),
            matches=matches,
            **analysis_fields,
        )

    except ValueError as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=str(e),
        )
    except Exception as e:
        return TorrentAnalyzeResponse(
            status="error",
            error=f"Failed to analyze torrent: {str(e)}",
        )


@router.post("/magnet", response_model=ImportResponse)
async def import_magnet(
    meta_type: str = Form(...),
    magnet_link: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    poster: str = Form(None),
    background: str = Form(None),
    logo: str = Form(None),
    catalogs: str = Form(None),
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    audio: str = Form(None),
    hdr: str = Form(None),
    file_data: str = Form(None),  # JSON stringified array
    created_at: str | None = Form(None),
    sports_category: str | None = Form(None),
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    anonymous_display_name: str | None = Form(None),
    user: User = Depends(require_auth),
):
    """
    Import a magnet link.
    For active users, imports are auto-approved and processed immediately.
    For deactivated users, imports require manual review.

    Set is_anonymous=True to contribute anonymously (uploader shows as "Anonymous").
    Set is_anonymous=False to show your username.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)
    async with get_async_session_context() as session:
        await enforce_upload_permissions(user, session)

    if not settings.enable_fetching_torrent_metadata_from_p2p:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fetching torrent metadata from P2P is disabled.",
        )

    # Parse magnet link
    info_hash, trackers = torrent.parse_magnet(magnet_link)
    if not info_hash:
        return ImportResponse(
            status="error",
            message="Failed to parse magnet link.",
        )

    info_hash = info_hash.lower()

    # Check if torrent already exists
    if not force_import:
        async with get_async_session_context() as session:
            existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
            existing_torrent = existing.first()
            if existing_torrent:
                attachment_details = {"count": 0, "items": []}
                try:
                    attachment_details = await _get_stream_media_attachment_details(session, existing_torrent.stream_id)
                except Exception as error:
                    logger.warning(
                        "Failed to resolve duplicate torrent attachment details for %s: %s", info_hash, error
                    )

                return ImportResponse(
                    status="warning",
                    message=_build_existing_torrent_warning_message(info_hash, attachment_details),
                    details={
                        "reason": "already_exists",
                        "action": "skipped",
                        "info_hash": info_hash,
                        "existing_stream_id": existing_torrent.stream_id,
                        "attached_media_count": attachment_details.get("count", 0),
                        "attached_media": attachment_details.get("items", []),
                    },
                )

    try:
        # Fetch basic metadata from P2P
        torrent_data_list = await torrent.info_hashes_to_torrent_metadata([info_hash], trackers, is_raise_error=True)

        if not torrent_data_list or not torrent_data_list[0]:
            return ImportResponse(
                status="error",
                message="Failed to fetch torrent metadata from P2P network.",
            )

        torrent_data = torrent_data_list[0]
        torrent_data, resolved_sports_category = _normalize_sports_import_metadata(
            meta_type, torrent_data, sports_category
        )
        resolved_catalogs = _resolve_catalogs(meta_type, catalogs, resolved_sports_category)
        resolved_created_at = _resolve_created_at_date(created_at, torrent_data)
        resolved_audio_formats = _resolve_import_multi_value(audio, torrent_data, "audio")
        resolved_hdr_formats = _resolve_import_multi_value(hdr, torrent_data, "hdr")
        resolved_channels = _resolve_import_multi_value(None, torrent_data, "channels")

        # Parse file_data if provided, otherwise use from torrent
        parsed_file_data = []
        if file_data:
            try:
                parsed_file_data = json.loads(file_data)
            except json.JSONDecodeError:
                logger.warning("Failed to parse file_data JSON")

        if not parsed_file_data and torrent_data.get("file_data"):
            parsed_file_data = torrent_data.get("file_data", [])
        parsed_file_data = _normalize_import_file_data(parsed_file_data)

        resolved_title, title_validation_error = resolve_and_validate_import_title(
            title,
            torrent_data.get("torrent_name"),
            additional_titles=_collect_torrent_title_candidates(torrent_data),
        )
        if title_validation_error:
            return ImportResponse(
                status="error",
                message=title_validation_error,
            )
        if await _is_import_target_adult(meta_id, meta_type, resolved_sports_category, resolved_title):
            return ImportResponse(
                status="error",
                message=ADULT_CONTENT_METADATA_ERROR_MESSAGE,
            )

        # Build contribution data with all fields
        contribution_data = {
            "info_hash": info_hash,
            "magnet_link": magnet_link,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": resolved_title,
            "name": torrent_data.get("torrent_name"),
            "total_size": torrent_data.get("total_size"),
            "catalogs": resolved_catalogs,
            "languages": _resolve_import_languages(languages, torrent_data),
            "resolution": resolution or torrent_data.get("resolution"),
            "quality": quality or torrent_data.get("quality"),
            "codec": codec or torrent_data.get("codec"),
            # Keep legacy keys for compatibility while writing normalized relation fields.
            "audio": resolved_audio_formats,
            "hdr": resolved_hdr_formats,
            "audio_formats": resolved_audio_formats,
            "hdr_formats": resolved_hdr_formats,
            "channels": resolved_channels,
            "bit_depth": torrent_data.get("bit_depth"),
            "release_group": torrent_data.get("group"),
            "is_remastered": bool(torrent_data.get("remastered", False)),
            "is_upscaled": bool(torrent_data.get("upscaled", False)),
            "is_proper": bool(torrent_data.get("proper", False)),
            "is_repack": bool(torrent_data.get("repack", False)),
            "is_extended": bool(torrent_data.get("extended", False)),
            "is_complete": bool(torrent_data.get("complete", False)),
            "is_dubbed": bool(torrent_data.get("dubbed", False)),
            "is_subbed": bool(torrent_data.get("subbed", False)),
            "file_data": parsed_file_data,
            "file_count": len(parsed_file_data) or len(torrent_data.get("file_data", [])) or 1,
            "created_at": resolved_created_at,
            "poster": poster,
            "background": background,
            "logo": logo,
            "year": torrent_data.get("year"),
            "sports_category": resolved_sports_category,
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
        }

        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING
        contribution_data["is_public"] = should_auto_approve

        import_result = None
        async with get_async_session_context() as session:
            contribution = Contribution(
                user_id=None if resolved_is_anonymous else user.id,
                contribution_type="torrent",
                target_id=meta_id,
                data=contribution_data,
                status=initial_status,
                admin_review_requested=False,
                reviewed_by="auto" if should_auto_approve else None,
                reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
                review_notes=(
                    "Auto-approved: Privileged reviewer"
                    if is_privileged_reviewer
                    else ("Auto-approved: Active user content import" if should_auto_approve else None)
                ),
            )

            session.add(contribution)
            await session.flush()
            if should_auto_approve:
                await award_import_approval_points(
                    session,
                    contribution.user_id,
                    contribution.contribution_type,
                    logger,
                )

            try:
                import_result = await process_torrent_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process torrent import: {e}")
                contribution.review_notes = (
                    f"Auto-approved but import failed: {str(e)}"
                    if should_auto_approve
                    else f"Pending private stream creation failed: {str(e)}"
                )

            await session.commit()
            await session.refresh(contribution)
        await _notify_pending_contribution(
            contribution,
            user,
            resolved_is_anonymous,
            normalized_anonymous_display_name,
        )

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return ImportResponse(
                status="success",
                message="Torrent imported successfully!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return ImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return ImportResponse(
                status="success",
                message="Magnet link submitted for review and saved privately for your account.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except Exception as e:
        logger.exception(f"Failed to import magnet: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import magnet: {str(e)}",
        )


@router.post("/torrent", response_model=ImportResponse)
async def import_torrent_file(
    torrent_file: UploadFile = File(...),
    meta_type: str = Form(...),
    meta_id: str = Form(None),
    title: str = Form(None),
    poster: str = Form(None),
    background: str = Form(None),
    logo: str = Form(None),
    catalogs: str = Form(None),
    languages: str = Form(None),
    resolution: str = Form(None),
    quality: str = Form(None),
    codec: str = Form(None),
    audio: str = Form(None),
    hdr: str = Form(None),
    file_data: str = Form(None),  # JSON stringified array
    created_at: str | None = Form(None),
    sports_category: str | None = Form(None),
    force_import: bool = Form(False),
    is_anonymous: bool | None = Form(None),  # None means use user's preference
    anonymous_display_name: str | None = Form(None),
    user: User = Depends(require_auth),
):
    """
    Import a torrent file.
    For active users, imports are auto-approved and processed immediately.
    For deactivated users, imports require manual review.

    Set is_anonymous=True to contribute anonymously (uploader shows as "Anonymous").
    Set is_anonymous=False to show your username.
    If not provided, uses your account's default contribution preference.
    """
    # Resolve anonymity: explicit param > user preference
    resolved_is_anonymous = is_anonymous if is_anonymous is not None else user.contribute_anonymously
    normalized_anonymous_display_name = normalize_anonymous_display_name(anonymous_display_name)
    async with get_async_session_context() as session:
        await enforce_upload_permissions(user, session)

    if not torrent_file.filename or not torrent_file.filename.endswith(".torrent"):
        return ImportResponse(
            status="error",
            message="Invalid file. Please upload a .torrent file.",
        )

    try:
        content = await torrent_file.read()

        if len(content) > settings.max_torrent_file_size:
            return ImportResponse(
                status="error",
                message=f"Torrent file too large. Maximum size is {settings.max_torrent_file_size // (1024 * 1024)} MB.",
            )

        torrent_data = torrent.extract_torrent_metadata(content, is_raise_error=True)

        if not torrent_data:
            return ImportResponse(
                status="error",
                message="Failed to parse torrent file.",
            )
        torrent_data, resolved_sports_category = _normalize_sports_import_metadata(
            meta_type, torrent_data, sports_category
        )
        resolved_catalogs = _resolve_catalogs(meta_type, catalogs, resolved_sports_category)
        resolved_created_at = _resolve_created_at_date(created_at, torrent_data)
        resolved_audio_formats = _resolve_import_multi_value(audio, torrent_data, "audio")
        resolved_hdr_formats = _resolve_import_multi_value(hdr, torrent_data, "hdr")
        resolved_channels = _resolve_import_multi_value(None, torrent_data, "channels")

        info_hash = torrent_data.get("info_hash", "").lower()

        # Check if torrent already exists
        if not force_import:
            async with get_async_session_context() as session:
                existing = await session.exec(select(TorrentStream).where(TorrentStream.info_hash == info_hash))
                existing_torrent = existing.first()
                if existing_torrent:
                    attachment_details = {"count": 0, "items": []}
                    try:
                        attachment_details = await _get_stream_media_attachment_details(
                            session, existing_torrent.stream_id
                        )
                    except Exception as error:
                        logger.warning(
                            "Failed to resolve duplicate torrent attachment details for %s: %s", info_hash, error
                        )

                    return ImportResponse(
                        status="warning",
                        message=_build_existing_torrent_warning_message(info_hash, attachment_details),
                        details={
                            "reason": "already_exists",
                            "action": "skipped",
                            "info_hash": info_hash,
                            "existing_stream_id": existing_torrent.stream_id,
                            "attached_media_count": attachment_details.get("count", 0),
                            "attached_media": attachment_details.get("items", []),
                        },
                    )

        # Parse file_data if provided, otherwise use from torrent
        parsed_file_data = []
        if file_data:
            try:
                parsed_file_data = json.loads(file_data)
            except json.JSONDecodeError:
                logger.warning("Failed to parse file_data JSON")

        if not parsed_file_data and torrent_data.get("file_data"):
            parsed_file_data = torrent_data.get("file_data", [])
        parsed_file_data = _normalize_import_file_data(parsed_file_data)

        resolved_title, title_validation_error = resolve_and_validate_import_title(
            title,
            torrent_data.get("torrent_name"),
            additional_titles=_collect_torrent_title_candidates(torrent_data),
        )
        if title_validation_error:
            return ImportResponse(
                status="error",
                message=title_validation_error,
            )
        if await _is_import_target_adult(meta_id, meta_type, resolved_sports_category, resolved_title):
            return ImportResponse(
                status="error",
                message=ADULT_CONTENT_METADATA_ERROR_MESSAGE,
            )

        # Build contribution data with all fields
        contribution_data = {
            "info_hash": info_hash,
            "meta_type": meta_type,
            "meta_id": meta_id,
            "title": resolved_title,
            "name": torrent_data.get("torrent_name"),
            "total_size": torrent_data.get("total_size"),
            "catalogs": resolved_catalogs,
            "languages": _resolve_import_languages(languages, torrent_data),
            "resolution": resolution or torrent_data.get("resolution"),
            "quality": quality or torrent_data.get("quality"),
            "codec": codec or torrent_data.get("codec"),
            # Keep legacy keys for compatibility while writing normalized relation fields.
            "audio": resolved_audio_formats,
            "hdr": resolved_hdr_formats,
            "audio_formats": resolved_audio_formats,
            "hdr_formats": resolved_hdr_formats,
            "channels": resolved_channels,
            "bit_depth": torrent_data.get("bit_depth"),
            "release_group": torrent_data.get("group"),
            "is_remastered": bool(torrent_data.get("remastered", False)),
            "is_upscaled": bool(torrent_data.get("upscaled", False)),
            "is_proper": bool(torrent_data.get("proper", False)),
            "is_repack": bool(torrent_data.get("repack", False)),
            "is_extended": bool(torrent_data.get("extended", False)),
            "is_complete": bool(torrent_data.get("complete", False)),
            "is_dubbed": bool(torrent_data.get("dubbed", False)),
            "is_subbed": bool(torrent_data.get("subbed", False)),
            "file_data": parsed_file_data,
            "file_count": len(parsed_file_data) or len(torrent_data.get("file_data", [])) or 1,
            "created_at": resolved_created_at,
            "poster": poster,
            "background": background,
            "logo": logo,
            "year": torrent_data.get("year"),
            "sports_category": resolved_sports_category,
            "is_anonymous": resolved_is_anonymous,
            "anonymous_display_name": normalized_anonymous_display_name,
        }

        is_privileged_reviewer = user.role in {UserRole.MODERATOR, UserRole.ADMIN}
        should_auto_approve = is_privileged_reviewer or (user.is_active and not resolved_is_anonymous)
        initial_status = ContributionStatus.APPROVED if should_auto_approve else ContributionStatus.PENDING
        contribution_data["is_public"] = should_auto_approve

        import_result = None
        async with get_async_session_context() as session:
            contribution = Contribution(
                user_id=None if resolved_is_anonymous else user.id,
                contribution_type="torrent",
                target_id=meta_id,
                data=contribution_data,
                status=initial_status,
                admin_review_requested=False,
                reviewed_by="auto" if should_auto_approve else None,
                reviewed_at=datetime.now(pytz.UTC) if should_auto_approve else None,
                review_notes=(
                    "Auto-approved: Privileged reviewer"
                    if is_privileged_reviewer
                    else ("Auto-approved: Active user content import" if should_auto_approve else None)
                ),
            )

            session.add(contribution)
            await session.flush()
            if should_auto_approve:
                await award_import_approval_points(
                    session,
                    contribution.user_id,
                    contribution.contribution_type,
                    logger,
                )

            try:
                import_result = await process_torrent_import(session, contribution_data, user)
            except Exception as e:
                logger.error(f"Failed to process torrent import: {e}")
                contribution.review_notes = (
                    f"Auto-approved but import failed: {str(e)}"
                    if should_auto_approve
                    else f"Pending private stream creation failed: {str(e)}"
                )

            await session.commit()
            await session.refresh(contribution)
        await _notify_pending_contribution(
            contribution,
            user,
            resolved_is_anonymous,
            normalized_anonymous_display_name,
        )

        if should_auto_approve and import_result and import_result.get("status") == "success":
            return ImportResponse(
                status="success",
                message="Torrent imported successfully!",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "stream_id": import_result.get("stream_id"),
                    "auto_approved": True,
                },
            )
        elif should_auto_approve:
            return ImportResponse(
                status="warning",
                message="Contribution auto-approved but import may need attention.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": True,
                },
            )
        else:
            return ImportResponse(
                status="success",
                message="Torrent file submitted for review and saved privately for your account.",
                import_id=contribution.id,
                details={
                    "info_hash": info_hash,
                    "title": contribution_data.get("title"),
                    "auto_approved": False,
                },
            )

    except ValueError as e:
        return ImportResponse(
            status="error",
            message=str(e),
        )
    except Exception as e:
        logger.exception(f"Failed to import torrent: {e}")
        return ImportResponse(
            status="error",
            message=f"Failed to import torrent: {str(e)}",
        )
