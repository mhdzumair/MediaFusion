"""
TVDB-based discovery helpers — popular/trending series and movies
for the Discover feature.  Uses per-user API keys with a server key fallback.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx

from db.config import settings
from db.schemas.config import UserData
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

TVDB_API_URL = "https://api4.thetvdb.com/v4"
TVDB_IMAGE_BASE = "https://artworks.thetvdb.com/banners/"

# Per-key bearer-token cache: api_key -> {token, expires_at}
_token_cache: dict[str, dict[str, Any]] = {}


def resolve_tvdb_key(user_data: UserData) -> str | None:
    if user_data.tvdb_config and user_data.tvdb_config.api_key:
        return user_data.tvdb_config.api_key
    return None


async def _get_tvdb_token(api_key: str) -> str | None:
    cached = _token_cache.get(api_key)
    if cached and cached.get("token") and datetime.now() < cached["expires_at"]:
        return cached["token"]

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=15) as client:
            resp = await client.post(
                f"{TVDB_API_URL}/login",
                json={"apikey": api_key},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            token = resp.json().get("data", {}).get("token")
            if token:
                _token_cache[api_key] = {
                    "token": token,
                    "expires_at": datetime.now() + timedelta(days=27),
                }
                return token
    except Exception as e:
        logger.error(f"TVDB token fetch error: {e}")
    return None


def _tvdb_poster(image: str | None) -> str | None:
    if not image:
        return None
    if image.startswith("http"):
        return image
    # Some TVDB paths already include the "banners/" prefix — strip it to avoid doubling.
    path = image.lstrip("/")
    if path.startswith("banners/"):
        path = path[len("banners/") :]
    return TVDB_IMAGE_BASE + path


def _extract_remote_ids(item: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in item.get("remoteIds") or []:
        if not r:
            continue
        src = (r.get("sourceName") or "").lower()
        if "imdb" in src and r.get("id"):
            out["imdb"] = str(r["id"])
        elif ("tmdb" in src or "themoviedb" in src) and r.get("id"):
            out["tmdb"] = str(r["id"])
    return out


def _extract_genres(item: dict[str, Any]) -> list[str]:
    """Extract genre names safely — TVDB returns genres as objects, strings, or IDs."""
    genres: list[str] = []
    for g in item.get("genres") or []:
        if isinstance(g, dict):
            name = g.get("name") or g.get("slug") or ""
            if name:
                genres.append(name)
        elif isinstance(g, str) and g:
            genres.append(g)
    return genres


def _normalize_tvdb_series(item: dict[str, Any]) -> dict[str, Any]:
    year_str = item.get("firstAired") or item.get("year") or ""
    year_str = str(year_str)[:4] if year_str else None
    remote_ids = _extract_remote_ids(item)
    return {
        "provider": "tvdb",
        "external_id": str(item["id"]),
        "media_type": "series",
        "title": item.get("name") or "",
        "year": year_str,
        "release_date": item.get("firstAired"),
        "poster": _tvdb_poster(item.get("image")),
        "backdrop": None,
        "overview": item.get("overview") or "",
        "popularity": item.get("score") or 0,
        "vote_average": 0.0,
        "genre_ids": [],
        "genres": _extract_genres(item),
        "imdb_id": remote_ids.get("imdb"),
    }


def _normalize_tvdb_movie(item: dict[str, Any]) -> dict[str, Any]:
    year_val = item.get("year") or item.get("releaseYear")
    year_str = str(year_val)[:4] if year_val else None
    remote_ids = _extract_remote_ids(item)
    return {
        "provider": "tvdb",
        "external_id": str(item["id"]),
        "media_type": "movie",
        "title": item.get("name") or "",
        "year": year_str,
        "release_date": item.get("year") and f"{item['year']}-01-01",
        "poster": _tvdb_poster(item.get("image")),
        "backdrop": None,
        "overview": item.get("overview") or "",
        "popularity": item.get("score") or 0,
        "vote_average": 0.0,
        "genre_ids": [],
        "genres": _extract_genres(item),
        "imdb_id": remote_ids.get("imdb"),
    }


async def tvdb_filter(
    api_key: str,
    media_type: Literal["movie", "tv"] = "tv",
    sort: str = "score",
    sort_type: str = "desc",
    page: int = 1,
) -> dict[str, Any]:
    """
    Fetch popular/filtered series or movies from TVDB.
    TVDB uses 0-based page numbers; we accept and return 1-based.
    """
    token = await _get_tvdb_token(api_key)
    if not token:
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}

    endpoint = "series" if media_type == "tv" else "movies"
    tvdb_page = page - 1  # 0-based

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=15) as client:
            resp = await client.get(
                f"{TVDB_API_URL}/{endpoint}/filter",
                params={"sort": sort, "sortType": sort_type, "page": tvdb_page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    **UA_HEADER,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        items_raw = data.get("data") or []
        links = data.get("links") or {}
        total_items = links.get("total_items") or len(items_raw)
        page_size = links.get("page_size") or max(len(items_raw), 20)
        total_pages = max(1, (total_items + page_size - 1) // page_size)

        normalize = _normalize_tvdb_series if media_type == "tv" else _normalize_tvdb_movie
        items = []
        for raw in items_raw:
            if not raw or not raw.get("id"):
                continue
            try:
                items.append(normalize(raw))
            except Exception as norm_err:
                logger.debug(f"TVDB normalize skip id={raw.get('id')}: {norm_err}")

        return {
            "items": items,
            "page": page,
            "total_pages": total_pages,
            "total_results": total_items,
        }
    except Exception as e:
        logger.error(f"TVDB filter ({media_type}) page={page} error: {e}", exc_info=True)
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}
