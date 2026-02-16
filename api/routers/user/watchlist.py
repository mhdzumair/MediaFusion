"""
Debrid Watchlist API endpoints.

Provides access to content downloaded/cached in user's debrid accounts.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from PTT import parse_title
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.user.auth import require_auth
from db.database import get_read_session, get_async_session
from db.enums import MediaType
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
) -> list[dict]:
    """Fetch detailed torrent information from a debrid provider."""
    fetch_function = mapper.FETCH_TORRENT_DETAILS_FUNCTIONS.get(provider_service)
    if not fetch_function:
        return []

    try:
        details = await fetch_function(streaming_provider=streaming_provider, user_ip=user_ip)
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

    # Fetch detailed torrent info from provider
    torrent_details = await fetch_torrent_details_for_provider(provider, provider_obj, user_ip)

    if not torrent_details:
        return MissingTorrentsResponse(
            items=[],
            total=0,
            provider=provider,
            provider_name=provider_obj.name,
        )

    # Get info hashes that already exist in our database
    all_hashes = [t["hash"] for t in torrent_details]
    existing_hashes = await get_existing_info_hashes(session, all_hashes)

    # Filter to only missing torrents and parse metadata
    missing_items = []
    for torrent in torrent_details:
        info_hash = torrent["hash"].lower()
        if info_hash in existing_hashes:
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

        missing_items.append(
            MissingTorrentItem(
                info_hash=info_hash,
                name=torrent_name,
                size=torrent.get("size", 0),
                files=files,
                parsed_title=parsed.get("title"),
                parsed_year=parsed.get("year"),
                parsed_type=parsed_type,
            )
        )

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

    # Fetch torrent details
    torrent_details = await fetch_torrent_details_for_provider(provider, provider_obj, user_ip)

    # Create lookup by hash
    torrents_by_hash = {t["hash"].lower(): t for t in torrent_details}

    # Check which are already in DB
    existing_hashes = await get_existing_info_hashes(session, import_request.info_hashes)

    # Initialize metadata fetcher
    meta_fetcher = MetadataFetcher()

    results = []
    imported = 0
    failed = 0
    skipped = 0

    for info_hash in import_request.info_hashes:
        info_hash_lower = info_hash.lower()

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

        # Get torrent details
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
            # Parse torrent name
            torrent_name = torrent.get("filename", "")
            parsed = parse_title(torrent_name, True) if torrent_name else {}

            # Apply user overrides if provided
            override = import_request.overrides.get(info_hash_lower) if import_request.overrides else None
            if override:
                if override.title:
                    parsed["title"] = override.title
                if override.year:
                    parsed["year"] = override.year

            if not parsed.get("title"):
                results.append(
                    ImportResultItem(
                        info_hash=info_hash_lower,
                        status="failed",
                        message="Could not parse title from torrent name",
                    )
                )
                failed += 1
                continue

            # Determine media type (use override if provided)
            if override and override.type:
                media_type = override.type
            else:
                media_type = parse_torrent_for_type(parsed, torrent.get("files", []))

            # Search for metadata
            search_results = await meta_fetcher.search_multiple_results(
                title=parsed["title"],
                year=parsed.get("year"),
                media_type=media_type,
                limit=5,
                min_similarity=70,
            )

            if not search_results:
                results.append(
                    ImportResultItem(
                        info_hash=info_hash_lower,
                        status="failed",
                        message=f"No TMDB/IMDB match found for '{parsed['title']}'",
                    )
                )
                failed += 1
                continue

            # Use best match
            best_match = search_results[0]
            external_id = best_match.get("imdb_id") or best_match.get("id")

            if not external_id:
                results.append(
                    ImportResultItem(
                        info_hash=info_hash_lower,
                        status="failed",
                        message="No valid external ID found",
                    )
                )
                failed += 1
                continue

            # Get or create metadata in DB
            metadata = {
                "id": external_id,
                "title": best_match.get("title", parsed["title"]),
                "year": best_match.get("year", parsed.get("year")),
                "poster": best_match.get("poster"),
                "background": best_match.get("background"),
                "description": best_match.get("description"),
                "genres": best_match.get("genres", []),
            }

            media_obj = None
            async for write_session in get_async_session():
                media_obj = await crud.get_or_create_metadata(
                    write_session,
                    metadata,
                    media_type,
                    is_search_imdb_title=False,
                )
                await write_session.commit()

            if not media_obj:
                results.append(
                    ImportResultItem(
                        info_hash=info_hash_lower,
                        status="failed",
                        message="Failed to create metadata",
                    )
                )
                failed += 1
                continue

            meta_id = f"tt{media_obj.id}" if not external_id.startswith("tt") else external_id

            # Build files list
            files = []
            video_extensions = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v"}
            for idx, f in enumerate(torrent.get("files", [])):
                file_path = f.get("path", "")
                if any(file_path.lower().endswith(ext) for ext in video_extensions):
                    files.append(
                        StreamFileData(
                            file_index=idx,
                            filename=file_path.split("/")[-1] if "/" in file_path else file_path,
                            size=f.get("size", 0),
                            file_type="video",
                        )
                    )

            # If no video files found but we have files, use the largest one
            if not files and torrent.get("files"):
                largest = max(torrent["files"], key=lambda x: x.get("size", 0))
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

            # Create stream data
            stream_data = TorrentStreamData(
                info_hash=info_hash_lower,
                meta_id=meta_id,
                name=torrent_name,
                size=torrent.get("size", 0),
                source="debrid_import",
                files=files,
                # Quality attributes from PTT
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

            # Store in database
            async for write_session in get_async_session():
                await crud.store_new_torrent_streams(write_session, [stream_data.model_dump(by_alias=True)])
                await write_session.commit()

            results.append(
                ImportResultItem(
                    info_hash=info_hash_lower,
                    status="success",
                    message=f"Imported as {media_obj.title}",
                    media_id=media_obj.id,
                    media_title=media_obj.title,
                )
            )
            imported += 1

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
    torrent_details = await fetch_torrent_details_for_provider(provider, provider_obj, user_ip)
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
        success = await delete_function(streaming_provider=provider_obj, user_ip=user_ip, info_hash=remove_request.info_hash)

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
