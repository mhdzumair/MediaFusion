"""
Anime metadata scraper using AniList GraphQL API.

AniList provides anime/manga data with native MAL ID cross-references (idMal).
Docs: https://docs.anilist.co/
"""

import asyncio
import logging
from typing import Any

import httpx

from db.config import settings
from utils.const import UA_HEADER

logger = logging.getLogger(__name__)

ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"

# ---------------------------------------------------------------------------
# GraphQL query fragments & templates
# ---------------------------------------------------------------------------

_MEDIA_FIELDS = """
    id
    idMal
    title { romaji english native }
    format
    status
    episodes
    duration
    startDate { year month day }
    endDate { year month day }
    season
    seasonYear
    description(asHtml: false)
    genres
    studios(isMain: true) { nodes { name } }
    coverImage { large extraLarge }
    bannerImage
    averageScore
    popularity
    favourites
    rankings { rank type context }
    source
    synonyms
    trailer { id site thumbnail }
    isAdult
"""

SEARCH_QUERY = """
query ($search: String!, $type: MediaType, $format: MediaFormat,
       $formatIn: [MediaFormat], $perPage: Int, $isAdult: Boolean) {
  Page(perPage: $perPage) {
    media(search: $search, type: $type, format: $format,
          format_in: $formatIn, isAdult: $isAdult, sort: SEARCH_MATCH) {
      %s
    }
  }
}
""" % _MEDIA_FIELDS

GET_BY_MAL_ID_QUERY = """
query ($malId: Int!) {
  Media(idMal: $malId, type: ANIME) {
    %s
  }
}
""" % _MEDIA_FIELDS

GET_BY_ANILIST_ID_QUERY = """
query ($id: Int!) {
  Media(id: $id, type: ANIME) {
    %s
  }
}
""" % _MEDIA_FIELDS


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

async def _anilist_request(
    query: str,
    variables: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """
    Execute a GraphQL request against AniList.

    Handles 429 rate-limit responses using the Retry-After header with
    exponential backoff fallback.
    """
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    headers = {
        **UA_HEADER,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(
                proxy=settings.requests_proxy_url, timeout=30
            ) as client:
                response = await client.post(
                    ANILIST_GRAPHQL_URL, json=payload, headers=headers
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        int(retry_after)
                        if retry_after and retry_after.isdigit()
                        else min(2 * (2**attempt), 60)
                    )
                    logger.warning(
                        f"AniList rate limited, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                result = response.json()

                if errors := result.get("errors"):
                    for err in errors:
                        if err.get("status") == 404:
                            logger.debug(f"AniList resource not found: {err.get('message')}")
                            return None
                    logger.error(f"AniList GraphQL errors: {errors}")
                    return None

                return result.get("data")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"AniList HTTP error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))
        except Exception as e:
            logger.error(f"AniList request error: {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(1 * (attempt + 1))

    return None


# ---------------------------------------------------------------------------
# Helper: convert AniList date dict to "YYYY-MM-DD" string
# ---------------------------------------------------------------------------

def _format_date(date_obj: dict[str, Any] | None) -> str | None:
    """Convert AniList {year, month, day} to 'YYYY-MM-DD' string."""
    if not date_obj or not date_obj.get("year"):
        return None
    y = date_obj["year"]
    m = date_obj.get("month") or 1
    d = date_obj.get("day") or 1
    return f"{y:04d}-{m:02d}-{d:02d}"


# ---------------------------------------------------------------------------
# AniList format → internal media_type mapping
# ---------------------------------------------------------------------------

_MOVIE_FORMATS = {"MOVIE"}
_SERIES_FORMATS = {"TV", "TV_SHORT", "ONA", "OVA", "SPECIAL"}

# AniList status → human-readable (matches Jikan output)
_STATUS_MAP = {
    "FINISHED": "Finished Airing",
    "RELEASING": "Currently Airing",
    "NOT_YET_RELEASED": "Not yet aired",
    "CANCELLED": "Cancelled",
    "HIATUS": "On Hiatus",
}

# AniList source → human-readable (matches Jikan output)
_SOURCE_MAP = {
    "ORIGINAL": "Original",
    "MANGA": "Manga",
    "LIGHT_NOVEL": "Light Novel",
    "VISUAL_NOVEL": "Visual Novel",
    "VIDEO_GAME": "Video Game",
    "NOVEL": "Novel",
    "DOUJINSHI": "Doujinshi",
    "ANIME": "Anime",
    "WEB_NOVEL": "Web Novel",
    "LIVE_ACTION": "Live Action",
    "GAME": "Game",
    "COMIC": "Comic",
    "MULTIMEDIA_PROJECT": "Multimedia Project",
    "PICTURE_BOOK": "Picture Book",
    "OTHER": "Other",
}


# ---------------------------------------------------------------------------
# Formatting helpers – produce the same dict shape as the old Jikan code
# ---------------------------------------------------------------------------

def _format_anilist_response(data: dict[str, Any]) -> dict[str, Any]:
    """Format AniList media data into the standardized dict used by callers."""
    title_obj = data.get("title") or {}
    title = title_obj.get("english") or title_obj.get("romaji") or "Unknown"
    original_title = title_obj.get("native")

    aka_titles = list(filter(None, [
        title_obj.get("romaji") if title_obj.get("romaji") != title else None,
        title_obj.get("english") if title_obj.get("english") != title else None,
        *(data.get("synonyms") or []),
    ]))

    poster = None
    cover = data.get("coverImage") or {}
    poster = cover.get("extraLarge") or cover.get("large")

    background = data.get("bannerImage")

    genres = data.get("genres") or []

    studios_nodes = (data.get("studios") or {}).get("nodes") or []
    studios = [{"name": s["name"]} for s in studios_nodes if s.get("name")]

    start_date = data.get("startDate")
    end_date = data.get("endDate")
    release_date = _format_date(start_date)
    end_date_str = _format_date(end_date)

    year = (start_date or {}).get("year")
    if not year and release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass

    end_year = None
    status_raw = data.get("status", "")
    if end_date_str and status_raw == "FINISHED":
        try:
            end_year = int(end_date_str[:4])
        except (ValueError, TypeError):
            pass

    al_format = data.get("format") or ""
    media_type = "movie" if al_format in _MOVIE_FORMATS else "series"

    # Trailer
    videos = []
    trailer = data.get("trailer") or {}
    if trailer.get("id") and trailer.get("site") == "youtube":
        yt_id = trailer["id"]
        videos.append({
            "name": "Trailer",
            "url": f"https://www.youtube.com/watch?v={yt_id}",
            "youtube_id": yt_id,
            "type": "Trailer",
            "thumbnail": trailer.get("thumbnail"),
        })

    # Rating: AniList uses 0-100 scale, convert to 0-10
    average_score = data.get("averageScore")
    mal_rating = round(average_score / 10, 1) if average_score else None

    # Duration: AniList stores minutes per episode
    duration_min = data.get("duration")
    runtime = f"{duration_min} min per ep" if duration_min else None

    # Season
    season_raw = data.get("season")
    season = season_raw.lower() if season_raw else None

    # MAL ID (AniList natively stores this)
    mal_id = data.get("idMal")
    mal_id_str = str(mal_id) if mal_id else f"al:{data.get('id')}"

    status = _STATUS_MAP.get(status_raw, status_raw or "Unknown")
    source = _SOURCE_MAP.get(data.get("source") or "", data.get("source"))

    return {
        "mal_id": mal_id_str,
        "title": title,
        "original_title": original_title,
        "year": year,
        "end_year": end_year,
        "release_date": release_date,
        "poster": poster,
        "background": background,
        "description": data.get("description"),
        "genres": genres,
        "runtime": runtime,
        "status": status,
        "studios": studios,
        "network": studios[0]["name"] if studios else None,
        "type": media_type,
        "episodes": [],
        "videos": videos,
        "aka_titles": aka_titles,
        "mal_rating": mal_rating,
        "mal_vote_count": data.get("favourites"),
        "mal_rank": None,
        "mal_popularity": data.get("popularity"),
        "mal_members": None,
        "episode_count": data.get("episodes"),
        "source": source,
        "season": season,
        "broadcast": None,
        "parent_guide_certificates": [],
        "external_ids": {
            "mal_id": mal_id_str,
        },
    }


def _format_anilist_search_result(data: dict[str, Any]) -> dict[str, Any]:
    """Format AniList search result (lighter dict without full details)."""
    title_obj = data.get("title") or {}
    title = title_obj.get("english") or title_obj.get("romaji") or "Unknown"

    cover = data.get("coverImage") or {}
    poster = cover.get("extraLarge") or cover.get("large")

    genres = data.get("genres") or []

    start_date = data.get("startDate")
    release_date = _format_date(start_date)

    year = (start_date or {}).get("year")
    if not year and release_date:
        try:
            year = int(release_date[:4])
        except (ValueError, TypeError):
            pass

    al_format = data.get("format") or ""
    media_type = "movie" if al_format in _MOVIE_FORMATS else "series"

    average_score = data.get("averageScore")
    mal_rating = round(average_score / 10, 1) if average_score else None

    status_raw = data.get("status", "")
    status = _STATUS_MAP.get(status_raw, status_raw or "Unknown")

    mal_id = data.get("idMal")
    mal_id_str = str(mal_id) if mal_id else f"al:{data.get('id')}"

    return {
        "mal_id": mal_id_str,
        "title": title,
        "year": year,
        "release_date": release_date,
        "poster": poster,
        "description": data.get("description"),
        "genres": genres,
        "type": media_type,
        "mal_rating": mal_rating,
        "status": status,
        "episode_count": data.get("episodes"),
        "external_ids": {
            "mal_id": mal_id_str,
        },
    }


# ---------------------------------------------------------------------------
# Public API – same signatures as the old Jikan-based implementation
# ---------------------------------------------------------------------------

async def search_mal(
    query: str,
    media_type: str = "series",
    limit: int = 10,
    sfw: bool = True,
) -> list[dict[str, Any]]:
    """
    Search for anime via AniList.

    Args:
        query: Search query
        media_type: "movie" or "series" (mapped to AniList format)
        limit: Maximum results
        sfw: Filter out NSFW content

    Returns:
        List of raw AniList media dicts (used internally by other functions)
    """
    variables: dict[str, Any] = {
        "search": query,
        "type": "ANIME",
        "perPage": min(limit, 50),
    }

    if sfw:
        variables["isAdult"] = False

    if media_type == "movie":
        variables["format"] = "MOVIE"
    elif media_type == "series":
        variables["formatIn"] = ["TV", "TV_SHORT", "ONA", "OVA"]

    result = await _anilist_request(SEARCH_QUERY, variables)
    if not result:
        return []

    page = result.get("Page") or {}
    return page.get("media") or []


async def get_mal_anime_data(
    mal_id: str,
    include_episodes: bool = True,
) -> dict[str, Any] | None:
    """
    Get detailed anime data by MAL ID (via AniList cross-reference).

    Args:
        mal_id: MAL anime ID
        include_episodes: Kept for API compatibility (AniList does not
            provide per-episode data)

    Returns:
        Formatted anime metadata dict
    """
    try:
        mal_id_int = int(mal_id)
    except (ValueError, TypeError):
        logger.warning(f"Invalid MAL ID: {mal_id}")
        return None

    result = await _anilist_request(GET_BY_MAL_ID_QUERY, {"malId": mal_id_int})
    if not result or not result.get("Media"):
        return None

    return _format_anilist_response(result["Media"])


async def get_mal_anime_episodes(mal_id: str) -> list[dict[str, Any]]:
    """
    Get all episodes for an anime.

    AniList does not provide per-episode titles/synopses, so this returns
    an empty list. Kept for API compatibility.

    Args:
        mal_id: MAL anime ID

    Returns:
        Empty list
    """
    return []


async def get_mal_anime_characters(mal_id: str) -> list[dict[str, Any]]:
    """
    Get character list for an anime.

    Not yet implemented for AniList backend. Kept for API compatibility.

    Args:
        mal_id: MAL anime ID

    Returns:
        Empty list
    """
    return []


async def search_multiple_mal(
    title: str,
    limit: int = 5,
    media_type: str | None = None,
    sfw: bool = True,
) -> list[dict[str, Any]]:
    """
    Search for multiple matching anime on AniList.

    Args:
        title: Title to search for
        limit: Maximum results to return
        media_type: "movie" or "series" (None searches both)
        sfw: Filter out NSFW content

    Returns:
        List of metadata dicts
    """
    results = await search_mal(title, media_type, limit=limit * 2, sfw=sfw)

    full_results = []
    for item in results[:limit]:
        if not item.get("id"):
            continue
        try:
            data = _format_anilist_search_result(item)
            if data:
                full_results.append(data)
        except Exception as e:
            logger.error(f"Error formatting AniList data for {item.get('id')}: {e}")

    return full_results


async def get_mal_by_title(
    title: str,
    year: int | None = None,
    media_type: str = "series",
) -> dict[str, Any] | None:
    """
    Search AniList and return the best matching result with full details.

    Args:
        title: Title to search for
        year: Optional year filter
        media_type: "movie" or "series"

    Returns:
        Best matching metadata dict
    """
    from thefuzz import fuzz

    results = await search_mal(title, media_type, limit=10)

    if not results:
        return None

    best_match = None
    best_score = 0

    for item in results:
        title_obj = item.get("title") or {}
        candidates = filter(None, [
            title_obj.get("english"),
            title_obj.get("romaji"),
            title_obj.get("native"),
        ])
        similarity = max(
            (fuzz.ratio(c.lower(), title.lower()) for c in candidates),
            default=0,
        )

        result_year = (item.get("startDate") or {}).get("year")
        if year and result_year and result_year == year:
            similarity += 10

        if similarity > best_score:
            best_score = similarity
            best_match = item

    if best_match and best_score >= 70:
        mal_id = best_match.get("idMal")
        if mal_id:
            return await get_mal_anime_data(str(mal_id))
        return _format_anilist_response(best_match)

    return None
