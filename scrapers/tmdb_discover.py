"""
TMDB discovery helpers for the Discover feature.

These functions take an explicit api_key (resolved per-user) rather than reading
settings.tmdb_api_key, so each user's quota is consumed against their own key.
"""

import asyncio
import hashlib
import logging
import time
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

from db.config import settings
from db.schemas.config import UserData
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3/"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"

# Simple in-process TTL cache: key -> (expires_at, data)
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 600  # 10 minutes


def _cache_key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and time.monotonic() < entry[0]:
        return entry[1]
    _cache.pop(key, None)
    return None


def _cache_set(key: str, value: Any, ttl: int = _CACHE_TTL) -> None:
    _cache[key] = (time.monotonic() + ttl, value)


def resolve_tmdb_key(user_data: UserData) -> str | None:
    """Return the TMDB API key to use for this user, or None if unavailable."""
    if user_data.tmdb_config and user_data.tmdb_config.api_key:
        return user_data.tmdb_config.api_key
    if settings.discover_allow_server_key:
        return settings.tmdb_api_key
    return None


def _key_fingerprint(api_key: str) -> str:
    """Short fingerprint so the cache key doesn't embed the raw API key."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


async def _tmdb_get(endpoint: str, params: dict, api_key: str, max_retries: int = 3) -> dict[str, Any] | None:
    full_params = {"api_key": api_key, **params}
    url = urljoin(TMDB_BASE_URL, endpoint)
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=10) as client:
                resp = await client.get(url, params=full_params, headers=UA_HEADER)
                if resp.status_code == 429:
                    await asyncio.sleep(2**attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise
            logger.warning(f"TMDB HTTP {e.response.status_code} on {endpoint}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"TMDB request error on {endpoint}: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1)
    return None


def _normalize_movie(item: dict) -> dict:
    poster = item.get("poster_path")
    backdrop = item.get("backdrop_path")
    return {
        "provider": "tmdb",
        "external_id": str(item["id"]),
        "media_type": "movie",
        "title": item.get("title") or item.get("original_title", ""),
        "year": (item.get("release_date") or "")[:4] or None,
        "release_date": item.get("release_date"),
        "poster": f"{TMDB_IMAGE_BASE_URL}w500{poster}" if poster else None,
        "backdrop": f"{TMDB_IMAGE_BASE_URL}w1280{backdrop}" if backdrop else None,
        "overview": item.get("overview", ""),
        "popularity": item.get("popularity", 0),
        "vote_average": item.get("vote_average", 0),
        "genre_ids": item.get("genre_ids", []),
    }


def _normalize_tv(item: dict) -> dict:
    poster = item.get("poster_path")
    backdrop = item.get("backdrop_path")
    return {
        "provider": "tmdb",
        "external_id": str(item["id"]),
        "media_type": "series",
        "title": item.get("name") or item.get("original_name", ""),
        "year": (item.get("first_air_date") or "")[:4] or None,
        "release_date": item.get("first_air_date"),
        "poster": f"{TMDB_IMAGE_BASE_URL}w500{poster}" if poster else None,
        "backdrop": f"{TMDB_IMAGE_BASE_URL}w1280{backdrop}" if backdrop else None,
        "overview": item.get("overview", ""),
        "popularity": item.get("popularity", 0),
        "vote_average": item.get("vote_average", 0),
        "genre_ids": item.get("genre_ids", []),
    }


def _normalize_multi(item: dict) -> dict | None:
    mt = item.get("media_type")
    if mt == "movie":
        return _normalize_movie(item)
    if mt == "tv":
        return _normalize_tv(item)
    return None


async def tmdb_trending(
    api_key: str,
    media_type: Literal["movie", "tv", "all"] = "all",
    window: Literal["day", "week"] = "week",
    language: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Fetch trending titles from TMDB."""
    ck = _cache_key(_key_fingerprint(api_key), "trending", media_type, window, language, page)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    endpoint = f"trending/{media_type}/{window}"
    params: dict = {"page": page, "language": "en-US"}
    if language:
        params["with_original_language"] = language
    data = await _tmdb_get(endpoint, params, api_key)
    if not data:
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}

    items = []
    for item in data.get("results", []):
        if media_type == "all":
            norm = _normalize_multi(item)
        elif media_type == "movie":
            norm = _normalize_movie(item)
        else:
            norm = _normalize_tv(item)
        if norm:
            items.append(norm)

    result = {
        "items": items,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", len(items)),
    }
    _cache_set(ck, result)
    return result


_TV_KIND_MAP = {
    "popular": "popular",
    "top_rated": "top_rated",
    "now_playing": "airing_today",
    "upcoming": "on_the_air",
}


async def tmdb_list(
    api_key: str,
    kind: Literal["popular", "top_rated", "now_playing", "upcoming"],
    media_type: Literal["movie", "tv"],
    language: str | None = None,
    page: int = 1,
    region: str | None = None,
) -> dict[str, Any]:
    """Fetch a named TMDB list (popular, top_rated, now_playing, upcoming)."""
    ck = _cache_key(_key_fingerprint(api_key), "list", kind, media_type, language, page, region)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    if media_type == "tv":
        endpoint = f"tv/{_TV_KIND_MAP.get(kind, kind)}"
    else:
        endpoint = f"movie/{kind}"

    params: dict = {"page": page, "language": "en-US"}
    if region:
        params["region"] = region
    if language:
        params["with_original_language"] = language

    data = await _tmdb_get(endpoint, params, api_key)
    if not data:
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}

    normalizer = _normalize_tv if media_type == "tv" else _normalize_movie
    items = [normalizer(item) for item in data.get("results", [])]
    result = {
        "items": items,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", len(items)),
    }
    _cache_set(ck, result)
    return result


async def tmdb_discover(
    api_key: str,
    media_type: Literal["movie", "tv"],
    language: str | None = None,
    page: int = 1,
    with_watch_providers: list[int] | None = None,
    watch_region: str | None = None,
    sort_by: str = "popularity.desc",
    with_genres: list[int] | None = None,
    primary_release_date_gte: str | None = None,
    primary_release_date_lte: str | None = None,
) -> dict[str, Any]:
    """Generic TMDB discover endpoint — used for per-OTT-provider feeds."""
    ck = _cache_key(
        _key_fingerprint(api_key),
        "discover",
        media_type,
        language,
        page,
        with_watch_providers,
        watch_region,
        sort_by,
        with_genres,
        primary_release_date_gte,
        primary_release_date_lte,
    )
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    endpoint = f"discover/{media_type}"
    params: dict = {"page": page, "language": "en-US", "sort_by": sort_by}
    if language:
        params["with_original_language"] = language
    if with_watch_providers:
        params["with_watch_providers"] = "|".join(str(p) for p in with_watch_providers)
        params["watch_monetization_types"] = "flatrate"
    if watch_region:
        params["watch_region"] = watch_region
    if with_genres:
        params["with_genres"] = "|".join(str(g) for g in with_genres)
    if primary_release_date_gte:
        if media_type == "movie":
            params["primary_release_date.gte"] = primary_release_date_gte
        else:
            params["first_air_date.gte"] = primary_release_date_gte
    if primary_release_date_lte:
        if media_type == "movie":
            params["primary_release_date.lte"] = primary_release_date_lte
        else:
            params["first_air_date.lte"] = primary_release_date_lte

    data = await _tmdb_get(endpoint, params, api_key)
    if not data:
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}

    normalizer = _normalize_tv if media_type == "tv" else _normalize_movie
    items = [normalizer(item) for item in data.get("results", [])]
    result = {
        "items": items,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", len(items)),
    }
    _cache_set(ck, result)
    return result


async def tmdb_watch_provider_list(
    api_key: str,
    media_type: Literal["movie", "tv"],
    watch_region: str = "US",
) -> list[dict[str, Any]]:
    """Return available watch providers for a region. Long TTL cached."""
    ck = _cache_key(_key_fingerprint(api_key), "watch_providers", media_type, watch_region)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    endpoint = f"watch/providers/{media_type}"
    data = await _tmdb_get(endpoint, {"watch_region": watch_region, "language": "en-US"}, api_key)
    if not data:
        return []

    providers = [
        {
            "provider_id": p["provider_id"],
            "name": p["provider_name"],
            "logo": f"{TMDB_IMAGE_BASE_URL}original{p['logo_path']}" if p.get("logo_path") else None,
        }
        for p in data.get("results", [])
    ]
    _cache_set(ck, providers, ttl=3600)
    return providers


async def tmdb_search(
    api_key: str,
    query: str,
    media_type: Literal["movie", "tv", "all"] = "all",
    page: int = 1,
    language: str | None = None,
) -> dict[str, Any]:
    """Search TMDB by title."""
    ck = _cache_key(_key_fingerprint(api_key), "search", query, media_type, page, language)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    if media_type == "all":
        endpoint = "search/multi"
        normalizer = None
    elif media_type == "movie":
        endpoint = "search/movie"
        normalizer = _normalize_movie
    else:
        endpoint = "search/tv"
        normalizer = _normalize_tv

    params: dict = {"query": query, "page": page, "language": "en-US"}
    if language:
        params["with_original_language"] = language

    data = await _tmdb_get(endpoint, params, api_key)
    if not data:
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}

    items = []
    for item in data.get("results", []):
        if media_type == "all":
            norm = _normalize_multi(item)
        else:
            norm = normalizer(item)  # type: ignore[misc]
        if norm:
            items.append(norm)

    result = {
        "items": items,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results", len(items)),
    }
    _cache_set(ck, result, ttl=300)  # 5 min for search results
    return result
