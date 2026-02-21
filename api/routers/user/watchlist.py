"""
Debrid Watchlist API endpoints.

Provides access to content downloaded/cached in user's debrid accounts.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from PTT import parse_title
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from thefuzz import fuzz

from api.routers.user.auth import require_auth
from db.database import get_read_session, get_async_session
from db.enums import MediaType
from db.redis_database import REDIS_ASYNC_CLIENT
from db.models import (
    Media,
    MediaImage,
    Stream,
    StreamMediaLink,
    TorrentStream,
    User,
    UserProfile,
)
from db.crud import get_default_profile, get_profile_by_id
from db import crud
from db.schemas import StreamFileData, TorrentStreamData
from db.schemas.config import StreamingProvider

from scrapers.scraper_tasks import MetadataFetcher
from streaming_providers import mapper
from utils.network import get_user_public_ip
from utils.profile_context import ProfileDataProvider
from utils.profile_crypto import profile_crypto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])

TORRENT_DETAILS_CACHE_TTL_SECONDS = 120
IMPORT_PREPARE_CONCURRENCY = 6
IMPORT_DB_BATCH_SIZE = 25
TORRENT_DETAILS_CACHE_KEY_PREFIX = "watchlist:torrent_details"


# ============================================
# Response Models
# ============================================


class ExternalIds(BaseModel):
    """External IDs for a media item."""

    imdb: str | None = None
    tmdb: str | None = None
    tvdb: str | None = None


class WatchlistItem(BaseModel):
    """A single item in the debrid watchlist."""

    id: int
    title: str
    type: str  # movie, series
    year: int | None = None
    poster: str | None = None
    external_ids: ExternalIds
    info_hashes: list[str] = []  # Info hashes from debrid for this media


class WatchlistProviderInfo(BaseModel):
    """Information about a debrid provider that supports watchlist."""

    service: str
    name: str | None = None
    supports_watchlist: bool = True


class WatchlistResponse(BaseModel):
    """Response containing watchlist items."""

    items: list[WatchlistItem]
    total: int
    page: int
    page_size: int
    has_more: bool
    provider: str
    provider_name: str | None = None


class WatchlistProvidersResponse(BaseModel):
    """Response containing available watchlist providers."""

    providers: list[WatchlistProviderInfo]
    profile_id: int


class MissingTorrentFile(BaseModel):
    """A file within a missing torrent."""

    path: str
    size: int


class MissingExternalIds(BaseModel):
    imdb: str | None = None
    tmdb: str | None = None
    tvdb: str | None = None


class MissingTorrentItem(BaseModel):
    """A torrent in the debrid account that is not in our database."""

    info_hash: str
    name: str
    size: int
    files: list[MissingTorrentFile]
    # Parsed metadata (best-effort from torrent name)
    parsed_title: str | None = None
    parsed_year: int | None = None
    parsed_type: str | None = None  # movie, series
    matched_title: str | None = None
    external_ids: MissingExternalIds | None = None


class MissingTorrentsResponse(BaseModel):
    """Response containing missing torrents."""

    items: list[MissingTorrentItem]
    total: int
    provider: str
    provider_name: str | None = None


class TorrentOverride(BaseModel):
    """User-provided metadata override for a torrent."""

    title: str | None = None
    year: int | None = None
    type: str | None = None  # movie, series


class ImportRequest(BaseModel):
    """Request to import torrents."""

    info_hashes: list[str]  # Selected torrents to import
    overrides: dict[str, TorrentOverride] | None = None  # info_hash -> override


class ImportResultItem(BaseModel):
    """Result of importing a single torrent."""

    info_hash: str
    status: str  # success, failed, skipped
    message: str | None = None
    media_id: int | None = None
    media_title: str | None = None


class ImportResponse(BaseModel):
    """Response from import operation."""

    imported: int
    failed: int
    skipped: int
    details: list[ImportResultItem]


class RemoveRequest(BaseModel):
    """Request to remove a torrent from debrid."""

    info_hash: str


class RemoveResponse(BaseModel):
    """Response from remove operation."""

    success: bool
    message: str


# Advanced import types for multi-content support
class FileAnnotationData(BaseModel):
    """File annotation for advanced import."""

    filename: str
    size: int | None = None
    index: int
    season_number: int | None = None
    episode_number: int | None = None
    episode_end: int | None = None
    included: bool = True
    # Multi-content: link this file to a different media
    meta_id: str | None = None
    meta_title: str | None = None
    meta_type: str | None = None  # movie, series


class AdvancedTorrentImport(BaseModel):
    """Advanced import data for a single torrent."""

    info_hash: str
    meta_type: str  # movie, series
    meta_id: str  # Primary media external ID (e.g., tt1234567)
    title: str | None = None
    file_data: list[FileAnnotationData] | None = None


class AdvancedImportRequest(BaseModel):
    """Request for advanced import with file annotations."""

    advanced_imports: list[AdvancedTorrentImport]


# ============================================
# Helper Functions
# ============================================


# Providers that support fetching downloaded info hashes
WATCHLIST_SUPPORTED_PROVIDERS = set(mapper.FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS.keys())


@dataclass(slots=True)
class PreparedImportItem:
    index: int
    info_hash: str
    media_type: str
    external_id: str
    metadata: dict[str, Any]
    stream_data: TorrentStreamData


@dataclass(slots=True)
class ImportPreparationResult:
    index: int
    prepared_item: PreparedImportItem | None = None
    result_item: ImportResultItem | None = None
    metadata_search_ms: float = 0.0


def _torrent_cache_key(user_id: int, profile_id: int | None, provider: str) -> str:
    return f"{TORRENT_DETAILS_CACHE_KEY_PREFIX}:{user_id}:{profile_id or 0}:{provider}"


async def get_cached_torrent_details(
    user_id: int,
    profile_id: int | None,
    provider: str,
) -> dict[str, dict[str, Any]] | None:
    cache_key = _torrent_cache_key(user_id, profile_id, provider)
    cached_data = await REDIS_ASYNC_CLIENT.get(cache_key)
    if not cached_data:
        return None

    try:
        if isinstance(cached_data, bytes):
            cached_data = cached_data.decode()
        parsed = json.loads(cached_data)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception as error:
        logger.debug("Failed to parse cached torrent details for key=%s: %s", cache_key, error)
        return None


async def set_cached_torrent_details(
    user_id: int,
    profile_id: int | None,
    provider: str,
    torrent_details: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    torrents_by_hash: dict[str, dict[str, Any]] = {}
    for torrent in torrent_details:
        info_hash = str(torrent.get("hash", "")).lower()
        if info_hash:
            torrents_by_hash[info_hash] = torrent

    cache_key = _torrent_cache_key(user_id, profile_id, provider)
    if torrents_by_hash:
        await REDIS_ASYNC_CLIENT.set(
            cache_key,
            json.dumps(torrents_by_hash),
            ex=TORRENT_DETAILS_CACHE_TTL_SECONDS,
        )
    else:
        await REDIS_ASYNC_CLIENT.delete(cache_key)

    return torrents_by_hash


def build_stream_files_from_torrent(torrent_files: list[dict[str, Any]]) -> list[StreamFileData]:
    files = []
    video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"}

    for idx, file_info in enumerate(torrent_files):
        file_path = file_info.get("path", "")
        if any(file_path.lower().endswith(ext) for ext in video_extensions):
            files.append(
                StreamFileData(
                    file_index=idx,
                    filename=file_path.split("/")[-1] if "/" in file_path else file_path,
                    size=file_info.get("size", 0),
                    file_type="video",
                )
            )

    # If no video files found but we have files, use the largest one
    if not files and torrent_files:
        largest = max(torrent_files, key=lambda x: x.get("size", 0))
        files.append(
            StreamFileData(
                file_index=0,
                filename=largest.get("path", "").split("/")[-1]
                if "/" in largest.get("path", "")
                else largest.get("path", ""),
                size=largest.get("size", 0),
                file_type="video",
            )
        )

    return files


def iter_chunks(items: list[PreparedImportItem], chunk_size: int):
    for start in range(0, len(items), chunk_size):
        yield items[start : start + chunk_size]


def build_missing_external_ids(match: dict[str, Any]) -> MissingExternalIds | None:
    imdb_id = match.get("imdb_id")
    tmdb_id = match.get("tmdb_id")
    tvdb_id = match.get("tvdb_id")

    fallback_id = match.get("id")
    if fallback_id is not None:
        fallback_id = str(fallback_id)
        if not imdb_id and fallback_id.startswith("tt"):
            imdb_id = fallback_id
        elif not tmdb_id and fallback_id.isdigit():
            tmdb_id = fallback_id

    if not imdb_id and not tmdb_id and not tvdb_id:
        return None

    return MissingExternalIds(
        imdb=str(imdb_id) if imdb_id else None,
        tmdb=str(tmdb_id) if tmdb_id else None,
        tvdb=str(tvdb_id) if tvdb_id else None,
    )


def normalize_title_for_matching(title: str | None) -> str:
    if not title:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def get_match_similarity_threshold(normalized_title: str) -> int:
    compact_length = len(normalized_title.replace(" ", ""))
    if compact_length <= 4:
        return 96
    if compact_length <= 8:
        return 90
    return 78


def select_best_missing_external_match(
    title: str,
    year: int | None,
    candidates: list[dict[str, Any]],
) -> tuple[MissingExternalIds | None, str | None]:
    normalized_title = normalize_title_for_matching(title)
    if not normalized_title:
        return None, None

    min_similarity = get_match_similarity_threshold(normalized_title)
    best_score = -1
    best_external_ids: MissingExternalIds | None = None
    best_title: str | None = None

    for candidate in candidates:
        external_ids = build_missing_external_ids(candidate)
        if not external_ids:
            continue

        candidate_title = str(candidate.get("title") or "")
        normalized_candidate_title = normalize_title_for_matching(candidate_title)
        if not normalized_candidate_title:
            continue

        similarity = fuzz.token_set_ratio(normalized_title, normalized_candidate_title)
        if similarity < min_similarity:
            continue

        score = similarity
        candidate_year = candidate.get("year")
        if year and isinstance(candidate_year, int):
            if candidate_year == year:
                score += 8
            elif abs(candidate_year - year) <= 1:
                score += 2

        if external_ids.imdb:
            score += 2
        if external_ids.tmdb:
            score += 1

        if score > best_score:
            best_score = score
            best_external_ids = external_ids
            best_title = candidate_title

    return best_external_ids, best_title


async def prepare_import_item(
    index: int,
    info_hash: str,
    torrent: dict[str, Any],
    override: TorrentOverride | None,
    meta_fetcher: MetadataFetcher,
) -> ImportPreparationResult:
    try:
        torrent_name = torrent.get("filename", "")
        parsed = parse_title(torrent_name, True) if torrent_name else {}

        if override:
            if override.title:
                parsed["title"] = override.title
            if override.year:
                parsed["year"] = override.year

        if not parsed.get("title"):
            return ImportPreparationResult(
                index=index,
                result_item=ImportResultItem(
                    info_hash=info_hash,
                    status="failed",
                    message="Could not parse title from torrent name",
                ),
            )

        # Determine media type (use override if provided)
        if override and override.type:
            media_type = override.type
        else:
            media_type = parse_torrent_for_type(parsed, torrent.get("files", []))

        search_results = await meta_fetcher.search_multiple_results(
            title=parsed["title"],
            year=parsed.get("year"),
            media_type=media_type,
            limit=5,
            min_similarity=70,
        )

        if not search_results:
            return ImportPreparationResult(
                index=index,
                result_item=ImportResultItem(
                    info_hash=info_hash,
                    status="failed",
                    message=f"No TMDB/IMDB match found for '{parsed['title']}'",
                ),
            )

        best_match = search_results[0]
        raw_external_id = best_match.get("imdb_id") or best_match.get("id")
        if not raw_external_id:
            return ImportPreparationResult(
                index=index,
                result_item=ImportResultItem(
                    info_hash=info_hash,
                    status="failed",
                    message="No valid external ID found",
                ),
            )
        external_id = str(raw_external_id)

        metadata = {
            "id": external_id,
            "title": best_match.get("title", parsed["title"]),
            "year": best_match.get("year", parsed.get("year")),
            "poster": best_match.get("poster"),
            "background": best_match.get("background"),
            "description": best_match.get("description"),
            "genres": best_match.get("genres", []),
        }

        stream_data = TorrentStreamData(
            info_hash=info_hash,
            meta_id=external_id,
            name=torrent_name,
            size=torrent.get("size", 0),
            source="debrid_import",
            files=build_stream_files_from_torrent(torrent.get("files", [])),
            resolution=parsed.get("resolution"),
            codec=parsed.get("codec"),
            quality=parsed.get("quality"),
            bit_depth=parsed.get("bit_depth"),
            release_group=parsed.get("group"),
            audio_formats=parsed.get("audio", []) if isinstance(parsed.get("audio"), list) else [],
            channels=parsed.get("channels", []) if isinstance(parsed.get("channels"), list) else [],
            hdr_formats=parsed.get("hdr", []) if isinstance(parsed.get("hdr"), list) else [],
            languages=parsed.get("languages", []),
        )

        return ImportPreparationResult(
            index=index,
            prepared_item=PreparedImportItem(
                index=index,
                info_hash=info_hash,
                media_type=media_type,
                external_id=external_id,
                metadata=metadata,
                stream_data=stream_data,
            ),
        )
    except Exception as error:
        logger.exception("Failed to prepare import item for %s: %s", info_hash, error)
        return ImportPreparationResult(
            index=index,
            result_item=ImportResultItem(
                info_hash=info_hash,
                status="failed",
                message=str(error)[:200],
            ),
        )


async def find_missing_external_match_in_db(
    session: AsyncSession,
    title: str,
    year: int | None,
    media_type: str,
) -> tuple[MissingExternalIds | None, str | None]:
    db_media_type = MediaType.SERIES if media_type == "series" else MediaType.MOVIE

    query = (
        select(Media)
        .options(selectinload(Media.external_ids))
        .where(Media.type == db_media_type)
        .where(Media.title_tsv.match(title))
        .order_by(Media.popularity.desc().nullslast())
        .limit(12)
    )
    result = await session.exec(query)
    media_candidates = result.all()

    if not media_candidates:
        return None, None

    candidates: list[dict[str, Any]] = []
    for media in media_candidates:
        candidate: dict[str, Any] = {"title": media.title, "year": media.year}
        if hasattr(media, "external_ids") and media.external_ids:
            for ext_id in media.external_ids:
                if ext_id.provider == "imdb":
                    candidate["imdb_id"] = ext_id.external_id
                elif ext_id.provider == "tmdb":
                    candidate["tmdb_id"] = ext_id.external_id
                elif ext_id.provider == "tvdb":
                    candidate["tvdb_id"] = ext_id.external_id
        candidates.append(candidate)

    return select_best_missing_external_match(
        title=title,
        year=year,
        candidates=candidates,
    )


async def get_profile_with_secrets(
    user_id: int, session: AsyncSession, profile_id: int | None = None
) -> UserProfile | None:
    """Get user profile with decrypted secrets for API calls."""

    if profile_id:
        profile = await get_profile_by_id(session, profile_id)
        if profile and profile.user_id == user_id:
            return profile
    return await get_default_profile(session, user_id)


def get_watchlist_providers_from_config(config: dict) -> list[WatchlistProviderInfo]:
    """Extract providers that support watchlist from user config."""
    providers = []

    # Check multi-provider format (sps alias)
    sps = config.get("sps") or config.get("streaming_providers") or []
    for sp in sps:
        if isinstance(sp, dict):
            service = sp.get("sv") or sp.get("service")
            if not service:
                continue
            enabled = sp.get("en", True) if "en" in sp else sp.get("enabled", True)
            # Check if watchlist is enabled for this provider
            watchlist_enabled = sp.get("ewc", True) if "ewc" in sp else sp.get("enable_watchlist_catalogs", True)

            if enabled and watchlist_enabled and service in WATCHLIST_SUPPORTED_PROVIDERS:
                providers.append(
                    WatchlistProviderInfo(
                        service=service,
                        name=sp.get("n") or sp.get("name"),
                        supports_watchlist=True,
                    )
                )

    # Fall back to legacy single provider
    if not providers:
        sp = config.get("sp") or config.get("streaming_provider")
        if sp and isinstance(sp, dict):
            service = sp.get("sv") or sp.get("service")
            if service and service in WATCHLIST_SUPPORTED_PROVIDERS:
                providers.append(
                    WatchlistProviderInfo(
                        service=service,
                        supports_watchlist=True,
                    )
                )

    return providers


async def fetch_info_hashes_for_provider(
    provider_service: str,
    streaming_provider: StreamingProvider,
    user_ip: str | None,
) -> list[str]:
    """Fetch downloaded info hashes from a debrid provider."""
    fetch_function = mapper.FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS.get(provider_service)
    if not fetch_function:
        return []

    try:
        info_hashes = await fetch_function(streaming_provider=streaming_provider, user_ip=user_ip)
        return info_hashes or []
    except Exception as e:
        logger.warning(f"Failed to fetch info hashes from {provider_service}: {e}")
        return []


async def fetch_torrent_details_for_provider(
    provider_service: str,
    streaming_provider: StreamingProvider,
    user_ip: str | None,
    target_hashes: set[str] | None = None,
) -> list[dict]:
    """Fetch detailed torrent information from a debrid provider."""
    fetch_function = mapper.FETCH_TORRENT_DETAILS_FUNCTIONS.get(provider_service)
    if not fetch_function:
        return []

    try:
        details = await fetch_function(
            streaming_provider=streaming_provider,
            user_ip=user_ip,
            target_hashes=target_hashes,
        )
        return details or []
    except Exception as e:
        logger.warning(f"Failed to fetch torrent details from {provider_service}: {e}")
        return []


async def get_existing_info_hashes(session: AsyncSession, info_hashes: list[str]) -> set[str]:
    """Get set of info hashes that already exist in our database."""
    if not info_hashes:
        return set()

    normalized = [h.lower() for h in info_hashes]
    query = select(TorrentStream.info_hash).where(TorrentStream.info_hash.in_(normalized))
    result = await session.exec(query)
    return set(result.all())


def parse_torrent_for_type(parsed_data: dict, files: list[dict]) -> str:
    """Determine if torrent is movie or series based on parsed data and files."""
    # Check if PTT detected season/episode
    if parsed_data.get("season") or parsed_data.get("episode"):
        return "series"

    # Check file count - multiple video files often indicate series
    video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"}
    video_files = [f for f in files if any(f.get("path", "").lower().endswith(ext) for ext in video_extensions)]
    if len(video_files) > 3:
        return "series"

    return "movie"


async def get_media_by_info_hashes(
    session: AsyncSession,
    info_hashes: list[str],
    media_type: MediaType | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[tuple[Media, list[str]]], int]:
    """Get media items that have streams matching the given info hashes.

    Returns: tuple of (list of (Media, info_hashes) tuples, total count)
    """
    if not info_hashes:
        return [], 0

    # Normalize info hashes to lowercase
    normalized_hashes = [h.lower() for h in info_hashes]
    hash_set = set(normalized_hashes)

    # Build base query - eagerly load external_ids to avoid lazy loading issues in async
    # Also select the info_hash so we can group them
    query = (
        select(Media, TorrentStream.info_hash)
        .options(selectinload(Media.external_ids))
        .join(StreamMediaLink, StreamMediaLink.media_id == Media.id)
        .join(Stream, Stream.id == StreamMediaLink.stream_id)
        .join(TorrentStream, TorrentStream.stream_id == Stream.id)
        .where(TorrentStream.info_hash.in_(normalized_hashes))
        .where(Stream.is_active.is_(True))
        .where(Stream.is_blocked.is_(False))
    )

    # Filter by media type if specified
    if media_type:
        query = query.where(Media.type == media_type)
    else:
        # Exclude TV channels from watchlist (they don't make sense here)
        query = query.where(Media.type.in_([MediaType.MOVIE, MediaType.SERIES]))

    # Execute query to get all media-hash pairs
    result = await session.exec(query)
    rows = result.all()

    # Group info_hashes by media_id
    media_dict: dict[int, tuple[Media, set[str]]] = {}
    for media, info_hash in rows:
        if media.id not in media_dict:
            media_dict[media.id] = (media, set())
        # Only include hashes that are actually in our debrid list
        if info_hash.lower() in hash_set:
            media_dict[media.id][1].add(info_hash.lower())

    # Convert to list and sort by title
    media_list = [(m, list(hashes)) for m, hashes in media_dict.values()]
    media_list.sort(key=lambda x: x[0].title)

    total = len(media_list)

    # Apply pagination
    offset = (page - 1) * page_size
    paginated = media_list[offset : offset + page_size]

    return paginated, total


async def get_primary_poster(session: AsyncSession, media_id: int) -> str | None:
    """Get the primary poster URL for a media item."""
    query = (
        select(MediaImage.url)
        .where(MediaImage.media_id == media_id)
        .where(MediaImage.image_type == "poster")
        .where(MediaImage.is_primary.is_(True))
        .limit(1)
    )
    result = await session.exec(query)
    return result.first()


def media_to_watchlist_item(
    media: Media,
    poster: str | None = None,
    info_hashes: list[str] | None = None,
) -> WatchlistItem:
    """Convert a Media model to a WatchlistItem."""
    # Get external IDs
    external_ids = ExternalIds()
    if hasattr(media, "external_ids") and media.external_ids:
        for ext_id in media.external_ids:
            if ext_id.provider == "imdb":
                external_ids.imdb = ext_id.external_id
            elif ext_id.provider == "tmdb":
                external_ids.tmdb = ext_id.external_id
            elif ext_id.provider == "tvdb":
                external_ids.tvdb = ext_id.external_id

    return WatchlistItem(
        id=media.id,
        title=media.title,
        type=media.type.value,
        year=media.year,
        poster=poster,
        external_ids=external_ids,
        info_hashes=info_hashes or [],
    )


# ============================================
# API Endpoints
# ============================================


@router.get("/providers", response_model=WatchlistProvidersResponse)
async def get_watchlist_providers(
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get list of debrid providers that support watchlist for the user's profile.

    Returns providers that:
    1. Are configured in the profile
    2. Support the watchlist feature (have FETCH_DOWNLOADED_INFO_HASHES_FUNCTIONS)
    3. Have watchlist catalogs enabled (ewc config)
    """
    # Get full config to check provider settings
    profile = await get_profile_with_secrets(current_user.id, session, profile_id)
    if not profile:
        return WatchlistProvidersResponse(providers=[], profile_id=0)

    # Decrypt config if needed

    config = profile.config or {}
    if profile.encrypted_secrets:
        try:
            secrets = profile_crypto.decrypt_secrets(profile.encrypted_secrets)
            config = profile_crypto.merge_secrets(config, secrets)
        except Exception:
            pass

    providers = get_watchlist_providers_from_config(config)

    return WatchlistProvidersResponse(
        providers=providers,
        profile_id=profile.id,
    )


@router.get("/{provider}", response_model=WatchlistResponse)
async def get_watchlist(
    provider: str,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    media_type: str | None = Query(None, description="Filter by media type (movie, series)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get watchlist items from a specific debrid provider.

    Returns media items that have been downloaded/cached in the user's debrid account.
    """
    # Validate provider supports watchlist
    if provider not in WATCHLIST_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support watchlist. Supported: {list(WATCHLIST_SUPPORTED_PROVIDERS)}",
        )

    # Get profile context with user data
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Find the specific provider in user's config
    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )

    # Get user IP for API calls
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)

    # Fetch info hashes from the debrid provider
    info_hashes = await fetch_info_hashes_for_provider(provider, provider_obj, user_ip)

    if not info_hashes:
        return WatchlistResponse(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            has_more=False,
            provider=provider,
            provider_name=provider_obj.name,
        )

    # Convert media_type string to enum
    media_type_enum = None
    if media_type:
        media_type_lower = media_type.lower()
        if media_type_lower == "movie":
            media_type_enum = MediaType.MOVIE
        elif media_type_lower == "series":
            media_type_enum = MediaType.SERIES

    # Get media items from database
    media_with_hashes, total = await get_media_by_info_hashes(
        session,
        info_hashes,
        media_type=media_type_enum,
        page=page,
        page_size=page_size,
    )

    # Build response items with posters and info_hashes
    items = []
    for media, hashes in media_with_hashes:
        poster = await get_primary_poster(session, media.id)
        items.append(media_to_watchlist_item(media, poster, hashes))

    has_more = (page * page_size) < total

    return WatchlistResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=has_more,
        provider=provider,
        provider_name=provider_obj.name,
    )


# Providers that support fetching detailed torrent info for import
IMPORT_SUPPORTED_PROVIDERS = set(mapper.FETCH_TORRENT_DETAILS_FUNCTIONS.keys())


@router.get("/{provider}/missing", response_model=MissingTorrentsResponse)
async def get_missing_torrents(
    provider: str,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Get torrents from debrid account that are NOT in our database.

    These are torrents the user has downloaded but we haven't scraped yet.
    Returns parsed metadata from torrent names for potential import.
    """
    # Validate provider supports detailed fetch
    if provider not in IMPORT_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support import. Supported: {list(IMPORT_SUPPORTED_PROVIDERS)}",
        )

    # Get profile context with user data
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Find the specific provider in user's config
    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )

    # Get user IP for API calls
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)

    all_hashes = await fetch_info_hashes_for_provider(provider, provider_obj, user_ip)
    existing_hashes = await get_existing_info_hashes(session, all_hashes)
    missing_hashes = [info_hash.lower() for info_hash in all_hashes if info_hash.lower() not in existing_hashes]
    missing_hash_set = set(missing_hashes)

    torrent_details: list[dict[str, Any]] = []
    if missing_hash_set:
        # Fetch detailed torrent info only for hashes that are missing in DB.
        torrent_details = await fetch_torrent_details_for_provider(
            provider,
            provider_obj,
            user_ip,
            target_hashes=missing_hash_set,
        )
        await set_cached_torrent_details(current_user.id, profile_id, provider, torrent_details)

    if not missing_hashes or not torrent_details:
        return MissingTorrentsResponse(
            items=[],
            total=0,
            provider=provider,
            provider_name=provider_obj.name,
        )

    # Filter to only missing torrents and parse metadata
    missing_items: list[MissingTorrentItem] = []
    metadata_candidates: list[tuple[int, str, int | None, str]] = []
    for torrent in torrent_details:
        info_hash = torrent["hash"].lower()
        if info_hash not in missing_hash_set:
            continue

        # Parse torrent name for metadata
        torrent_name = torrent.get("filename", "")
        parsed = parse_title(torrent_name, True) if torrent_name else {}

        # Convert files to response format
        files = [
            MissingTorrentFile(
                path=f.get("path", ""),
                size=f.get("size", 0),
            )
            for f in torrent.get("files", [])
        ]

        # Determine type
        parsed_type = parse_torrent_for_type(parsed, torrent.get("files", []))

        missing_item = MissingTorrentItem(
            info_hash=info_hash,
            name=torrent_name,
            size=torrent.get("size", 0),
            files=files,
            parsed_title=parsed.get("title"),
            parsed_year=parsed.get("year"),
            parsed_type=parsed_type,
        )
        missing_items.append(missing_item)
        if missing_item.parsed_title:
            metadata_candidates.append(
                (len(missing_items) - 1, missing_item.parsed_title, missing_item.parsed_year, parsed_type)
            )

    if metadata_candidates:
        for item_index, title, year, media_type in metadata_candidates:
            try:
                external_ids, matched_title = await find_missing_external_match_in_db(
                    session=session,
                    title=title,
                    year=year,
                    media_type=media_type,
                )
                if external_ids:
                    missing_items[item_index].external_ids = external_ids
                if matched_title:
                    missing_items[item_index].matched_title = matched_title
            except Exception:
                logger.exception("Failed to resolve external IDs for missing torrent title=%s", title)

    return MissingTorrentsResponse(
        items=missing_items,
        total=len(missing_items),
        provider=provider,
        provider_name=provider_obj.name,
    )


@router.post("/{provider}/import", response_model=ImportResponse)
async def import_torrents(
    provider: str,
    import_request: ImportRequest,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Import selected torrents from debrid account into our database.

    Parses torrent metadata, searches TMDB/IMDB for matching media,
    and creates stream entries.
    """
    # Validate provider supports import
    if provider not in IMPORT_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support import. Supported: {list(IMPORT_SUPPORTED_PROVIDERS)}",
        )

    if not import_request.info_hashes:
        raise HTTPException(status_code=400, detail="No torrents selected for import")

    # Normalize info hashes and overrides once for consistent lookups.
    requested_hashes = [info_hash.lower() for info_hash in import_request.info_hashes]
    overrides_lookup = {k.lower(): v for k, v in (import_request.overrides or {}).items()}

    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)
    cached_torrents_by_hash = await get_cached_torrent_details(current_user.id, profile_id, provider)
    cache_hit = bool(cached_torrents_by_hash) and all(
        info_hash in cached_torrents_by_hash for info_hash in requested_hashes
    )
    if cache_hit:
        torrents_by_hash = cached_torrents_by_hash or {}
    else:
        torrent_details = await fetch_torrent_details_for_provider(
            provider,
            provider_obj,
            user_ip,
            target_hashes=set(requested_hashes),
        )
        torrents_by_hash = await set_cached_torrent_details(current_user.id, profile_id, provider, torrent_details)
    existing_hashes = await get_existing_info_hashes(session, requested_hashes)

    meta_fetcher = MetadataFetcher()
    results_by_index: dict[int, ImportResultItem] = {}
    prep_candidates: list[tuple[int, str, dict[str, Any], TorrentOverride | None]] = []

    for index, info_hash in enumerate(requested_hashes):
        if info_hash in existing_hashes:
            results_by_index[index] = ImportResultItem(
                info_hash=info_hash,
                status="skipped",
                message="Already exists in database",
            )
            continue

        torrent = torrents_by_hash.get(info_hash)
        if not torrent:
            results_by_index[index] = ImportResultItem(
                info_hash=info_hash,
                status="failed",
                message="Torrent not found in debrid account",
            )
            continue

        prep_candidates.append((index, info_hash, torrent, overrides_lookup.get(info_hash)))

    prepared_items: list[PreparedImportItem] = []

    if prep_candidates:
        semaphore = asyncio.Semaphore(IMPORT_PREPARE_CONCURRENCY)

        async def run_preparation(candidate: tuple[int, str, dict[str, Any], TorrentOverride | None]):
            index, info_hash, torrent, override = candidate
            async with semaphore:
                return await prepare_import_item(index, info_hash, torrent, override, meta_fetcher)

        preparation_results = await asyncio.gather(*(run_preparation(candidate) for candidate in prep_candidates))
        for prep_result in preparation_results:
            if prep_result.result_item:
                results_by_index[prep_result.index] = prep_result.result_item
            elif prep_result.prepared_item:
                prepared_items.append(prep_result.prepared_item)

    if prepared_items:
        async for write_session in get_async_session():
            for batch in iter_chunks(prepared_items, IMPORT_DB_BATCH_SIZE):
                batch_hashes = [item.info_hash for item in batch]
                try:
                    pre_existing_hashes = await get_existing_info_hashes(write_session, batch_hashes)
                    stream_payloads: list[dict[str, Any]] = []
                    prepared_with_media: dict[str, tuple[PreparedImportItem, Any]] = {}

                    for item in batch:
                        if item.info_hash in pre_existing_hashes:
                            results_by_index[item.index] = ImportResultItem(
                                info_hash=item.info_hash,
                                status="skipped",
                                message="Already exists in database",
                            )
                            continue

                        media_obj = await crud.get_or_create_metadata(
                            write_session,
                            item.metadata,
                            item.media_type,
                            is_search_imdb_title=False,
                        )
                        if not media_obj:
                            results_by_index[item.index] = ImportResultItem(
                                info_hash=item.info_hash,
                                status="failed",
                                message="Failed to create metadata",
                            )
                            continue

                        meta_id = item.external_id if item.external_id.startswith("tt") else f"tt{media_obj.id}"
                        stream_payloads.append(
                            item.stream_data.model_copy(update={"meta_id": meta_id}).model_dump(by_alias=True)
                        )
                        prepared_with_media[item.info_hash] = (item, media_obj)

                    if stream_payloads:
                        await crud.store_new_torrent_streams(write_session, stream_payloads)

                    post_existing_hashes = (
                        await get_existing_info_hashes(write_session, list(prepared_with_media.keys()))
                        if prepared_with_media
                        else set()
                    )

                    for info_hash, (item, media_obj) in prepared_with_media.items():
                        if info_hash in post_existing_hashes:
                            results_by_index[item.index] = ImportResultItem(
                                info_hash=info_hash,
                                status="success",
                                message=f"Imported as {media_obj.title}",
                                media_id=media_obj.id,
                                media_title=media_obj.title,
                            )
                        else:
                            results_by_index[item.index] = ImportResultItem(
                                info_hash=info_hash,
                                status="failed",
                                message="Failed to store imported stream",
                            )

                    await write_session.commit()
                except Exception as error:
                    await write_session.rollback()
                    logger.exception("Failed to import torrent batch for provider=%s: %s", provider, error)
                    for item in batch:
                        if item.index not in results_by_index:
                            results_by_index[item.index] = ImportResultItem(
                                info_hash=item.info_hash,
                                status="failed",
                                message=str(error)[:200],
                            )
            break
    # Fill any unreported slot defensively to keep response length deterministic.
    for index, info_hash in enumerate(requested_hashes):
        if index not in results_by_index:
            results_by_index[index] = ImportResultItem(
                info_hash=info_hash,
                status="failed",
                message="Import did not produce a result",
            )

    ordered_results = [results_by_index[index] for index in range(len(requested_hashes))]
    imported = sum(1 for result in ordered_results if result.status == "success")
    failed = sum(1 for result in ordered_results if result.status == "failed")
    skipped = sum(1 for result in ordered_results if result.status == "skipped")

    return ImportResponse(
        imported=imported,
        failed=failed,
        skipped=skipped,
        details=ordered_results,
    )


@router.post("/{provider}/import/advanced", response_model=ImportResponse)
async def advanced_import_torrents(
    provider: str,
    import_request: AdvancedImportRequest,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Advanced import with file annotations for multi-content torrents.

    This endpoint allows importing torrents with per-file metadata linking,
    enabling movie collections and multi-series packs where each file
    is linked to a different media entry.
    """
    from api.routers.content.torrent_import import process_torrent_import

    # Validate provider supports import
    if provider not in IMPORT_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support import. Supported: {list(IMPORT_SUPPORTED_PROVIDERS)}",
        )

    if not import_request.advanced_imports:
        raise HTTPException(status_code=400, detail="No torrents provided for import")

    # Get profile context
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Find provider
    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )

    # Get user IP for API calls
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)

    # Fetch torrent details from provider
    requested_hashes = {imp.info_hash.lower() for imp in import_request.advanced_imports}
    torrent_details = await fetch_torrent_details_for_provider(
        provider,
        provider_obj,
        user_ip,
        target_hashes=requested_hashes,
    )
    torrents_by_hash = {t["hash"].lower(): t for t in torrent_details}

    # Check which are already in DB
    info_hashes = [imp.info_hash for imp in import_request.advanced_imports]
    existing_hashes = await get_existing_info_hashes(session, info_hashes)

    results = []
    imported = 0
    failed = 0
    skipped = 0

    for import_data in import_request.advanced_imports:
        info_hash_lower = import_data.info_hash.lower()

        # Skip if already exists
        if info_hash_lower in existing_hashes:
            results.append(
                ImportResultItem(
                    info_hash=info_hash_lower,
                    status="skipped",
                    message="Already exists in database",
                )
            )
            skipped += 1
            continue

        # Get torrent details from debrid
        torrent = torrents_by_hash.get(info_hash_lower)
        if not torrent:
            results.append(
                ImportResultItem(
                    info_hash=info_hash_lower,
                    status="failed",
                    message="Torrent not found in debrid account",
                )
            )
            failed += 1
            continue

        try:
            torrent_name = torrent.get("filename", "")
            parsed = parse_title(torrent_name, True) if torrent_name else {}

            # Build file_data from annotations or from torrent files
            file_data = []
            if import_data.file_data:
                # Use provided annotations
                for f in import_data.file_data:
                    if f.included:
                        file_data.append(
                            {
                                "index": f.index,
                                "filename": f.filename,
                                "size": f.size or 0,
                                "season_number": f.season_number,
                                "episode_number": f.episode_number,
                                "episode_end": f.episode_end,
                                "meta_id": f.meta_id,
                                "meta_title": f.meta_title,
                                "meta_type": f.meta_type,
                            }
                        )
            else:
                # Build from torrent files (video files only)
                video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"}
                for idx, f in enumerate(torrent.get("files", [])):
                    file_path = f.get("path", "")
                    if any(file_path.lower().endswith(ext) for ext in video_extensions):
                        filename = file_path.split("/")[-1] if "/" in file_path else file_path
                        file_data.append(
                            {
                                "index": idx,
                                "filename": filename,
                                "size": f.get("size", 0),
                            }
                        )

            # Build contribution data for process_torrent_import
            contribution_data = {
                "info_hash": info_hash_lower,
                "meta_type": import_data.meta_type,
                "meta_id": import_data.meta_id,
                "title": import_data.title or parsed.get("title", torrent_name),
                "name": torrent_name,
                "total_size": torrent.get("size", 0),
                "file_data": file_data,
                "file_count": len(file_data) or 1,
                # Quality attributes from PTT
                "resolution": parsed.get("resolution"),
                "codec": parsed.get("codec"),
                "quality": parsed.get("quality"),
                "audio": parsed.get("audio", []) if isinstance(parsed.get("audio"), list) else [],
                "hdr": parsed.get("hdr", []) if isinstance(parsed.get("hdr"), list) else [],
                "languages": parsed.get("languages", []),
                "is_anonymous": False,
            }

            # Process the import
            async for write_session in get_async_session():
                try:
                    import_result = await process_torrent_import(
                        write_session,
                        contribution_data,
                        current_user,
                    )
                    await write_session.commit()

                    if import_result.get("status") == "success":
                        # Get the media title for the result
                        media_id = import_result.get("media_id")
                        linked_count = import_result.get("linked_media_count", 1)
                        media_title = import_data.title or parsed.get("title", "Unknown")

                        if media_id:
                            media = await write_session.get(Media, media_id)
                            if media:
                                media_title = media.title

                        # Build message based on linked count
                        if linked_count > 1:
                            message = f"Imported with {linked_count} linked media entries (primary: {media_title})"
                        else:
                            message = f"Imported as {media_title}"

                        results.append(
                            ImportResultItem(
                                info_hash=info_hash_lower,
                                status="success",
                                message=message,
                                media_id=media_id,
                                media_title=media_title,
                            )
                        )
                        imported += 1
                    else:
                        results.append(
                            ImportResultItem(
                                info_hash=info_hash_lower,
                                status="skipped",
                                message=import_result.get("message", "Already exists"),
                            )
                        )
                        skipped += 1

                except Exception as e:
                    await write_session.rollback()
                    raise e

        except Exception as e:
            logger.exception(f"Failed to import torrent {info_hash_lower}: {e}")
            results.append(
                ImportResultItem(
                    info_hash=info_hash_lower,
                    status="failed",
                    message=str(e)[:200],
                )
            )
            failed += 1

    return ImportResponse(
        imported=imported,
        failed=failed,
        skipped=skipped,
        details=results,
    )


# Providers that support deleting individual torrents
DELETE_SUPPORTED_PROVIDERS = set(mapper.DELETE_TORRENT_FUNCTIONS.keys())


@router.post("/{provider}/remove", response_model=RemoveResponse)
async def remove_torrent_from_debrid(
    provider: str,
    remove_request: RemoveRequest,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Remove a specific torrent from debrid account by info_hash.

    This deletes the torrent from the user's debrid service.
    """
    # Validate provider supports delete
    if provider not in DELETE_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support torrent removal. Supported: {list(DELETE_SUPPORTED_PROVIDERS)}",
        )

    if not remove_request.info_hash:
        raise HTTPException(status_code=400, detail="info_hash is required")

    # Get profile context
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Find provider
    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )

    # Get user IP for API calls
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)

    # Get the delete function
    delete_function = mapper.DELETE_TORRENT_FUNCTIONS.get(provider)
    if not delete_function:
        return RemoveResponse(success=False, message="Delete not supported for this provider")

    try:
        success = await delete_function(
            streaming_provider=provider_obj, user_ip=user_ip, info_hash=remove_request.info_hash
        )

        if success:
            return RemoveResponse(success=True, message="Torrent removed from debrid account")
        else:
            return RemoveResponse(success=False, message="Torrent not found in debrid account")

    except Exception as e:
        logger.exception(f"Failed to remove torrent {remove_request.info_hash}: {e}")
        return RemoveResponse(success=False, message=str(e)[:200])


# Providers that support clearing all torrents
CLEAR_ALL_SUPPORTED_PROVIDERS = set(mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.keys())


@router.post("/{provider}/clear-all", response_model=RemoveResponse)
async def clear_all_torrents_from_debrid(
    provider: str,
    request: Request,
    profile_id: int | None = Query(None, description="Profile ID to use"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    """
    Clear all torrents from debrid account.

    WARNING: This deletes ALL torrents from the user's debrid service.
    """
    # Validate provider supports clear all
    if provider not in CLEAR_ALL_SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' does not support clearing all torrents. Supported: {list(CLEAR_ALL_SUPPORTED_PROVIDERS)}",
        )

    # Get profile context
    profile_ctx = await ProfileDataProvider.get_context(current_user.id, session, profile_id=profile_id)

    # Find provider
    provider_obj = None
    for sp in profile_ctx.user_data.get_active_providers():
        if sp.service == provider:
            provider_obj = sp
            break

    if not provider_obj:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured in your profile",
        )

    # Get user IP for API calls
    user_ip = await get_user_public_ip(request, profile_ctx.user_data, streaming_provider=provider_obj)

    # Get the clear all function
    clear_function = mapper.DELETE_ALL_WATCHLIST_FUNCTIONS.get(provider)
    if not clear_function:
        return RemoveResponse(success=False, message="Clear all not supported for this provider")

    try:
        await clear_function(streaming_provider=provider_obj, user_ip=user_ip)
        return RemoveResponse(success=True, message="All torrents cleared from debrid account")

    except Exception as e:
        logger.exception(f"Failed to clear all torrents from {provider}: {e}")
        return RemoveResponse(success=False, message=str(e)[:200])
