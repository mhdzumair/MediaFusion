"""
MDBList-based discovery helpers — fetch items from a user's configured list
and return them in the common DiscoverItem shape.
"""

import logging
from typing import Any, Literal

import httpx

from db.config import settings

logger = logging.getLogger(__name__)

MDBLIST_BASE = "https://api.mdblist.com"
PAGE_SIZE = 20


def _normalize_mdblist_item(
    item: dict[str, Any],
    catalog_type: Literal["movie", "series"],
) -> dict[str, Any] | None:
    imdb_id = item.get("imdb_id")
    if not imdb_id or not imdb_id.startswith("tt"):
        return None

    release_year = item.get("release_year")
    poster = f"{settings.poster_host_url}/poster/{catalog_type}/{imdb_id}.jpg"

    genres_raw = item.get("genre") or []
    genres = [g for g in genres_raw if g] if isinstance(genres_raw, list) else []

    score = item.get("score") or 0
    vote = round(score / 10, 1) if score else 0.0

    return {
        "provider": "imdb",
        "external_id": imdb_id,
        "media_type": catalog_type,
        "title": item.get("title") or "",
        "year": str(release_year) if release_year else None,
        "release_date": f"{release_year}-01-01" if release_year else None,
        "poster": poster,
        "backdrop": None,
        "overview": item.get("description") or "",
        "popularity": score,
        "vote_average": vote,
        "genre_ids": [],
        "genres": genres,
    }


async def mdblist_list_items(
    api_key: str,
    list_id: int,
    catalog_type: Literal["movie", "series"],
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> dict[str, Any]:
    """
    Fetch one page of items from a user's MDBList list.
    Returns a DiscoverPage-shaped dict.
    """
    offset = (page - 1) * page_size
    mdb_type = "movies" if catalog_type == "movie" else "shows"

    params = {
        "apikey": api_key,
        "limit": page_size,
        "offset": offset,
        "append_to_response": "genre",
    }

    try:
        async with httpx.AsyncClient(proxy=settings.requests_proxy_url, timeout=20) as client:
            resp = await client.get(
                f"{MDBLIST_BASE}/lists/{list_id}/items",
                params=params,
            )
            if resp.status_code == 401:
                logger.warning("MDBList API key is invalid")
                return {"items": [], "page": page, "total_pages": 0, "total_results": 0}
            resp.raise_for_status()
            data = resp.json()

        raw_items = data.get(mdb_type) or []
        total = data.get("total") or 0

        items = [n for raw in raw_items if (n := _normalize_mdblist_item(raw, catalog_type))]

        if total:
            import math

            total_pages = max(1, math.ceil(total / page_size))
        else:
            # No total returned — infer: if full page arrived there may be more
            total_pages = page if len(raw_items) < page_size else page + 1
            total = offset + len(items)

        return {
            "items": items,
            "page": page,
            "total_pages": total_pages,
            "total_results": total,
        }
    except Exception as e:
        logger.error(f"MDBList list items error (list={list_id}): {e}")
        return {"items": [], "page": page, "total_pages": 0, "total_results": 0}
