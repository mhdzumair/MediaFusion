"""
Discover API endpoints — browse external catalogs (TMDB, AniList, Kitsu)
without requiring streams to already exist in MediaFusion.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from api.routers.content.scraping import get_full_profile_config
from api.routers.user.auth import require_auth
from db.config import settings
from db.database import get_read_session
from db.models import User, UserProfile
from db.models.providers import MediaExternalID
from db.schemas.config import UserData
from scrapers.mdblist_discover import mdblist_list_items
from scrapers.tmdb_discover import (
    resolve_tmdb_key,
    tmdb_discover,
    tmdb_list,
    tmdb_search,
    tmdb_trending,
    tmdb_watch_provider_list,
)
from scrapers.tvdb_discover import resolve_tvdb_key, tvdb_filter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/discover", tags=["Discover"])


async def _get_user_data(session: AsyncSession, user: User) -> UserData:
    """Load UserData (with decrypted secrets) for the current user's default profile."""
    result = await session.exec(
        select(UserProfile).where(UserProfile.user_id == user.id, UserProfile.is_default == True)
    )
    profile = result.first()
    if not profile:
        result = await session.exec(select(UserProfile).where(UserProfile.user_id == user.id))
        profile = result.first()
    if not profile:
        raise HTTPException(status_code=400, detail="No profile configured")
    config = get_full_profile_config(profile)
    return UserData.model_validate(config)


def _require_tmdb_key(user_data: UserData) -> str:
    """Return the resolved TMDB API key or raise 412."""
    if not settings.discover_enabled:
        raise HTTPException(status_code=404, detail="Discover feature is disabled")
    key = resolve_tmdb_key(user_data)
    if not key:
        raise HTTPException(
            status_code=412,
            detail={
                "code": "tmdb_key_required",
                "message": (
                    "Add your TMDB API key in Settings → Configure → External Services to use the Discover feature."
                ),
            },
        )
    return key


async def _build_db_index(session: AsyncSession, items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build a map of "<provider>:<external_id>" -> {"id": media_id, "imdb_id": str | None}
    for items already in DB. Uses two batch queries to avoid N+1.
    """
    if not items:
        return {}

    pairs: list[tuple[str, str]] = [(i["provider"], i["external_id"]) for i in items]
    from sqlalchemy import tuple_ as sa_tuple

    stmt = select(MediaExternalID).where(sa_tuple(MediaExternalID.provider, MediaExternalID.external_id).in_(pairs))
    result = await session.exec(stmt)
    rows = result.all()
    media_id_map: dict[str, int] = {f"{row.provider}:{row.external_id}": row.media_id for row in rows}

    found_media_ids = list(set(media_id_map.values()))
    imdb_stmt = select(MediaExternalID).where(
        MediaExternalID.provider == "imdb",
        MediaExternalID.media_id.in_(found_media_ids),
    )
    imdb_result = await session.exec(imdb_stmt)
    imdb_rows = imdb_result.all()
    imdb_by_media: dict[int, str] = {row.media_id: row.external_id for row in imdb_rows}

    return {key: {"id": media_id, "imdb_id": imdb_by_media.get(media_id)} for key, media_id in media_id_map.items()}


def _paginated_response(raw: dict[str, Any], db_index: dict[str, int]) -> dict[str, Any]:
    return {
        "items": raw["items"],
        "page": raw["page"],
        "total_pages": raw["total_pages"],
        "total_results": raw["total_results"],
        "db_index": db_index,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/trending")
async def discover_trending(
    media_type: Literal["movie", "tv", "all"] = Query("all"),
    window: Literal["day", "week"] = Query("week"),
    language: str | None = Query(None),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    user_data = await _get_user_data(session, current_user)
    api_key = _require_tmdb_key(user_data)
    raw = await tmdb_trending(api_key, media_type=media_type, window=window, language=language, page=page)
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/list")
async def discover_list(
    kind: Literal["popular", "top_rated", "now_playing", "upcoming"] = Query("popular"),
    media_type: Literal["movie", "tv"] = Query("movie"),
    language: str | None = Query(None),
    page: int = Query(1, ge=1),
    region: str | None = Query(None),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    user_data = await _get_user_data(session, current_user)
    api_key = _require_tmdb_key(user_data)
    raw = await tmdb_list(api_key, kind=kind, media_type=media_type, language=language, page=page, region=region)
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/watch-providers")
async def discover_watch_providers(
    media_type: Literal["movie", "tv"] = Query("movie"),
    region: str = Query("US"),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    user_data = await _get_user_data(session, current_user)
    api_key = _require_tmdb_key(user_data)
    providers = await tmdb_watch_provider_list(api_key, media_type=media_type, watch_region=region)
    return {"providers": providers, "region": region}


@router.get("/provider-feed")
async def discover_provider_feed(
    media_type: Literal["movie", "tv"] = Query("movie"),
    provider_id: int = Query(...),
    region: str = Query("US"),
    sort_by: str = Query("primary_release_date.desc"),
    language: str | None = Query(None),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    user_data = await _get_user_data(session, current_user)
    api_key = _require_tmdb_key(user_data)
    raw = await tmdb_discover(
        api_key,
        media_type=media_type,
        language=language,
        page=page,
        with_watch_providers=[provider_id],
        watch_region=region,
        sort_by=sort_by,
    )
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/anime")
async def discover_anime(
    kind: Literal["trending", "seasonal"] = Query("trending"),
    season: str | None = Query(None, description="winter|spring|summer|fall"),
    year: int | None = Query(None),
    source: Literal["anilist", "kitsu"] = Query("anilist"),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    if not settings.discover_enabled:
        raise HTTPException(status_code=404, detail="Discover feature is disabled")

    if source == "anilist":
        from scrapers.mal_data import anilist_seasonal, anilist_trending

        if kind == "seasonal":
            if not season or not year:
                raise HTTPException(status_code=422, detail="season and year are required for kind=seasonal")
            raw = await anilist_seasonal(season=season, year=year, page=page)
        else:
            raw = await anilist_trending(page=page)
    else:
        from scrapers.kitsu_data import kitsu_trending

        raw = await kitsu_trending(page=page)

    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/search")
async def discover_search(
    query: str = Query(..., min_length=1),
    media_type: Literal["movie", "tv", "all"] = Query("all"),
    language: str | None = Query(None),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    user_data = await _get_user_data(session, current_user)
    api_key = _require_tmdb_key(user_data)
    raw = await tmdb_search(api_key, query=query, media_type=media_type, page=page, language=language)
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/tvdb-filter")
async def discover_tvdb_filter(
    media_type: Literal["movie", "tv"] = Query("tv"),
    sort: str = Query("score"),
    sort_type: str = Query("desc"),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    if not settings.discover_enabled:
        raise HTTPException(status_code=404, detail="Discover feature is disabled")
    user_data = await _get_user_data(session, current_user)
    api_key = resolve_tvdb_key(user_data)
    if not api_key:
        raise HTTPException(
            status_code=412,
            detail={"code": "tvdb_key_required", "message": "Add your TVDB API key in Settings to use TVDB Discover."},
        )
    raw = await tvdb_filter(api_key, media_type=media_type, sort=sort, sort_type=sort_type, page=page)
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/mdblist")
async def discover_mdblist(
    list_id: int = Query(...),
    catalog_type: Literal["movie", "series"] = Query("movie"),
    page: int = Query(1, ge=1),
    current_user: User = Depends(require_auth),
    session: AsyncSession = Depends(get_read_session),
):
    if not settings.discover_enabled:
        raise HTTPException(status_code=404, detail="Discover feature is disabled")
    user_data = await _get_user_data(session, current_user)
    api_key = user_data.mdblist_config and user_data.mdblist_config.api_key
    if not api_key:
        raise HTTPException(
            status_code=412,
            detail={
                "code": "mdblist_key_required",
                "message": "Configure your MDBList API key to use MDBList Discover.",
            },
        )
    raw = await mdblist_list_items(api_key, list_id=list_id, catalog_type=catalog_type, page=page)
    db_index = await _build_db_index(session, raw["items"])
    return _paginated_response(raw, db_index)


@router.get("/verify-tmdb-key")
async def verify_tmdb_key(
    api_key: str = Query(...),
    current_user: User = Depends(require_auth),
):
    """Validate a TMDB API key by making a lightweight test request."""
    import httpx
    from urllib.parse import urljoin

    from scrapers.tmdb_discover import TMDB_BASE_URL
    from utils.const import UA_HEADER

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=8) as client:
            resp = await client.get(
                urljoin(TMDB_BASE_URL, "configuration"),
                params={"api_key": api_key},
                headers=UA_HEADER,
            )
            if resp.status_code == 401:
                return {"valid": False, "error": "Invalid API key"}
            resp.raise_for_status()
            return {"valid": True}
    except Exception as e:
        logger.warning(f"TMDB key verification error: {e}")
        return {"valid": False, "error": str(e)}
